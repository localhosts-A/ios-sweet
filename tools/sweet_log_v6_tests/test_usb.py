#!/usr/bin/env python3
"""Execute actual USB log consumer and v3 producer/copy with mocked UDP."""
from pathlib import Path
import subprocess
import sys
import tempfile
D=Path(__file__).resolve().parents[1]
P=D.parent/'ufs-hsg4-root-20261104-v1'
sys.dont_write_bytecode=True
sys.path.insert(0,str(P/'fixtures/legacy/tools'))
import test_dali_ufs_startup as fixture
f=fixture.source.function
ufs=(D/'xnu/bsd/dev/sweet_ufs.c').read_text(encoding='latin-1')
usb=(P/'xnu/bsd/dev/sweet_usb.c').read_text(encoding='latin-1')
prefix=r'''
#include <assert.h>
#include <stdatomic.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>
#include <setjmp.h>
#define SWEET_LOG_SIZE (4u<<20)
#define SWEET_LOG_APPEND_MAX 256u
#define SWEET_LOG_COPY_MAX 4096u
#define MIN(a,b) ((a)<(b)?(a):(b))
#define os_atomic_load(p,o) atomic_load_explicit(p,memory_order_acquire)
#define os_atomic_store(p,v,o) atomic_store_explicit(p,v,memory_order_release)
#define os_atomic_add_orig(p,n,o) atomic_fetch_add_explicit(p,n,memory_order_relaxed)
static bool cmp(_Atomic uint32_t *p,uint32_t b,uint32_t a){return atomic_compare_exchange_strong_explicit(p,&b,a,memory_order_acquire,memory_order_relaxed);}
#define os_atomic_cmpxchg(p,b,a,o) cmp(p,b,a)
#define bzero(p,n) memset(p,0,n)
#ifndef __unused
#define __unused __attribute__((unused))
#endif
typedef int wait_result_t;
static char sweet_log_ring[SWEET_LOG_SIZE];static _Atomic uint64_t sweet_log_total,sweet_log_dropped_chunks,sweet_log_dropped_bytes;
static _Atomic uint32_t sweet_log_busy;
static void sweet_ramoops_append(const char *p,int n){assert(p && n>0 && n<=256);}
'''
net=r'''
#define PF_INET 2
#define AF_INET 2
#define SOCK_DGRAM 2
#define IPPROTO_UDP 17
typedef void *socket_t;
struct sockaddr_in{uint8_t sin_len,sin_family;uint16_t sin_port;struct{uint32_t s_addr;}sin_addr;};
struct iovec{void *iov_base;size_t iov_len;};
struct msghdr{void *msg_name;size_t msg_namelen;struct iovec *msg_iov;size_t msg_iovlen;};
static struct{bool configured;}ud;
static char received[6000];static unsigned recv_n,sends,attempts,sleeps,scenario;static jmp_buf stop;
static uint16_t htons(uint16_t p){return p;}
static uint32_t htonl(uint32_t p){return p;}
static int sock_socket(int a,int b,int c,void *d,void *e,socket_t *s){assert(a==PF_INET&&b==SOCK_DGRAM&&c==IPPROTO_UDP&&!d&&!e);*s=(void*)1;return 0;}
static int sock_send(socket_t s,struct msghdr *msg,int flags,size_t *sent){
 assert(s==(void*)1 && !flags && msg->msg_iovlen==1 && msg->msg_iov->iov_len<=1400);
 attempts++;if(scenario==2 && attempts==1)return 5;
 size_t n=msg->msg_iov->iov_len;assert(recv_n+n<=sizeof(received));
 memcpy(received+recv_n,msg->msg_iov->iov_base,n);recv_n+=(unsigned)n;*sent=n;sends++;return 0;
}
static void ud_sleep_ms(unsigned ms){assert(ms==100);++sleeps;
 if(scenario==1 && sleeps==1)atomic_store(&sweet_log_busy,1);
 if(scenario==1 && sleeps==2){assert(!attempts);atomic_store(&sweet_log_busy,0);}
 if(sleeps==3)longjmp(stop,1);
}
'''
tail=r'''
static char source[5000];
static void reset(unsigned s){memset(sweet_log_ring,0,sizeof(sweet_log_ring));atomic_store(&sweet_log_total,0);
 atomic_store(&sweet_log_busy,0);atomic_store(&sweet_log_dropped_chunks,0);atomic_store(&sweet_log_dropped_bytes,0);
 recv_n=sends=attempts=sleeps=0;scenario=s;ud.configured=true;sweet_log_append(source,sizeof(source));}
int main(void){
 for(unsigned i=0;i<sizeof(source);i++)source[i]=(char)(i%127);
 reset(1);if(!setjmp(stop))ud_log_thread(NULL,0);
 assert(attempts==4 && sends==4 && recv_n==5000 && !memcmp(received,source,5000));
 reset(2);if(!setjmp(stop))ud_log_thread(NULL,0);
 /* Existing consumer advances pos before send; failed datagram is not retried. */
 assert(attempts==4 && sends==3 && recv_n==3600 && !memcmp(received,source+1400,3600));
 puts("PASS actual USB consumer: busy-gate retry, 1400-byte UDP copies, existing send-failure loss");return 0;
}
'''
start=ufs.index('uint64_t\nsweet_log_position(void)\n{')
end=ufs.index('\n}',start)+2
position=ufs[start:end]+'\n'
code=prefix+f(ufs,'sweet_log_append')+f(ufs,'sweet_log_copy')+position+net+f(usb,'ud_log_thread')+tail
with tempfile.TemporaryDirectory(prefix='sweetlog-usb-') as t:
 p=Path(t);(p/'t.c').write_text(code)
 subprocess.run(['clang','-std=gnu11','-O1','-Wall','-Wextra','-Werror','-fsanitize=address,undefined','-fno-sanitize-recover=all',str(p/'t.c'),'-o',str(p/'t')],check=True)
 subprocess.run([str(p/'t')],check=True,timeout=30)
