#!/usr/bin/env python3
"""Exercise recorder mode 2 and kernel-owned spawn flags, without a device."""
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / 'xnu/patches/0169-sweet-bounded-springboard-user-entry.patch'


def fragment(path, patch=PATCH):
    for block in patch.read_text().split('diff --git ')[1:]:
        if f'b/{path}\n' in block:
            return ''.join(line[1:] for line in block.splitlines(keepends=True)
                           if line.startswith(('+', ' ')) and not line.startswith('+++'))
    raise AssertionError(path)


HEADER = fragment('bsd/dev/SweetFeatureTrace.h')
SAMPLER = fragment('bsd/dev/sweet_sample.c')
EXEC = fragment('bsd/kern/kern_exec.c')
FAULT = fragment('bsd/dev/SweetFeatureFault.h',
                 ROOT / 'xnu/patches/0165-sweet-visible-screen-and-user-fault-diagnostics.patch')
GUI_POLICY = fragment('bsd/kern/SweetGUIExecPolicy.h',
                      ROOT / 'xnu/patches/0168-sweet-bounded-gui-exec-outcomes.patch')


class GUIUserEntryTest(unittest.TestCase):
    def run_c(self, body):
        compiler = shutil.which('clang') or shutil.which('cc')
        self.assertIsNotNone(compiler)
        source = '''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <stdarg.h>
''' + body
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'test.c').write_text(source)
            subprocess.run([compiler, '-std=c11', '-O2', '-Wall', '-Wextra', '-Werror',
                            str(root / 'test.c'), '-o', str(root / 'test')], check=True)
            result = subprocess.run([str(root / 'test')], capture_output=True, text=True,
                                    timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), 'PASS')

    def test_modes_exact_names_and_unchanged_ring_bounds(self):
        self.run_c(HEADER + FAULT + '''
int main(void) {
    for(unsigned int mode=0; mode<1000; ++mode)
        assert(sweet_ff_mode(mode)==((mode==1 || mode==2)?mode:0));
    assert(sweet_ff_mode(UINT32_MAX)==0);
    assert(sweet_ff_target(1,"init_featureflag"));
    assert(sweet_ff_target(2,"SpringBoard"));
    assert(!sweet_ff_target(0,"SpringBoard") && !sweet_ff_target(3,"SpringBoard"));
    assert(!sweet_ff_target(1,"SpringBoard") && !sweet_ff_target(2,"init_featureflag"));
    assert(!sweet_ff_target(2,"SpringBoardX") && !sweet_ff_target(2,"SpringBoar"));
    assert(!sweet_ff_target(1,"init_featureflags") && !sweet_ff_target(2,NULL));
    assert(SWEET_FF_RECORDS==64 && SWEET_FF_EVENT_LIMIT==65536 && SWEET_FF_FAULT_LIMIT==4096);
    struct sweet_ff_ring ring={0};struct sweet_ff_event event={.pid=42},out[64];
    assert(sweet_ff_record(&ring,&event) && sweet_ff_snapshot(&ring,out)==1);
    struct sweet_ff_fault_event last={0},fault={.pid=42};char line[256];
    assert(sweet_ff_fault_record(&last,&fault) && sweet_ff_fault_format(line,sizeof(line),&last)>0);
    puts("PASS");return 0;
}
''')

    def test_actual_svc_and_fault_producers_retarget_without_state_mutation(self):
        svc = SAMPLER[SAMPLER.index('static unsigned int sweet_ff_enabled;'):
                      SAMPLER.index('#include "SweetFeatureFault.h"')]
        start = SAMPLER.index('static unsigned int sweet_ff_screen_enabled')
        fault = SAMPLER[start:SAMPLER.index('/* Called only from the normal sampler', start)]
        self.run_c(HEADER + FAULT + '''
typedef uint32_t UInt32;
struct process { char p_comm[32];int pid; };
typedef struct process *proc_t;
#define PROC_NULL NULL
struct arm_saved_state { uint64_t pc,lr,sp,x0;uint32_t cpsr;bool is64; };
static struct process process={"init_featureflag",42};
static bool absent;
static unsigned int current_calls,appends;
static char retained[256];
static proc_t current_proc(void) { ++current_calls;return absent?NULL:&process; }
static int proc_pid(proc_t p) { return p->pid; }
static void *current_thread(void) { return NULL; }
static uint64_t thread_tid(void *p) { (void)p;return 77; }
static bool is_saved_state64(struct arm_saved_state *s) { return s->is64; }
static uint64_t get_saved_state_pc(struct arm_saved_state *s) { return s->pc; }
static uint64_t get_saved_state_lr(struct arm_saved_state *s) { return s->lr; }
static uint64_t get_saved_state_sp(struct arm_saved_state *s) { return s->sp; }
static uint64_t get_saved_state_reg(struct arm_saved_state *s,unsigned n) { assert(n==0);return s->x0; }
static uint32_t get_saved_state_cpsr(struct arm_saved_state *s) { return s->cpsr; }
static bool OSCompareAndSwap(UInt32 old,UInt32 value,volatile UInt32 *p) {
    return __atomic_compare_exchange_n(p,&old,value,false,__ATOMIC_SEQ_CST,__ATOMIC_SEQ_CST);
}
''' + svc + fault + '''
void sweet_ramoops_append(const char *line,int size) {
    assert(!sweet_ff_guard && size>0 && size<256);
    memcpy(retained,line,(unsigned int)size);retained[size]=0;++appends;
}
int main(void) {
    struct arm_saved_state regs={0x1234,0x5678,0x8000,0x99,0,true},before=regs;
    sweet_ff_trace(0,31,&regs);sweet_ff_fault_trace(0,&regs,0,0,-1);
    assert(current_calls==0 && !appends && !sweet_ff_events.total);
    sweet_ff_enabled=3;sweet_ff_fault_enabled=3;
    sweet_ff_trace(0,31,&regs);sweet_ff_fault_trace(0,&regs,0,0,-1);
    assert(current_calls==0 && !appends && !sweet_ff_events.total);
    sweet_ff_enabled=1;sweet_ff_fault_enabled=1;
    sweet_ff_trace(0,31,&regs);sweet_ff_fault_trace(0,&regs,0,0,-1);
    assert(sweet_ff_events.total==1 && appends==1);
    sweet_ff_enabled=2;sweet_ff_fault_enabled=2;
    sweet_ff_trace(0,31,&regs);sweet_ff_fault_trace(0,&regs,0,0,-1);
    assert(sweet_ff_events.total==1 && appends==1);
    strcpy(process.p_comm,"SpringBoard");struct process pbefore=process;
    sweet_ff_enabled=1;sweet_ff_fault_enabled=1;
    sweet_ff_trace(0,31,&regs);sweet_ff_fault_trace(0,&regs,0,0,-1);
    assert(sweet_ff_events.total==1 && appends==1);
    sweet_ff_enabled=2;sweet_ff_fault_enabled=2;
    absent=true;sweet_ff_trace(0,31,&regs);sweet_ff_fault_trace(0,&regs,0,0,-1);absent=false;
    regs.is64=false;sweet_ff_trace(0,31,&regs);sweet_ff_fault_trace(0,&regs,0,0,-1);regs=before;
    assert(sweet_ff_events.total==1 && appends==1);
    sweet_ff_guard=1;sweet_ff_trace(0,31,&regs);sweet_ff_fault_trace(0,&regs,0,0,-1);
    assert(sweet_ff_dropped && sweet_ff_fault_dropped && sweet_ff_guard==1);sweet_ff_guard=0;
    for(unsigned i=0;i<4;++i)sweet_ff_trace(i,31,&regs);
    sweet_ff_fault_trace(0,&regs,0x92000004,0x1234000,-1);
    sweet_ff_fault_trace(1,&regs,0x92000004,0x1234000,0);
    assert(sweet_ff_events.total==5 && appends==3 && strstr(retained,"phase=1") && strstr(retained,"result=0"));
    assert(sweet_ff_events.events[4].pid==42 && sweet_ff_events.events[4].phase==3);
    while(sweet_ff_events.total<SWEET_FF_EVENT_LIMIT)sweet_ff_trace(0,31,&regs);
    while(sweet_ff_last_fault.seq<SWEET_FF_FAULT_LIMIT)sweet_ff_fault_trace(0,&regs,0,0,-1);
    for(unsigned i=0;i<100;++i) { sweet_ff_trace(0,31,&regs);sweet_ff_fault_trace(0,&regs,0,0,-1); }
    assert(sweet_ff_events.total==65536 && sweet_ff_events.count==64 && appends==4096);
    assert(!sweet_ff_guard && !sweet_ff_screen_enabled);
    assert(!memcmp(&regs,&before,sizeof(regs)) && !memcmp(&process,&pbefore,sizeof(process)));
    struct sweet_ff_event snapshot[64];
    assert(sweet_ff_snapshot(&sweet_ff_events,snapshot)==64 && sweet_ff_events.count==0);
    puts("PASS");return 0;
}
''')

    def test_actual_startup_validates_modes_and_keeps_screen_opt_in(self):
        start = SAMPLER.index('void\nsweet_sample_start(void)')
        startup = SAMPLER[start:]
        self.run_c(HEADER + '''
#define DEVELOPMENT 1
#define ARM64_BOARD_CONFIG_SWEET 1
#define KERN_SUCCESS 0
typedef void *thread_t;
static unsigned int sweet_ff_enabled,sweet_ff_fault_enabled,sweet_ff_screen_enabled;
static unsigned int svc,fault,screen,starts,deallocations,outputs;
static bool found;
static void sweet_sample_thread(void *p,int result) { (void)p;(void)result; }
static int PE_parse_boot_argn(const char *key,void *value,unsigned int size) {
    assert(size==sizeof(unsigned int));
    if(!found)return 0;
    if(!strcmp(key,"sweetfftrace"))*(unsigned int *)value=svc;
    else if(!strcmp(key,"sweetfffault"))*(unsigned int *)value=fault;
    else { assert(!strcmp(key,"sweetffscreen"));*(unsigned int *)value=screen; }
    return 1;
}
static int kernel_thread_start(void (*callback)(void *,int),void *arg,thread_t *thread) {
    assert(callback==sweet_sample_thread && !arg);*thread=(void *)(uintptr_t)1;++starts;return 0;
}
static void thread_deallocate(thread_t thread) { assert(thread==(void *)(uintptr_t)1);++deallocations; }
static int capture(const char *format,unsigned int a,unsigned int b) {
    assert(strstr(format,"SWEET UENTRY CONFIG:") && a==sweet_ff_mode(svc) && b==sweet_ff_mode(fault));
    assert(a || b);++outputs;return 0;
}
#define printf capture
''' + startup + '''
#undef printf
int main(void) {
    sweet_sample_start();assert(!sweet_ff_enabled && !sweet_ff_fault_enabled && !sweet_ff_screen_enabled && !outputs);
    found=true;
    for(unsigned int i=0;i<5;++i) {
        svc=fault=screen=i;unsigned before=outputs;sweet_sample_start();
        assert(sweet_ff_enabled==sweet_ff_mode(i) && sweet_ff_fault_enabled==sweet_ff_mode(i));
        assert(sweet_ff_screen_enabled==(i==1));assert(outputs==before+((i==1 || i==2)?1:0));
    }
    assert(starts==6 && deallocations==6 && sweet_ff_target(2,"SpringBoard"));
    struct sweet_ff_ring ring={0};struct sweet_ff_event event={0},snapshot[64];
    assert(sweet_ff_record(&ring,&event) && sweet_ff_snapshot(&ring,snapshot)==1);
    puts("PASS");return 0;
}
''')

    def test_actual_exec_observer_spawnflags_validity_and_unchanged_quota(self):
        observer = EXEC[EXEC.index('static void\nsweet_gui_exec_observe('):EXEC.index('\n#else')]
        self.run_c('''
typedef uint32_t UInt32;
static UInt32 OSAddAtomic(int n,volatile UInt32 *p) { return __atomic_add_fetch(p,(UInt32)n,__ATOMIC_RELAXED); }
static bool OSCompareAndSwap(UInt32 old,UInt32 value,volatile UInt32 *p) {
    return __atomic_compare_exchange_n(p,&old,value,false,__ATOMIC_RELAXED,__ATOMIC_RELAXED);
}
''' + GUI_POLICY + '''
struct proc { const char *p_name;int pid;uint64_t csflags; };
typedef struct proc *proc_t;
#define PROC_NULL NULL
static unsigned int enabled,outputs,expected_spawn;
static int PE_parse_boot_argn(const char *key,void *out,int size) {
    assert(!strcmp(key,"sweetguiexec") && size==(int)sizeof(unsigned));*(unsigned *)out=enabled;return 1;
}
static int proc_getpid(proc_t p) { return p->pid; }
static uint64_t proc_getcsflags(proc_t p) { return p->csflags; }
static int capture(const char *format,const char *name,int pid,const char *phase,int result,
    unsigned long long cs,unsigned image,unsigned spawn,unsigned valid,unsigned ticket,unsigned exhausted) {
    assert(strstr(format,"spawnflags_valid=%u") && !strcmp(name,"SpringBoard") && pid==26);
    assert(result==13 && cs==0x123456789abcdef0ULL && image==0x41c && spawn==expected_spawn);
    assert(valid==(!strcmp(phase,"spawnattr-return")?1u:0u));
    assert(ticket==outputs+1 && exhausted==(ticket==32));++outputs;return 0;
}
#define printf capture
''' + observer + '''
#undef printf
int main(void) {
    struct proc process={"SpringBoard",26,0x123456789abcdef0ULL},before=process;
    sweet_gui_exec_observe(&process,"spawnattr-return",13,0x41c,0x8180);assert(!outputs);
    enabled=1;expected_spawn=0x8180;
    sweet_gui_exec_observe(&process,"spawnattr-return",13,0x41c,0x8180);
    expected_spawn=0;
    for(unsigned i=0;i<100;++i)sweet_gui_exec_observe(&process,"release-after",13,0x41c,0);
    assert(outputs==32 && !memcmp(&process,&before,sizeof(process)));
    puts("PASS");return 0;
}
''')

    def test_no_new_hooks_or_resume_and_flags_read_only_in_live_attr_scope(self):
        text = PATCH.read_text()
        files = re.findall(r'^diff --git a/(\S+) b/', text, re.M)
        self.assertEqual(files, ['bsd/dev/SweetFeatureTrace.h', 'bsd/dev/sweet_sample.c',
                                 'bsd/kern/kern_exec.c'])
        self.assertEqual(EXEC.count('psa->psa_flags'), 1)
        self.assertIn('"spawnattr-return", error, imgp->ip_flags, (unsigned int)(uint16_t)psa->psa_flags', EXEC)
        calls = re.findall(r'sweet_gui_exec_observe\(p, ("[^"]+"), ([^,]+), imgp->ip_flags, ([^;]+)\);', EXEC)
        self.assertEqual(len(calls), 12)
        for phase, result, flags in calls:
            self.assertEqual(flags, '(unsigned int)(uint16_t)psa->psa_flags' if phase=='"spawnattr-return"' else '0')
        observer = EXEC[EXEC.index('static void\nsweet_gui_exec_observe('):EXEC.index('\n#else')]
        for forbidden in ['ip_px_sa', 'p_stat', 'proc_lock', 'task_lock', 'proc_iterate',
                          'copyin', 'task_resume', 'task_suspend']:
            self.assertNotIn(forbidden, observer)
        self.assertIn('sweet_ff_enabled = sweet_ff_mode(enabled);', SAMPLER)
        self.assertIn('sweet_ff_fault_enabled = sweet_ff_mode(enabled);', SAMPLER)
        self.assertIn('&& sweet_ff_enabled == 0 && sweet_ff_screen_enabled != 1', SAMPLER)
        self.assertIn('sweet_ff_screen_enabled = enabled == 1 ? 1u : 0u;', SAMPLER)
        self.assertIn('i < 40', SAMPLER)
        self.assertEqual(SAMPLER.count('kernel_thread_start('), 1)


if __name__ == '__main__':
    unittest.main()
