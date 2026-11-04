#!/usr/bin/env python3
"""Execute v4/v5 workers with deterministic producer-gate collisions."""
from pathlib import Path
import subprocess,sys,tempfile
D=Path(__file__).resolve().parents[1];P=D.parent/'ufs-hsg4-root-20261104-v1'
sys.dont_write_bytecode=True;sys.path.insert(0,str(P/'fixtures/legacy/tools'))
import test_dali_ufs_startup as fixture
f=fixture.source.function
new=(D/'xnu/bsd/dev/sweet_ufs.c').read_text(encoding='latin-1')
old=(D.parent/'sweetlog-slots-20261104-v4/xnu/bsd/dev/sweet_ufs.c').read_text(encoding='latin-1')
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
#define SWEET_LOG_BATCH (64u<<10)
#define UFS_BLOCK 4096u
#define SWEET_LOG_BLOCKS (1+SWEET_LOG_SIZE/UFS_BLOCK)
#define DMA_BOUNCE 0
#define DEVELOPMENT 0
#define PZERO 0
#define ETIMEDOUT 60
#ifndef __unused
#define __unused __attribute__((unused))
#endif
#define MIN(a,b) ((a)<(b)?(a):(b))
#define MAX(a,b) ((a)>(b)?(a):(b))
#define bzero(p,n) memset(p,0,n)
#define os_atomic_load(p,o) atomic_load_explicit(p,memory_order_acquire)
#define os_atomic_store(p,v,o) atomic_store_explicit(p,v,memory_order_release)
static unsigned cmp_calls,fail_cmp;static bool always_busy;
static bool cmp(_Atomic uint32_t *p,uint32_t b,uint32_t a){++cmp_calls;if(always_busy || cmp_calls==fail_cmp)return false;return atomic_compare_exchange_strong_explicit(p,&b,a,memory_order_acquire,memory_order_relaxed);}
#define os_atomic_cmpxchg(p,b,a,o) cmp(p,b,a)
typedef int wait_result_t;
static char sweet_log_ring[SWEET_LOG_SIZE],sweet_log_snapshot[SWEET_LOG_BATCH];
static _Atomic uint64_t sweet_log_total,sweet_log_dropped_chunks,sweet_log_dropped_bytes;
static _Atomic uint32_t sweet_log_busy;
static int sweet_log_chan,sweet_log_fair_chan,hz=100;static unsigned sweet_log_secs=10;
static unsigned char dma[SWEET_LOG_BATCH],disk[SWEET_LOG_BLOCKS*UFS_BLOCK];
static struct{bool dali,log_partition_configured,log_partition_ready,command_poisoned,hsg4_root;uint64_t first_block,nblocks,log_first_block;uintptr_t dma;char log_partition_name[64];}su;
static int sweet_ufs_lock;static bool held;static unsigned sleeps,writes,reads,syncs,exit_after;static int seen[64];static bool fair[64];static jmp_buf exit_log;
static bool ufs_dali_run_id(char out[33]){memcpy(out,"0123456789abcdef0123456789abcdef",33);return true;}
static uint32_t crc32(uint32_t v,const void *p,size_t n){(void)v;(void)p;(void)n;return 0;}
static int output(const char *fmt,...){(void)fmt;return 0;}
#define printf output
static void lck_mtx_lock(int *p){assert(p==&sweet_ufs_lock && !held);held=true;}
static void lck_mtx_unlock(int *p){assert(p==&sweet_ufs_lock && held);held=false;}
static int ufs_read_blocks(uint64_t lba,uint32_t n){assert(held && lba>=su.log_first_block && lba+n<=su.log_first_block+SWEET_LOG_BLOCKS);memcpy(dma,disk+(lba-su.log_first_block)*UFS_BLOCK,n*UFS_BLOCK);++reads;return 0;}
static int ufs_write_log_blocks(uint64_t lba,uint32_t n){assert(held && lba>=su.log_first_block && lba+n<=su.log_first_block+SWEET_LOG_BLOCKS);memcpy(disk+(lba-su.log_first_block)*UFS_BLOCK,dma,n*UFS_BLOCK);++writes;return 0;}
static int ufs_write_blocks(uint64_t lba,uint32_t n){return ufs_write_log_blocks(lba,n);}
static int ufs_sync_cache(void){assert(held);++syncs;return 0;}
static int tsleep(void *channel,int priority,const char *name,int ticks){
 assert(priority==PZERO && !held && !strcmp(name,"sweetlog") && sleeps<64);
 assert(channel==&sweet_log_chan || channel==&sweet_log_fair_chan);seen[sleeps]=ticks;fair[sleeps]=channel==&sweet_log_fair_chan;++sleeps;
 if(sleeps==exit_after)longjmp(exit_log,1);return 0;
}
'''
header=new[new.index('struct sweet_log_header {'):new.index('\n/* The pre-root probes',new.index('struct sweet_log_header {'))]
worker=f(new,'sweet_log_thread');old_worker=f(old,'sweet_log_thread').replace('sweet_log_thread(','old_log_thread(',1)
tail=r'''
#undef printf
static void reset(void){memset(&su,0,sizeof(su));memset(dma,0,sizeof(dma));memset(disk,0,sizeof(disk));memset(sweet_log_ring,0,sizeof(sweet_log_ring));
 su.dali=su.log_partition_configured=su.log_partition_ready=su.hsg4_root=true;su.log_first_block=1000;su.dma=(uintptr_t)dma;
 memcpy(sweet_log_ring,"DATA",4);atomic_store(&sweet_log_total,4);atomic_store(&sweet_log_busy,0);
 held=always_busy=false;cmp_calls=fail_cmp=sleeps=writes=reads=syncs=0;memset(seen,0,sizeof(seen));memset(fair,0,sizeof(fair));}
int main(void){
 /* v4 negative control: either collision returns to a full 10-second sleep. */
 for(unsigned collision=1;collision<=2;collision++){reset();fail_cmp=collision;exit_after=2;
  if(!setjmp(exit_log))old_log_thread(NULL,0);assert(sleeps==2 && seen[0]==1000 && seen[1]==1000 && !fair[1] && !writes);}
 /* v5 retries both initial and snapshot-copy collisions after a single tick. */
 for(unsigned collision=1;collision<=2;collision++){reset();fail_cmp=collision;exit_after=3;
  if(!setjmp(exit_log))sweet_log_thread(NULL,0);assert(sleeps==3 && seen[0]==1000 && seen[1]==1 && fair[1]);
  assert(!fair[2] && seen[2]==1000 && writes==2 && reads==1 && syncs==2 && !memcmp(disk+UFS_BLOCK,"DATA",4));}
 /* A gate held forever backs off, caps at configured sleep, and performs no I/O. */
 reset();always_busy=true;exit_after=14;if(!setjmp(exit_log))sweet_log_thread(NULL,0);
 int expected[]={1000,1,2,4,8,16,32,64,128,256,512,1000,1000,1000};
 assert(sleeps==14 && !memcmp(seen,expected,sizeof(expected)) && !fair[0]);
 for(unsigned i=1;i<sleeps;i++)assert(fair[i]);assert(!writes && !reads && !syncs && !held);
 assert(sweet_log_retry_next(0)==1 && sweet_log_retry_next(512)==1000 && sweet_log_retry_next(1000)==1000);
 puts("PASS actual v4 negative and v5 gate/copy collision backoff, permanent-busy cap, no I/O while busy");return 0;
}
'''
code=prefix+header+f(new,'sweet_log_retry_next')+worker+old_worker+tail
with tempfile.TemporaryDirectory(prefix='sweetlog-retry-') as t:
 p=Path(t);(p/'t.c').write_text(code)
 subprocess.run(['clang','-std=gnu11','-O1','-Wall','-Wextra','-Werror','-fsanitize=address,undefined','-fno-sanitize-recover=all',str(p/'t.c'),'-o',str(p/'t')],check=True)
 subprocess.run([str(p/'t')],check=True,timeout=30)
