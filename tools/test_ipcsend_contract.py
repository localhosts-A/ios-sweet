#!/usr/bin/env python3
"""Check the materialized IPC-send trace and exercise its bounded ring."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
source_value = os.environ.get('SWEET_IPCSEND_SOURCE_DIR')
if not source_value:
    raise AssertionError('SWEET_IPCSEND_SOURCE_DIR must name the patch-applied XNU source')
source_root = Path(source_value).absolute()
if source_root.is_symlink() or not source_root.is_dir():
    raise AssertionError(f'SWEET_IPCSEND_SOURCE_DIR must be a real XNU directory: {source_root}')

mq = (source_root / 'osfmk/ipc/ipc_mqueue.c').read_text()
sample = (source_root / 'bsd/dev/sweet_sample.c').read_text()
header = (source_root / 'osfmk/kern/SweetIPCSendTrace.h').read_text()
progress = (source_root / 'osfmk/kern/SweetLoadProgress.h').read_text()
port_header = (source_root / 'osfmk/ipc/ipc_port.h').read_text()
kern_makefile = (source_root / 'osfmk/kern/Makefile').read_text()

# Bind the instrumentation to the real XNU interfaces and target-gated build.
assert '#if DEVELOPMENT && defined(ARM64_BOARD_CONFIG_SWEET)' in mq
assert '#include <sys/types.h>' not in header
assert '#define SWEET_IPCSEND_CAPACITY 128u' in header
assert 'sweet_load_rw_target(void *);' in progress
assert 'sweet_load_rw_target(current_task())' in mq
assert 'uint32_t\nsweet_load_rw_target(void *task)' in sample
assert 'ipc_port_get_receiver_task_locked(port, NULL)' in mq
assert 'extern pid_t ipc_port_get_receiver_task_locked(ipc_port_t port, uintptr_t *task);' in port_header
xnu_only_exports = kern_makefile[kern_makefile.index('XNU_ONLY_EXPORTS ='):kern_makefile.index('INSTALL_MI_LIST')]
assert 'SweetIPCSendTrace.h' in xnu_only_exports

# Entry is captured only on the actual wait-armed branch and before port unlock.
wait_arm = mq.index('wresult = waitq_assert_wait64_leeway(', mq.index('ipc_mqueue_send_locked('))
entry = mq.index('SWEET_IPCSEND_ENTRY', wait_arm)
unlock_after_entry = mq.index('ip_mq_unlock(port);', entry)
assert entry < unlock_after_entry
assert 'wresult == THREAD_WAITING' in mq[wait_arm:entry]
entry_record = mq[entry:unlock_after_entry]
for scalar in ('port->ip_receiver_name', 'ikm_header(kmsg)->msgh_id', 'option', 'send_timeout'):
    assert scalar in entry_record

# Exit is captured after reacquiring the port lock and before unlocking it.
relock = mq.index('ip_mq_lock(port);', unlock_after_entry)
turnstile = mq.index('turnstile_complete(', relock)
exit_record = mq.index('SWEET_IPCSEND_EXIT', turnstile)
unlock_after_exit = mq.index('ip_mq_unlock(port);', exit_record)
assert turnstile < exit_record < unlock_after_exit
exit_scalars = mq[exit_record:unlock_after_exit]
for scalar in ('port->ip_receiver_name', 'ikm_header(kmsg)->msgh_id', 'option', 'send_timeout'):
    assert scalar in exit_scalars

# Resolve the receiver name only in the lock-free dump path and label its time.
dump_start = mq.index('sweet_ipcsend_dump(unsigned int sample')
dump_end = mq.index('\n}\n#endif', dump_start)
dump = mq[dump_start:dump_end]
assert dump.index('sweet_ipcsend_ring_drain') < dump.index('sweet_ipcsend_receiver_name(')
assert 'ip_mq_lock' not in dump and 'ip_mq_unlock' not in dump
assert 'receiver_name_at_dump=%s' in dump
assert 'dump-time name is not proof of the receiver\'s event-time identity' in dump
name_helper = sample[sample.index('sweet_ipcsend_receiver_name(int pid'):]
name_helper = name_helper[:name_helper.index('\n}\n#endif')]
assert 'proc_name(pid, name, size)' in name_helper
assert name_helper.count('strlcpy(name, "unknown"') == 2
assert '#include <sys/types.h>' not in sample

# The retained ring record is scalar-only and its shared loss count is atomic.
record_start = header.index('struct sweet_ipcsend_record')
record = header[record_start:header.index('};', record_start)]
for forbidden in ('task_t', 'ipc_port_t', 'ipc_kmsg_t', 'void *', 'address', 'payload'):
    assert forbidden not in record
for required in ('token', 'tid', 'target', 'phase', 'msgcount', 'qlimit', 'fullwaiters_flag',
                 'receiver_pid', 'receiver_name', 'msg_id', 'option', 'timeout', 'wait_result'):
    assert required in record
assert 'os_atomic_inc((ptr), relaxed)' in header
assert 'os_atomic_load((ptr), relaxed)' in header
assert 'sweet_ipcsend_ring_append(&sweet_ipcsend_ring, &record)' in mq
assert 'sweet_ipcsend_ring_drain(&sweet_ipcsend_ring, &snapshot)' in mq
assert 'printf("SWEET IPCSENDR:' in mq
close_at = sample.index('sweet_ipcsend_close();')
ufs_close_at = sample.index('sweet_ufs_load_close();', close_at)
assert close_at < ufs_close_at
assert 'sweet_ipcsend_dump(sample, sample == SWEET_LOAD_SAMPLES - 1);' in sample
assert sample.index('sweet_ufs_load_dump(sample);') < sample.index('sweet_ipcsend_dump(sample,')
assert 'SWEET_IPCSEND_CLOSED(&ring->closed)' in header
assert 'SWEET_IPCSEND_CLOSE(&ring->closed)' in header
assert 'ring->used == SWEET_IPCSEND_CAPACITY' in header
assert '++ring->used;' in header
assert 'snapshot->used = ring->used;' in header
assert 'record->target == 0' in header
assert 'sweet_target != 0 && wresult == THREAD_WAITING' in mq
assert 'sweet_entry_recorded = sweet_ipcsend_record(sweet_target, SWEET_IPCSEND_ENTRY,' in mq
assert '== 0;' in mq[entry:unlock_after_entry]
assert 'if (sweet_entry_recorded)' in mq
assert 'sweet_entered_wait' not in mq
assert 'static int\nsweet_ipcsend_record(' in mq
assert 'return sweet_ipcsend_ring_append(&sweet_ipcsend_ring, &record);' in mq
assert 'ip_fullwaiters may be sticky; this scalar is a flag, not a count.' in header
assert 'fullwaiters_flag=%u' in mq

# Compile the host fixture against the exact header materialized for this XNU.
compiler = shutil.which('clang') or shutil.which('cc')
if compiler is None:
    raise AssertionError('a C compiler is required for the IPC-send host fixture')
fixture = ROOT / 'tools/test_ipcsend_ring.c'
with tempfile.TemporaryDirectory(prefix='sweet-ipcsend-ring-') as td:
    temp = Path(td)
    include_dir = temp / 'include'
    include_dir.mkdir()
    shutil.copyfile(source_root / 'osfmk/kern/SweetIPCSendTrace.h',
                    include_dir / 'SweetIPCSendTrace.h')
    binary = temp / 'test_ipcsend_ring'
    subprocess.run([
        compiler, '-std=c11', '-Wall', '-Wextra', '-Werror', '-pthread',
        '-fsanitize=address,undefined', '-fno-omit-frame-pointer',
        '-I', str(include_dir), str(fixture), '-o', str(binary),
    ], check=True, timeout=60)
    result = subprocess.run([str(binary)], capture_output=True, text=True, check=True, timeout=30)
    if 'PASS:' not in result.stdout:
        raise AssertionError(result.stdout + result.stderr)
    print(result.stdout, end='')

# Execute the actual BSD helper with only its XNU leaf calls stubbed.
helper_start = sample.index('void\nsweet_ipcsend_receiver_name(int pid')
helper_end = sample.index('\n}\n#endif', helper_start) + 2
name_helper = sample[helper_start:helper_end]
name_fixture = r'''#define strlcpy sweet_fixture_strlcpy
#include <assert.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>
static int looked_up = -1;
static void proc_name(int pid, char *name, int size) {
    looked_up = pid;
    if (size > 0) name[0] = '\0';
    if (pid == 56 && size > 1) { name[0] = 'b'; name[1] = '\0'; }
}
static size_t strlcpy(char *dst, const char *src, size_t size) {
    size_t len = strlen(src);
    if (size) { size_t n = len < size - 1 ? len : size - 1; memcpy(dst, src, n); dst[n] = '\0'; }
    return len;
}
''' + name_helper + r'''
int main(void) {
    char name[32];
    sweet_ipcsend_receiver_name(56, name, sizeof(name));
    assert(looked_up == 56 && !strcmp(name, "b"));
    looked_up = -1;
    sweet_ipcsend_receiver_name(-1, name, sizeof(name));
    assert(looked_up == -1 && !strcmp(name, "unknown"));
    sweet_ipcsend_receiver_name(99, name, sizeof(name));
    assert(looked_up == 99 && !strcmp(name, "unknown"));
    looked_up = -1;
    sweet_ipcsend_receiver_name(56, name, 0);
    assert(looked_up == -1);
    puts("PASS: dump-time receiver-name lookup and unknown fallback");
    return 0;
}
'''
with tempfile.TemporaryDirectory(prefix='sweet-ipcsend-name-') as td:
    temp = Path(td)
    source = temp / 'test_receiver_name.c'
    source.write_text(name_fixture)
    binary = temp / 'test_receiver_name'
    subprocess.run([
        compiler, '-std=c11', '-Wall', '-Wextra', '-Werror',
        '-fsanitize=address,undefined', '-fno-omit-frame-pointer',
        str(source), '-o', str(binary),
    ], check=True, timeout=60)
    result = subprocess.run([str(binary)], capture_output=True, text=True, check=True, timeout=30)
    if 'PASS:' not in result.stdout:
        raise AssertionError(result.stdout + result.stderr)
    print(result.stdout, end='')

print('PASS: IPC-send source integration contract assertions')
