#!/usr/bin/env python3
"""Execute final0180 PWM policy/UIC/PMC/root-entry functions, never a hardware model.
The MMIO fixture is a protocol simulation, not proof of platform clocks/PHY,
complete kernel initialization, hardware return, line speed or desktop activity.
Production ceiling2 still needs live clock admission; fixture caps are not hardware evidence.
"""
from pathlib import Path
import os,shutil,subprocess,tempfile,unittest
import test_dali_gui_load_progress as source
ROOT=Path(__file__).resolve().parents[1]
UFS=source.UFS
OLD=source.fixture.fragment('bsd/dev/sweet_ufs.c',source.CURRENT_PATCH)
POLICY=source.SOURCE['bsd/dev/SweetUFSPWMPolicy.h']
function=source.function


POLICY_PREFIX=r'''
#include <assert.h>
#include <stdio.h>
#include "SweetUFSPWMPolicy.h"
static struct sweet_pwm_snapshot initial(void) {
 return (struct sweet_pwm_snapshot){.mode=0x55,.rx_gear=1,.tx_gear=1,
  .rx_active=1,.tx_active=1,.rx_connected=1,.tx_connected=1,.rx_term=0,.tx_term=0};
}
'''

FUNCS=('ufs_mtk_log_uic_error','ufs_mtk_ack_uic','ufs_mtk_link_reset_event','ufs_mtk_uic',
 'ufs_pwm_clock_cap','ufs_pwm_latch','ufs_pwm_failure','ufs_pwm_reentry_error','ufs_pwm_root_enter','ufs_pwm_root_leave',
 'ufs_pwm_idle','ufs_pwm_get','ufs_pwm_snapshot','ufs_pwm_set','ufs_pwm_pmc','ufs_dali_prepare_pwm','sweet_ufs_rootdev')

HW_PREFIX=r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/types.h>
#include "SweetUFSPWMPolicy.h"
#include "SweetUFSPWMClock.h"
#define EIO 5
#define EBUSY 16
#define EINVAL 22
#define ENXIO 6
#define ETIMEDOUT 60
#define NSEC_PER_MSEC 1000000ull
#define NSEC_PER_USEC 1000ull
#define NSEC_PER_SEC 1000000000ull
#define DMA_UTRL 0
#define os_atomic_load(p,o) __atomic_load_n((p),__ATOMIC_ACQUIRE)
#define os_atomic_store(p,v,o) __atomic_store_n((p),(v),__ATOMIC_RELEASE)
static bool cas32(uint32_t *p,uint32_t old,uint32_t value){return __atomic_compare_exchange_n(p,&old,value,false,__ATOMIC_ACQ_REL,__ATOMIC_ACQUIRE);}
#define os_atomic_cmpxchg(p,old,v,o) cas32((p),(old),(v))
static struct {bool dali,ready,command_poisoned;uint64_t dma_pa;const char *failure_stage;dev_t bdev;} su;
static uint32_t sweet_ufs_pwm_terminal,sweet_ufs_pwm_root_control;
static uint32_t regs[4096],attrs[65536],peer_max;
static uint32_t trace_attr[64],trace_value[64],trace_op[64];
typedef uintptr_t vm_offset_t;
static uint32_t clock_top[0x800/4],clock_pll[0x1000/4];
static unsigned clock_maps;
static vm_offset_t ml_io_map(uint64_t address,unsigned size){++clock_maps;
 if(address==0x10000000ull){assert(size==0x800);return (uintptr_t)clock_top;}
 assert(address==0x10000800ull && size==0x1000);return (uintptr_t)clock_pll;
}
#define __builtin_arm_dsb(x) do {assert((x)==0xf);} while(0)
static uint64_t now,command_at;
static int uccs_after,upms_after;
static uint32_t upmcrs,command_result,bad_reg;
static unsigned reads,writes,commands,sets,pmcs,fail_command,ramoops_calls,init_calls,capacity_calls;
static uint32_t requested;
static bool pmc_active,pmc_completed,ack_stuck,corrupt_readback,freeze_clock,pmc_ack_stuck,reenter_on_init;
int sweet_ufs_rootdev(dev_t *);
static unsigned per_command_us;
static unsigned char dma[4096],saved_dma[4096];
static uint32_t ufs_pwm_clock_cap(void);
static int ufs_dali_prepare_pwm(uint32_t,bool,uint32_t);
static int ufs_pwm_reentry_error(void);
static int ufs_pwm_root_enter(bool);
static void ufs_pwm_root_leave(bool,int);
static uint64_t mach_absolute_time(void){return now;}
static void nanoseconds_to_absolutetime(uint64_t ns,uint64_t *out){*out=ns/1000;}
static void delay(unsigned us){if(!freeze_clock)now+=us;}
static uint32_t ufs_rd(unsigned off){
 ++reads;assert(off/4<4096 && !(off&3));if(off==bad_reg)return UINT32_MAX;
 if(off==0x20 && pmc_active){
  if(uccs_after>=0 && now-command_at>=(unsigned)uccs_after){regs[0x20/4]|=0x400;regs[0x98/4]=command_result;}
  if(upms_after>=0 && now-command_at>=(unsigned)upms_after){regs[0x20/4]|=0x10;regs[0x30/4]=(regs[0x30/4]&~0x700u)|(upmcrs<<8);}
 }
 return regs[off/4];
}
static void ufs_wr(unsigned off,uint32_t v){
 ++writes;assert(off/4<4096 && !(off&3));
 if(off==0x20){if(!ack_stuck)regs[off/4]&=~v;
  if(v==0x410 && !ack_stuck){attrs[0x1571]=regs[0x9c/4];pmc_active=false;pmc_completed=true;}return;}
 regs[off/4]=v;
 if(off!=0x90)return;
 ++commands;command_at=now;uint32_t attr=regs[0x94/4]>>16;
 assert(commands<=64);trace_attr[commands-1]=attr;trace_value[commands-1]=regs[0x9c/4];trace_op[commands-1]=v;
 if(v==2 && attr==0x1571){++pmcs;pmc_active=true;if(pmc_ack_stuck)ack_stuck=true;return;}
 assert(!pmc_active);now+=per_command_us;regs[0x20/4]|=0x400;
 regs[0x98/4]=commands==fail_command?1:0;
 if(regs[0x98/4])return;
 if(v==1 || v==3){
  uint32_t val=v==3?peer_max:attrs[attr];
  if(corrupt_readback && pmc_completed && attr==0x1583)++val;
  regs[0x9c/4]=val;
 }else{assert(v==2);++sets;attrs[attr]=regs[0x9c/4];}
}
static int ufs_wait_reg(unsigned off,unsigned mask,unsigned want,unsigned limit){
 for(unsigned n=0;n<=limit;n+=10){if((ufs_rd(off)&mask)==want)return 0;delay(10);}return ETIMEDOUT;
}
static bool PE_parse_boot_argn(const char *name,void *value,unsigned size){assert(!strcmp(name,"ufspwm") && size==4);memcpy(value,&requested,4);return requested!=0;}
static unsigned early_returns;
static void dali_fastboot_return_root_failure(void){assert(!su.ready && sweet_ufs_pwm_terminal);++early_returns;}
static int sweet_ramoops_start(void){++ramoops_calls;return 0;}
static int sweet_ufs_init(void){++init_calls;
 if(reenter_on_init){dev_t inner=0;unsigned before=ramoops_calls;
  assert(sweet_ufs_rootdev(&inner)==EBUSY && inner==0 && ramoops_calls==before && init_calls==1);}
 int error=0;
 /* ACTUAL_PWM_INIT_CALL */
 if(error)return error;++capacity_calls;su.ready=true;su.bdev=9;return 0;}
'''

HW_TAIL=r'''
static void reset(void){
 memset(&su,0,sizeof(su));su.dali=true;su.dma_pa=0x123400000ull;
 memset(regs,0,sizeof(regs));memset(attrs,0,sizeof(attrs));
 regs[REG_HCE/4]=1;regs[REG_HCS/4]=0xf;regs[REG_UTRLRSR/4]=1;
 regs[REG_UTRLBA/4]=(uint32_t)su.dma_pa;regs[REG_UTRLBAU/4]=(uint32_t)(su.dma_pa>>32);
 attrs[0x1571]=0x55;attrs[0x1583]=attrs[0x1568]=1;
 attrs[0x1580]=attrs[0x1560]=attrs[0x1581]=attrs[0x1561]=1;
 attrs[0x1586]=peer_max=4;upmcrs=1;uccs_after=upms_after=10;command_result=0;
 bad_reg=UINT32_MAX;now=command_at=0;reads=writes=commands=sets=pmcs=fail_command=0;
 ramoops_calls=init_calls=capacity_calls=requested=early_returns=0;
 pmc_active=pmc_completed=ack_stuck=corrupt_readback=freeze_clock=pmc_ack_stuck=reenter_on_init=false;
 per_command_us=0;clock_maps=0;memset(clock_top,0,sizeof(clock_top));memset(clock_pll,0,sizeof(clock_pll));
 sweet_ufs_pwm_terminal=sweet_ufs_pwm_root_control=0;
 memset(dma,0xa7,sizeof(dma));memcpy(saved_dma,dma,sizeof(dma));
}
static void check_dma(void){
 assert(!memcmp(dma,saved_dma,sizeof(dma)));
 if(!reads)assert(!writes); /* No write-only path bypasses the ownership reads. */
 assert(init_calls<=ramoops_calls); /* Every actual root init followed the entry hook. */
 assert(early_returns<=init_calls);
 assert(clock_maps<=2*init_calls); /* No clock mapping outside actual gated initializer. */
}
'''

def fixture():
 constants=UFS[UFS.index('#define REG_CAP'):UFS.index('struct utrd {')]
 init=function(UFS,'sweet_ufs_init')
 begin=init.index('\tif (!error) {\n        uint32_t pwm_requested = 0;')
 end=init.index('\tif (!error) {\n\t\tsu.failure_stage = "read-capacity";',begin)
 call=init[begin:end]
 assert call.count('ufs_pwm_clock_cap()')==1 and call.count('ufs_dali_prepare_pwm(')==1
 return HW_PREFIX.replace('/* ACTUAL_PWM_INIT_CALL */',call)+constants+''.join(function(UFS,n) for n in FUNCS)+HW_TAIL


class PWMPolicyTest(unittest.TestCase):
 def run_c(self,body):
  cc=shutil.which('clang') or shutil.which('cc');self.assertIsNotNone(cc)
  with tempfile.TemporaryDirectory(prefix='pwm-policy-') as td:
   p=Path(td);(p/'SweetUFSPWMPolicy.h').write_text(POLICY)
   (p/'test.c').write_text(POLICY_PREFIX+'\nint main(void) { (void)&initial;\n'+body+'\nputs("PASS");return 0; }\n')
   subprocess.run([cc,'-std=gnu11','-O2','-Wall','-Wextra','-Werror',str(p/'test.c'),'-o',str(p/'test')],check=True)
   result=subprocess.run([str(p/'test')],capture_output=True,text=True,timeout=10)
   self.assertEqual(result.returncode,0,result.stdout+result.stderr);self.assertEqual(result.stdout.strip(),'PASS')
 def test_admission_and_production_cap_is_not_overrideable(self):
  self.run_c(r'''
assert(SWEET_PWM_PLATFORM_CAP==2);
for(unsigned request=0;request<4;++request)for(unsigned dali=0;dali<2;++dali)
for(unsigned cold=0;cold<2;++cold) {
 assert(sweet_pwm_admitted(request,dali,cold,SWEET_PWM_PLATFORM_CAP)==(request==1 && dali && cold));
 for(unsigned cap=0;cap<9;++cap)
  assert(sweet_pwm_admitted(request,dali,cold,cap)==(request==1 && dali && cold && cap>1 && cap<=7));
}
''')
 def test_exact_minimum_and_no_downgrade_exhaustive(self):
  self.run_c(r'''
for(unsigned start=0;start<2;++start)for(unsigned local=1;local<=7;++local)for(unsigned peer=1;peer<=7;++peer)
for(unsigned cap=1;cap<=7;++cap)for(unsigned rx=1;rx<=7;++rx)for(unsigned tx=1;tx<=7;++tx){
 struct sweet_pwm_snapshot s=initial();s.mode=start?0x22:0x55;s.rx_gear=rx;s.tx_gear=tx;unsigned target=99;
 unsigned q=sweet_pwm_choose(&s,local,peer,cap,&target);
 unsigned expected=local<peer?local:peer;if(cap<expected)expected=cap;
 if(rx>local || tx>peer){assert(q==SWEET_PWM_INVALID && target==0);continue;}
 if(expected<rx || expected<tx || (expected==rx && expected==tx))assert(q==SWEET_PWM_SKIP && target==0);
 else assert(q==SWEET_PWM_APPLY && target==expected && target>=rx && target>=tx);
}
''')
 def test_invalid_capability_is_fatal_not_clamped(self):
  self.run_c(r'''
struct sweet_pwm_snapshot s=initial();unsigned target;
for(unsigned bad=0;bad<=8;bad+=8){
 assert(sweet_pwm_choose(&s,bad,7,7,&target)==SWEET_PWM_INVALID && !target);
 assert(sweet_pwm_choose(&s,7,bad,7,&target)==SWEET_PWM_INVALID && !target);
 assert(sweet_pwm_choose(&s,7,7,bad,&target)==SWEET_PWM_INVALID && !target);
}
assert(sweet_pwm_choose(0,7,7,7,&target)==SWEET_PWM_INVALID);
assert(sweet_pwm_choose(&s,7,7,7,0)==SWEET_PWM_INVALID);
''')
 def test_healthy_nonapplicable_vs_invalid_snapshot(self):
  self.run_c(r'''
unsigned t=99;struct sweet_pwm_snapshot s=initial();
s.mode=0x22;assert(sweet_pwm_choose(&s,7,7,7,&t)==SWEET_PWM_APPLY && t==7);
s.mode=0x25;assert(sweet_pwm_choose(&s,7,7,7,&t)==SWEET_PWM_SKIP && !t);
s.mode=0x11;s.rx_term=s.tx_term=1;
assert(sweet_pwm_choose(&s,7,7,7,&t)==SWEET_PWM_SKIP && !t);
s.mode=0x44;assert(sweet_pwm_choose(&s,7,7,7,&t)==SWEET_PWM_SKIP);
s=initial();s.rx_active=s.tx_active=s.rx_connected=s.tx_connected=2;
assert(sweet_pwm_choose(&s,7,7,7,&t)==SWEET_PWM_SKIP);
for(unsigned which=0;which<9;++which){
 s=initial();
 switch(which){case 0:s.mode=0x77;break;case 1:s.mode=0x155;break;
 case 2:s.rx_gear=0;break;case 3:s.tx_gear=8;break;
 case 4:s.rx_connected=0;break;case 5:s.tx_connected=3;break;
 case 6:s.rx_active=2;break;case 7:s.tx_active=0;break;case 8:s.rx_term=1;break;}
 assert(sweet_pwm_choose(&s,7,7,7,&t)==SWEET_PWM_INVALID && !t);
}
s=initial();s.mode=0x22;s.rx_gear=2;
assert(sweet_pwm_choose(&s,1,7,7,&t)==SWEET_PWM_INVALID);
''')
 def test_readback_preserves_every_field(self):
  self.run_c(r'''
struct sweet_pwm_snapshot b=initial(),a=b;a.mode=0x22;a.rx_gear=a.tx_gear=3;
assert(sweet_pwm_readback_matches(&b,&a,3));
for(unsigned which=0;which<9;++which){struct sweet_pwm_snapshot bad=a;
 switch(which){case 0:bad.mode=0x55;break;case 1:bad.rx_gear=2;break;case 2:bad.tx_gear=2;break;
 case 3:bad.rx_active=2;break;case 4:bad.tx_active=2;break;
 case 5:bad.rx_connected=2;break;case 6:bad.tx_connected=2;break;
 case 7:bad.rx_term=1;break;case 8:bad.tx_term=1;break;}
 assert(!sweet_pwm_readback_matches(&b,&bad,3));}
assert(!sweet_pwm_readback_matches(&b,&a,0));
assert(!sweet_pwm_readback_matches(&b,&a,8));
''')
 def test_two_completions_order_and_unused_arguments(self):
  self.run_c(r'''
const unsigned u=SWEET_PWM_UCCS,p=SWEET_PWM_UPMS,h=0x10f;
for(unsigned order=0;order<3;++order){struct sweet_pwm_completion c={0};
 assert(sweet_pwm_complete_step(&c,0,UINT32_MAX,0xf)==SWEET_PWM_STEP_WAIT);
 if(order==0){assert(sweet_pwm_complete_step(&c,u,0,0xf)==SWEET_PWM_STEP_WAIT);
  assert(c.uccs && !c.upms);assert(sweet_pwm_complete_step(&c,p,UINT32_MAX,h)==SWEET_PWM_STEP_DONE);}
 if(order==1){assert(sweet_pwm_complete_step(&c,p,UINT32_MAX,h)==SWEET_PWM_STEP_WAIT);
  assert(!c.uccs && c.upms);assert(sweet_pwm_complete_step(&c,u,0,0xf)==SWEET_PWM_STEP_DONE);}
 if(order==2)assert(sweet_pwm_complete_step(&c,u|p,0,h)==SWEET_PWM_STEP_DONE);
 assert(c.uccs && c.upms && !c.failed);
}
''')
 def test_upms_result_change_before_uccs_rejects_stale_success(self):
  self.run_c(r'''
struct sweet_pwm_completion c={0};
assert(sweet_pwm_complete_step(&c,0x10,UINT32_MAX,0x10f)==SWEET_PWM_STEP_WAIT);
assert(c.upms && !c.uccs);
assert(sweet_pwm_complete_step(&c,0x410,0,0x20f)==SWEET_PWM_STEP_ERROR && c.failed);
assert(sweet_pwm_complete_step(&c,0x410,0,0x10f)==SWEET_PWM_STEP_ERROR);
''')
 def test_all_upmcrs_values_error_wins_and_sticky_failure(self):
  self.run_c(r'''
for(unsigned power=0;power<8;++power){struct sweet_pwm_completion c={0};
 unsigned q=sweet_pwm_complete_step(&c,SWEET_PWM_UCCS|SWEET_PWM_UPMS,0,0xf|(power<<8));
 assert(q==(power==SWEET_PWM_PWR_LOCAL?SWEET_PWM_STEP_DONE:SWEET_PWM_STEP_ERROR));
 if(power!=SWEET_PWM_PWR_LOCAL){assert(c.failed);
  assert(sweet_pwm_complete_step(&c,0x410,0,0x10f)==SWEET_PWM_STEP_ERROR);}
}
for(unsigned bit=0;bit<32;++bit){unsigned mask=1u<<bit;if(mask==0x400 || mask==0x10)continue;
 struct sweet_pwm_completion c={0};assert(sweet_pwm_complete_step(&c,0x410|mask,0,0x10f)==SWEET_PWM_STEP_ERROR && c.failed);}
struct sweet_pwm_completion c={0};
assert(sweet_pwm_complete_step(&c,0x410,1,0x10f)==SWEET_PWM_STEP_ERROR && c.failed);
c=(struct sweet_pwm_completion){0};assert(sweet_pwm_complete_step(&c,0x410,UINT32_MAX,0x10f)==SWEET_PWM_STEP_ERROR);
c=(struct sweet_pwm_completion){0};assert(sweet_pwm_complete_step(&c,UINT32_MAX,0,0x10f)==SWEET_PWM_STEP_ERROR);
c=(struct sweet_pwm_completion){0};assert(sweet_pwm_complete_step(&c,0,0,UINT32_MAX)==SWEET_PWM_STEP_ERROR);
for(unsigned h=0;h<0x40;++h){c=(struct sweet_pwm_completion){0};
 unsigned q=sweet_pwm_complete_step(&c,0,UINT32_MAX,h);
 assert(q==((h&0x31)==1?SWEET_PWM_STEP_WAIT:SWEET_PWM_STEP_ERROR));}
''')

class PWMHardwareTest(unittest.TestCase):
 def run_c(self,body,synthetic_cap=None,extra=''):
  cc=shutil.which('clang') or shutil.which('cc');self.assertIsNotNone(cc)
  with tempfile.TemporaryDirectory(prefix='pwm-mmio-') as td:
   p=Path(td);header=POLICY
   if synthetic_cap is not None:
    self.assertEqual(synthetic_cap,3)
    self.assertEqual(header.count('#define SWEET_PWM_PLATFORM_CAP 2u'),1)
    header=header.replace('#define SWEET_PWM_PLATFORM_CAP 2u','#define SWEET_PWM_PLATFORM_CAP 3u')
   (p/'SweetUFSPWMPolicy.h').write_text(header)
   (p/'SweetUFSPWMClock.h').write_text(source.SOURCE['bsd/dev/SweetUFSPWMClock.h'])
   code=fixture()+extra+'\nint main(void){(void)&ufs_pwm_set;(void)&ufs_pwm_root_leave;\n'+body+'\ncheck_dma();return 0;}\n'
   (p/'test.c').write_text(code)
   subprocess.run([cc,'-std=gnu11','-O2','-Wall','-Wextra','-Werror','-pthread',str(p/'test.c'),'-o',str(p/'test')],check=True)
   result=subprocess.run([str(p/'test')],capture_output=True,text=True,timeout=20)
   self.assertEqual(result.returncode,0,result.stdout+result.stderr)
 def test_native_git_apply_creates_exact_six_files(self):
  git=shutil.which('git');self.assertIsNotNone(git)
  paths=('bsd/dev/sweet_ufs.c','bsd/dev/SweetUFSPWMPolicy.h','bsd/dev/SweetUFSPWMClock.h',
   'pexpert/arm/DaliFastbootReturnPolicy.h','pexpert/arm/pe_dali_watchdog.c','osfmk/arm64/dali_watchdog.h')
  patch=source.PWM_PATCH.read_text();env=dict(os.environ,LC_ALL='C')
  with tempfile.TemporaryDirectory(prefix='pwm-git-apply-') as td:
   tree=Path(td)
   # A real Git root prevents a parent worktree prefix silently skipping every file.
   subprocess.run([git,'init','--quiet',str(tree)],check=True,env=env)
   for rel in paths:
    before=source.old_fragment(rel,source.PWM_PATCH)
    if before:
     target=tree/rel;target.parent.mkdir(parents=True,exist_ok=True);target.write_text(before)
   bad=patch.replace('new file mode 100644\n','')
   self.assertNotEqual(bad,patch)
   negative=subprocess.run([git,'-C',td,'apply','--check','--verbose','-'],input=bad,
    capture_output=True,text=True,env=env)
   self.assertNotEqual(negative.returncode,0,negative.stdout+negative.stderr)
   self.assertIn('dev/null',negative.stderr)
   for flag in ('--check',''):
    command=[git,'-C',td,'apply','--verbose']+([flag] if flag else [])+['-']
    result=subprocess.run(command,input=patch,capture_output=True,text=True,env=env)
    self.assertEqual(result.returncode,0,result.stdout+result.stderr)
    self.assertNotRegex(result.stdout+result.stderr,r'(?i)skip|offset|fuzz|failed|error:')
   for rel in paths:self.assertEqual((tree/rel).read_text(),source.SOURCE[rel],rel)
   self.assertTrue((tree/'bsd/dev/SweetUFSPWMPolicy.h').is_file())
   self.assertTrue((tree/'bsd/dev/SweetUFSPWMClock.h').is_file())
 def test_ordinary_io_and_uic_functions_unchanged(self):
  for name in ('ufs_mtk_log_uic_error','ufs_mtk_ack_uic','ufs_mtk_link_reset_event','ufs_mtk_uic',
   'ufs_scsi_on_lun_timeout','ufs_scsi','ufs_query_current_power_mode','ufs_query_fdeviceinit_flag',
   'ufs_read_blocks','ufs_write_blocks','ufs_write_log_blocks','ufs_sync_cache','sweet_ufs_strategy','sweet_ufs_ioctl','sweet_log_thread'):
   self.assertEqual(function(UFS,name),function(OLD,name),name)
 def test_disabled_invalid_profile_or_effective_cap1_zero_uic(self):
  self.run_c(r'''
for(unsigned req=0;req<4;++req)for(unsigned dali=0;dali<2;++dali)for(unsigned cold=0;cold<2;++cold){
 reset();su.dali=dali;assert(ufs_dali_prepare_pwm(req,cold,1)==0);
 assert(!reads && !writes && !commands);}
for(unsigned which=0;which<3;++which){reset();
 assert(ufs_dali_prepare_pwm(which==0?0:1,which!=1,which==2?0:3)==0);
 assert(!reads && !writes && !commands);}
''')
 def test_actual_get_snapshot_and_synthetic_upgrade(self):
  self.run_c(r'''
reset();assert(ufs_dali_prepare_pwm(1,true,3)==0);
assert(pmcs==1 && sets==16 && attrs[0x1583]==3 && attrs[0x1568]==3);
assert(pmc_completed && !pmc_active && regs[REG_IS/4]==0 && !sweet_ufs_pwm_terminal);
assert(commands==37 && attrs[0x1571]==0x22 && attrs[0x1560]==1 && attrs[0x1580]==1);
''')
 def test_fixed_vendor_attributes_exact_order_slow_mode_and_no_hs(self):
  self.run_c(r'''
const unsigned attr[]={0x15d4,0x1583,0x1580,0x1584,0x1568,0x1560,0x1569,
 0x15b0,0x15b1,0x15b2,0x15b3,0x15b4,0x15b5,0xd041,0xd042,0xd043};
const unsigned value[]={3,2,1,0,2,1,0,8191,65535,32767,8191,65535,32767,8191,65535,32767};
for(unsigned start=0;start<2;++start){reset();attrs[0x1571]=start?0x22:0x55;
 assert(ufs_dali_prepare_pwm(1,true,2)==0 && commands==37 && sets==16 && pmcs==1);
 for(unsigned i=0;i<16;++i){assert(trace_op[11+i]==2 && trace_attr[11+i]==attr[i]);
  assert(trace_value[11+i]==value[i] && attrs[attr[i]]==value[i]);}
 assert(trace_op[27]==2 && trace_attr[27]==0x1571 && trace_value[27]==0x22);
 assert(attrs[0x1571]==0x22 && attrs[0x1583]==2 && attrs[0x1568]==2);
 for(unsigned i=0;i<commands;++i){assert(trace_op[i]!=4 && trace_attr[i]!=0x156a);
  if(trace_op[i]==3)assert(i==10 && trace_attr[i]==0x1586);}
}
''')
 def test_completion_order_and_fixed_shared_deadline(self):
  self.run_c(r'''
for(unsigned order=0;order<3;++order){reset();uccs_after=order==0?10:30;upms_after=order==1?10:30;
 assert(ufs_dali_prepare_pwm(1,true,3)==0 && pmcs==1);}
for(unsigned missing=0;missing<2;++missing){reset();if(missing)uccs_after=-1;else upms_after=-1;
 assert(ufs_dali_prepare_pwm(1,true,3)==ETIMEDOUT && pmcs==1 && commands==28);
 assert(now==500000 && ufs_pwm_reentry_error()==ETIMEDOUT);
 unsigned r=reads,w=writes;dev_t dev=0;assert(sweet_ufs_rootdev(&dev)==ETIMEDOUT);
 assert(reads==r && writes==w && !ramoops_calls && !init_calls && !capacity_calls && dev==0);}
''')
 def test_wrong_power_status_and_command_error_are_fatal(self):
  self.run_c(r'''
for(unsigned mode=0;mode<8;++mode){if(mode==1)continue;reset();upmcrs=mode;
 assert(ufs_dali_prepare_pwm(1,true,3)==EIO && ufs_pwm_reentry_error()==EIO);
 assert(pmcs==1 && commands==28 && !su.ready);}
reset();command_result=2;assert(ufs_dali_prepare_pwm(1,true,3)==EIO && commands==28);
''')
 def test_stale_completion_ownership_ahit_and_poison(self):
  self.run_c(r'''
unsigned offsets[]={REG_IE,REG_AHIT,REG_UTRLDBR,REG_UTMRLDBR,REG_UTMRLRSR};
for(unsigned k=0;k<sizeof(offsets)/sizeof(*offsets);++k){reset();regs[offsets[k]/4]=1;
 assert(ufs_dali_prepare_pwm(1,true,3)==EIO && !commands && !writes);}
reset();regs[REG_IS/4]=0x10;assert(ufs_dali_prepare_pwm(1,true,3)==EIO && !commands && !writes);
reset();regs[REG_UTRLBA/4]^=4;assert(ufs_dali_prepare_pwm(1,true,3)==EIO && !commands);
reset();regs[REG_UTRLRSR/4]=0;assert(ufs_dali_prepare_pwm(1,true,3)==EIO && !commands);
reset();su.command_poisoned=true;assert(ufs_dali_prepare_pwm(1,true,3)==EIO && !commands);
reset();su.ready=true;assert(ufs_dali_prepare_pwm(1,true,3)==EIO && !commands);
''')
 def test_capability_and_each_ordinary_command_failure_stop(self):
  self.run_c(r'''
for(unsigned bad=0;bad<=8;bad+=8){reset();attrs[0x1586]=bad;
 assert(ufs_dali_prepare_pwm(1,true,3)==EIO && !sets && !pmcs);
 reset();peer_max=bad;assert(ufs_dali_prepare_pwm(1,true,3)==EIO && !sets && !pmcs);}
for(unsigned command=1;command<=37;++command){if(command==28)continue;
 reset();fail_command=command;assert(ufs_dali_prepare_pwm(1,true,3)==EIO);
 assert(commands==command && sweet_ufs_pwm_terminal && !su.ready);
 unsigned r=reads,w=writes;dev_t d=0;assert(sweet_ufs_rootdev(&d)==EIO);
 assert(reads==r && writes==w && !ramoops_calls && !init_calls);}
''')
 def test_no_benefit_and_healthy_nonscope_have_no_set(self):
  self.run_c(r'''
reset();attrs[0x1583]=attrs[0x1568]=3;
assert(ufs_dali_prepare_pwm(1,true,3)==0 && !sets && !pmcs && commands==11);
reset();attrs[0x1583]=4;attrs[0x1568]=3;
assert(ufs_dali_prepare_pwm(1,true,3)==0 && !sets && !pmcs);
reset();attrs[0x1571]=0x11;attrs[0x1584]=attrs[0x1569]=1;
assert(ufs_dali_prepare_pwm(1,true,3)==0 && !sets && !pmcs);
''')
 def test_ack_failure_readback_mismatch_and_mmio_all_ones(self):
  self.run_c(r'''
reset();ack_stuck=true;assert(ufs_dali_prepare_pwm(1,true,3)==EIO && commands==1);
reset();corrupt_readback=true;assert(ufs_dali_prepare_pwm(1,true,3)==EIO && pmcs==1 && sweet_ufs_pwm_terminal);
unsigned offsets[]={REG_HCE,REG_HCS,REG_IE,REG_AHIT,REG_UTRLDBR,REG_UTMRLDBR,REG_UTRLRSR,REG_UTMRLRSR,REG_UTRLBA,REG_UTRLBAU,REG_IS};
for(unsigned i=0;i<sizeof(offsets)/sizeof(*offsets);++i){reset();bad_reg=offsets[i];assert(ufs_dali_prepare_pwm(1,true,3)!=0 && !commands);}
''')
 def test_pmc_clock_overflow_and_frozen_clock_are_bounded(self):
  self.run_c(r'''
reset();now=UINT64_MAX-100;assert(ufs_pwm_pmc(0x22,UINT64_MAX)==ETIMEDOUT && !commands);
reset();uccs_after=upms_after=-1;freeze_clock=true;
assert(ufs_pwm_pmc(0x22,UINT64_MAX)==ETIMEDOUT && commands==1 && now==0);
reset();assert(ufs_pwm_set(0x1571,0x55,UINT64_MAX)==EINVAL && !reads && !writes);
''')
 def test_overall_stage_budget_and_pmc_ack_failure(self):
  self.run_c(r'''
reset();per_command_us=2000000;
assert(ufs_dali_prepare_pwm(1,true,3)==ETIMEDOUT);
assert(now>=30000000 && now<32000000 && commands==15 && pmcs==0);
assert(ufs_pwm_reentry_error()==ETIMEDOUT && !su.ready && !capacity_calls);
reset();pmc_ack_stuck=true;
assert(ufs_dali_prepare_pwm(1,true,3)==EIO && pmcs==1 && commands==28);
assert(ufs_pwm_reentry_error()==EIO && !su.ready);
''')
 def test_root_claim_is_single_and_terminal_before_ramoops(self):
  self.run_c(r'''
reset();assert(ufs_pwm_root_enter(true)==0);assert(ufs_pwm_root_enter(true)==EBUSY);
assert(!reads && !writes);ufs_pwm_root_leave(true,EIO);
assert(ufs_pwm_root_enter(true)==EIO);dev_t d=0;assert(sweet_ufs_rootdev(&d)==EIO);
assert(!reads && !writes && !ramoops_calls && !init_calls);
reset();assert(ufs_pwm_root_enter(true)==0);su.ready=true;ufs_pwm_root_leave(true,0);
assert(ufs_pwm_root_enter(true)==0 && sweet_ufs_pwm_root_control==2);
reset();requested=1;assert(sweet_ufs_rootdev(&d)==0 && d==9 && init_calls==1 && capacity_calls==1);
assert(!reads && !writes && !commands); /* Production clock unknown: effective cap1, no new UIC. */
''')
 def test_terminal_certainty_is_atomic_and_first_error_wins(self):
  self.run_c(r'''
reset();attrs[0x1586]=0;
assert(ufs_dali_prepare_pwm(1,true,3)==EIO && !(sweet_ufs_pwm_terminal&1));
uint32_t first=sweet_ufs_pwm_terminal;unsigned r=reads,w=writes;
assert(ufs_pwm_failure(ETIMEDOUT)==EIO && sweet_ufs_pwm_terminal==first);
dev_t d=0;assert(sweet_ufs_rootdev(&d)==EIO && reads==r && writes==w && !ramoops_calls);
reset();upms_after=-1;
assert(ufs_dali_prepare_pwm(1,true,3)==ETIMEDOUT && (sweet_ufs_pwm_terminal&1));
first=sweet_ufs_pwm_terminal;assert(ufs_pwm_latch(EINVAL,false)==ETIMEDOUT && sweet_ufs_pwm_terminal==first);
reset();corrupt_readback=true;
assert(ufs_dali_prepare_pwm(1,true,3)==EIO && !(sweet_ufs_pwm_terminal&1));
reset();assert(ufs_pwm_latch(-1,false)==EIO && !(sweet_ufs_pwm_terminal&1));
''')
 def test_actual_root_entry_synthetic_cap_reentry_and_fatal(self):
  self.run_c(r'''
reset();requested=1;reenter_on_init=true;dev_t d=0;
clock_top[0xd0/4]=3u<<8;clock_pll[0x3c/4]=0xc0;clock_pll[0x254/4]=84u<<14;
assert(sweet_ufs_rootdev(&d)==0 && d==9 && pmcs==1 && init_calls==1 && capacity_calls==1);
assert(sweet_ufs_pwm_root_control==2 && !sweet_ufs_pwm_terminal);
unsigned count=commands;assert(sweet_ufs_rootdev(&d)==0 && init_calls==1 && commands==count);
reset();requested=1;fail_command=1;d=0;
clock_top[0xd0/4]=3u<<8;clock_pll[0x3c/4]=0xc0;clock_pll[0x254/4]=84u<<14;
assert(sweet_ufs_rootdev(&d)==EIO && !su.ready && !capacity_calls && !d && early_returns==1);
assert(sweet_ufs_pwm_root_control==1 && ufs_pwm_reentry_error()==EIO);
unsigned r=reads,w=writes,init=init_calls,ram=ramoops_calls;
assert(sweet_ufs_rootdev(&d)==EIO && reads==r && writes==w && init_calls==init && ramoops_calls==ram);
''',synthetic_cap=3)
 def test_production_clock_g2_admission_and_unknown_clock_skip(self):
  self.run_c(r'''
reset();requested=1;dev_t d=0;
assert(sweet_ufs_rootdev(&d)==0 && d==9 && clock_maps==2);
assert(!commands && !reads && !writes && capacity_calls==1);
reset();requested=1;clock_top[0xd0/4]=6u<<8;
clock_pll[0x3c/4]=0xc0;clock_pll[0x268/4]=96u<<14;
assert(sweet_ufs_rootdev(&d)==0 && d==9 && clock_maps==2);
assert(commands==37 && pmcs==1 && attrs[0x1583]==2 && attrs[0x1568]==2 && attrs[0x1571]==0x22);
''')
 def test_actual_caller_disabled_invalid_or_other_device_zero_clock_map(self):
  self.run_c(r'''
for(unsigned req=0;req<4;++req){if(req==1)continue;reset();requested=req;dev_t d=0;
 assert(sweet_ufs_rootdev(&d)==0 && !clock_maps && !commands && !reads && !writes);}
reset();requested=1;su.dali=false;dev_t d=0;
assert(sweet_ufs_rootdev(&d)==0 && !clock_maps && !commands && !reads && !writes);
''')
 def test_already_ready_first_guarded_entry_releases_claim(self):
  self.run_c(r'''
reset();requested=1;su.ready=true;su.bdev=9;dev_t d=0;
assert(sweet_ufs_rootdev(&d)==0 && d==9 && !init_calls);
assert(sweet_ufs_pwm_root_control==2);
assert(sweet_ufs_rootdev(&d)==0 && !init_calls && !commands);
''',synthetic_cap=3)
 def test_real_atomic_root_claim_concurrency(self):
  extra=r'''
#include <pthread.h>
static unsigned winners,busy,failed;
static void *claim_thread(void *unused){(void)unused;int result=ufs_pwm_root_enter(true);
 if(result==0)__atomic_fetch_add(&winners,1,__ATOMIC_RELAXED);
 else if(result==EBUSY)__atomic_fetch_add(&busy,1,__ATOMIC_RELAXED);
 else __atomic_fetch_add(&failed,1,__ATOMIC_RELAXED);return NULL;}
'''
  self.run_c(r'''
reset();pthread_t threads[32];
for(unsigned i=0;i<32;++i)assert(pthread_create(&threads[i],NULL,claim_thread,NULL)==0);
for(unsigned i=0;i<32;++i)assert(pthread_join(threads[i],NULL)==0);
assert(winners==1 && busy==31 && failed==0 && !reads && !writes);
ufs_pwm_root_leave(true,EIO);assert(ufs_pwm_root_enter(true)==EIO);
''',extra=extra)

if __name__=='__main__':unittest.main(verbosity=2)
