#!/usr/bin/env python3
"""Execute actual batched worker and fastboot waiter with thread-safe mock I/O."""
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
src=(D/'xnu/bsd/dev/sweet_ufs.c').read_text(encoding='latin-1')
prefix=r'''
#include <assert.h>
#include <stdatomic.h>
#include <pthread.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>
#include <time.h>
#include <sched.h>
#define SWEET_LOG_SIZE (4u<<20)
#define SWEET_LOG_BATCH (64u<<10)
#define UFS_BLOCK 4096u
#define SWEET_LOG_BLOCKS (1+SWEET_LOG_SIZE/UFS_BLOCK)
#define DMA_BOUNCE 0
#define DEVELOPMENT 1
#define PZERO 0
#define ETIMEDOUT 60
#ifndef __unused
#define __unused __attribute__((unused))
#endif
#define MIN(a,b) ((a)<(b)?(a):(b))
#define MAX(a,b) ((a)>(b)?(a):(b))
#define bzero(p,n) memset(p,0,n)
#define os_atomic(t) _Atomic(t)
#define os_atomic_load(p,o) atomic_load_explicit((p),memory_order_acquire)
#define os_atomic_store(p,v,o) atomic_store_explicit((p),(v),memory_order_release)
static bool cmp(_Atomic uint32_t *p,uint32_t before,uint32_t after){return atomic_compare_exchange_strong_explicit(p,&before,after,memory_order_acquire,memory_order_relaxed);}
#define os_atomic_cmpxchg(p,b,a,o) cmp(p,b,a)
typedef int wait_result_t;
static char sweet_log_ring[SWEET_LOG_SIZE],sweet_log_snapshot[SWEET_LOG_BATCH];
static _Atomic uint64_t sweet_log_total,sweet_log_dropped_chunks,sweet_log_dropped_bytes;
static _Atomic uint32_t sweet_log_busy;
static int sweet_log_chan,sweet_log_fair_chan,sweet_log_fastboot_chan,hz=100;
static unsigned sweet_log_secs=10;
static unsigned char dma[SWEET_LOG_BATCH],disk[SWEET_LOG_BLOCKS*UFS_BLOCK];
static struct{bool dali,log_partition_configured,log_partition_ready,command_poisoned,hsg4_root;uint64_t first_block,nblocks,log_first_block;uintptr_t dma;char log_partition_name[64];}su;
static int sweet_ufs_lock;static pthread_mutex_t io_lock=PTHREAD_MUTEX_INITIALIZER;
static pthread_mutex_t ev_lock=PTHREAD_MUTEX_INITIALIZER;static pthread_cond_t ev=PTHREAD_COND_INITIALIZER;
static bool signaled,stop;static _Atomic unsigned normal_sleeps,fair_sleeps,writes,reads,syncs;
static _Thread_local bool held;
static uint64_t mach_absolute_time(void){return 1;}
static bool ml_get_interrupts_enabled(void){return true;}
static bool ml_at_interrupt_context(void){return false;}
static int get_preemption_level(void){return 0;}
static bool ufs_dali_run_id(char out[33]){memcpy(out,"0123456789abcdef0123456789abcdef",33);return true;}
static uint32_t crc32(uint32_t v,const void *p,size_t n){(void)v;(void)p;(void)n;return 0;}
static int output(const char *fmt,...){(void)fmt;return 0;}
#define printf output
static void lck_mtx_lock(int *p){assert(p==&sweet_ufs_lock && !held);assert(!pthread_mutex_lock(&io_lock));held=true;}
static void lck_mtx_unlock(int *p){assert(p==&sweet_ufs_lock && held);held=false;assert(!pthread_mutex_unlock(&io_lock));}
static int ufs_read_blocks(uint64_t lba,uint32_t n){assert(held && lba>=su.log_first_block && lba+n<=su.log_first_block+SWEET_LOG_BLOCKS);memcpy(dma,disk+(lba-su.log_first_block)*UFS_BLOCK,n*UFS_BLOCK);atomic_fetch_add(&reads,1);return 0;}
static int ufs_write_log_blocks(uint64_t lba,uint32_t n){assert(held && lba>=su.log_first_block && lba+n<=su.log_first_block+SWEET_LOG_BLOCKS);memcpy(disk+(lba-su.log_first_block)*UFS_BLOCK,dma,n*UFS_BLOCK);atomic_fetch_add(&writes,1);return 0;}
static int ufs_write_blocks(uint64_t lba,uint32_t n){return ufs_write_log_blocks(lba,n);}
static int ufs_sync_cache(void){assert(held);atomic_fetch_add(&syncs,1);return 0;}
static void wakeup(void *channel){assert(!held);assert(!pthread_mutex_lock(&ev_lock));if(channel==&sweet_log_chan)signaled=true;else assert(channel==&sweet_log_fastboot_chan);pthread_cond_broadcast(&ev);pthread_mutex_unlock(&ev_lock);}
static int tsleep(void *channel,int priority,const char *name,int ticks){
 assert(priority==PZERO && !held);
 if(channel==&sweet_log_fair_chan){
  assert(ticks==1 && !strcmp(name,"sweetlog"));atomic_fetch_add(&fair_sleeps,1);
  assert(!pthread_mutex_trylock(&io_lock));pthread_mutex_unlock(&io_lock);sched_yield();return 0;
 }
 assert(!pthread_mutex_lock(&ev_lock));
 if(channel==&sweet_log_chan){
  assert(ticks==hz*10 && !strcmp(name,"sweetlog"));atomic_fetch_add(&normal_sleeps,1);
  while(!signaled && !stop)pthread_cond_wait(&ev,&ev_lock);
  if(stop){pthread_mutex_unlock(&ev_lock);pthread_exit(NULL);}
  signaled=false;pthread_mutex_unlock(&ev_lock);return 0;
 }
 assert(channel==&sweet_log_fastboot_chan && ticks==1 && !strcmp(name,"fbdrain"));
 struct timespec t;clock_gettime(CLOCK_REALTIME,&t);t.tv_nsec+=1000000;
 if(t.tv_nsec>=1000000000){t.tv_sec++;t.tv_nsec-=1000000000;}
 pthread_cond_timedwait(&ev,&ev_lock,&t);pthread_mutex_unlock(&ev_lock);return 0;
}
'''
header=src[src.index('struct sweet_log_header {'):src.index('\n/* The pre-root probes',src.index('struct sweet_log_header {'))]
a=src.index('#if DEVELOPMENT\n/* Completed logger publications only:')
b=src.index('static void\nsweet_log_thread(',a)
hooks=src[a:b]
tail=r'''
#undef printf
static void *thread_main(void *x){(void)x;sweet_log_thread(NULL,0);return NULL;}
int main(void){
 const uint64_t target=200001;
 su.dali=su.log_partition_ready=su.log_partition_configured=true;su.log_first_block=1000;su.dma=(uintptr_t)dma;
 memset(sweet_log_ring,'Q',target);atomic_store(&sweet_log_total,target);
 pthread_t thread;assert(!pthread_create(&thread,NULL,thread_main,NULL));
 for(unsigned n=0;!atomic_load(&sweet_log_fastboot_running)&&n<100000;n++)sched_yield();
 assert(atomic_load(&sweet_log_fastboot_running));
 assert(sweet_log_fastboot_drain(1000000));
 assert(atomic_load(&sweet_log_fastboot_persisted)>=target);
 assert(atomic_load(&fair_sleeps)==3 && atomic_load(&writes)==8 && atomic_load(&syncs)==8 && atomic_load(&reads)==1);
 struct sweet_log_header *h=(void*)disk;assert(h->total==target && !memcmp(disk+UFS_BLOCK,sweet_log_ring,target));
 assert(!pthread_mutex_lock(&ev_lock));stop=true;pthread_cond_broadcast(&ev);pthread_mutex_unlock(&ev_lock);
 assert(!pthread_join(thread,NULL));
 puts("PASS actual 200001-byte backlog and fastboot drain: four batches, three unlocked one-tick yields, one partial read");return 0;
}
'''
code=prefix+header+hooks+f(src,'sweet_log_thread')+tail
with tempfile.TemporaryDirectory(prefix='sweetlog-backlog-') as t:
 p=Path(t);(p/'t.c').write_text(code)
 subprocess.run(['clang','-std=gnu11','-O1','-Wall','-Wextra','-Werror','-fsanitize=address,undefined','-fno-sanitize-recover=all','-pthread',str(p/'t.c'),'-o',str(p/'t')],check=True)
 subprocess.run([str(p/'t')],check=True,timeout=30)
