#!/usr/bin/env python3
"""Execute final 0172 policy/producers/consumer/startup and timer call-site code."""
from pathlib import Path
import re
import unittest
import test_dali_gui_user_entry as fixture

ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT/'xnu/patches/0172-sweet-bounded-backboard-interrupt-pc.patch'
POLICY = fixture.fragment('bsd/dev/SweetBackboardPCPolicy.h', PATCH)
SOURCE = fixture.fragment('bsd/dev/sweet_sample.c', PATCH)
IRQ = fixture.fragment('osfmk/arm64/sleh.c', PATCH)
LEGACY = fixture.fragment('bsd/dev/SweetFeatureTrace.h', ROOT/'xnu/patches/0171-sweet-bounded-backboard-user-entry.patch')
HELPERS = SOURCE[SOURCE.index('static volatile UInt32 sweet_bbpc_configured;'):] if 'static volatile UInt32 sweet_bbpc_configured;' in SOURCE else SOURCE[SOURCE.index('static volatile UInt32 sweet_bbpc_configured,'):]
HELPERS = HELPERS[:HELPERS.index('\nvoid sweet_ff_trace(')]


def function(source, name):
    match = re.search(r'(?:static )?(?:void|bool)\n'+name+r'\(', source)
    if not match:
        raise AssertionError(name)
    start = source.index('{', match.start());depth = 1;end = start+1
    while depth:
        depth += (source[end]=='{')-(source[end]=='}');end += 1
    return source[match.start():end]+'\n'

SVC = function(SOURCE, 'sweet_ff_trace')
SVC_DUMP = function(SOURCE, 'sweet_ff_dump')
STARTUP = function(SOURCE, 'sweet_sample_start')
THREAD = function(SOURCE, 'sweet_sample_thread')
MOCKS = r'''
typedef uint32_t UInt32;
struct process { char p_comm[32];int pid; };
typedef struct process *proc_t;
#define PROC_NULL NULL
struct arm_saved_state { uint64_t pc,lr,sp,x0;uint32_t cpsr;bool wide; };
#define PSR64_IS_USER64(c) (((c)&31u)==0)
static struct process process={"backboardd",57};
static bool absent;static _Thread_local bool irq_context;
static _Thread_local bool owns_pc_guard;
static volatile UInt32 *pc_guard_address;
static unsigned int current_calls,clock_reads,outputs;
static uint64_t now,clock_unit=1000000,override_ticks,tid=312;
static bool override_clock;
static unsigned int sweet_ff_enabled;
static volatile UInt32 sweet_ff_guard,sweet_ff_dropped;
static struct sweet_ff_ring sweet_ff_events;
static char captured[65536];static size_t captured_size;
static proc_t current_proc(void) { __atomic_add_fetch(&current_calls,1,__ATOMIC_RELAXED);return absent?NULL:&process; }
static int proc_pid(proc_t p) { return p->pid; }
static void *current_thread(void) { return &process; }
static uint64_t thread_tid(void *p) { assert(p==&process);return tid; }
static bool is_saved_state64(struct arm_saved_state *s) { return s->wide; }
static uint32_t get_saved_state_cpsr(struct arm_saved_state *s) { return s->cpsr; }
static uint64_t get_saved_state_pc(struct arm_saved_state *s) { return s->pc; }
static uint64_t get_saved_state_lr(struct arm_saved_state *s) { return s->lr; }
static uint64_t get_saved_state_sp(struct arm_saved_state *s) { return s->sp; }
static uint64_t get_saved_state_reg(struct arm_saved_state *s,unsigned n) { assert(n==0);return s->x0; }
static bool OSCompareAndSwap(UInt32 old,UInt32 value,volatile UInt32 *p) {
 bool success=__atomic_compare_exchange_n(p,&old,value,false,__ATOMIC_ACQ_REL,__ATOMIC_ACQUIRE);
 if(success && p==pc_guard_address) {
  if(old==0 && value==1)owns_pc_guard=true;
  if(old==1 && value==0)owns_pc_guard=false;
 }
 return success;
}
static void nanoseconds_to_absolutetime(uint64_t ns,uint64_t *ticks) { assert(!irq_context);*ticks=override_clock?override_ticks:ns/clock_unit; }
static void absolutetime_to_nanoseconds(uint64_t ticks,uint64_t *ns) { assert(!irq_context);*ns=ticks>UINT64_MAX/clock_unit?UINT64_MAX:ticks*clock_unit; }
static uint64_t mach_absolute_time(void) { __atomic_add_fetch(&clock_reads,1,__ATOMIC_RELAXED);return now; }
static int capture(const char *,...);
#define printf capture
'''
CAPTURE = r'''
#undef printf
static int capture(const char *format,...) {
 assert(!irq_context);
 assert(!owns_pc_guard && !sweet_ff_guard);
 assert(captured_size<sizeof(captured));va_list args;va_start(args,format);
 int n=vsnprintf(captured+captured_size,sizeof(captured)-captured_size,format,args);va_end(args);
 assert(n>0 && (size_t)n<sizeof(captured)-captured_size);captured_size+=(size_t)n;++outputs;return n;
}
static void legacy_checks(void) {
 assert(sweet_ff_mode(3)==0 && sweet_ff_svc_mode(3)==3 && sweet_ff_target(3,"backboardd"));
 struct sweet_ff_ring ring={0};struct sweet_ff_event e={0},out[64];
 assert(sweet_ff_record(&ring,&e) && sweet_ff_snapshot(&ring,out)==1);
 assert(!sweet_ff_guard && !sweet_ff_dropped && !sweet_ff_events.total);
 struct arm_saved_state check={.x0=42};assert(get_saved_state_reg(&check,0)==42);
}
static void reset_pc(void) {
 sweet_bbpc_configured=sweet_bbpc_active=sweet_bbpc_guard=sweet_bbpc_dropped=0;
 pc_guard_address=&sweet_bbpc_guard;owns_pc_guard=false;
 sweet_bbpc_interval=0;memset(&sweet_bbpc_events,0,sizeof(sweet_bbpc_events));
 captured_size=outputs=current_calls=clock_reads=0;captured[0]=0;
 now=0;clock_unit=1000000;override_clock=false;absent=false;irq_context=false;
 sweet_ff_enabled=3;strcpy(process.p_comm,"backboardd");process.pid=57;tid=312;
}
static struct arm_saved_state regs={.pc=0x1234000,.lr=0x1234560,.sp=0xfffffff0,.x0=19,.cpsr=0,.wide=true};
static void tick(void) { irq_context=true;sweet_bbpc_trace(&regs,&regs,0);irq_context=false; }
'''


class InterruptPCTest(unittest.TestCase):
    run_c = fixture.GUIUserEntryTest.run_c

    def runtime(self, body, extra='', include_svc=False):
        code = LEGACY+POLICY+MOCKS+HELPERS+(SVC+SVC_DUMP if include_svc else '')+CAPTURE+extra+body
        # Exercise helpers shared by all runtime fixtures without changing policy tests.
        code = code.replace('int main(void) {', 'int main(void) { legacy_checks();')
        self.run_c(code)

    def test_policy_first_zero_rate_limit_clock_and_boot_quota(self):
        self.run_c(POLICY+r'''
int main(void) {
 assert(SWEET_BBPC_LIMIT==40 && SWEET_BBPC_INTERVAL_NS==30000000000ULL);
 assert(!sweet_bbpc_ceil_ticks(0,0) && !sweet_bbpc_ceil_ticks(UINT64_MAX,0));
 assert(sweet_bbpc_ceil_ticks(10,SWEET_BBPC_INTERVAL_NS)==10);
 assert(sweet_bbpc_ceil_ticks(10,SWEET_BBPC_INTERVAL_NS-1)==11);
 struct sweet_bbpc_buffer b={0};struct sweet_bbpc_event e={.pid=57},copy=e,out[41]={0};
 assert(!sweet_bbpc_record(&b,0,&e) && !b.has_last);
 assert(sweet_bbpc_record(&b,30,&e) && b.has_last && b.total==1 && b.last==0);
 e.abstime=29;assert(!sweet_bbpc_record(&b,30,&e) && b.last==0);
 for(unsigned i=1;i<40;++i) { e.abstime=i*30;e.pid=(int)i;assert(sweet_bbpc_record(&b,30,&e)); }
 assert(b.total==40 && b.last==1170);e.abstime=UINT64_MAX;
 assert(!sweet_bbpc_record(&b,30,&e) && b.total==40 && b.last==1170);
 out[40].seq=0xaabbccdd;assert(sweet_bbpc_snapshot(&b,out)==40 && b.dumped==40 && b.total==40);
 assert(out[0].seq==1 && out[0].abstime==0 && out[39].seq==40 && out[40].seq==0xaabbccdd);
 assert(!sweet_bbpc_snapshot(&b,out) && !sweet_bbpc_record(&b,30,&e));
 b=(struct sweet_bbpc_buffer){0};e=copy;e.abstime=UINT64_MAX-31;
 assert(sweet_bbpc_record(&b,30,&e));e.abstime=UINT64_MAX;
 assert(sweet_bbpc_record(&b,30,&e));--e.abstime;
 assert(!sweet_bbpc_record(&b,30,&e) && b.clock_stopped);
 e.abstime=UINT64_MAX;assert(!sweet_bbpc_record(&b,30,&e));
 puts("PASS");return 0;
}
''')

    def test_actual_producer_default_filter_provenance_and_state(self):
        self.runtime(r'''
int main(void) {
 reset_pc();sweet_ff_enabled=0;sweet_bbpc_prepare(1);sweet_bbpc_begin();tick();
 assert(!current_calls && !clock_reads && !outputs && !sweet_bbpc_events.total);
 sweet_ff_enabled=3;sweet_bbpc_prepare(0);sweet_bbpc_begin();tick();
 assert(!current_calls && !clock_reads && !outputs);
 sweet_bbpc_prepare(2);assert(!sweet_bbpc_configured);
 sweet_bbpc_prepare(1);assert(sweet_bbpc_configured && !sweet_bbpc_active);tick();assert(!current_calls);
 sweet_bbpc_begin();assert(sweet_bbpc_active && outputs==1);
 struct arm_saved_state before=regs,other=regs;
 irq_context=true;
 sweet_bbpc_trace(NULL,NULL,0);sweet_bbpc_trace(&regs,&other,0);
 regs.wide=false;sweet_bbpc_trace(&regs,&regs,0);regs=before;
 regs.cpsr=4;sweet_bbpc_trace(&regs,&regs,0);regs.cpsr=16;sweet_bbpc_trace(&regs,&regs,0);regs=before;
 irq_context=false;assert(!current_calls && !clock_reads);
 absent=true;tick();absent=false;
 strcpy(process.p_comm,"kernel_task");tick();strcpy(process.p_comm,"backboarddX");tick();
 strcpy(process.p_comm,"backboard");tick();strcpy(process.p_comm,"SpringBoard");tick();
 assert(!sweet_bbpc_events.total && !clock_reads);
 strcpy(process.p_comm,"backboardd");struct process pbefore=process;tick();
 assert(sweet_bbpc_events.total==1 && sweet_bbpc_events.events[0].pc==regs.pc);
 assert(sweet_bbpc_events.events[0].sp==regs.sp && sweet_bbpc_events.events[0].tid==312 && sweet_bbpc_events.events[0].pid==57);
 assert(!memcmp(&before,&regs,sizeof(regs)) && !memcmp(&pbefore,&process,sizeof(process)));
 assert(!sweet_ff_events.total && outputs==1);sweet_bbpc_dump(39);assert(!sweet_bbpc_active);
 assert(strstr(captured,"source=gic-vtimer-el0") && strstr(captured,"n=1"));
 reset_pc();puts("PASS");return 0;
}
''')

    def test_actual_prepare_rounding_and_no_consumer_no_admission(self):
        self.runtime(r'''
int main(void) {
 reset_pc();override_clock=true;override_ticks=0;sweet_bbpc_prepare(1);sweet_bbpc_begin();tick();
 assert(!sweet_bbpc_configured && !sweet_bbpc_active && !current_calls);
 override_ticks=UINT64_MAX;sweet_bbpc_prepare(1);assert(!sweet_bbpc_configured);
 override_clock=false;clock_unit=7;sweet_bbpc_prepare(1);
 assert(sweet_bbpc_interval==SWEET_BBPC_INTERVAL_NS/7+1);
 tick();assert(!current_calls && !sweet_bbpc_events.total);
 sweet_bbpc_begin();tick();assert(sweet_bbpc_events.total==1);
 sweet_bbpc_dump(39);assert(!sweet_bbpc_active);reset_pc();puts("PASS");return 0;
}
''')

    def test_actual_busy_rate_limit_backwards_clock_and_pid_quota(self):
        self.runtime(r'''
int main(void) {
 reset_pc();sweet_bbpc_prepare(1);sweet_bbpc_begin();sweet_bbpc_guard=1;tick();
 assert(sweet_bbpc_dropped && !sweet_bbpc_events.total && !sweet_bbpc_events.has_last && !clock_reads);
 sweet_bbpc_guard=0;tick();assert(sweet_bbpc_events.total==1 && sweet_bbpc_events.has_last && sweet_bbpc_events.last==0);
 now=sweet_bbpc_interval-1;tick();assert(sweet_bbpc_events.total==1 && sweet_bbpc_events.last==0);
 now=sweet_bbpc_interval;tick();assert(sweet_bbpc_events.total==2 && sweet_bbpc_events.last==now);
 --now;tick();assert(sweet_bbpc_events.clock_stopped && !sweet_bbpc_active && sweet_bbpc_events.total==2);
 now=UINT64_MAX;tick();assert(sweet_bbpc_events.total==2);
 sweet_bbpc_dump(39);reset_pc();sweet_bbpc_prepare(1);sweet_bbpc_begin();
 for(unsigned i=0;i<40;++i) {now=(uint64_t)i*sweet_bbpc_interval;process.pid=100+(int)i;tid=900+i;tick();}
 assert(sweet_bbpc_events.total==40 && !sweet_bbpc_active);
 now=UINT64_MAX;tick();assert(sweet_bbpc_events.total==40);
 sweet_bbpc_dump(39);assert(sweet_bbpc_events.dumped==40);
 reset_pc();puts("PASS");return 0;
}
''')

    def test_actual_independent_svc_guard_snapshot_and_final_busy(self):
        self.runtime(r'''
int main(void) {
 reset_pc();sweet_bbpc_prepare(1);sweet_bbpc_begin();sweet_ff_guard=1;tick();
 assert(sweet_bbpc_events.total==1 && sweet_ff_guard==1 && !sweet_ff_dropped && !sweet_ff_events.total);
 sweet_ff_guard=0;sweet_ff_trace(0,500,&regs);sweet_ff_trace(1,500,&regs);
 assert(sweet_ff_events.total==2 && sweet_bbpc_events.total==1);
 now=123;sweet_ff_dump(0);
 assert(strstr(captured,"SWEET BBPC: svc-progress sample=0 selected_svc_total=2 snapshot_abs=0x7b"));
 assert(strstr(captured,"SWEET FFSVC: n=1 phase=0") && !strstr(captured,"SWEET FFSVC: n=3"));
 sweet_bbpc_dump(0);assert(sweet_bbpc_events.dumped==1 && sweet_ff_events.total==2);
 now=sweet_bbpc_interval;tick();sweet_bbpc_guard=1;sweet_bbpc_dump(39);
 assert(!sweet_bbpc_active && sweet_bbpc_events.total==2 && sweet_bbpc_events.dumped==1);
 assert(strstr(captured,"snapshot incomplete"));sweet_bbpc_guard=0;
 now+=sweet_bbpc_interval;tick();assert(sweet_bbpc_events.total==2);
 reset_pc();sweet_ff_trace(0,31,&regs);sweet_ff_dump(1);
 assert(!strstr(captured,"SWEET BBPC:"));sweet_bbpc_begin();sweet_bbpc_dump(39);
 reset_pc();puts("PASS");return 0;
}
''', include_svc=True)

    def test_actual_startup_consumer_failure_and_finite_lifecycle(self):
        extra = r'''
#define DEVELOPMENT 1
#define ARM64_BOARD_CONFIG_SWEET 1
#ifndef __unused
#define __unused __attribute__((__unused__))
#endif
#ifdef strlcpy
#undef strlcpy
#endif
#define strlcpy fixture_pc_strlcpy
#define PZERO 0
#define PROC_ALLPROCLIST 0
#define KERN_SUCCESS 0
typedef int wait_result_t;typedef void *thread_t;
static unsigned sweet_ff_fault_enabled,sweet_ff_screen_enabled;
static unsigned requested=1,svc_requested=3,starts,deallocated,dumps,walks,sleeps;
static unsigned elapsed;static int hz=100;static bool fail_thread;
static char sweet_sample_names[128];
static void (*started_callback)(void *,int);
static size_t fixture_pc_strlcpy(char *dst,const char *src,size_t n) {size_t len=strlen(src);assert(len<n);memcpy(dst,src,len+1);return len;}
static int PE_parse_boot_argn(const char *key,void *out,unsigned size) {
 if(!strcmp(key,"sweetsample")){assert(size==128);strcpy(out,"-");return 1;}
 assert(size==sizeof(unsigned));unsigned v=0;
 if(!strcmp(key,"sweetfftrace"))v=svc_requested;
 else if(!strcmp(key,"sweetffpc"))v=requested;
 else if(!strcmp(key,"sweetsampledelay"))v=5;
 else if(!strcmp(key,"sweetsampleinterval"))v=30;
 else assert(!strcmp(key,"sweetheartbeat") || !strcmp(key,"sweetfffault") || !strcmp(key,"sweetffscreen"));
 *(unsigned *)out=v;return 1;
}
static int kernel_thread_start(void (*fn)(void *,int),void *arg,thread_t *out) {assert(!arg);++starts;started_callback=fn;*out=&process;return fail_thread?1:0;}
static void thread_deallocate(thread_t thread) {assert(thread==&process);++deallocated;}
static void sweet_ff_dump(unsigned sample) {assert(sample==dumps && elapsed==5+30*sample);++dumps;}
static void sweet_ff_screen_dump(unsigned sample) {assert(sample<40);}
static void sweet_sample_proc(void) { }
static void proc_iterate(int kind,void (*fn)(void),void *a,void *b,void *c) {assert(kind==0 && fn==sweet_sample_proc && !a && !b && !c);++walks;}
static int tsleep(void *p,int pri,const char *name,int ticks) {
 assert(p && pri==0 && !strcmp(name,"sweetsmp") && ticks==(sleeps?30:5)*hz);
 elapsed+=(unsigned)(ticks/hz);now=(uint64_t)elapsed*SWEET_BBPC_INTERVAL_NS/30/clock_unit;tick();++sleeps;return 0;
}
#define printf capture
'''+THREAD+STARTUP+r'''
#undef printf
'''
        self.runtime(r'''
int main(void) {
 reset_pc();fail_thread=true;sweet_sample_start();assert(starts==1 && !deallocated && sweet_bbpc_configured && !sweet_bbpc_active);
 tick();assert(!current_calls && !sweet_bbpc_events.total);
 reset_pc();fail_thread=false;sweet_sample_start();assert(starts==2 && deallocated==1 && !sweet_bbpc_active);
 started_callback(NULL,0);
 assert(dumps==40 && sleeps==41 && elapsed==1205 && !walks && !sweet_bbpc_active);
 assert(sweet_bbpc_events.total==sweet_bbpc_events.dumped && sweet_bbpc_events.total<=40);
 assert(!sweet_ff_fault_enabled && !sweet_ff_screen_enabled);
 reset_pc();requested=0;svc_requested=0;sweet_sample_start();started_callback(NULL,0);
 assert(dumps==40 && sleeps==41 && !sweet_bbpc_active && !sweet_bbpc_configured && !walks);
 reset_pc();puts("PASS");return 0;
}
''', extra=extra)

    def test_actual_gic_timer_callsite_and_non_development_isolation(self):
        start=IRQ.index('\tif (intid == sweet_vtimer_intid) {')
        branch=IRQ[start:IRQ.index('\n\t} else if (intid < 16)',start)]+'\n\t}\n'
        self.assertEqual(branch.count('__builtin_arm_wsr64("ICC_EOIR1_EL1", iar);'),1)
        branch=branch.replace('__builtin_arm_wsr64("ICC_EOIR1_EL1", iar);','test_eoi(iar);')
        extra = r'''
#define DEVELOPMENT 1
#define ARM64_BOARD_CONFIG_SWEET 1
#define TRUE true
#define DBG_INTR_TYPE_TIMER 1
static struct cpu {struct arm_saved_state *cpu_int_state;int cpu_number,cpu_decrementer;} cpu;
static unsigned prologues,timers,eois;
static void sleh_interrupt_handler_prologue(struct arm_saved_state *s,int type) {assert(s==cpu.cpu_int_state && type==1);++prologues;}
static void test_eoi(uint64_t iar) {assert(iar==27);++eois;}
static void rtclock_intr(bool value) {assert(value && prologues==1 && sweet_bbpc_events.total==1);++timers;}
#define INTERRUPT_MASKED_DEBUG_START(fn,type) do {assert(type==1);} while(0)
#define INTERRUPT_MASKED_DEBUG_END() do {} while(0)
static void dispatch(unsigned int intid,struct arm_saved_state *state) {
 struct cpu *cdp=&cpu;const unsigned sweet_vtimer_intid=27;uint64_t iar=intid;
'''+branch+r'''
}
'''
        self.runtime(r'''
int main(void) {
 reset_pc();tick();assert(!current_calls && !clock_reads);sweet_bbpc_prepare(1);sweet_bbpc_begin();cpu.cpu_int_state=&regs;
 irq_context=true;dispatch(16,&regs);assert(!prologues && !timers && !current_calls);
 dispatch(27,&regs);irq_context=false;
 assert(prologues==1 && timers==1 && eois==1 && cpu.cpu_decrementer==-1);
 sweet_bbpc_dump(39);reset_pc();puts("PASS");return 0;
}
''', extra=extra)
        self.assertIn('#if DEVELOPMENT && defined(ARM64_BOARD_CONFIG_SWEET)\n        sweet_bbpc_trace',branch)

    def test_actual_timer_callsite_disabled_builds_still_run_timer(self):
        start=IRQ.index('\tif (intid == sweet_vtimer_intid) {')
        branch=IRQ[start:IRQ.index('\n\t} else if (intid < 16)',start)]+'\n\t}\n'
        branch=branch.replace('__builtin_arm_wsr64("ICC_EOIR1_EL1", iar);','test_eoi(iar);')
        base=r'''
#define DEVELOPMENT 0
#define TRUE true
#define DBG_INTR_TYPE_TIMER 1
static struct cpu {struct arm_saved_state *cpu_int_state;int cpu_number,cpu_decrementer;} cpu;
static unsigned prologues,timers,eois;
static void sleh_interrupt_handler_prologue(struct arm_saved_state *s,int type) {assert(s==cpu.cpu_int_state && type==1);++prologues;}
static void test_eoi(uint64_t iar) {assert(iar==27);++eois;}
static void rtclock_intr(bool value) {assert(value && prologues==1 && sweet_bbpc_events.total==0);++timers;}
#define INTERRUPT_MASKED_DEBUG_START(fn,type) do {assert(type==1);} while(0)
#define INTERRUPT_MASKED_DEBUG_END() do {} while(0)
static void dispatch(unsigned int intid,struct arm_saved_state *state) {
 struct cpu *cdp=&cpu;const unsigned sweet_vtimer_intid=27;uint64_t iar=intid;
'''+branch+r'''
}
'''
        main=r'''
int main(void) {
 reset_pc();tick();assert(!current_calls);sweet_bbpc_prepare(1);sweet_bbpc_begin();cpu.cpu_int_state=&regs;
 irq_context=true;dispatch(27,&regs);irq_context=false;
 assert(prologues==1 && timers==1 && eois==1 && !sweet_bbpc_events.total && cpu.cpu_decrementer==-1);
 sweet_bbpc_dump(39);reset_pc();puts("PASS");return 0;
}
'''
        self.runtime(main,extra=base)
        self.runtime(main,extra=base.replace('#define DEVELOPMENT 0','#define DEVELOPMENT 1'))

    def test_actual_concurrent_producers_and_consumer_keep_atomic_events(self):
        extra=r'''
#include <pthread.h>
static void *produce(void *arg) {
 irq_context=true;
 for(unsigned i=0;i<500;++i)sweet_bbpc_trace(&regs,&regs,(unsigned)(uintptr_t)arg);
 irq_context=false;return NULL;
}
static void *consume(void *arg) {
 assert(!arg);
 for(unsigned i=0;i<4;++i)sweet_bbpc_dump(0);
 return NULL;
}
'''
        self.runtime(r'''
int main(void) {
 reset_pc();tick();assert(!current_calls);sweet_bbpc_prepare(1);sweet_bbpc_begin();
 for(unsigned round=0;round<40;++round) {
  now=(uint64_t)round*sweet_bbpc_interval;
  pthread_t a,b,c;assert(!pthread_create(&a,NULL,produce,(void *)(uintptr_t)0));
  assert(!pthread_create(&b,NULL,produce,(void *)(uintptr_t)1));assert(!pthread_create(&c,NULL,consume,NULL));
  assert(!pthread_join(a,NULL) && !pthread_join(b,NULL) && !pthread_join(c,NULL));
  assert(sweet_bbpc_events.total<=round+1 && sweet_bbpc_events.total<=40);
 }
 sweet_bbpc_dump(39);assert(!sweet_bbpc_active && sweet_bbpc_events.dumped==sweet_bbpc_events.total);
 for(unsigned i=0;i<sweet_bbpc_events.total;++i) {
  const struct sweet_bbpc_event *e=&sweet_bbpc_events.events[i];
  assert(e->seq==i+1 && e->cpu<=1 && e->pid==57 && e->tid==312 && e->pc==regs.pc && e->lr==regs.lr && e->sp==regs.sp);
  if(i)assert(e->abstime-sweet_bbpc_events.events[i-1].abstime>=sweet_bbpc_interval);
 }
 assert(!sweet_ff_events.total && !sweet_ff_dropped);
 reset_pc();puts("PASS");return 0;
}
''',extra=extra)

    def test_scope_no_irq_io_no_old_layout_or_timer_behavior_change(self):
        patch=PATCH.read_text();self.assertEqual(patch.count('diff --git '),3)
        producer=function(SOURCE,'sweet_bbpc_trace')
        for forbidden in ['printf(', 'sweet_ramoops_append', 'copyin', 'vm_map_read_user',
                          'task_lock', 'proc_iterate', 'proc_name(', 'proc_find(', 'while (',
                          'kernel_thread_start', 'PE_parse_boot_argn']:
            self.assertNotIn(forbidden,producer)
        self.assertIn('state != published',producer)
        self.assertIn('PSR64_IS_USER64',producer)
        self.assertIn('sweet_ff_target(3, p->p_comm)',producer)
        self.assertIn('sweet_bbpc_prepare(enabled)',STARTUP)
        self.assertEqual(IRQ.count('sweet_bbpc_trace(state,'),1)
        self.assertNotIn('SweetFeatureTrace.h\n+++',patch)
        added='\n'.join(x[1:] for x in patch.splitlines() if x.startswith('+') and not x.startswith('+++'))
        for forbidden in ['rtclock_intr(TRUE);','dali_watchdog_poll','ICC_CTLR','IORPC','task_resume','sweethidwait']:
            self.assertNotIn(forbidden,added)


if __name__=='__main__':unittest.main()
