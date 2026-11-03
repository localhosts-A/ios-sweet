/* Exact XNU adapter, only its SMC assembly entry replaced by a mock.
 * This exercises firmware-init policy/software flow, not secure firmware. */
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>
#include <stdarg.h>
static int checked_printf(const char *,...) __attribute__((format(printf,1,2)));
static int checked_printf(const char *format,...){
 char buffer[1024];va_list ap;va_start(ap,format);int n=vsnprintf(buffer,sizeof(buffer),format,ap);va_end(ap);
 assert(n>=0 && n<256);return fputs(buffer,stdout);
}
#define printf checked_printf
typedef uintptr_t vm_offset_t;
#define DALI_UFS_AO_GATE0 0x104u
#define DALI_UFS_AO_GATE1 0x4u
#define REG_MTK_REF_CLOCK 0x144u
#define NSEC_PER_USEC 1000u
static uint32_t dvfs[0x1000/4],top[0x800/4],pll[0x1000/4],hci[0x3000/4],ao[0x1000/4];
static struct {bool dali,ready,command_poisoned;vm_offset_t regs,ao_regs;}su;
static unsigned maps,smcs,acquires,releases,fail_map,fail_smc,barriers,inits,flag_reads;
static bool allow_init,init_populates,init_clock_change,complete_requests,init_target_busy,never_idle,android_calibration,minus_one_resource;
static uint64_t microseconds,init_fw_status;
static bool ufs_dali_speed_flag(const char *key){assert(!strcmp(key,"dali-dvfsrc-init"));++flag_reads;return allow_init;}
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
static void delay(uint32_t n){microseconds+=n;if(complete_requests && acquires && !releases){dvfs[0x6ac/4]=(dvfs[0x18/4]&0x70u)<<4;if(!never_idle)dvfs[0x5f0/4]&=~(1u<<16);}}
/* ADAPTER */
static void reset(void){
 memset(&dali_hs_platform,0,sizeof(dali_hs_platform));memset(&su,0,sizeof(su));
 memset(dvfs,0,sizeof(dvfs));memset(top,0,sizeof(top));memset(pll,0,sizeof(pll));
 memset(hci,0,sizeof(hci));memset(ao,0,sizeof(ao));
 maps=smcs=acquires=releases=fail_map=fail_smc=barriers=inits=flag_reads=0;microseconds=1;
 allow_init=init_clock_change=init_target_busy=never_idle=android_calibration=minus_one_resource=false;init_populates=complete_requests=true;init_fw_status=0;
 su.dali=true;su.regs=(uintptr_t)hci;su.ao_regs=(uintptr_t)ao;
 dvfs[0]=86u<<20;dvfs[0xfc/4]=5;dvfs[0x6ac/4]=2u<<8;dvfs[0x18/4]=0x52000;
 top[0xd0/4]=6u<<8;pll[0x3c/4]=1u<<6;pll[0x268/4]=0x180000;
 hci[0x144/4]=3;
}
static void unconfigured(void){dvfs[0]=dvfs[0xfc/4]=dvfs[0x6ac/4]=0;dvfs[0x18/4]=0x50000;}
int main(void){
 reset();uint64_t a0=0,a1=0;
 assert(ufs_hs_smc(NULL,1,0,&a0,&a1)==EINVAL && !smcs);
 assert(ufs_hs_smc(NULL,0,1,&a0,&a1)==EINVAL && !smcs);
 assert(ufs_hs_smc(NULL,4,6,&a0,&a1)==EINVAL && !smcs);
 assert(ufs_hs_smc(NULL,0x40,1,&a0,&a1)==EINVAL && !smcs);
 assert(ufs_hs_smc(NULL,0,0,NULL,&a1)==EINVAL && !smcs);
 reset();assert(!ufs_dali_hs_platform_adopt());assert(maps==3 && smcs==8 && acquires==1 && releases==1 && !inits);
 assert(dvfs[0x18/4]==0x52020 && dali_hs_platform.floor.phase==HSF_ACTIVE);
 assert(!ufs_dali_hs_platform_validate() && smcs==8);
 assert(ufs_dali_hs_platform_adopt()==EBUSY && maps==3);
 for(unsigned i=1;i<=3;i++){reset();fail_map=i;assert(ufs_dali_hs_platform_adopt()==ENOMEM && !smcs && !flag_reads && dvfs[0x18/4]==0x52000);}
 for(unsigned i=0;i<4;i++){
  reset();if(i==0)su.dali=false;if(i==1)su.ready=true;if(i==2)su.command_poisoned=true;if(i==3)top[0xd0/4]|=1u<<15;
  assert(ufs_dali_hs_platform_adopt()!=0 && !smcs && !flag_reads && dvfs[0x18/4]==0x52000);
 }
 reset();fail_smc=7;assert(ufs_dali_hs_platform_adopt()!=0 && !acquires && !releases && dvfs[0x18/4]==0x52020);
 reset();assert(!ufs_dali_hs_platform_adopt());top[0xd0/4]^=1;
 assert(ufs_dali_hs_platform_validate()==EIO);unsigned calls=smcs;
 assert(ufs_dali_hs_platform_validate()==EIO && smcs==calls);
 reset();assert(!ufs_dali_hs_platform_adopt());dvfs[0x18/4]&=~HSF_REQUEST_MASK;
 assert(ufs_dali_hs_platform_validate()==EIO && dali_hs_platform.failure==EIO);
 reset();assert(ufs_hs_floor_write(NULL,0x52020)==EINVAL);
 /* Explicit opt-in alone does not reinitialize an existing configuration. */
 reset();allow_init=true;assert(!ufs_dali_hs_platform_adopt() && !inits && smcs==8);
 reset();unconfigured();assert(ufs_dali_hs_platform_adopt()==EIO && !smcs && !inits);
 assert(dali_hs_platform.floor.failure==HSF_CONFIGURATION && !dali_hs_platform.floor.count_read);
 reset();unconfigured();allow_init=true;
 assert(!ufs_dali_hs_platform_adopt() && inits==1 && smcs==9 && dali_hs_platform.init_attempted);
 assert(dali_hs_platform.floor.max_opp==86 && dali_hs_platform.floor.opp_count==6 && dvfs[0x18/4]==0x50020);
 assert(ufs_hs_firmware_prepare(true)==EBUSY && inits==1 && smcs==9);
 /* A returned INIT is not configured/voltage success. No retry/START. */
 reset();unconfigured();allow_init=true;init_populates=false;
 assert(ufs_dali_hs_platform_adopt()==EIO && inits==1 && smcs==1 && !acquires && !releases);
 assert(dali_hs_platform.floor.failure==HSF_CONFIGURATION && dvfs[0x18/4]==0x50000);
 calls=smcs;assert(ufs_dali_hs_platform_adopt()==EIO && smcs==calls);
 reset();unconfigured();allow_init=true;init_fw_status=UINT64_MAX;
 assert(ufs_dali_hs_platform_adopt()==EIO && inits==1 && smcs==1 && !acquires && !releases);
 assert(dali_hs_platform.init_status==UINT64_MAX && dvfs[0x18/4]==0x50000);
 reset();unconfigured();allow_init=true;fail_smc=1;
 assert(ufs_dali_hs_platform_adopt()==EIO && smcs==1 && !acquires && !releases && dali_hs_platform.init_attempted);
 reset();unconfigured();allow_init=true;init_clock_change=true;
 assert(ufs_dali_hs_platform_adopt()==EIO && inits==1 && smcs==1 && !acquires && !releases && dvfs[0x18/4]==0x50000);
 for(unsigned i=0;i<6;i++){
  reset();unconfigured();allow_init=true;
  if(i==0)dvfs[0x18/4]|=1u<<4;if(i==1)dvfs[0x604/4]=1;if(i==2)dvfs[0xc4/4]=2;if(i==3)dvfs[0x5f0/4]=1u<<16;
  if(i==4)dvfs[0x18/4]|=2u<<12;if(i==5)dvfs[0x18/4]|=1u;
  uint32_t old=dvfs[0x18/4];assert(ufs_dali_hs_platform_adopt()==EIO && !inits && !smcs && dvfs[0x18/4]==old);
 }
 reset();unconfigured();allow_init=true;dvfs[0xfc/4]=5;
 assert(ufs_dali_hs_platform_adopt()==EIO && !smcs && !inits && dvfs[0x18/4]==0x50000);
 reset();allow_init=true;dvfs[0xfc/4]=0;
 assert(ufs_dali_hs_platform_adopt()==EIO && !smcs && !inits && dali_hs_platform.floor.failure==HSF_COUNT_ERROR);
 reset();unconfigured();allow_init=true;complete_requests=false;
 assert(ufs_dali_hs_platform_adopt()==EIO && inits==1 && acquires==1 && releases==1);
 assert(dali_hs_platform.floor.failure==HSF_TIMEOUT && dali_hs_platform.failure==EIO);
 reset();unconfigured();allow_init=true;init_target_busy=true;android_calibration=true;
 assert(!ufs_dali_hs_platform_adopt() && inits==1 && smcs==9 && acquires==1 && releases==1);
 assert(dali_hs_platform.floor.request==3 && dali_hs_platform.floor.calibrated_uv==705000 && dvfs[0x18/4]==0x50030);
 assert(dali_hs_platform.floor.health_valid==31 && !(dali_hs_platform.floor.raw_target&(1u<<16)));
 reset();unconfigured();allow_init=true;init_target_busy=true;never_idle=true;
 assert(ufs_dali_hs_platform_adopt()==EIO && inits==1 && acquires==1 && releases==1);
 assert(dali_hs_platform.floor.failure==HSF_TIMEOUT && dali_hs_platform.floor.health_valid==31);
 reset();unconfigured();allow_init=true;init_target_busy=true;android_calibration=minus_one_resource=true;
 assert(!ufs_dali_hs_platform_adopt() && inits==1 && acquires==1 && releases==1 && dvfs[0x18/4]==0x50030);
 assert(dali_hs_platform.floor.phase==HSF_ACTIVE && dali_hs_platform.floor.resource_request_result==UINT64_MAX && dali_hs_platform.floor.resource_release_result==UINT64_MAX);
 assert(dali_hs_platform.floor.resource_request_valid && dali_hs_platform.floor.resource_release_valid && !dali_hs_platform.floor.resource_requested);
 reset();unconfigured();allow_init=true;init_target_busy=true;minus_one_resource=never_idle=true;
 assert(ufs_dali_hs_platform_adopt()==EIO && acquires==1 && releases==1 && dali_hs_platform.floor.failure==HSF_TIMEOUT);
 puts("PASS exact adapter: resource raw is not grant, hardware completion required, transport errors stop, no START, all printf messages below256B; firmware mocked");
 return 0;
}
