#!/usr/bin/env python3
"""Exercise actual bounded GUI exec observation code without a device."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / 'xnu/patches/0168-sweet-bounded-gui-exec-outcomes.patch'


def patch_file(path):
    for block in PATCH.read_text().split('diff --git ')[1:]:
        if f'b/{path}\n' in block:
            return ''.join(line[1:] for line in block.splitlines(keepends=True)
                           if line.startswith(('+', ' ')) and not line.startswith('+++'))
    raise AssertionError(path)


POLICY = patch_file('bsd/kern/SweetGUIExecPolicy.h')
EXEC = patch_file('bsd/kern/kern_exec.c')
OBSERVER = EXEC[EXEC.index('static void\nsweet_gui_exec_observe('):EXEC.index('\n#else')]


class GUIExecTest(unittest.TestCase):
    def run_c(self, body):
        compiler = shutil.which('clang') or shutil.which('cc')
        self.assertIsNotNone(compiler)
        source = '''
#include <assert.h>
#include <pthread.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
typedef uint32_t UInt32;
static unsigned int cas_calls, force_cas_failure;
static UInt32 OSAddAtomic(int n, volatile UInt32 *p) {
    return __atomic_add_fetch(p, (UInt32)n, __ATOMIC_RELAXED);
}
static bool OSCompareAndSwap(UInt32 old, UInt32 n, volatile UInt32 *p) {
    __atomic_add_fetch(&cas_calls, 1, __ATOMIC_RELAXED);
    if (__atomic_load_n(&force_cas_failure, __ATOMIC_RELAXED)) return false;
    return __atomic_compare_exchange_n(p, &old, n, false,
        __ATOMIC_RELAXED, __ATOMIC_RELAXED);
}
''' + POLICY + body
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'test.c').write_text(source)
            subprocess.run([compiler, '-std=c11', '-O2', '-Wall', '-Wextra', '-Werror',
                            '-pthread', str(root / 'test.c'), '-o', str(root / 'test')],
                           check=True)
            result = subprocess.run([str(root / 'test')], capture_output=True, text=True,
                                    timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), 'PASS')

    def test_exact_targets_independent_quota_and_nonwaiting_contention(self):
        self.run_c('''
int main(void) {
    assert(SWEET_GUI_EXEC_TARGETS == 5 && SWEET_GUI_EXEC_LIMIT == 32);
    assert(sweet_gui_exec_kind(NULL) == 5);
    const char *bad[] = {"", "Spring", "SpringBoardX", "backboard", "xpcproxy2", "launchd"};
    for(unsigned i=0; i<sizeof(bad)/sizeof(bad[0]); ++i)
        assert(sweet_gui_exec_kind(bad[i]) == 5);
    volatile UInt32 counters[5] = {0};
    for(unsigned k=0; k<5; ++k) {
        assert(sweet_gui_exec_kind(sweet_gui_exec_names[k]) == k);
        for(unsigned i=1; i<=32; ++i)
            assert(sweet_gui_exec_ticket(&counters[k]) == i);
        for(unsigned i=0; i<100; ++i)
            assert(sweet_gui_exec_ticket(&counters[k]) == 0);
        assert(counters[k] == 32);
        for(unsigned next=k+1; next<5; ++next) assert(counters[next] == 0);
    }
    volatile UInt32 contended = 3;
    unsigned before = cas_calls; force_cas_failure = 1;
    assert(sweet_gui_exec_ticket(&contended) == 0);
    assert(cas_calls == before+1 && contended == 3);
    force_cas_failure = 0;
    assert(sweet_gui_exec_ticket(&contended) == 4);
    contended = UINT32_MAX;
    assert(sweet_gui_exec_ticket(&contended) == 0 && contended == UINT32_MAX);
    puts("PASS"); return 0;
}
''')

    def test_concurrent_quota_never_overflows_or_duplicates(self):
        self.run_c('''
static volatile UInt32 counter;
static unsigned tickets[33];
static void *producer(void *arg) {
    (void)arg;
    for(unsigned i=0; i<10000; ++i) {
        unsigned ticket = sweet_gui_exec_ticket(&counter);
        if(ticket) {
            assert(ticket<=32);
            __atomic_add_fetch(&tickets[ticket], 1, __ATOMIC_RELAXED);
        }
    }
    return NULL;
}
int main(void) {
    assert(sweet_gui_exec_kind("SpringBoard") == 0);
    pthread_t threads[8];
    for(unsigned i=0; i<8; ++i) assert(!pthread_create(&threads[i], NULL, producer, NULL));
    for(unsigned i=0; i<8; ++i) assert(!pthread_join(threads[i], NULL));
    assert(counter==32);
    for(unsigned i=1; i<=32; ++i) assert(tickets[i]==1);
    puts("PASS"); return 0;
}
''')

    def test_actual_observer_defaults_filter_state_invariance_and_raw_result(self):
        self.run_c('''
struct proc { const char *p_name; int pid; uint64_t csflags; };
typedef struct proc *proc_t;
#define PROC_NULL NULL
static int boot_found, boot_value, outputs;
static int PE_parse_boot_argn(const char *name, void *p, int size) {
    assert(!strcmp(name,"sweetguiexec") && size==(int)sizeof(unsigned int));
    if(boot_found) *(unsigned int *)p=(unsigned int)boot_value;
    return boot_found;
}
static int proc_getpid(proc_t p) { return p->pid; }
static uint64_t proc_getcsflags(proc_t p) { return p->csflags; }
static int capture(const char *format, const char *name, int pid, const char *phase,
    int result, unsigned long long flags, unsigned int imageflags,
    unsigned int ticket, unsigned int exhausted) {
    assert(strstr(format,"SWEET GUIEXEC:") && !strcmp(name,"SpringBoard"));
    assert(pid==42 && !strcmp(phase,"fixture") && result==13);
    assert(flags==0x123456789abcdef0ULL && imageflags==0xa5);
    assert(ticket==(unsigned int)outputs+1 && exhausted==(ticket==32));
    ++outputs; return 0;
}
#define printf capture
''' + OBSERVER + '''
#undef printf
int main(void) {
    struct proc target={"SpringBoard",42,0x123456789abcdef0ULL};
    struct proc original=target;
    sweet_gui_exec_observe(&target,"fixture",13,0xa5);
    boot_found=1; boot_value=0;
    sweet_gui_exec_observe(&target,"fixture",13,0xa5);
    boot_value=2; sweet_gui_exec_observe(&target,"fixture",13,0xa5);
    boot_value=1; sweet_gui_exec_observe(NULL,"fixture",13,0xa5);
    struct proc ignored={"SpringBoardX",43,0};
    sweet_gui_exec_observe(&ignored,"fixture",13,0xa5);
    assert(outputs==0);
    for(unsigned i=0; i<100; ++i) sweet_gui_exec_observe(&target,"fixture",13,0xa5);
    assert(outputs==32 && !memcmp(&target,&original,sizeof(target)));
    puts("PASS"); return 0;
}
''')

    def test_actual_signature_failure_retains_error_before_spawn_zeroes_it(self):
        start = EXEC.index('\t\tif (!unexpected_failure)')
        end = EXEC.index('\n\tif (port != IPC_PORT_NULL)', start)
        failure = EXEC[start:end]
        final = next(line for line in EXEC.splitlines() if '"signature-final"' in line)
        self.run_c('''
struct proc { const char *p_name; int pid; uint64_t csflags; };
typedef struct proc *proc_t;
#define PROC_NULL NULL
#define CS_KILLED 0x1000000u
#define SIGKILL 9
#define OS_REASON_NULL 0
static int observed_failure, observed_final, signals;
static int PE_parse_boot_argn(const char *name, void *p, int size) {
    assert(!strcmp(name,"sweetguiexec") && size==(int)sizeof(unsigned int));
    *(unsigned int *)p=1; return 1;
}
static int proc_getpid(proc_t p) { return p->pid; }
static uint64_t proc_getcsflags(proc_t p) { return p->csflags; }
static void proc_csflags_set(proc_t p, uint64_t flags) { p->csflags |= flags; }
static void *proc_task(proc_t p) { return p; }
static void psignal_with_reason(proc_t p, int sig, unsigned reason) {
    assert(p && sig==SIGKILL && reason==7 && observed_failure!=0); ++signals;
}
static void psignal_vfork_with_reason(proc_t p, void *task, int thread,
    int sig, unsigned reason) {
    assert(task==p && thread==17); psignal_with_reason(p,sig,reason);
}
static int capture(const char *format, const char *name, int pid, const char *phase,
    int result, unsigned long long flags, unsigned int imageflags,
    unsigned int ticket, unsigned int exhausted) {
    assert(strstr(format,"SWEET GUIEXEC:") && !strcmp(name,"SpringBoard"));
    assert(pid==42 && ticket && ticket<=32 && !exhausted && imageflags==0xa5);
    assert(flags==0 || flags==CS_KILLED);
    if(!strcmp(phase,"signature-failure-before-kill")) observed_failure=result;
    else { assert(!strcmp(phase,"signature-final")); observed_final=result; }
    return 0;
}
#define printf capture
''' + OBSERVER + '''
static int drive(int spawn, int unexpected_failure, int error) {
    struct proc target={"SpringBoard",42,0}; proc_t p=&target;
    struct { unsigned ip_flags; int ip_new_thread; } image={0xa5,17}, *imgp=&image;
    const int vfexec=0;
    unsigned signature_failure_reason=7;
    observed_failure=0; observed_final=-1;
    if(error==0) {} else {
''' + failure + '\n' + final + '''
    assert(signature_failure_reason==OS_REASON_NULL);
    assert((target.csflags==CS_KILLED)==!unexpected_failure);
    return error;
}
#undef printf
int main(void) {
    assert(drive(1,0,13)==0 && observed_failure==13 && observed_final==0);
    assert(drive(0,0,22)==22 && observed_failure==22 && observed_final==22);
    assert(drive(1,1,18)==0 && observed_failure==18 && observed_final==0);
    assert(signals==3);
    puts("PASS"); return 0;
}
''')

    def test_hooks_are_additive_and_cover_both_paths_and_failure_before_reset(self):
        text = PATCH.read_text()
        removed = [line for line in text.splitlines()
                   if line.startswith('-') and not line.startswith('---')]
        self.assertEqual(removed, [], 'Existing exec/signature/control flow must be unchanged')
        self.assertEqual(EXEC.count('"activate-return"'), 2)
        self.assertEqual(EXEC.count('"release-before"'), 2)
        self.assertEqual(EXEC.count('"release-after"'), 2)
        self.assertIn('"spawnattr-return"', EXEC)
        self.assertIn('"signature-enter"', EXEC)
        self.assertIn('"proc-signature-return"', EXEC)
        self.assertIn('"thread-signature-return"', EXEC)
        failure = EXEC.index('"signature-failure-before-kill"')
        signal = EXEC.index('psignal_vfork_with_reason', failure)
        reset = EXEC.index('error = 0;', signal)
        self.assertLess(failure, signal)
        self.assertLess(signal, reset)
        for block in text.split('@@')[2::2]:
            if '"activate-return"' in block:
                self.assertLess(block.index('proc_exec_switch_task'),
                                block.index('"activate-return"'))
            if '"release-before"' in block:
                self.assertLess(block.index('"release-before"'),
                                block.index('TCRW_CLEAR_FINAL_WAIT'))
                self.assertLess(block.index('TCRW_CLEAR_FINAL_WAIT'),
                                block.index('"release-after"'))
                self.assertLess(block.index('"release-after"'),
                                block.index('thread_deallocate'))
        for forbidden in ['copyin', 'proc_iterate', 'task_lock', 'thread_deallocate',
                          'proc_rele', 'psignal', 'while (']:
            self.assertNotIn(forbidden, OBSERVER)


if __name__ == '__main__':
    unittest.main()
