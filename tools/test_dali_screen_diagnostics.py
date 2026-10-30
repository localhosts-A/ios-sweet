#!/usr/bin/env python3
"""Exercise actual visible-diagnostic helpers without a phone or kernel."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / 'xnu/patches/0165-sweet-visible-screen-and-user-fault-diagnostics.patch'


def added(path, patch=PATCH):
    for block in patch.read_text().split('diff --git ')[1:]:
        if f'b/{path}\n' in block:
            return ''.join(line[1:] for line in block.splitlines(keepends=True)
                           if line.startswith('+') and not line.startswith('+++'))
    raise AssertionError(path)


class ScreenDiagnosticsTest(unittest.TestCase):
    def compile_run(self, source):
        compiler = shutil.which('clang') or shutil.which('cc')
        self.assertIsNotNone(compiler)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'trace.c').write_text(source)
            subprocess.run([compiler, '-std=c11', '-O2', '-Wall', '-Wextra', '-Werror',
                            str(root / 'trace.c'), '-o', str(root / 'trace')], check=True)
            result = subprocess.run([str(root / 'trace')], capture_output=True, text=True,
                                    timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), 'PASS')

    def test_actual_fault_header_bounds_format_and_immutability(self):
        self.compile_run('''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
''' + added('bsd/dev/SweetFeatureFault.h') + '''
int main(void) {
    struct sweet_ff_fault_event last={0};
    struct sweet_ff_fault_event input={.phase=UINT32_MAX,.esr=UINT32_MAX,
      .pid=INT32_MIN,.result=INT32_MIN,.tid=UINT64_MAX,.pc=UINT64_MAX,
      .lr=UINT64_MAX,.sp=UINT64_MAX,.far=UINT64_MAX};
    struct sweet_ff_fault_event before=input;
    char output[257];output[256]='X';
    for(unsigned int i=1;i<=SWEET_FF_FAULT_LIMIT;++i) {
        assert(sweet_ff_fault_record(&last,&input) && last.seq==i);
        assert(!memcmp(&input,&before,sizeof(input)));
        int n=sweet_ff_fault_format(output,256,&last);
        assert(n>0 && n<256 && output[n-1]=='\\n' && output[n]==0 && output[256]=='X');
        assert(strstr(output,"SWEET FFFAULT:") && strstr(output,"far=0xffffffffffffffff"));
    }
    struct sweet_ff_fault_event full=last;
    for(unsigned int i=0;i<100;++i)assert(!sweet_ff_fault_record(&last,&input));
    assert(!memcmp(&last,&full,sizeof(last)));
    assert(!sweet_ff_fault_format(output,1,&last) && output[0]==0);
    assert(!sweet_ff_fault_format(output,8,&last));
    puts("PASS");return 0;
}
''')

    def test_actual_helpers_ram_only_faults_and_safe_thread_screen(self):
        source = added('bsd/dev/sweet_sample.c')
        start = source.index('static unsigned int sweet_ff_screen_enabled')
        # Stop at the later loop-condition hunk, not at an unrelated #endif.
        end = source.index('\n        && sweet_ff_enabled', start)
        helper = source[start:end]
        old = ROOT / 'xnu/patches/0164-sweet-bounded-featureflags-svc-ring.patch'
        self.compile_run('''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <stdarg.h>
''' + added('bsd/dev/SweetFeatureTrace.h', old) + added('bsd/dev/SweetFeatureFault.h') + '''
typedef uint32_t UInt32;
struct process { char p_comm[32]; int pid; };
typedef struct process *proc_t;
#define PROC_NULL NULL
struct arm_saved_state { uint64_t pc,lr,sp;bool is64; };
static struct process process={"init_featureflag",42};
static bool absent;
static unsigned int prints,appends;
static char retained[256];
static volatile UInt32 sweet_ff_guard;
static struct sweet_ff_ring sweet_ff_events;
int sweet_console_muted=1;
static proc_t current_proc(void) { return absent ? NULL : &process; }
static int proc_pid(proc_t p) { return p->pid; }
static void *current_thread(void) { return NULL; }
static uint64_t thread_tid(void *p) { (void)p;return 77; }
static bool is_saved_state64(struct arm_saved_state *s) { return s->is64; }
static uint64_t get_saved_state_pc(struct arm_saved_state *s) { return s->pc; }
static uint64_t get_saved_state_lr(struct arm_saved_state *s) { return s->lr; }
static uint64_t get_saved_state_sp(struct arm_saved_state *s) { return s->sp; }
static bool OSCompareAndSwap(UInt32 old,UInt32 value,volatile UInt32 *p) {
    return __atomic_compare_exchange_n(p,&old,value,false,__ATOMIC_SEQ_CST,__ATOMIC_SEQ_CST);
}
void sweet_ramoops_append(const char *s,int size) {
    assert(!sweet_ff_guard && size>0 && size<256);
    memcpy(retained,s,(unsigned int)size);retained[size]=0;++appends;
}
static int screen_output(const char *format,...) {
    assert(!sweet_console_muted && strstr(format,"SWEET SCREEN:"));
    assert(!sweet_ff_guard || strstr(format,"observer-busy"));++prints;return 0;
}
#define printf screen_output
''' + helper + '''
#undef printf
int main(void) {
    struct arm_saved_state regs={0x1234,0x5678,0x8000,true},before=regs;
    struct process pbefore=process;
    sweet_ff_fault_trace(0,&regs,0x92000004,0x1234000,-1);
    sweet_ff_screen_dump(0);assert(!appends && !prints && sweet_console_muted);
    sweet_ff_fault_enabled=2;sweet_ff_fault_trace(0,&regs,0,0,-1);assert(!appends);
    sweet_ff_fault_enabled=1;absent=true;sweet_ff_fault_trace(0,&regs,0,0,-1);absent=false;
    strcpy(process.p_comm,"other");sweet_ff_fault_trace(0,&regs,0,0,-1);process=pbefore;
    regs.is64=false;sweet_ff_fault_trace(0,&regs,0,0,-1);regs=before;assert(!appends);
    sweet_ff_guard=1;sweet_ff_fault_trace(0,&regs,0,0,-1);assert(sweet_ff_fault_dropped);
    sweet_ff_screen_enabled=1;sweet_ff_screen_dump(0);assert(prints==1 && !sweet_console_muted);
    sweet_ff_guard=0;
    sweet_ff_fault_trace(0,&regs,0x92000004,0x1234000,-1);
    assert(appends==1 && prints==1 && strstr(retained,"phase=0") && strstr(retained,"result=-1"));
    sweet_ff_fault_trace(1,&regs,0x92000004,0x1234000,0);
    assert(appends==2 && prints==1 && strstr(retained,"phase=1") && strstr(retained,"result=0"));
    struct sweet_ff_event svc={.call=-12,.phase=1,.pc=0xabcdef};
    assert(sweet_ff_record(&sweet_ff_events,&svc));
    sweet_console_muted=1;sweet_ff_screen_dump(9);
    assert(prints==4 && !sweet_console_muted && !sweet_ff_fault_dropped);
    while(sweet_ff_last_fault.seq<SWEET_FF_FAULT_LIMIT)sweet_ff_fault_trace(0,&regs,0,0,-1);
    assert(appends==SWEET_FF_FAULT_LIMIT);
    for(unsigned int i=0;i<20;++i)sweet_ff_fault_trace(0,&regs,0,0,-1);
    assert(appends==SWEET_FF_FAULT_LIMIT && !sweet_ff_guard);
    assert(!memcmp(&regs,&before,sizeof(regs)) && !memcmp(&process,&pbefore,sizeof(process)));
    sweet_ff_screen_enabled=0;sweet_ff_screen_dump(10);assert(prints==4);
    struct sweet_ff_event snapshot[SWEET_FF_RECORDS];
    assert(sweet_ff_snapshot(&sweet_ff_events,snapshot)==1 && snapshot[0].call==-12);
    puts("PASS");return 0;
}
''')

    def test_fault_hooks_and_screen_mode_are_explicit_and_separate(self):
        sampler = added('bsd/dev/sweet_sample.c')
        sleh = added('osfmk/arm64/sleh.c')
        self.assertIn('"sweetffscreen"', sampler)
        self.assertIn('"sweetfffault"', sampler)
        self.assertIn('sweet_ff_screen_enabled = enabled == 1 ? 1u : 0u', sampler)
        self.assertIn('sweet_ff_fault_enabled = enabled == 1 ? 1u : 0u', sampler)
        self.assertIn('sweet_ff_enabled != 1 && sweet_ff_screen_enabled != 1', sampler)
        self.assertIn('sweet_ff_fault_trace(0, state, esr, fault_addr, -1)', sleh)
        self.assertIn('sweet_ff_fault_trace(1, state, esr, fault_addr, result)', sleh)
        producer = sampler[sampler.index('void\nsweet_ff_fault_trace('):
                           sampler.index('/* Called only from the normal sampler')]
        for forbidden in ['printf(', 'sweet_early_', 'dali_display_', 'vm_map_read_user',
                          'copyin(', 'proc_iterate(', 'task_lock(']:
            self.assertNotIn(forbidden, producer)
        self.assertIn('sweet_ramoops_append(line, size)', producer)


if __name__ == '__main__':
    unittest.main()
