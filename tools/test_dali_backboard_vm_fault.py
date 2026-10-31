#!/usr/bin/env python3
"""Execute final0173 VM policy, producers, finite consumer and real call branch."""
from pathlib import Path
import re
import unittest
import test_dali_gui_user_entry as fixture

ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT/'xnu/patches/0173-sweet-bounded-backboard-vm-fault.patch'
POLICY = fixture.fragment('bsd/dev/SweetBackboardVMPolicy.h', PATCH)
SOURCE = fixture.fragment('bsd/dev/sweet_sample.c', PATCH)
SLEH = fixture.fragment('osfmk/arm64/sleh.c', PATCH)
LEGACY = fixture.fragment('bsd/dev/SweetFeatureTrace.h', ROOT/'xnu/patches/0171-sweet-bounded-backboard-user-entry.patch')
HELPER = SOURCE[SOURCE.index('static volatile UInt32 sweet_bbvm_configured,'):SOURCE.index('\nvoid sweet_ff_trace(')]


def function(source, name):
    m = re.search(r'(?:static )?(?:void|bool|uint32_t)\n'+name+r'\(', source)
    if m is None:
        raise AssertionError(name)
    start = source.index('{', m.start());end = start+1;depth = 1
    while depth:
        depth += (source[end]=='{')-(source[end]=='}');end += 1
    return source[m.start():end]+'\n'


MOCK = r'''
typedef uint32_t UInt32;
struct process {char p_comm[32];int pid;};
typedef struct process *proc_t;
#define PROC_NULL NULL
struct arm_saved_state {uint64_t pc,lr,sp;uint32_t cpsr;bool wide;};
#define PSR64_IS_USER64(c) (((c)&31u)==0)
static struct process process={"backboardd",57};
static bool absent,override_clock;
static unsigned current_calls,clock_reads,outputs,lines_seen;
static uint64_t clock_unit=1000000,override_ticks;
static _Thread_local uint64_t now,tid=315;
static _Thread_local bool owns_guard;
static volatile UInt32 *guard_address;
static unsigned sweet_ff_enabled;
static char captured[131072];static size_t captured_size;
static proc_t current_proc(void) {__atomic_add_fetch(&current_calls,1,__ATOMIC_RELAXED);return absent?NULL:&process;}
static int proc_pid(proc_t p) {return p->pid;}
static void *current_thread(void) {return &process;}
static uint64_t thread_tid(void *p) {assert(p==&process);return tid;}
static bool is_saved_state64(struct arm_saved_state *p) {return p->wide;}
static uint32_t get_saved_state_cpsr(struct arm_saved_state *p) {return p->cpsr;}
static uint64_t get_saved_state_pc(struct arm_saved_state *p) {return p->pc;}
static uint64_t get_saved_state_lr(struct arm_saved_state *p) {return p->lr;}
static uint64_t get_saved_state_sp(struct arm_saved_state *p) {return p->sp;}
static void (*cas_observer)(volatile UInt32 *,UInt32,UInt32,bool);
static bool OSCompareAndSwap(UInt32 old,UInt32 value,volatile UInt32 *p) {
 bool ok=__atomic_compare_exchange_n(p,&old,value,false,__ATOMIC_ACQ_REL,__ATOMIC_ACQUIRE);
 if(ok && p==guard_address) {
  if(old==0 && value==1)owns_guard=true;
  if(old==1 && value==0)owns_guard=false;
 }
 if(cas_observer)cas_observer(p,old,value,ok);
 return ok;
}
static uint64_t mach_absolute_time(void) {__atomic_add_fetch(&clock_reads,1,__ATOMIC_RELAXED);return now;}
static void nanoseconds_to_absolutetime(uint64_t ns,uint64_t *ticks) {*ticks=override_clock?override_ticks:ns/clock_unit;}
static void absolutetime_to_nanoseconds(uint64_t ticks,uint64_t *ns) {*ns=ticks>UINT64_MAX/clock_unit?UINT64_MAX:ticks*clock_unit;}
static int capture(const char *,...);
#define printf capture
'''
TAIL = r'''
#undef printf
static int capture(const char *fmt,...) {
 assert(!owns_guard);char line[1024];va_list ap;va_start(ap,fmt);
 int n=vsnprintf(line,sizeof(line),fmt,ap);va_end(ap);assert(n>0 && n<512);
 assert(captured_size+(size_t)n<sizeof(captured));memcpy(captured+captured_size,line,(size_t)n+1);captured_size+=(size_t)n;++outputs;
 for(int i=0;i<n;++i)if(line[i]=='\n')++lines_seen;
 return n;
}
static struct arm_saved_state regs={.pc=0x1234000,.lr=0x1234560,.sp=0xffffffe0,.cpsr=0,.wide=true};
static void reset(void) {
 sweet_bbvm_configured=sweet_bbvm_open=sweet_bbvm_admit=sweet_bbvm_guard=0;
 sweet_bbvm_begin_busy=sweet_bbvm_end_busy=0;sweet_bbvm_interval=0;
 memset(&sweet_bbvm_events,0,sizeof(sweet_bbvm_events));guard_address=&sweet_bbvm_guard;owns_guard=false;
 cas_observer=NULL;absent=override_clock=false;clock_unit=1000000;override_ticks=0;now=0;tid=315;
 current_calls=clock_reads=outputs=lines_seen=0;captured_size=0;captured[0]=0;
 strcpy(process.p_comm,"backboardd");process.pid=57;sweet_ff_enabled=3;
}
static uint32_t enter(void) {return sweet_bbvm_enter(&regs,0x96000007,0xab001000,0x1000,1);}
static void common(void) {
 assert(sweet_ff_svc_mode(3)==3 && sweet_ff_mode(3)==0);
 struct sweet_ff_ring ring={0};struct sweet_ff_event e={0},out[64];
 assert(sweet_ff_record(&ring,&e) && sweet_ff_snapshot(&ring,out)==1);
 reset();sweet_bbvm_prepare(0);sweet_bbvm_begin();sweet_bbvm_dump(0);
 assert(!enter());sweet_bbvm_leave(0,0);assert(!outputs && !current_calls && !clock_reads);
 reset();
}
'''


class VMFaultTest(unittest.TestCase):
    run_c = fixture.GUIUserEntryTest.run_c

    def runtime(self, body, extra=''):
        code = LEGACY+POLICY+MOCK+HELPER+TAIL+extra+body
        self.run_c(code.replace('int main(void) {','int main(void) { common();'))

    def test_actual_policy_budget_rate_out_of_order_and_once_exports(self):
        self.run_c(POLICY+r'''
int main(void) {
 struct sweet_bbvm_buffer b={0};struct sweet_bbvm_event e={.tid=9},out[81]={0};
 assert(sweet_bbvm_ceil_ticks(10,SWEET_BBVM_INTERVAL_NS)==10);
 char line[512];assert(sweet_bbvm_format(line,sizeof(line),&e)>0);
 assert(!sweet_bbvm_enter_record(&b,0,&e));
 assert(sweet_bbvm_enter_record(&b,30,&e)==1 && b.has_last && !b.last);
 e.start=29;assert(!sweet_bbvm_enter_record(&b,30,&e) && b.total==1 && !b.last);
 for(unsigned i=1;i<40;++i) {e.start=i*30;e.pid=(int)i;e.tid=9+i;assert(sweet_bbvm_enter_record(&b,30,&e)==i+1);}
 e.start=UINT64_MAX;assert(!sweet_bbvm_enter_record(&b,30,&e) && b.total==40);
 assert(!sweet_bbvm_leave_record(&b,0,9,1500,0) && !sweet_bbvm_leave_record(&b,41,9,1500,0));
 assert(!sweet_bbvm_leave_record(&b,1,10,1500,0));
 for(unsigned t=40;t>0;--t)assert(sweet_bbvm_leave_record(&b,t,8+t,1500,(int)t));
 assert(b.completed==40 && !sweet_bbvm_leave_record(&b,1,9,1600,0));
 out[80].token=0xaabbccdd;assert(sweet_bbvm_snapshot(&b,out)==80 && out[80].token==0xaabbccdd);
 for(unsigned i=0;i<40;++i) {assert(out[2*i].token==i+1 && out[2*i].version==1 && out[2*i].result==-1 && !out[2*i].end);assert(out[2*i+1].version==2 && out[2*i+1].result==(int)i+1);}
 assert(!sweet_bbvm_snapshot(&b,out) && b.total==40 && b.exported_entry==40 && b.exported_return==40);
 puts("PASS");return 0;
}
''')

    def test_actual_policy_clock_bounds_late_completion_and_line_bound(self):
        self.run_c(POLICY+r'''
int main(void) {
 assert(!sweet_bbvm_ceil_ticks(0,0) && !sweet_bbvm_ceil_ticks(UINT64_MAX,0));
 assert(sweet_bbvm_ceil_ticks(5,SWEET_BBVM_INTERVAL_NS)==5 && sweet_bbvm_ceil_ticks(5,SWEET_BBVM_INTERVAL_NS-1)==6);
 struct sweet_bbvm_buffer b={0};struct sweet_bbvm_event e={.tid=1,.start=UINT64_MAX-31},out[80];
 assert(sweet_bbvm_enter_record(&b,30,&e)==1);e.start=UINT64_MAX;assert(sweet_bbvm_enter_record(&b,30,&e)==2);
 --e.start;assert(!sweet_bbvm_enter_record(&b,30,&e) && b.clock_stopped);
 assert(sweet_bbvm_leave_record(&b,1,1,UINT64_MAX,14) && b.slots[0].event.duration==31);
 assert(sweet_bbvm_leave_record(&b,2,1,1,0) && !b.slots[1].event.duration_valid && !b.slots[1].event.duration);
 assert(sweet_bbvm_snapshot(&b,out)==4);
 memset(&e,255,sizeof(e));e.version=2;e.pid=INT32_MIN;e.result=INT32_MIN;e.duration_valid=true;
 char line[513];memset(line,'Z',sizeof(line));int n=sweet_bbvm_format(line,512,&e);
 assert(n>0 && n<512 && line[512]=='Z');assert(!sweet_bbvm_format(line,1,&e));
 assert(sizeof(b)+sizeof(out)+32<=32768);puts("PASS");return 0;
}
''')

    def test_actual_producer_default_filter_and_register_transparency(self):
        self.runtime(r'''
int main(void) {
 sweet_ff_enabled=0;sweet_bbvm_prepare(1);sweet_bbvm_begin();assert(!enter() && !current_calls);
 sweet_ff_enabled=2;sweet_bbvm_prepare(1);assert(!sweet_bbvm_configured);
 sweet_ff_enabled=3;sweet_bbvm_prepare(2);assert(!sweet_bbvm_configured);
 sweet_bbvm_prepare(1);assert(sweet_bbvm_configured && !sweet_bbvm_admit && !enter() && !current_calls);
 sweet_bbvm_begin();struct arm_saved_state before=regs;
 assert(!sweet_bbvm_enter(NULL,0,0,0,0));regs.wide=false;assert(!enter());regs=before;
 regs.cpsr=4;assert(!enter());regs.cpsr=16;assert(!enter());regs=before;assert(!current_calls && !clock_reads);
 absent=true;assert(!enter());absent=false;
 strcpy(process.p_comm,"kernel_task");assert(!enter());strcpy(process.p_comm,"backboarddX");assert(!enter());
 strcpy(process.p_comm,"SpringBoard");assert(!enter());strcpy(process.p_comm,"backboardd");
 struct process pbefore=process;uint32_t t=enter();assert(t==1 && !memcmp(&regs,&before,sizeof(regs)) && !memcmp(&process,&pbefore,sizeof(process)));
 struct sweet_bbvm_event *e=&sweet_bbvm_events.slots[0].event;
 assert(e->pc==regs.pc && e->lr==regs.lr && e->sp==regs.sp && e->tid==315 && e->far==0xab001000 && e->address==0x1000 && e->prot==1 && e->esr==0x96000007);
 sweet_bbvm_dump(0);assert(strstr(captured,"kind=entry") && !strstr(captured,"kind=return"));
 tid=999;sweet_bbvm_leave(t,0);assert(!sweet_bbvm_events.completed && e->version==1);
 tid=315;strcpy(process.p_comm,"changed_after_call");unsigned calls=current_calls;now=15;sweet_bbvm_leave(t,14);
 assert(current_calls==calls && e->result==14 && e->duration==15 && e->duration_valid);
 sweet_bbvm_dump(39);assert(strstr(captured,"kind=return") && !sweet_bbvm_open && !sweet_bbvm_admit);
 assert(sweet_ff_mode(3)==0 && sweet_ff_svc_mode(3)==3);
 puts("PASS");return 0;
}
''')

    def test_actual_busy_quota_clock_stop_and_final_cutoff(self):
        self.runtime(r'''
int main(void) {
 sweet_bbvm_prepare(1);sweet_bbvm_begin();sweet_bbvm_guard=1;
 assert(!enter() && sweet_bbvm_begin_busy && !sweet_bbvm_events.total && !sweet_bbvm_events.has_last);
 sweet_bbvm_guard=0;uint32_t t=enter();sweet_bbvm_guard=1;now=10;sweet_bbvm_leave(t,0);
 assert(sweet_bbvm_end_busy && !sweet_bbvm_events.completed);sweet_bbvm_guard=0;
 sweet_bbvm_dump(0);assert(strstr(captured,"end_busy=1") && !strstr(captured,"kind=return"));
 sweet_bbvm_guard=1;sweet_bbvm_dump(39);assert(!sweet_bbvm_open && !sweet_bbvm_admit && strstr(captured,"snapshot incomplete"));
 sweet_bbvm_guard=0;sweet_bbvm_leave(t,0);assert(!sweet_bbvm_events.completed && !enter());
 reset();sweet_bbvm_prepare(1);sweet_bbvm_begin();
 for(unsigned i=0;i<40;++i) {now=(uint64_t)i*sweet_bbvm_interval;process.pid=(int)i+1;assert(enter()==i+1);}
 assert(!sweet_bbvm_admit && sweet_bbvm_open && !enter());now+=1;
 for(unsigned i=40;i>0;--i)sweet_bbvm_leave(i,0);
 assert(sweet_bbvm_events.completed==40);sweet_bbvm_dump(39);assert(sweet_bbvm_events.exported_return==40);
 reset();sweet_bbvm_prepare(1);sweet_bbvm_begin();now=100;t=enter();now=99;assert(!enter());
 assert(!sweet_bbvm_admit && sweet_bbvm_open && sweet_bbvm_events.clock_stopped);
 now=120;sweet_bbvm_leave(t,0);assert(sweet_bbvm_events.completed==1);sweet_bbvm_dump(39);
 reset();sweet_bbvm_prepare(1);sweet_bbvm_begin();now=100;t=enter();now=99;sweet_bbvm_leave(t,14);
 assert(sweet_bbvm_events.completed==1 && !sweet_bbvm_events.slots[0].event.duration_valid && !sweet_bbvm_admit && sweet_bbvm_open);
 sweet_bbvm_dump(39);
 puts("PASS");return 0;
}
''')

    def test_actual_clock_preparation_roundup_zero_overflow(self):
        self.runtime(r'''
int main(void) {
 override_clock=true;override_ticks=0;sweet_bbvm_prepare(1);assert(!sweet_bbvm_configured);
 override_ticks=UINT64_MAX;sweet_bbvm_prepare(1);assert(!sweet_bbvm_configured);
 override_clock=false;clock_unit=7;sweet_bbvm_prepare(1);assert(sweet_bbvm_interval==SWEET_BBVM_INTERVAL_NS/7+1);
 assert(!sweet_bbvm_admit);sweet_bbvm_begin();assert(enter()==1);now=1;sweet_bbvm_leave(1,0);sweet_bbvm_dump(39);
 puts("PASS");return 0;
}
''')

    def test_actual_out_of_order_returns_and_bounded_all_lines(self):
        self.runtime(r'''
int main(void) {
 sweet_bbvm_prepare(1);sweet_bbvm_begin();
 for(unsigned i=0;i<40;++i) {
  tid=i+1;now=(uint64_t)i*sweet_bbvm_interval;assert(enter()==i+1);
  if(i<39)sweet_bbvm_dump(i);
 }
 assert(sweet_bbvm_events.exported_entry==39);
 for(unsigned i=40;i>0;--i){tid=i;sweet_bbvm_leave(i,14);}
 tid=999;sweet_bbvm_leave(40,0);sweet_bbvm_leave(UINT32_MAX,0);
 sweet_bbvm_dump(39);assert(sweet_bbvm_events.exported_entry==40 && sweet_bbvm_events.exported_return==40);
 assert(lines_seen==121 && captured_size<=61952);unsigned saved=outputs;
 assert(!enter());sweet_bbvm_leave(1,0);assert(outputs==saved);
 puts("PASS");return 0;
}
''')

    def test_real_vm_branch_only_and_disabled_build_isolation(self):
        start=SLEH.index('\tif (is_vm_fault(fault_code)) {')
        stop=SLEH.index('\n\t\tif (thread->t_rr_state',start)
        branch=SLEH[start:stop]+'\n\t}\n'
        for development,board in ((1,True),(0,True),(1,False)):
            extra=r'''
#define DEVELOPMENT DEVELOPMENT_VALUE
BOARD_DEFINITION
#define KERN_SUCCESS 0
#define FALSE false
#define VM_KERN_MEMORY_NONE 0
#define THREAD_ABORTSAFE 1
#define VM_PROT_EXECUTE 4
struct vmmap {void *pmap;};
static struct vmmap map_object={.pmap=&process};
typedef struct vmmap *vm_map_t;
struct vmthread {vm_map_t map;};
typedef uint64_t vm_offset_t;
typedef int kern_return_t;
#define KERN_FAILURE 5
static unsigned vm_calls,fast_calls,old_returns;static int fast_result,vm_result=14;
static bool is_vm_fault(int c) {return c==1 || c==2;}
static bool is_translation_fault(int c) {return c==1;}
static uint64_t tbi_clear(uint64_t v) {return v&0x00ffffffffffffffULL;}
static int arm_fast_fault(void *map,uint64_t addr,unsigned prot,bool af,bool user) {assert(map==&process && addr==0x1000 && prot==1 && !af && user);++fast_calls;return fast_result;}
#define FSC_ACCESS_FLAG_FAULT_L3 9
static int vm_fault(void *map,uint64_t addr,unsigned prot,bool wiring,int tag,int interruptible,void *pm,uint64_t pa) {assert(!owns_guard && map==&map_object && addr==0x1000 && prot==1 && !wiring && !tag && interruptible==1 && !pm && !pa);++vm_calls;return vm_result;}
static void sweet_ff_fault_trace(unsigned phase,struct arm_saved_state *state,uint32_t esr,uint64_t far,int result) {assert(phase==1 && state==&regs && esr==0x96000007 && far==0xab00000000001000ULL && result==(vm_calls?vm_result:0));++old_returns;}
#define TRUE true
static void run_abort(int fault_code) {
 struct vmthread owner={.map=&map_object},*thread=&owner;void *kernel_map=NULL;
 struct arm_saved_state *state=&regs;uint32_t esr=0x96000007;uint64_t fault_addr=0xab00000000001000ULL;unsigned fault_type=1;
BRANCH
 (void)kernel_map;(void)state;(void)esr;
}
'''.replace('DEVELOPMENT_VALUE',str(development)).replace('BOARD_DEFINITION','#define ARM64_BOARD_CONFIG_SWEET 1' if board else '').replace('BRANCH',branch)
            # Reference the old hook even in disabled builds to keep -Werror on.
            body=r'''
int main(void) {
 sweet_bbvm_prepare(1);sweet_bbvm_begin();run_abort(0);assert(!vm_calls && !sweet_bbvm_events.total);
 run_abort(2);assert(!vm_calls && fast_calls==1 && !sweet_bbvm_events.total);
 if(!ENABLED)sweet_ff_fault_trace(1,&regs,0x96000007,0xab00000000001000ULL,0);
 run_abort(1);assert(vm_calls==1 && sweet_bbvm_events.total==ENABLED);
 assert(old_returns==(ENABLED?2:1));
 now=sweet_bbvm_interval;fast_result=5;run_abort(2);assert(vm_calls==2 && sweet_bbvm_events.total==2*ENABLED);
 assert(regs.pc==0x1234000 && regs.cpsr==0);sweet_bbvm_dump(39);puts("PASS");return 0;
}
'''.replace('ENABLED','1' if development and board else '0')
            self.runtime(body,extra)

    def test_actual_call_entry_exports_while_vm_is_blocked(self):
        start=SLEH.index('\t\t\t\t/* We have to fault the page in */')
        end=SLEH.index('\n\t\t\t}',start)
        call=SLEH[start:end]
        extra=r"""
#include <pthread.h>
#define DEVELOPMENT 1
#define ARM64_BOARD_CONFIG_SWEET 1
#define FALSE false
#define VM_KERN_MEMORY_NONE 0
#define THREAD_ABORTSAFE 1
static pthread_mutex_t mutex=PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t condition=PTHREAD_COND_INITIALIZER;
static bool blocked,released;
static int vm_fault(void *map,uint64_t addr,unsigned prot,bool wiring,int tag,int intr,void *pm,uint64_t pa) {
 assert(!owns_guard && map==&process && addr==0x1000 && prot==1 && !wiring && !tag && intr==1 && !pm && !pa);
 assert(!pthread_mutex_lock(&mutex));blocked=true;assert(!pthread_cond_signal(&condition));
 while(!released)assert(!pthread_cond_wait(&condition,&mutex));
 assert(!pthread_mutex_unlock(&mutex));now=77;return 14;
}
static void *worker(void *arg) {
 assert(!arg);tid=7;now=0;
 struct arm_saved_state *state=&regs;void *map=&process;uint64_t vm_fault_addr=0x1000,fault_addr=0xab00000000001000ULL;
 uint32_t esr=0x96000007;unsigned fault_type=1;int result=-1;
CALL
 assert(result==14);return NULL;
}
""".replace('CALL',call)
        self.runtime(r"""
int main(void) {
 sweet_bbvm_prepare(1);sweet_bbvm_begin();pthread_t thread;assert(!pthread_create(&thread,NULL,worker,NULL));
 assert(!pthread_mutex_lock(&mutex));while(!blocked)assert(!pthread_cond_wait(&condition,&mutex));
 assert(sweet_bbvm_events.total==1 && !sweet_bbvm_events.completed);
 sweet_bbvm_dump(0);assert(strstr(captured,"kind=entry") && !strstr(captured,"kind=return"));
 released=true;assert(!pthread_cond_signal(&condition));assert(!pthread_mutex_unlock(&mutex));assert(!pthread_join(thread,NULL));
 assert(sweet_bbvm_events.completed==1 && sweet_bbvm_events.slots[0].event.tid==7 && sweet_bbvm_events.slots[0].event.duration==77);
 now=77;sweet_bbvm_dump(39);assert(strstr(captured,"kind=return") && !sweet_bbvm_open);
 puts("PASS");return 0;
}
""",extra)

    def test_actual_concurrent_admission_completion_and_consumer(self):
        extra=r"""
#include <pthread.h>
struct worker_input {uint64_t time,owner;};
static void *worker(void *arg) {
 struct worker_input *input=arg;tid=input->owner;now=input->time;
 uint32_t token=enter();now+=7;sweet_bbvm_leave(token,14);return NULL;
}
"""
        self.runtime(r"""
int main(void) {
 sweet_bbvm_prepare(1);sweet_bbvm_begin();
 for(unsigned round=0;round<40;++round) {
  struct worker_input a={(uint64_t)round*sweet_bbvm_interval,101},b={a.time,102};pthread_t x,y;now=a.time;
  assert(!pthread_create(&x,NULL,worker,&a) && !pthread_create(&y,NULL,worker,&b));
  if(round<39)sweet_bbvm_dump(round);
  assert(!pthread_join(x,NULL) && !pthread_join(y,NULL));assert(sweet_bbvm_events.total<=round+1);
 }
 sweet_bbvm_dump(39);assert(sweet_bbvm_events.exported_entry==sweet_bbvm_events.total);
 assert(sweet_bbvm_events.exported_return==sweet_bbvm_events.completed && lines_seen<=121 && captured_size<=61952);
 for(unsigned i=0;i<sweet_bbvm_events.total;++i) {
  const struct sweet_bbvm_event *e=&sweet_bbvm_events.slots[i].event;
  assert(e->token==i+1 && (e->tid==101 || e->tid==102) && e->pc==regs.pc && e->far==0xab001000);
  if(e->version==2)assert(e->result==14 && e->duration==7 && e->duration_valid);
  if(i)assert(e->start-sweet_bbvm_events.slots[i-1].event.start>=sweet_bbvm_interval);
 }
 puts("PASS");return 0;
}
""",extra)

    def test_actual_startup_failure_and_finite_consumer_lifecycle(self):
        thread=function(SOURCE,'sweet_sample_thread');startup=function(SOURCE,'sweet_sample_start')
        extra=r"""
#define DEVELOPMENT 1
#define ARM64_BOARD_CONFIG_SWEET 1
#ifndef __unused
#define __unused __attribute__((__unused__))
#endif
#ifdef strlcpy
#undef strlcpy
#endif
#define strlcpy fixture_vm_strlcpy
#define PZERO 0
#define PROC_ALLPROCLIST 0
#define KERN_SUCCESS 0
typedef int wait_result_t;typedef void *thread_t;
static unsigned sweet_ff_fault_enabled,sweet_ff_screen_enabled;
static unsigned requested=1,svc_requested=3,starts,deallocated,dumps,walks,sleeps,elapsed;
static int hz=100;static bool fail_thread;static char sweet_sample_names[128];
static void (*callback)(void *,int);
static size_t fixture_vm_strlcpy(char *dst,const char *src,size_t n) {size_t len=strlen(src);assert(len<n);memcpy(dst,src,len+1);return len;}
static int PE_parse_boot_argn(const char *key,void *out,unsigned size) {
 if(!strcmp(key,"sweetsample")){assert(size==128);strcpy(out,"-");return 1;}
 assert(size==sizeof(unsigned));unsigned v=0;
 if(!strcmp(key,"sweetfftrace"))v=svc_requested;
 else if(!strcmp(key,"sweetbbvm"))v=requested;
 else if(!strcmp(key,"sweetsampledelay"))v=5;
 else if(!strcmp(key,"sweetsampleinterval"))v=30;
 else assert(!strcmp(key,"sweetffpc") || !strcmp(key,"sweetheartbeat") || !strcmp(key,"sweetfffault") || !strcmp(key,"sweetffscreen"));
 *(unsigned *)out=v;return 1;
}
static int kernel_thread_start(void (*fn)(void *,int),void *arg,thread_t *out) {assert(!arg);++starts;callback=fn;*out=&process;return fail_thread?1:0;}
static void thread_deallocate(thread_t t) {assert(t==&process);++deallocated;}
static void sweet_bbpc_prepare(unsigned requested) {assert(!requested);}
static bool sweet_bbpc_is_configured(void) {return false;}
static void sweet_bbpc_begin(void) { }
static void sweet_bbpc_dump(unsigned sample) {assert(sample<40);}
static void sweet_ff_dump(unsigned sample) {assert(sample==dumps && elapsed==5+30*sample);++dumps;}
static void sweet_ff_screen_dump(unsigned sample) {assert(sample<40);}
static void sweet_sample_proc(void) { }
static void proc_iterate(int kind,void (*fn)(void),void *a,void *b,void *c) {assert(!kind && fn==sweet_sample_proc && !a && !b && !c);++walks;}
static int tsleep(void *p,int pri,const char *name,int ticks) {
 assert(p && !pri && !strcmp(name,"sweetsmp") && ticks==(sleeps?30:5)*hz);
 elapsed+=(unsigned)(ticks/hz);now=(uint64_t)elapsed*SWEET_BBVM_INTERVAL_NS/30/clock_unit;
 uint32_t token=enter();sweet_bbvm_leave(token,0);++sleeps;return 0;
}
#define printf capture
"""+thread+startup+r"""
#undef printf
"""
        self.runtime(r"""
int main(void) {
 fail_thread=true;sweet_sample_start();assert(starts==1 && !deallocated && sweet_bbvm_configured && !sweet_bbvm_open);
 assert(!enter());reset();fail_thread=false;sweet_sample_start();assert(starts==2 && deallocated==1 && !sweet_bbvm_open);
 callback(NULL,0);assert(dumps==40 && sleeps==41 && elapsed==1205 && !walks && !sweet_bbvm_open && !sweet_bbvm_admit);
 assert(sweet_bbvm_events.total==sweet_bbvm_events.exported_entry && sweet_bbvm_events.completed==sweet_bbvm_events.exported_return);
 assert(sweet_bbvm_events.total<=40 && !sweet_ff_fault_enabled && !sweet_ff_screen_enabled);
 reset();requested=0;svc_requested=0;sweet_sample_start();callback(NULL,0);assert(dumps==40 && sleeps==41 && !sweet_bbvm_configured && !walks);
 puts("PASS");return 0;
}
""",extra)

    def test_guard_owner_overlaps_final_closure_without_reopening(self):
        extra=r"""
#include <pthread.h>
static pthread_mutex_t mutex=PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t cond=PTHREAD_COND_INITIALIZER;
static bool paused,resume_owner;
static void observe(volatile UInt32 *p,UInt32 old,UInt32 value,bool success) {
 if(p==&sweet_bbvm_admit && old==1 && value==1 && success && owns_guard) {
  assert(!pthread_mutex_lock(&mutex));paused=true;assert(!pthread_cond_signal(&cond));
  while(!resume_owner)assert(!pthread_cond_wait(&cond,&mutex));
  assert(!pthread_mutex_unlock(&mutex));
 }
}
static void *worker(void *arg) {
 assert(!arg);now=0;uint32_t token=enter();assert(token==1);sweet_bbvm_leave(token,0);return NULL;
}
"""
        self.runtime(r"""
int main(void) {
 sweet_bbvm_prepare(1);sweet_bbvm_begin();cas_observer=observe;
 pthread_t producer;assert(!pthread_create(&producer,NULL,worker,NULL));
 assert(!pthread_mutex_lock(&mutex));while(!paused)assert(!pthread_cond_wait(&cond,&mutex));
 sweet_bbvm_dump(39);assert(!sweet_bbvm_open && !sweet_bbvm_admit && strstr(captured,"snapshot incomplete"));
 resume_owner=true;assert(!pthread_cond_signal(&cond));assert(!pthread_mutex_unlock(&mutex));
 assert(!pthread_join(producer,NULL));cas_observer=NULL;
 assert(sweet_bbvm_events.total==1 && !sweet_bbvm_events.completed && !sweet_bbvm_events.exported_entry);
 assert(!enter());puts("PASS");return 0;
}
""",extra)

    def test_source_scope_and_unchanged_legacy_paths(self):
        patch=PATCH.read_text();self.assertEqual(patch.count('diff --git '),3)
        self.assertEqual(SLEH.count('sweet_bbvm_enter(state,'),1)
        self.assertEqual(SLEH.count('sweet_bbvm_leave(sweet_bbvm_token, result);'),1)
        for name in ('sweet_ff_trace','sweet_ff_fault_trace','sweet_bbpc_trace'):
            old=fixture.fragment('bsd/dev/sweet_sample.c',ROOT/'xnu/patches/0172-sweet-bounded-backboard-interrupt-pc.patch')
            self.assertEqual(function(SOURCE,name),function(old,name))
        for name in ('sweet_bbvm_enter','sweet_bbvm_leave'):
            body=function(SOURCE,name)
            for forbidden in ('printf(', 'copyin', 'task_lock', 'proc_name(', 'proc_find(', 'proc_iterate', 'while (', 'sweet_ramoops_append', 'tsleep', 'kalloc'):
                self.assertNotIn(forbidden,body)
        self.assertNotIn('current_proc(',function(SOURCE,'sweet_bbvm_leave'))
        self.assertIn('sweet_bbvm_prepare(enabled)',function(SOURCE,'sweet_sample_start'))
        self.assertIn('!sweet_bbvm_is_configured()',function(SOURCE,'sweet_sample_thread'))
        added='\n'.join(line[1:] for line in patch.splitlines() if line.startswith('+') and not line.startswith('+++'))
        for forbidden in ('rtclock_intr(TRUE);','IORPC','dali_watchdog_poll','task_resume','ICC_EOIR1_EL1'):
            self.assertNotIn(forbidden,added)


if __name__=='__main__':unittest.main()
