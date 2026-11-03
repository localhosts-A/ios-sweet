/* Executed with the exact XNU adapter, except its SMC assembly entry is
 * replaced by a mock. This checks MMIO mapping/clock/vote wiring, not firmware. */
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>
typedef uintptr_t vm_offset_t;
#define DALI_UFS_AO_GATE0 0x104u
#define DALI_UFS_AO_GATE1 0x4u
#define REG_MTK_REF_CLOCK 0x144u
#define NSEC_PER_USEC 1000u
static uint32_t dvfs[0x1000/4],top[0x800/4],pll[0x1000/4],hci[0x3000/4],ao[0x1000/4];
static struct {bool dali,ready,command_poisoned;vm_offset_t regs,ao_regs;}su;
static unsigned maps,smcs,acquires,releases,fail_map,fail_smc,barriers;
static uint64_t microseconds;
static vm_offset_t ml_io_map(uint64_t pa,unsigned size){
 ++maps;if(maps==fail_map)return 0;
 if(pa==0x1c013000ull){assert(size==0x1000);return (uintptr_t)dvfs;}
 if(pa==0x10000000ull){assert(size==0x800);return (uintptr_t)top;}
 assert(pa==0x10000800ull && size==0x1000);return (uintptr_t)pll;
}
#define __builtin_arm_dsb(x) do{assert((x)==15);++barriers;}while(0)
static uint32_t ufs_rd(uint32_t off){assert(off==REG_MTK_REF_CLOCK);return hci[off/4];}
static uint32_t ufs_ao_rd(uint32_t off){assert(off==DALI_UFS_AO_GATE0 || off==DALI_UFS_AO_GATE1);return ao[off/4];}
static uint64_t mach_absolute_time(void){return microseconds*1000;}
static void absolutetime_to_nanoseconds(uint64_t t,uint64_t *out){*out=t;}
static void delay(uint32_t n){microseconds+=n;}
/* ADAPTER */
static void reset(void){
 memset(&dali_hs_platform,0,sizeof(dali_hs_platform));memset(&su,0,sizeof(su));
 memset(dvfs,0,sizeof(dvfs));memset(top,0,sizeof(top));memset(pll,0,sizeof(pll));
 memset(hci,0,sizeof(hci));memset(ao,0,sizeof(ao));
 maps=smcs=acquires=releases=fail_map=fail_smc=barriers=0;microseconds=1;
 su.dali=true;su.regs=(uintptr_t)hci;su.ao_regs=(uintptr_t)ao;
 dvfs[0xfc/4]=5;dvfs[0x6ac/4]=2u<<8;dvfs[0x18/4]=0x52000;
 top[0xd0/4]=6u<<8;pll[0x3c/4]=1u<<6;pll[0x268/4]=0x180000;
 hci[0x144/4]=3;
}
int main(void){
 reset();assert(!ufs_dali_hs_platform_adopt());assert(maps==3 && smcs==8 && acquires==1 && releases==1);
 assert(dvfs[0x18/4]==0x52020 && dali_hs_platform.floor.phase==HSF_ACTIVE);
 assert(!ufs_dali_hs_platform_validate() && smcs==8);
 assert(ufs_dali_hs_platform_adopt()==EBUSY && maps==3);
 for(unsigned i=1;i<=3;i++){reset();fail_map=i;assert(ufs_dali_hs_platform_adopt()==ENOMEM && !smcs && dvfs[0x18/4]==0x52000);}
 for(unsigned i=0;i<4;i++){
  reset();if(i==0)su.dali=false;if(i==1)su.ready=true;if(i==2)su.command_poisoned=true;if(i==3)top[0xd0/4]|=1u<<15;
  assert(ufs_dali_hs_platform_adopt()!=0 && !smcs && dvfs[0x18/4]==0x52000);
 }
 reset();fail_smc=7;assert(ufs_dali_hs_platform_adopt()!=0 && !acquires && !releases && dvfs[0x18/4]==0x52000);
 reset();assert(!ufs_dali_hs_platform_adopt());top[0xd0/4]^=1;
 assert(ufs_dali_hs_platform_validate()==EIO);unsigned calls=smcs;
 assert(ufs_dali_hs_platform_validate()==EIO && smcs==calls);
 reset();assert(!ufs_dali_hs_platform_adopt());dvfs[0x18/4]&=~HSF_REQUEST_MASK;
 assert(ufs_dali_hs_platform_validate()==EIO && dali_hs_platform.failure==EIO);
 reset();assert(ufs_hs_floor_write(NULL,0x52020)==EINVAL); /* Unmapped */
 puts("PASS exact XNU platform adapter with mocked SMC: maps, clock, AP request, completion and sticky faults");
 return 0;}
