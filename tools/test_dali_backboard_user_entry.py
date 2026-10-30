#!/usr/bin/env python3
"""Exercise SVC-only backboardd selection with unchanged bounded recorder paths."""
from pathlib import Path
import unittest
import test_dali_gui_user_entry as gui_fixture

FAULT = gui_fixture.FAULT
fragment = gui_fixture.fragment

ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / 'xnu/patches/0171-sweet-bounded-backboard-user-entry.patch'
HEADER = fragment('bsd/dev/SweetFeatureTrace.h', PATCH)
SAMPLER = fragment('bsd/dev/sweet_sample.c', PATCH)
SVC = SAMPLER[SAMPLER.index('static unsigned int sweet_ff_enabled;'):
              SAMPLER.index('#include "SweetFeatureFault.h"')]
START = SAMPLER.index('static unsigned int sweet_ff_screen_enabled')
FAULT_PRODUCER = SAMPLER[START:SAMPLER.index('/* Called only from the normal sampler', START)]
MOCKS = r'''
typedef uint32_t UInt32;
struct process { char p_comm[32];int pid; };
typedef struct process *proc_t;
#define PROC_NULL NULL
struct arm_saved_state { uint64_t pc,lr,sp,x0;uint32_t cpsr;bool is64; };
static struct process process={"backboardd",56};
static unsigned int current_calls,appends;
static bool absent;
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
'''
RETAIN = r'''
void sweet_ramoops_append(const char *line,int size) {
    assert(!sweet_ff_guard && size>0 && size<256 && strstr(line,"SWEET FFFAULT:"));++appends;
}
'''
PRODUCERS = HEADER + FAULT + MOCKS + SVC + FAULT_PRODUCER + RETAIN


class BackboardUserEntryTest(unittest.TestCase):
    run_c = gui_fixture.GUIUserEntryTest.run_c

    def test_exact_svc_only_mode_and_unchanged_ring(self):
        self.run_c(HEADER + FAULT + r'''
int main(void) {
    const char *names[]={"init_featureflag","SpringBoard","backboardd"};
    for(unsigned i=0;i<1000;++i) {
        assert(sweet_ff_mode(i)==((i==1 || i==2)?i:0));
        assert(sweet_ff_svc_mode(i)==((i>=1 && i<=3)?i:0));
        for(unsigned j=0;j<3;++j)assert(sweet_ff_target(i,names[j])==(i==j+1));
    }
    assert(!sweet_ff_svc_mode(UINT32_MAX) && !sweet_ff_mode(UINT32_MAX));
    assert(!sweet_ff_target(3,NULL) && !sweet_ff_target(3,"backboard") && !sweet_ff_target(3,"backboarddX"));
    assert(!sweet_ff_target(3,"Backboardd") && !sweet_ff_target(3,"xpcproxy"));
    assert(SWEET_FF_RECORDS==64 && SWEET_FF_EVENT_LIMIT==65536 && SWEET_FF_FAULT_LIMIT==4096);
    struct sweet_ff_ring ring={0};struct sweet_ff_event e={.pid=56},out[64];
    for(unsigned i=0;i<65536;++i)assert(sweet_ff_record(&ring,&e));
    assert(!sweet_ff_record(&ring,&e) && ring.total==65536 && ring.count==64);
    assert(sweet_ff_snapshot(&ring,out)==64 && !ring.count && out[0].seq==65473 && out[63].seq==65536);
    struct sweet_ff_fault_event last={0},f={.pid=56};char line[256];
    assert(sweet_ff_fault_record(&last,&f) && sweet_ff_fault_format(line,sizeof(line),&last)>0);
    puts("PASS");return 0;
}
''')

    def test_actual_producers_filter_drop_bound_and_preserve_state(self):
        self.run_c(PRODUCERS + r'''
int main(void) {
    struct arm_saved_state regs={0x1234,0x5678,0x8000,0x99,0,true},before=regs;
    sweet_ff_trace(0,31,&regs);sweet_ff_fault_trace(0,&regs,0,0,-1);
    assert(!current_calls && !appends);
    sweet_ff_fault_enabled=3;sweet_ff_fault_trace(0,&regs,0,0,-1);
    assert(!current_calls && !appends && !sweet_ff_last_fault.seq);
    sweet_ff_enabled=3;
    strcpy(process.p_comm,"SpringBoard");sweet_ff_trace(0,31,&regs);
    strcpy(process.p_comm,"backboarddX");sweet_ff_trace(0,31,&regs);
    strcpy(process.p_comm,"xpcproxy");sweet_ff_trace(0,31,&regs);
    strcpy(process.p_comm,"backboardd");struct process pbefore=process;
    absent=true;sweet_ff_trace(0,31,&regs);absent=false;
    regs.is64=false;sweet_ff_trace(0,31,&regs);regs=before;
    assert(!sweet_ff_events.total);
    sweet_ff_guard=1;sweet_ff_trace(0,31,&regs);
    assert(sweet_ff_guard==1 && sweet_ff_dropped && !sweet_ff_events.total);sweet_ff_guard=0;
    for(unsigned i=0;i<65540;++i)sweet_ff_trace(i%4,-31,&regs);
    assert(sweet_ff_events.total==65536 && sweet_ff_events.count==64 && !appends);
    struct sweet_ff_event snapshot[64];assert(sweet_ff_snapshot(&sweet_ff_events,snapshot)==64);
    assert(snapshot[63].pid==56 && snapshot[63].tid==77 && snapshot[63].call==-31);
    assert(snapshot[63].pc==regs.pc && snapshot[63].lr==regs.lr && snapshot[63].sp==regs.sp);
    assert(snapshot[63].x0==regs.x0 && snapshot[63].cpsr==regs.cpsr);
    assert(!memcmp(&regs,&before,sizeof(regs)) && !memcmp(&process,&pbefore,sizeof(process)));
    sweet_ff_fault_enabled=2;sweet_ff_fault_trace(0,&regs,0,0,-1);assert(!appends);
    strcpy(process.p_comm,"SpringBoard");sweet_ff_fault_trace(0,&regs,0,0,-1);assert(appends==1);
    assert(!sweet_ff_screen_enabled && !sweet_ff_guard);
    puts("PASS");return 0;
}
''')

    def test_actual_mode3_dump_drains_ring_outside_guard(self):
        start = SAMPLER.index('static void\nsweet_ff_dump(')
        dump = SAMPLER[start:SAMPLER.index('\n#endif', start)]
        self.run_c(PRODUCERS + r'''
static unsigned int summaries,events,busy;
static int capture(const char *format,...) {
    if(strstr(format,"busy=1")) { assert(sweet_ff_guard);++busy;return 0; }
    assert(!sweet_ff_guard);
    if(strstr(format,"sample=%u count=%u"))++summaries;
    else { assert(strstr(format,"n=%u phase=%u"));++events; }
    return 0;
}
#define printf capture
''' + dump + r'''
#undef printf
int main(void) {
    struct arm_saved_state regs={0x1234,0x5678,0x8000,0x99,0,true};
    sweet_ff_dump(0);assert(!summaries && !events);
    sweet_ff_enabled=3;for(unsigned i=0;i<70;++i)sweet_ff_trace(i%4,-31,&regs);
    assert(sweet_ff_events.total==70 && sweet_ff_events.count==64 && !sweet_ff_dropped);
    sweet_ff_dump(1);assert(summaries==1 && events==64 && !sweet_ff_events.count);
    sweet_ff_guard=1;sweet_ff_dump(2);assert(busy==1);sweet_ff_guard=0;
    sweet_ff_dump(3);assert(summaries==2 && events==64);
    sweet_ff_fault_enabled=3;sweet_ff_fault_trace(0,&regs,0,0,-1);
    assert(!appends && !sweet_ff_last_fault.seq && !sweet_ff_screen_enabled && current_calls==70);
    puts("PASS");return 0;
}
''')

    def test_actual_startup_independent_svc_fault_and_screen(self):
        startup = SAMPLER[SAMPLER.index('void\nsweet_sample_start(void)'):]
        self.run_c(HEADER + r'''
#define DEVELOPMENT 1
#define ARM64_BOARD_CONFIG_SWEET 1
#define KERN_SUCCESS 0
typedef void *thread_t;
static unsigned int sweet_ff_enabled,sweet_ff_fault_enabled,sweet_ff_screen_enabled;
static unsigned int svc,fault,screen,starts,deallocations,outputs;
static bool found;
static void sweet_sample_thread(void *p,int result) { (void)p;(void)result; }
static int PE_parse_boot_argn(const char *key,void *value,unsigned int size) {
    assert(size==sizeof(unsigned));if(!found)return 0;
    if(!strcmp(key,"sweetfftrace"))*(unsigned *)value=svc;
    else if(!strcmp(key,"sweetfffault"))*(unsigned *)value=fault;
    else { assert(!strcmp(key,"sweetffscreen"));*(unsigned *)value=screen; }return 1;
}
static int kernel_thread_start(void (*callback)(void *,int),void *arg,thread_t *thread) {
    assert(callback==sweet_sample_thread && !arg);*thread=(void *)(uintptr_t)1;++starts;return 0;
}
static void thread_deallocate(thread_t thread) { assert(thread==(void *)(uintptr_t)1);++deallocations; }
static int capture(const char *format,unsigned a,unsigned b) {
    assert(strstr(format,"svc3=backboardd fault3=off"));
    assert(a==sweet_ff_svc_mode(svc) && b==sweet_ff_mode(fault) && (a || b));++outputs;return 0;
}
#define printf capture
''' + startup + r'''
#undef printf
int main(void) {
    sweet_sample_start();assert(!sweet_ff_enabled && !sweet_ff_fault_enabled && !outputs);
    found=true;
    for(svc=0;svc<5;++svc)for(fault=0;fault<5;++fault)for(screen=0;screen<3;++screen) {
        unsigned before=outputs;sweet_sample_start();
        assert(sweet_ff_enabled==sweet_ff_svc_mode(svc) && sweet_ff_fault_enabled==sweet_ff_mode(fault));
        assert(sweet_ff_screen_enabled==(screen==1));
        assert(outputs==before+((sweet_ff_enabled || sweet_ff_fault_enabled)?1:0));
    }
    svc=3;fault=3;screen=0;sweet_sample_start();
    assert(sweet_ff_enabled==3 && !sweet_ff_fault_enabled && !sweet_ff_screen_enabled);
    assert(starts==77 && starts==deallocations && sweet_ff_target(3,"backboardd"));
    struct sweet_ff_ring ring={0};struct sweet_ff_event event={0},snapshot[64];
    assert(sweet_ff_record(&ring,&event) && sweet_ff_snapshot(&ring,snapshot)==1);
    puts("PASS");return 0;
}
''')

    def test_actual_sampler_late_finite_schedule_without_process_walk(self):
        start = SAMPLER.index('static void\nsweet_sample_thread(')
        thread = SAMPLER[start:SAMPLER.index('\nvoid\nsweet_sample_start(void)', start)]
        self.run_c(r'''
#define DEVELOPMENT 1
#define ARM64_BOARD_CONFIG_SWEET 1
#define __unused __attribute__((unused))
#define PZERO 0
#define PROC_ALLPROCLIST 0
typedef int wait_result_t;
static char sweet_sample_names[128];
static unsigned int sweet_ff_enabled=3,sweet_ff_screen_enabled;
static unsigned int sleeps,dumps,screen_dumps,walks,last_sample;
static unsigned int elapsed;
static int hz=100;
static size_t strlcpy(char *dst,const char *src,size_t n) { size_t len=strlen(src);assert(len<n);memcpy(dst,src,len+1);return len; }
static int PE_parse_boot_argn(const char *key,void *value,unsigned size) {
    if(!strcmp(key,"sweetsample")) { assert(size==128);strcpy(value,"-"); }
    else { assert(size==sizeof(unsigned));
        if(!strcmp(key,"sweetheartbeat"))*(unsigned *)value=0;
        else if(!strcmp(key,"sweetsampledelay"))*(unsigned *)value=5;
        else { assert(!strcmp(key,"sweetsampleinterval"));*(unsigned *)value=30; }
    }return 1;
}
static int tsleep(void *p,int priority,const char *label,int ticks) {
    assert(p && priority==0 && !strcmp(label,"sweetsmp"));
    assert(ticks==(sleeps?30:5)*hz);elapsed+=(unsigned)(ticks/hz);++sleeps;return 0;
}
static void sweet_ff_dump(unsigned sample) {
    assert(sample==dumps && sample<40 && elapsed==5+sample*30);last_sample=sample;++dumps;
}
static void sweet_ff_screen_dump(unsigned sample) { assert(sample==screen_dumps);++screen_dumps; }
static void sweet_sample_proc(void) { }
static void proc_iterate(int kind,void (*callback)(void),void *a,void *b,void *c) {
    assert(kind==0 && callback==sweet_sample_proc && !a && !b && !c);++walks;
}
static int capture(const char *format,...) { assert(format);return 0; }
#define printf capture
''' + thread + r'''
#undef printf
int main(void) {
    sweet_sample_thread(NULL,0);
    assert(dumps==40 && screen_dumps==40 && last_sample==39 && elapsed==1205 && sleeps==41 && !walks);
    sweet_ff_enabled=0;sweet_sample_thread(NULL,0);
    assert(dumps==40 && sleeps==41 && !walks);
    puts("PASS");return 0;
}
''')

    def test_scope_preserves_fault_hook_budget_and_wait_policies(self):
        text = PATCH.read_text()
        self.assertEqual(text.count('diff --git '), 2)
        self.assertIn('if (!sweet_ff_mode(sweet_ff_fault_enabled))', SAMPLER)
        self.assertIn('sweet_ff_fault_enabled = sweet_ff_mode(enabled);', SAMPLER)
        self.assertEqual(SAMPLER.count('if (!sweet_ff_svc_mode(sweet_ff_enabled))'), 2)
        self.assertIn('for (int i = 0; i < 40; i++)', SAMPLER)
        added = '\n'.join(line[1:] for line in text.splitlines()
                          if line.startswith('+') and not line.startswith('+++'))
        for forbidden in ['proc_iterate', 'task_lock', 'task_resume', 'copyin',
                          'sweet_ff_record(', 'sweet_ff_fault_trace(', 'daliwdtlease',
                          'sweethidwait', 'OSCompareAndSwap', 'thread_tid(']:
            self.assertNotIn(forbidden, added)


if __name__ == '__main__':
    unittest.main()
