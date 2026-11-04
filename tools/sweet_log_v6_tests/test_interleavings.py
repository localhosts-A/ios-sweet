#!/usr/bin/env python3
"""Deterministic real-code producer/worker interleavings and crash-wrap test."""
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
new=(D/'xnu/bsd/dev/sweet_ufs.c').read_text(encoding='latin-1')
old=(P/'xnu/bsd/dev/sweet_ufs.c').read_text(encoding='latin-1')
prefix=r'''
#include <assert.h>
#include <stdatomic.h>
#include <pthread.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>
#include <setjmp.h>
#define UFS_BLOCK 4096u
#define SWEET_LOG_SIZE (4u<<20)
#define SWEET_LOG_BLOCKS (1+SWEET_LOG_SIZE/UFS_BLOCK)
#define SWEET_LOG_BATCH (64u<<10)
#define BOUNCE_SIZE SWEET_LOG_BATCH
#define SWEET_LOG_APPEND_MAX 256u
#define SWEET_LOG_COPY_MAX 4096u
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
typedef int wait_result_t;
static char sweet_log_ring[SWEET_LOG_SIZE],sweet_log_snapshot[SWEET_LOG_BATCH];
static _Atomic uint64_t sweet_log_total,sweet_log_dropped_chunks,sweet_log_dropped_bytes;
static _Atomic uint32_t sweet_log_busy;
static int sweet_log_chan,sweet_log_fair_chan,hz=100;static unsigned sweet_log_secs=1;
static unsigned char dma[SWEET_LOG_BATCH],disk[SWEET_LOG_BLOCKS*UFS_BLOCK];
static struct {bool log_partition_configured,log_partition_ready,dali,command_poisoned,hsg4_root;uint64_t log_first_block,first_block,nblocks;uintptr_t dma;char log_partition_name[64];}su;
static int sweet_ufs_lock;static bool held;static int ticks,writes,syncs,mode;static jmp_buf exit_log;
static pthread_mutex_t mu=PTHREAD_MUTEX_INITIALIZER;static pthread_cond_t cv=PTHREAD_COND_INITIALIZER;
static bool paused,resume;static pthread_t writer;static _Thread_local bool pause_load;
static bool pause_mid,race_armed;static unsigned race_calls;static const char *race_text;
static bool audit,reenter;static uint64_t audit_last;static unsigned audit_acquires,ramoops_calls,ramoops_max;
void sweet_log_append(const char *,int);
static uint64_t get_total(_Atomic uint64_t *p,const char *order){
 if(pause_load && !strcmp(order,"relaxed")){pthread_mutex_lock(&mu);paused=true;pthread_cond_broadcast(&cv);while(!resume)pthread_cond_wait(&cv,&mu);pthread_mutex_unlock(&mu);pause_load=false;}
 uint64_t value=atomic_load_explicit(p,memory_order_acquire);
 if(audit && p==&sweet_log_total && !strcmp(order,"relaxed")){
  if(audit_acquires)assert(value>=audit_last && value-audit_last<=SWEET_LOG_APPEND_MAX);
  audit_last=value;++audit_acquires;
 }
 if(reenter && p==&sweet_log_total && !strcmp(order,"relaxed")){
  reenter=false;sweet_log_append("PANIC",5); /* interrupt/panic reentry with gate held */
 }
 return value;
}
static uint64_t reserve_old(_Atomic uint64_t *p,uint64_t n){
 uint64_t old=atomic_fetch_add(p,n);pthread_mutex_lock(&mu);paused=true;pthread_cond_broadcast(&cv);
 while(!resume)pthread_cond_wait(&cv,&mu);pthread_mutex_unlock(&mu);return old;
}
#define os_atomic_load(p,o) get_total((p),#o)
#define os_atomic_store(p,v,o) atomic_store_explicit((p),(v),memory_order_release)
static bool cmp(_Atomic uint32_t *p,uint32_t before,uint32_t after){
 if(race_armed && ++race_calls==2){race_armed=false;sweet_log_append(race_text,1);}
 return atomic_compare_exchange_strong_explicit(p,&before,after,memory_order_acquire,memory_order_relaxed);
}
#define os_atomic_cmpxchg(p,b,a,o) cmp(p,b,a)
static uint64_t add_orig(_Atomic uint64_t *p,uint64_t n){
 if(p==&sweet_log_total)return reserve_old(p,n);
 return atomic_fetch_add_explicit(p,n,memory_order_relaxed);
}
#define os_atomic_add_orig(p,n,o) add_orig(p,n)
static void test_copy_step(uint32_t i){
 if(pause_mid && i==63){pthread_mutex_lock(&mu);paused=true;pthread_cond_broadcast(&cv);
  while(!resume)pthread_cond_wait(&cv,&mu);pthread_mutex_unlock(&mu);pause_mid=false;}
}
static void sweet_ramoops_append(const char *p,int n){assert(p && n>0 && n<=(int)SWEET_LOG_APPEND_MAX);++ramoops_calls;if((unsigned)n>ramoops_max)ramoops_max=(unsigned)n;}
static bool ufs_dali_run_id(char out[33]){memcpy(out,"0123456789abcdef0123456789abcdef",33);return true;}
static uint32_t crc32(uint32_t c,const void *p,size_t n){(void)c;(void)p;(void)n;return 0;}
static int output(const char *fmt,...){(void)fmt;return 0;}
#define printf output
static void lck_mtx_lock(int *p){assert(p==&sweet_ufs_lock&&!held&&!atomic_load(&sweet_log_busy));held=true;}
static void lck_mtx_unlock(int *p){assert(p==&sweet_ufs_lock&&held);held=false;}
static int ufs_write_log_blocks(uint64_t lba,uint32_t n){
 assert(held && lba>=su.log_first_block && lba+n<=su.log_first_block+SWEET_LOG_BLOCKS);
 memcpy(disk+(lba-su.log_first_block)*UFS_BLOCK,dma,n*UFS_BLOCK);writes++;return 0;
}
static int ufs_write_blocks(uint64_t lba,uint32_t n){return ufs_write_log_blocks(lba,n);}
static int ufs_read_blocks(uint64_t lba,uint32_t n){
 assert(held && lba>=su.log_first_block && lba+n<=su.log_first_block+SWEET_LOG_BLOCKS);
 memcpy(dma,disk+(lba-su.log_first_block)*UFS_BLOCK,n*UFS_BLOCK);return 0;
}
static int ufs_sync_cache(void){assert(held);syncs++;if(mode==3 && ticks==65){held=false;longjmp(exit_log,1);}return 0;}
static void wake_writer(void){pthread_mutex_lock(&mu);resume=true;pthread_cond_broadcast(&cv);pthread_mutex_unlock(&mu);assert(!pthread_join(writer,NULL));}
void sweet_log_append(const char *,int);
static int tsleep(void *channel,int pri,const char *name,int duration){
 assert(pri==PZERO && !strcmp(name,"sweetlog") && !held);
 assert((channel==&sweet_log_chan && duration==100) || (channel==&sweet_log_fair_chan && duration==1));ticks++;
 if((mode==1||mode==2) && ticks==2)wake_writer();
 if((mode==1||mode==2) && ticks==3)longjmp(exit_log,1);
 if(mode==3 && ticks==65){static char b[UFS_BLOCK];memset(b,'B',sizeof(b));sweet_log_append(b,sizeof(b));}
 if(mode==3 && ticks>65)longjmp(exit_log,1);
 if(mode==6 && ticks==2)longjmp(exit_log,1);
 if(mode==7 && ticks==2){atomic_store(&sweet_log_total,SWEET_LOG_SIZE);race_armed=true;race_calls=0;race_text="B";}
 if(mode==7 && ticks==3)longjmp(exit_log,1);
 if(mode==8 && ticks==65)sweet_log_append("X",1);
 if(mode==8 && ticks==66){sweet_log_append("B",1);race_armed=true;race_calls=0;race_text="C";}
 if(mode==8 && ticks==67)longjmp(exit_log,1);
 return 0;
}
'''
header=new[new.index('struct sweet_log_header {'):new.index('\n/* The pre-root probes',new.index('struct sweet_log_header {'))]
new_append=f(new,'sweet_log_append'); new_copy=f(new,'sweet_log_copy'); new_thread=f(new,'sweet_log_thread')
# Instrument only the byte-copy point to pause a real writer mid-chunk.
needle='sweet_log_ring[(at + i) % SWEET_LOG_SIZE] = c[i];'
assert new_append.count(needle)==1
new_append=new_append.replace(needle,needle+' test_copy_step(i);',1)
old_append=f(old,'sweet_log_append').replace('sweet_log_append(', 'old_append(',1)
old_thread=f(old,'sweet_log_thread').replace('sweet_log_thread(', 'old_thread(',1)
tail=r'''
#undef printf
static void *writer_proc(void *arg){if(mode==2)pause_load=true;if(mode==1)old_append(arg,4);else sweet_log_append(arg,mode==5?300:4);return NULL;}
static void reset(void){memset(dma,0,sizeof(dma));memset(disk,0,sizeof(disk));memset(sweet_log_ring,0,sizeof(sweet_log_ring));
 atomic_store(&sweet_log_total,0);atomic_store(&sweet_log_busy,0);atomic_store(&sweet_log_dropped_chunks,0);atomic_store(&sweet_log_dropped_bytes,0);
 ticks=writes=syncs=0;held=paused=resume=audit=reenter=pause_mid=race_armed=false;
 race_calls=0;race_text=NULL;audit_last=audit_acquires=ramoops_calls=ramoops_max=0;
 memset(&su,0,sizeof(su));su.log_partition_configured=su.log_partition_ready=su.dali=true;su.log_first_block=1000;su.dma=(uintptr_t)dma;}
static void spawn_data(const char *p){assert(!pthread_create(&writer,NULL,writer_proc,(void *)p));pthread_mutex_lock(&mu);while(!paused)pthread_cond_wait(&cv,&mu);pthread_mutex_unlock(&mu);}
static void spawn(void){spawn_data("DATA");}
int main(void){
 /* Actual v1 reserves before copying: worker writes zeros then never revisits. */
 reset();mode=1;spawn();assert(atomic_load(&sweet_log_total)==4);
 if(!setjmp(exit_log))old_thread(NULL,0);
 assert(writes==2 && !memcmp(sweet_log_ring,"DATA",4) && !memcmp(disk+UFS_BLOCK,"\0\0\0\0",4));
 struct sweet_log_header *h=(void*)disk;assert(h->total==4);
 /* Interrupted writer: contender drops without waiting; worker skips until release. */
 reset();mode=2;su.hsg4_root=true;spawn();assert(!atomic_load(&sweet_log_total) && atomic_load(&sweet_log_busy));
 sweet_log_append("DROP",4);assert(!atomic_load(&sweet_log_total));
 assert(atomic_load(&sweet_log_dropped_chunks)==1 && atomic_load(&sweet_log_dropped_bytes)==4);
 if(!setjmp(exit_log))sweet_log_thread(NULL,0);
 assert(!atomic_load(&sweet_log_busy) && writes==2 && !memcmp(disk+UFS_BLOCK,"DATA",4));
 h=(void*)disk;assert(h->total==4 && h->dropped_chunks==1 && h->dropped_bytes==4);
 /* After wrap, crash after payload sync and before header: old header points to
  * a new B block. This is a demonstrated limitation, not a passing atomicity claim. */
 reset();mode=3;memset(sweet_log_ring,'A',sizeof(sweet_log_ring));atomic_store(&sweet_log_total,SWEET_LOG_SIZE);
 if(!setjmp(exit_log))sweet_log_thread(NULL,0);
 h=(void*)disk;assert(ticks==65 && h->total==SWEET_LOG_SIZE && !memcmp(disk+UFS_BLOCK,"BBBB",4));
 assert(disk[UFS_BLOCK+4096]=='A');
 /* A producer wraps the previously committed prefix before the snapshot. */
 reset();mode=7;memset(sweet_log_ring,'A',sizeof(sweet_log_ring));atomic_store(&sweet_log_total,1);
 if(!setjmp(exit_log))sweet_log_thread(NULL,0);
 h=(void*)disk;assert(ticks==3 && h->total==65536 && disk[UFS_BLOCK]=='A' && !race_armed);
 /* At wrap, a second concurrent append must not enter the uncommitted suffix;
  * the same RMW also preserves the already committed prefix of this block. */
 reset();mode=8;memset(sweet_log_ring,'A',sizeof(sweet_log_ring));atomic_store(&sweet_log_total,SWEET_LOG_SIZE);
 if(!setjmp(exit_log))sweet_log_thread(NULL,0);
 h=(void*)disk;assert(ticks==67 && h->total==SWEET_LOG_SIZE+2 && !race_armed);
 assert(disk[UFS_BLOCK]=='X' && disk[UFS_BLOCK+1]=='B' && disk[UFS_BLOCK+2]=='A');
 /* Overrun must not publish a header asserting a missing stream prefix. */
 reset();mode=4;atomic_store(&sweet_log_total,SWEET_LOG_SIZE+1);
 sweet_log_thread(NULL,0);assert(!writes && !syncs && !held && !atomic_load(&sweet_log_busy));
 /* A huge direct/panic call is split into bounded ring and ramoops sections. */
 static char huge[(1u<<20)+17];for(unsigned i=0;i<sizeof(huge);i++)huge[i]=(char)(i%127);
 reset();mode=4;audit=true;sweet_log_append(huge,sizeof(huge));audit=false;
 assert(atomic_load(&sweet_log_total)==sizeof(huge) && !memcmp(sweet_log_ring,huge,sizeof(huge)));
 assert(audit_acquires==(sizeof(huge)+255)/256 && ramoops_calls==audit_acquires && ramoops_max<=256);
 assert(atomic_load(&sweet_log_total)-audit_last<=256 && !atomic_load(&sweet_log_dropped_chunks));
 /* Reentrant panic while gate held never blocks; it counts and outer bytes survive. */
 reset();mode=4;reenter=true;sweet_log_append("OUTER",5);
 assert(!reenter && atomic_load(&sweet_log_total)==5 && !memcmp(sweet_log_ring,"OUTER",5));
 assert(atomic_load(&sweet_log_dropped_chunks)==1 && atomic_load(&sweet_log_dropped_bytes)==5);
 /* Pause at byte 64 of a real 256-byte producer chunk, then inject panic. */
 reset();mode=5;pause_mid=true;spawn_data(huge);
 assert(atomic_load(&sweet_log_busy) && !atomic_load(&sweet_log_total));
 assert(!memcmp(sweet_log_ring,huge,64) && sweet_log_ring[64]==0);
 sweet_log_append("PANIC",5);uint64_t pending=0;char one;
 assert(!sweet_log_copy(&pending,&one,1) && pending==0);
 assert(atomic_load(&sweet_log_dropped_chunks)==1 && atomic_load(&sweet_log_dropped_bytes)==5);
 wake_writer();assert(!atomic_load(&sweet_log_busy) && atomic_load(&sweet_log_total)==300);
 assert(!memcmp(sweet_log_ring,huge,300));
 /* A permanently halted holder leaves the gate set: every chunk drops, count in RAM only. */
 reset();mode=4;atomic_store(&sweet_log_busy,1);sweet_log_append(huge,600);
 assert(atomic_load(&sweet_log_busy) && !atomic_load(&sweet_log_total));
 assert(atomic_load(&sweet_log_dropped_chunks)==3 && atomic_load(&sweet_log_dropped_bytes)==600);
 assert(ramoops_calls==3 && ramoops_max<=256);
 /* A drop alone never triggers flash I/O or recursive logging. */
 reset();mode=6;su.hsg4_root=true;atomic_store(&sweet_log_busy,1);sweet_log_append("DROP",4);
 atomic_store(&sweet_log_busy,0);if(!setjmp(exit_log))sweet_log_thread(NULL,0);
 assert(!writes && !syncs && atomic_load(&sweet_log_dropped_chunks)==1);
 /* Counters wrap independently modulo 2^64; total-cursor overflow drops. */
 reset();mode=4;atomic_store(&sweet_log_busy,1);atomic_store(&sweet_log_dropped_chunks,UINT64_MAX);
 atomic_store(&sweet_log_dropped_bytes,UINT64_MAX-1);sweet_log_append("ABC",3);
 assert(!atomic_load(&sweet_log_dropped_chunks) && atomic_load(&sweet_log_dropped_bytes)==1);
 reset();mode=4;atomic_store(&sweet_log_total,UINT64_MAX-2);sweet_log_append("ABCDE",5);
 assert(atomic_load(&sweet_log_total)==UINT64_MAX-2 && !atomic_load(&sweet_log_busy));
 assert(atomic_load(&sweet_log_dropped_chunks)==1 && atomic_load(&sweet_log_dropped_bytes)==5);
 /* The USB consumer's 1400-byte reads see only committed chunks; a busy gate
  * returns zero without advancing and the following 100ms pass can retry. */
 reset();mode=4;su.dali=false;char usb[6000];sweet_log_append(huge,5000);uint64_t pos=0;unsigned got=0;
 atomic_store(&sweet_log_busy,1);assert(sweet_log_copy(&pos,usb,1400)==0 && pos==0);
 atomic_store(&sweet_log_busy,0);uint32_t n;while((n=sweet_log_copy(&pos,usb+got,1400))>0){assert(n<=1400);got+=n;}
 assert(got==5000 && pos==5000 && !memcmp(usb,huge,5000));
 pos=0;assert(sweet_log_copy(&pos,usb,6000)==4096 && pos==4096);
 atomic_store(&sweet_log_total,SWEET_LOG_SIZE+5);pos=0;assert(sweet_log_copy(&pos,usb,1)==1 && pos==6);
 puts("PASS real producer pause/reentry, bounded large calls, drop counts, USB retry, overrun, crash/wrap negative control");return 0;
}
'''
code=prefix+header+new_append+new_copy+old_append+f(new,'sweet_log_retry_next')+new_thread+old_thread+tail
with tempfile.TemporaryDirectory(prefix='sweetlog-interleavings-') as t:
 p=Path(t);(p/'t.c').write_text(code)
 subprocess.run(['clang','-std=gnu11','-O1','-Wall','-Wextra','-Werror','-Wno-unused-function','-fsanitize=address,undefined','-fno-sanitize-recover=all','-pthread',str(p/'t.c'),'-o',str(p/'t')],check=True)
 subprocess.run([str(p/'t')],check=True,timeout=60)
