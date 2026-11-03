#!/usr/bin/env python3
"""Execute the materialized IPC route policy and actual send/hooks/formatter."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

here = Path(__file__).resolve().parent
if len(sys.argv) != 2:
    raise SystemExit('usage: test_dali_ipcroute.py MATERIALIZED_XNU')
xnu = Path(sys.argv[1]).resolve()
source = (xnu / 'osfmk/ipc/ipc_mqueue.c').read_text()
include = xnu / 'osfmk/kern'
compiler = shutil.which('clang') or shutil.which('cc')
if compiler is None:
    raise AssertionError('a C compiler is required')
def function(name):
    start=source.index('\n'+name+'(')+1
    brace=source.index('{',start)
    depth=0
    for i in range(brace,len(source)):
        if source[i]=='{': depth+=1
        elif source[i]=='}':
            depth-=1
            if depth==0:return source[start:i+1]
    raise RuntimeError('unclosed '+name)
pre=function('sweet_ipcroute_prequeue')
record=function('sweet_ipcsend_record')
send=function('ipc_mqueue_send_locked')
dump=function('sweet_ipcroute_dump')
assert send.index('sweet_ipcroute_prequeue(')<send.index('if (!imq_full(')
assert 'sweet_load_rw_target(current_task()) != 2' in pre
assert 'ipc_port_get_receiver_task_locked(port, NULL)' in pre
assert 'find_user_regs(thread)' in pre and 'copyin' not in pre and 'printf' not in pre
prefix=r'''
#include <assert.h>
#include <stdarg.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define SWEET_IPCSEND_CAS(old, val, p) __extension__ ({ uint32_t expected_=(old); __atomic_compare_exchange_n((p),&expected_,(val),0,__ATOMIC_ACQ_REL,__ATOMIC_ACQUIRE); })
#define SWEET_IPCSEND_INC(p) __atomic_add_fetch((p),1,__ATOMIC_RELAXED)
#define SWEET_IPCSEND_LOAD(p) __atomic_load_n((p),__ATOMIC_RELAXED)
#define SWEET_IPCSEND_CLOSED(p) __atomic_load_n((p),__ATOMIC_ACQUIRE)
#define SWEET_IPCSEND_CLOSE(p) __atomic_store_n((p),1u,__ATOMIC_RELEASE)
#include <SweetIPCSendTrace.h>
#define DEVELOPMENT 1
#define ARM64_BOARD_CONFIG_SWEET 1
typedef int pid_t;
typedef uint32_t mach_msg_option_t;
typedef uint32_t mach_msg_timeout_t;
typedef int mach_msg_return_t;
struct fake_port { uint32_t ip_receiver_name; bool ip_fullwaiters; int receiver_pid; };
typedef struct fake_port *ipc_port_t;
struct fake_mq { ipc_port_t port; uint32_t imq_msgcount,imq_qlimit; bool full,hard_full; };
typedef struct fake_mq *ipc_mqueue_t;
struct fake_header { uint32_t msgh_bits; int32_t msgh_id; };
struct fake_kmsg {struct fake_header header;};
typedef struct fake_kmsg *ipc_kmsg_t;
struct arm_saved_state {bool valid,user;uint64_t pc,lr;};
typedef struct arm_saved_state arm_saved_state_t;
struct fake_thread {uint64_t tid; arm_saved_state_t state;};
typedef struct fake_thread *thread_t;
struct waitq {int unused;}; struct turnstile {struct waitq ts_waitq;};
static struct sweet_ipcsend_ring sweet_ipcsend_ring;
static struct sweet_ipcroute_book sweet_ipcroute_book;
static struct fake_thread thread={.tid=687,.state={true,true,0xabc,0xdef}};
static struct turnstile turnstile;
static uint32_t selected=2;static bool locked=true;static int posted,receiver_calls;
static uint64_t stamp=1000;
static void *current_task(void){return (void *)1;}
static uint32_t sweet_load_rw_target(void *p){assert(p==(void *)1);return selected;}
static thread_t current_thread(void){return &thread;}
static uint64_t thread_tid(thread_t t){return t->tid;}
static arm_saved_state_t *find_user_regs(thread_t t){return &t->state;}
static bool is_saved_state64(arm_saved_state_t *s){return s->valid;}
#define get_saved_state_pc(s) ((s)->pc)
#define get_saved_state_lr(s) ((s)->lr)
#define get_saved_state_cpsr(s) ((s)->user)
#define PSR64_IS_USER64(x) (x)
static uint64_t mach_absolute_time(void){return ++stamp;}
static pid_t ipc_port_get_receiver_task_locked(ipc_port_t p,void *u){assert(locked && !u);receiver_calls++;return p->receiver_pid;}
#define ikm_header(k) (&(k)->header)
#define ip_from_mq(mq) ((mq)->port)
#define imq_full(mq) ((mq)->full)
#define imq_full_kernel(mq) ((mq)->hard_full)
#define MACH_SEND_ALWAYS 1u
#define MACH_SEND_TIMEOUT 2u
#define MACH_MSG_TYPE_PORT_SEND_ONCE 3u
#define MACH_MSGH_BITS_REMOTE(x) (x)
#define MACH_MSG_SUCCESS 0
#define MACH_SEND_TIMED_OUT 100
#define MACH_SEND_NO_BUFFER 101
#define MACH_SEND_INTERRUPTED 102
#define MACH_SEND_INVALID_DEST 103
#define THREAD_WAITING 11
#define THREAD_AWAKENED 12
#define THREAD_TIMED_OUT 13
#define THREAD_INTERRUPTED 14
#define THREAD_RESTART 15
#define THREAD_ABORTSAFE 0
#define TIMEOUT_URGENCY_USER_NORMAL 0
#define TIMEOUT_NO_LEEWAY 0
#define TURNSTILE_NULL NULL
#define TURNSTILE_SYNC_IPC 0
#define TURNSTILE_DELAYED_UPDATE 0
#define TURNSTILE_INTERLOCK_NOT_HELD 0
#define THREAD_CONTINUE_NULL 0
#define IPC_MQUEUE_FULL 0
#define NSEC_PER_USEC 1000
#define kThreadWaitPortSend 4
static void ip_mq_unlock(ipc_port_t p){(void)p;assert(locked);locked=false;}
static void ip_mq_lock(ipc_port_t p){(void)p;assert(!locked);locked=true;}
static void ipc_mqueue_post(ipc_mqueue_t q,ipc_kmsg_t m,mach_msg_option_t o){(void)q;(void)m;(void)o;assert(!locked);posted++;}
static void clock_interval_to_deadline(uint32_t a,uint32_t b,uint64_t *c){*c=(uint64_t)a*b;}
static void thread_set_pending_block_hint(thread_t t,int x){(void)t;(void)x;}
static void *port_send_turnstile_address(ipc_port_t p){return p;}
static struct turnstile *turnstile_prepare(uintptr_t a,void *b,void *c,int d){(void)a;(void)b;(void)c;(void)d;return &turnstile;}
static void ipc_port_send_update_inheritor(ipc_port_t p,struct turnstile *t,int x){(void)p;(void)t;(void)x;}
static int waitq_assert_wait64_leeway(struct waitq *q,int a,int b,int c,uint64_t d,int e){(void)q;(void)a;(void)b;(void)c;(void)d;(void)e;return THREAD_WAITING;}
static void turnstile_update_inheritor_complete(struct turnstile *t,int x){(void)t;(void)x;assert(!locked);}
static int thread_block(int x){(void)x;return THREAD_AWAKENED;}
static void turnstile_complete(uintptr_t a,void *b,void *c,int d){(void)a;(void)b;(void)c;(void)d;assert(locked);}
static void turnstile_cleanup(void){assert(!locked);}
#define panic(...) abort()
static size_t max_line; static int output_lines; static bool output_sanitized;
void sweet_ipcsend_receiver_name(int pid,char *name,int size){(void)pid;snprintf(name,(size_t)size,"service with spaces");}
static int test_printf(const char *fmt,...){char buf[1024];va_list ap;va_start(ap,fmt);int n=vsnprintf(buf,sizeof(buf),fmt,ap);va_end(ap);assert(n>0 && n<256);if((size_t)n>max_line)max_line=n;output_lines++;if(strstr(buf,"service_with_spaces"))output_sanitized=true;return n;}
'''
tests=r'''
int main(void){
 struct fake_port p={.ip_receiver_name=0x333,.receiver_pid=77};
 struct fake_mq q={.port=&p,.imq_msgcount=1,.imq_qlimit=6};
 struct fake_kmsg k={.header={.msgh_id=12345}};
 struct sweet_ipcroute_snapshot s;
 selected=1;locked=true;
 assert(ipc_mqueue_send_locked(&q,&k,0,0)==MACH_MSG_SUCCESS && posted==1);
 assert(!sweet_ipcroute_snapshot(&sweet_ipcroute_book,&s)&&s.count==0&&receiver_calls==0);
 selected=2;locked=true;
 assert(ipc_mqueue_send_locked(&q,&k,0,0)==MACH_MSG_SUCCESS && posted==2);
 assert(!sweet_ipcroute_snapshot(&sweet_ipcroute_book,&s)&&s.count==1&&s.records[0].tid==687);
 assert(s.records[0].receiver_pid==77 && s.records[0].receiver_port==0x333 && s.records[0].msg_id==12345);
 assert(s.records[0].pc==0xabc && s.records[0].lr==0xdef && s.records[0].user_context_valid==1);
 q.full=true;locked=true;k.header.msgh_id=333;
 assert(ipc_mqueue_send_locked(&q,&k,MACH_SEND_TIMEOUT,0)==MACH_SEND_TIMED_OUT && posted==2);
 assert(!sweet_ipcroute_snapshot(&sweet_ipcroute_book,&s)&&s.count==1&&s.records[0].msg_id==333);
 q.full=true;locked=true;thread.state.user=false;k.header.msgh_id=444;
 assert(ipc_mqueue_send_locked(&q,&k,0,0)==MACH_MSG_SUCCESS && posted==3);
 assert(!sweet_ipcroute_snapshot(&sweet_ipcroute_book,&s)&&s.count==1&&s.records[0].msg_id==444);
 assert(s.records[0].user_context_valid==0 && s.records[0].pc==0 && s.records[0].lr==0);
 assert(sweet_ipcsend_ring.used==2); /* existing full-wait ENTRY/EXIT preserved */
 sweet_ipcroute_dump(21,0);assert(output_lines==3 && output_sanitized && max_line<256);
 sweet_ipcroute_close(&sweet_ipcroute_book);locked=true;q.full=false;
 int before=receiver_calls;assert(ipc_mqueue_send_locked(&q,&k,0,0)==MACH_MSG_SUCCESS);
 assert(receiver_calls==before && !sweet_ipcroute_snapshot(&sweet_ipcroute_book,&s) && s.records[0].msg_id==444);
 puts("actual ipc_mqueue send, producer and formatter fixture: PASS");return 0;
}
'''
with tempfile.TemporaryDirectory(prefix='sweet-ipcroute-') as folder:
    temporary = Path(folder)
    # Isolate the exact materialized header; XNU's kern/assert.h must not
    # shadow the host C library's assert.h in these host-only fixtures.
    header_directory = temporary / 'include'
    header_directory.mkdir()
    shutil.copyfile(include / 'SweetIPCSendTrace.h', header_directory / 'SweetIPCSendTrace.h')
    fixture = temporary / 'actual_send_fixture.c'
    fixture.write_text(prefix+'\nstatic void\n'+pre+'\nstatic int\n'+record+'\n#define printf test_printf\nstatic void\n'+dump+'\n#undef printf\nmach_msg_return_t\n'+send+'\n'+tests)
    for fixture_source, name in ((here / 'test_dali_ipcroute.c', 'policy'), (fixture, 'actual_send')):
        binary = temporary / name
        subprocess.run([compiler, '-std=gnu11', '-O1', '-g', '-Wall', '-Wextra', '-Werror',
                        '-pthread', '-fsanitize=address,undefined', '-fno-omit-frame-pointer',
                        '-I', str(header_directory), str(fixture_source), '-o', str(binary)], check=True, timeout=60)
        subprocess.run([str(binary)], check=True, timeout=30)
print('PASS: materialized IPC route source/header fixtures')
