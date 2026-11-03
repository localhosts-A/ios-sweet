#!/usr/bin/env python3
"""Compile/execute private pre-root bench source, never a device transport."""
import pathlib
import subprocess
import sys
import tempfile

source = pathlib.Path(sys.argv[1]).resolve()
fixture = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#include <errno.h>
#define UFS_BLOCK 4096u
#define BOUNCE_SIZE (1u << 20)
#define DMA_BOUNCE 0u
#define SWEET_LOG_SIZE (4u << 20)
#define SWEET_LOG_BLOCKS (1u + SWEET_LOG_SIZE/UFS_BLOCK)
#define DALI_ROOT_MAX_BLOCKS (16ull*1024*1024*1024/UFS_BLOCK)
#define os_atomic_load(p, order) (*(p))
#define relaxed 0
#define REG_IS 0x20u
struct {
 bool dali, ready, command_poisoned, capacity_valid, hsg1_active;
 bool log_partition_configured, log_partition_ready;
 uint64_t nblocks, first_block, capacity_blocks, log_nblocks, log_first_block;
 uintptr_t dma;
} su;
static uint64_t clock_value, sweet_log_total;
static unsigned reads, fail_read, corrupt_read, steps, fail_step, idle_calls;
static bool idle_bad, poison_failure, floor_bad;
static unsigned operation[8];
static char sweet_log_ring[SWEET_LOG_SIZE];
static uint32_t is_value;
struct sweet_log_header { char magic[8];uint32_t version,size;uint64_t total,seq; };
static uint64_t mach_absolute_time(void) { return clock_value; }
static uint32_t ufs_rd(unsigned reg) { assert(reg==REG_IS);return is_value; }
static int ufs_pwm_idle(void) { ++idle_calls;return idle_bad?EIO:0; }
static int ufs_dali_hs_platform_validate(void) {return floor_bad?EIO:0;}
static int ufs_pwm_failure(int error) {return error;}
static int ufs_read_blocks(uint64_t lba,uint32_t count) {
 ++reads;assert(count==BOUNCE_SIZE/UFS_BLOCK);assert(lba>=su.first_block);
 assert(lba-su.first_block+count<=su.nblocks);clock_value+=100;
 if(reads==fail_read){su.command_poisoned=poison_failure;return ETIMEDOUT;}
 uint8_t *b=(uint8_t *)su.dma;
 for(unsigned i=0;i<BOUNCE_SIZE;i++) b[i]=(uint8_t)((lba*13+i*7+(i>>12))&255);
 if(reads==corrupt_read)b[BOUNCE_SIZE-1]^=1;
 return 0;
}
static int ufs_write_log_blocks(uint64_t lba,uint32_t count) {
 assert(steps<8);operation[steps]=1;++steps;
 assert(lba>=su.log_first_block && lba-su.log_first_block+count<=su.log_nblocks);
 assert(!(lba>=su.first_block && lba-su.first_block<su.nblocks));
 if(steps==fail_step)return EIO;
 if(lba==su.log_first_block) {
  const struct sweet_log_header *h=(const void *)su.dma;
  assert(count==1 && !memcmp(h->magic,"SWEETLOG",8));
  assert(h->version==1 && h->size==SWEET_LOG_SIZE && h->total==sweet_log_total && h->seq==1);
 }else{assert(lba==su.log_first_block+1);assert(!memcmp((void *)su.dma,sweet_log_ring,count*UFS_BLOCK));}
 return 0;
}
static int ufs_sync_cache(void) {assert(steps<8);operation[steps]=2;++steps;return steps==fail_step?EIO:0;}
'''
tests = r'''
static void reset(void) {
 uintptr_t dma=su.dma;memset(&su,0,sizeof(su));su.dma=dma;
 su.dali=true;su.capacity_valid=true;su.hsg1_active=true;su.first_block=4096;
 su.nblocks=DALI_ROOT_MAX_BLOCKS;su.capacity_blocks=su.first_block+su.nblocks+100;
 su.log_first_block=su.first_block+su.nblocks+10;su.log_nblocks=SWEET_LOG_BLOCKS;
 su.log_partition_configured=su.log_partition_ready=true;
 clock_value=1;reads=fail_read=corrupt_read=steps=fail_step=idle_calls=0;
 idle_bad=poison_failure=floor_bad=false;is_value=0;sweet_log_total=12345;
 memset(operation,0,sizeof(operation));memset(sweet_log_ring,0x6a,sizeof(sweet_log_ring));
}
int main(void) {
 su.dma=(uintptr_t)malloc(BOUNCE_SIZE);assert(su.dma);
 uint8_t *p=malloc(DALI_SPEED_BYTES+2);assert(p);p[0]=0x18;p[DALI_SPEED_BYTES+1]=0x81;
 struct dali_speed_sample sample;
 reset();assert(!ufs_dali_speed_read_pass(p+1,DALI_SPEED_BYTES,false,10000,&sample));
 assert(sample.bytes==DALI_SPEED_BYTES && sample.commands==4 && sample.end-sample.begin==400);
 assert(!ufs_dali_speed_read_pass(p+1,DALI_SPEED_BYTES,true,10000,&sample));
 assert(reads==8 && sample.bytes==DALI_SPEED_BYTES);
 assert(p[0]==0x18 && p[DALI_SPEED_BYTES+1]==0x81);
 for(unsigned which=0;which<10;which++) {
  reset();if(which==0)su.dali=false;if(which==1)su.ready=true;if(which==2)su.command_poisoned=true;
  if(which==3)su.capacity_valid=false;if(which==4)su.nblocks=1;
  if(which==5)su.nblocks=DALI_ROOT_MAX_BLOCKS+1;if(which==6)su.first_block=UINT32_MAX;
  if(which==7)su.capacity_blocks=su.first_block+1;
  size_t n=which==8?DALI_SPEED_BYTES-1:DALI_SPEED_BYTES;
  void *buf=which==9?NULL:p+1;
  assert(ufs_dali_speed_read_pass(buf,n,false,10000,&sample)==EINVAL && !reads);
 }
 reset();assert(ufs_dali_speed_read_pass(p+1,DALI_SPEED_BYTES,false,1,&sample)==ETIMEDOUT && !reads);
 reset();assert(ufs_dali_speed_read_pass(p+1,DALI_SPEED_BYTES,false,150,&sample)==ETIMEDOUT);
 assert(reads==2 && sample.commands==2 && sample.bytes==2*BOUNCE_SIZE);
 for(unsigned fail=1;fail<=4;fail++) {
  reset();fail_read=fail;poison_failure=true;
  assert(ufs_dali_speed_read_pass(p+1,DALI_SPEED_BYTES,false,10000,&sample)==ETIMEDOUT);
  assert(reads==fail && sample.commands==fail && sample.bytes==(fail-1)*BOUNCE_SIZE);
  assert(su.command_poisoned);assert(ufs_dali_speed_save_log(true)==EPERM && !steps);
 }
 reset();assert(!ufs_dali_speed_read_pass(p+1,DALI_SPEED_BYTES,false,10000,&sample));
 corrupt_read=6;assert(ufs_dali_speed_read_pass(p+1,DALI_SPEED_BYTES,true,10000,&sample)==EIO);
 assert(reads==6 && sample.commands==2 && sample.bytes==BOUNCE_SIZE);
 reset();assert(!ufs_dali_speed_save_log(true));assert(steps==4);
 assert(operation[0]==1 && operation[1]==2 && operation[2]==1 && operation[3]==2);
 for(unsigned fail=1;fail<=4;fail++) {
  reset();fail_step=fail;assert(ufs_dali_speed_save_log(true)==EIO && steps==fail);
 }
 for(unsigned which=0;which<9;which++) {
  reset();if(which==0)su.dali=false;if(which==1)su.ready=true;if(which==2)su.command_poisoned=true;
  if(which==3)su.log_partition_configured=false;if(which==4)su.log_partition_ready=false;
  if(which==5)su.log_nblocks=SWEET_LOG_BLOCKS-1;if(which==6)idle_bad=true;
  if(which==7)sweet_log_total=0;if(which==8)sweet_log_total=(256u<<10)+1;
  assert(ufs_dali_speed_save_log(true)!=0 && !steps);
 }
 reset();assert(ufs_dali_speed_save_log(false)==EPERM && !steps && !idle_calls);
 reset();is_value=0x400;assert(ufs_dali_speed_save_log(true)==EIO && !steps);
 reset();floor_bad=true;assert(ufs_dali_speed_save_log(true)==EIO && !idle_calls && !steps);
 reset();su.hsg1_active=false;assert(ufs_dali_speed_save_log(true)==EPERM && !idle_calls && !steps);
 free(p);free((void *)su.dma);puts("PASS private actual pre-root read passes, exact byte compare, bounds and separate log commit ordering");
 return 0;
}
'''
with tempfile.TemporaryDirectory(prefix='ufs-speed-host-') as name:
    p = pathlib.Path(name)
    c = p / 'test.c'
    c.write_text(fixture + source.read_text() + tests)
    exe = p / 'test'
    subprocess.run(['clang', '-std=gnu11', '-O1', '-Wall', '-Wextra', '-Werror',
                    '-fsanitize=address,undefined', str(c), '-o', str(exe)], check=True)
    subprocess.run([str(exe)], check=True)
