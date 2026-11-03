#!/usr/bin/env python3
"""Execute the materialized lsd mode4 registry, SVC producer and BBCTX."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

if len(sys.argv) != 2:
    raise SystemExit('usage: test_dali_lsd_context.py MATERIALIZED_XNU')
xnu = Path(sys.argv[1]).resolve()
sample = (xnu / 'bsd/dev/sweet_sample.c').read_text()
def extract(name):
 start=sample.index('\n'+name+'(')+1; brace=sample.index('{',start); depth=0
 for i in range(brace,len(sample)):
  if sample[i]=='{':depth+=1
  elif sample[i]=='}':
   depth-=1
   if not depth:return sample[start:i+1]
 raise ValueError(name)
register=extract('sweet_lsd_register'); read=extract('sweet_lsd_identity_read'); producer=extract('sweet_ff_trace')
assert sample.index('sweet_lsd_register(phase, state);')<sample.index('sweet_bbctx_trace_mach(phase, call, state);')
assert 'if (!sweet_bbctx_identity_now(&identity)) return;' in extract('sweet_ff_trace')
source=r'''
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#define DEVELOPMENT 1
#define ARM64_BOARD_CONFIG_SWEET 1
#define MAXPATHLEN 1024
#undef ERESTART
#undef EJUSTRETURN
#define ERESTART (-1)
#define EJUSTRETURN (-2)
#define TASK_NULL NULL
#define PROC_NULL NULL
#define PSR64_IS_USER64(c) (((c)&31u)==0)
typedef uint32_t UInt32;
#define OSCompareAndSwap(old,val,p) __extension__ ({ uint32_t expected_=(old); __atomic_compare_exchange_n((p),&expected_,(val),false,__ATOMIC_ACQ_REL,__ATOMIC_ACQUIRE); })
#define os_atomic_load(p,order) __atomic_load_n((p),__ATOMIC_ACQUIRE)
#define os_atomic_store(p,value,order) __atomic_store_n((p),(value),__ATOMIC_RELEASE)
#define os_atomic_cmpxchg(p,old,next,order) __extension__ ({uint32_t expected_=(old);__atomic_compare_exchange_n((p),&expected_,(next),false,__ATOMIC_ACQ_REL,__ATOMIC_ACQUIRE);})
struct mocktask {int references;}; typedef struct mocktask *task_t;
struct mockproc {char p_comm[32];int32_t pid;uint32_t version;uint64_t uniqueid;}; typedef struct mockproc *proc_t;
struct proc_ident {int32_t p_pid;uint32_t p_idversion;uint64_t p_uniqueid;};
struct arm_saved_state {bool wide;uint32_t cpsr;uint64_t x[8];};
struct sweet_load_slot {task_t task;int32_t pid;uint32_t version;uint64_t uniqueid,registered;};
static struct mocktask tasks[3];static struct mockproc process;
static task_t active_task;static bool absent,irq;
static bool sweet_load_configured; static unsigned int sweet_ff_enabled;
static uint32_t sweet_load_control,sweet_load_ready;
static struct sweet_load_slot sweet_load_slots[2];
#define SWEET_LOAD_OPEN 4u
#define SWEET_LOAD_CLOSED 8u
static bool sweet_load_admitting(uint32_t v){return (v&(SWEET_LOAD_OPEN|SWEET_LOAD_CLOSED))==SWEET_LOAD_OPEN;}
static bool ml_at_interrupt_context(void){return irq;}
static proc_t current_proc(void){return absent?NULL:&process;}
static task_t current_task(void){return active_task;}
static void *current_thread(void){return (void *)(uintptr_t)55;}
static uint64_t thread_tid(void *p){return (uintptr_t)p;}
static void task_reference(task_t t){t->references++;}
static int proc_pid(proc_t q){return q->pid;}
static struct proc_ident proc_ident(proc_t q){return (struct proc_ident){q->pid,q->version,q->uniqueid};}
static bool is_saved_state64(struct arm_saved_state *s){return s->wide;}
static uint32_t get_saved_state_cpsr(struct arm_saved_state *s){return s->cpsr;}
static uint64_t get_saved_state_reg(struct arm_saved_state *s,unsigned i){return s->x[i];}
static uint64_t get_saved_state_pc(struct arm_saved_state *s){return s->x[5];}
static uint64_t get_saved_state_lr(struct arm_saved_state *s){return s->x[6];}
static uint64_t get_saved_state_sp(struct arm_saved_state *s){return s->x[7];}
static uint64_t clock_tick;static uint64_t mach_absolute_time(void){return ++clock_tick;}
#include "SweetFeatureTrace.h"
static uint32_t sweet_lsd_state;static struct sweet_load_slot sweet_lsd_slot;
static volatile UInt32 sweet_ff_guard,sweet_ff_dropped,sweet_bbipc_open;
static struct sweet_ff_ring sweet_ff_events;
struct sweet_bbipc_event {uint32_t seq;int pid;uint64_t tid,abstime,words[7];};
static struct sweet_bbipc_event sweet_bbipc_events;
static void sweet_bbipc_record(struct sweet_bbipc_event *a,struct sweet_bbipc_event *b){*a=*b;}
static void sweet_load_register(unsigned int p,struct arm_saved_state *s){(void)p;(void)s;}
static void
'''+register+r'''
#include "SweetBBContextRuntime.h"
bool
'''+read+r'''
void
'''+producer+r'''
int main(void){
 struct arm_saved_state state={.wide=true};struct sweet_lsd_identity_view view;
 assert(sweet_ff_svc_mode(4)==4 && sweet_ff_mode(4)==0 && sweet_ff_target(4,"lsd"));
 assert(!sweet_ff_target(4,"lsdX") && !sweet_ff_target(4,"LSD") && !sweet_ff_target(4,NULL));
 strcpy(process.p_comm,"lsd");process.pid=70;process.uniqueid=100;process.version=3;
 sweet_load_configured=true;sweet_ff_enabled=4;sweet_load_control=SWEET_LOAD_OPEN;active_task=&tasks[1];
 sweet_bbctx_prepare(1);sweet_bbctx_window_begin();
 sweet_lsd_register(1,&state);assert(!sweet_lsd_state);
 state.cpsr=1;sweet_lsd_register(0,&state);assert(!sweet_lsd_state);state.cpsr=0;
 state.x[5]=0x111;state.x[6]=0x222;state.x[7]=0x333;
 sweet_ff_trace(0,338,&state);assert(sweet_lsd_state==2 && tasks[1].references==1);
 assert(sweet_ff_events.total==1 && sweet_ff_events.events[0].pid==70 && sweet_ff_events.events[0].pc==0x111);
 assert(sweet_ff_events.events[0].lr==0x222 && sweet_ff_events.events[0].sp==0x333);
 sweet_ff_trace(1,338,&state);assert(sweet_ff_events.total==2 && sweet_ff_events.events[1].phase==1);
 assert(sweet_lsd_identity_read(&view) && view.task==&tasks[1] && view.pid==70 && view.uniqueid==100);
 struct sweet_bbctx_identity id;assert(sweet_bbctx_identity_now(&id));
 assert(id.uniqueid==100 && id.pid==70 && id.idversion==3 && id.tid==55);
 uint64_t epoch=sweet_bbctx_stat_enter();assert(epoch);
 char path[]="/private/var/db/lsd/test";
 assert(sweet_bbctx_namei_enter(path,sizeof(path))==epoch);
 sweet_bbctx_namei_return(epoch,2);sweet_bbctx_stat_return(epoch,2);
 struct sweet_bbctx_record *r=sweet_bbctx_find(&sweet_bbctx_stat_book,&id,epoch);
 assert(r && r->result==ENOENT && r->path_state==SWEET_BBCTX_PATH_COMPLETE);
 assert(!memcmp(r->path,path,sizeof(path)-1));
 uint64_t me=sweet_bbctx_mach_enter(-47,&state);assert(me);
 sweet_bbctx_mach_return(me,-47,&state);
 r=sweet_bbctx_find(&sweet_bbctx_mach_book,&id,me);assert(r && r->state==SWEET_BBCTX_COMPLETE);
 active_task=&tasks[2];process.pid=71;process.uniqueid=101;process.version=4;
 sweet_ff_trace(0,338,&state);assert(!tasks[2].references && !sweet_bbctx_identity_now(&id) && sweet_ff_events.total==2);
 active_task=&tasks[1];process.pid=70;process.uniqueid=100;process.version=4;assert(!sweet_bbctx_identity_now(&id));
 process.version=3;irq=true;assert(!sweet_bbctx_identity_now(&id));irq=false;
 sweet_load_control=SWEET_LOAD_OPEN|SWEET_LOAD_CLOSED;assert(!sweet_bbctx_identity_now(&id));
 /* Original BB mode still reads GUI slot zero. */
 sweet_load_control=SWEET_LOAD_OPEN;sweet_ff_enabled=3;sweet_load_ready=1;
 sweet_load_slots[0]=(struct sweet_load_slot){&tasks[0],42,7,55,1};active_task=&tasks[0];
 process.pid=42;process.version=7;process.uniqueid=55;
 assert(sweet_bbctx_identity_now(&id) && id.pid==42 && id.uniqueid==55);
 (void)&sweet_bbctx_trace_mach;(void)&sweet_bbctx_dump;
 puts("lsd mode4 real registration + BBCTX fixture: PASS");
 return 0;
}
'''
with tempfile.TemporaryDirectory(prefix='sweet-lsd-context-') as directory:
    p = Path(directory)
    include = p / 'include'
    (include / 'sys').mkdir(parents=True)
    for name in ('SweetFeatureTrace.h', 'SweetBBContextRuntime.h', 'SweetBBContextPolicy.h'):
        shutil.copyfile(xnu / 'bsd/dev' / name, include / name)
    shutil.copyfile(xnu / 'bsd/sys/SweetBBContext.h', include / 'sys/SweetBBContext.h')
    fixture = p / 'actual_lsd_fixture.c'
    fixture.write_text(source)
    binary = p / 'actual_lsd_fixture'
    compiler = shutil.which('clang') or shutil.which('cc')
    subprocess.run([compiler, '-std=gnu11', '-O1', '-g', '-Wall', '-Wextra', '-Werror',
                    '-fsanitize=address,undefined', '-fno-omit-frame-pointer',
                    '-I', str(include), str(fixture), '-o', str(binary)], check=True, timeout=60)
    subprocess.run([str(binary)], check=True, timeout=30)
