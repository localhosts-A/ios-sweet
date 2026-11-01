#!/usr/bin/env python3
"""Execute final0180 pre-root abort policy/gate. No real SMC, device or drain."""
import shutil,subprocess,tempfile,unittest
from pathlib import Path
import test_dali_gui_load_progress as source
from test_dali_fastboot_return import function,fragment
POLICY=source.SOURCE['pexpert/arm/DaliFastbootReturnPolicy.h']
NATIVE=source.SOURCE['pexpert/arm/pe_dali_watchdog.c']
OLD_POLICY=fragment('pexpert/arm/DaliFastbootReturnPolicy.h')
OLD_NATIVE=fragment('pexpert/arm/pe_dali_watchdog.c')

MOCK = '\n#include <assert.h>\n#include <stdio.h>\n#include <string.h>\nstatic uint32_t registers[10],writes,resets,drains,records,fences;\nstatic bool wrong_marker,fail_drain,stale_mode,expire_drain;\nstatic uint64_t ticks;\nstatic bool mutate_after_record;\nstatic uint32_t rd(void *c,uint32_t off){(void)c;return registers[off/4];}\nstatic void wr(void *c,uint32_t off,uint32_t value){(void)c;assert(off==0x24);++writes;registers[off/4]=wrong_marker?value^1:value;}\nstatic void fence(void *c){(void)c;++fences;}\nstatic void record(void *c,const char *s,uint32_t m,uint32_t mode){(void)c;(void)m;(void)mode;assert(s);++records;if(mutate_after_record && !strcmp(s,"root-failure-prepared"))registers[0]^=8;if(expire_drain && !strcmp(s,"root-failure-prepared"))ticks=1000+13000000ULL*1130;}\nstatic uint64_t now(void *c){(void)c;return ticks;}\nstatic bool drain(void *c,uint64_t deadline){(void)c;++drains;assert(deadline>ticks);if(stale_mode)registers[0]^=8;if(expire_drain)ticks=deadline;return !fail_drain;}\nstatic uint32_t reset(void *c){(void)c;++resets;return (uint32_t)-1;}\nstatic const struct dali_fb_ops ops={0,rd,wr,fence,record,now,drain,reset};\nstatic struct dali_fb_state state;\nstatic void initial(void){memset(&state,0,sizeof(state));memset(registers,0,sizeof(registers));registers[0]=0x17d;registers[1]=0xf000;registers[9]=0x40002000;writes=resets=drains=records=fences=0;wrong_marker=fail_drain=stale_mode=expire_drain=false;ticks=1000;}\nstatic bool arm(void){return dali_fb_arm(&state,&ops,true,true,1000,13000000,1080,1140);}\n'

class EarlyReturnTest(unittest.TestCase):
 def run_c(self,body,native=False):
  code=POLICY+MOCK
  if native:
   code+='''
#define DEVELOPMENT 1
static bool irq_on=true,in_irq;
static unsigned preempt;
static uint32_t dali_fastboot_ready;
#define dali_fastboot_state state
#define dali_fastboot_ops ops
static bool ml_get_interrupts_enabled(void){return irq_on;}
static bool ml_at_interrupt_context(void){return in_irq;}
static unsigned get_preemption_level(void){return preempt;}
static bool cmp(uint32_t *p,uint32_t a,uint32_t b){return __atomic_compare_exchange_n(p,&a,b,false,__ATOMIC_ACQ_REL,__ATOMIC_ACQUIRE);}
#define os_atomic_cmpxchg(p,a,b,o) cmp(p,a,b)
'''+function(NATIVE,'dali_fastboot_return_root_failure')
  cc=shutil.which('clang') or shutil.which('cc');self.assertIsNotNone(cc)
  with tempfile.TemporaryDirectory(prefix='pwm-early-abort-') as tmp:
   p=Path(tmp);(p/'test.c').write_text(code+'\nint main(void){(void)&dali_fb_step;assert(dali_fb_contract_valid(0x1c010000ULL,0x1000,0x24,15,3));'+body+'\nassert(writes<=2);return 0;}\n')
   subprocess.run([cc,'-std=gnu11','-O2','-Wall','-Wextra','-Werror',str(p/'test.c'),'-o',str(p/'test')],check=True)
   r=subprocess.run([str(p/'test')],capture_output=True,text=True,timeout=10)
   self.assertEqual(r.returncode,0,r.stdout+r.stderr)
 def test_normal_return_and_arm_remain_byte_identical(self):
  for name in ('dali_fb_step','dali_fb_arm','dali_fb_rollback','dali_fb_parameters_valid'):
   self.assertEqual(function(POLICY,name),function(OLD_POLICY,name))
  for name in ('dali_fastboot_return_poll','dali_fastboot_prepare','dali_fastboot_reset','dali_fastboot_drain','dali_watchdog_poll'):
   self.assertEqual(function(NATIVE,name),function(OLD_NATIVE,name))
 def test_armed_early_failure_single_reset_no_drain_no_mode_length_write(self):
  self.run_c('''
initial();assert(arm());fail_drain=true;
assert(!dali_fb_root_failure(&state,&ops));
assert(resets==1 && drains==0 && state.phase==3 && registers[0]==0x17d && registers[1]==0xf000);
assert(registers[9]==0x40002000 && writes==2);
for(unsigned i=0;i<5;++i)assert(!dali_fb_root_failure(&state,&ops));
assert(resets==1 && drains==0);
''')
 def test_unarmed_bad_identity_deadline_or_clock_does_not_reset(self):
  self.run_c('''
for(unsigned bad=0;bad<8;++bad){initial();if(bad)assert(arm());
 if(bad==1)registers[9]^=0x100;
 if(bad==2)registers[0]^=8;
 if(bad==3)registers[1]^=1;
 if(bad==4)ticks=state.started+state.frequency*(state.lease-10);
 if(bad==5)ticks=0;
 if(bad==6)state.frequency=0;
 if(bad==7)state.started=UINT64_MAX-2;
 unsigned before=writes;assert(!dali_fb_root_failure(&state,&ops));
 assert(!resets && !drains && writes==before);
 assert(!dali_fb_root_failure(&state,&ops) && !resets);
}
''')
 def test_post_record_state_change_blocks_reset_and_rollback_failure_terminal(self):
  self.run_c('''
initial();assert(arm());mutate_after_record=true;
assert(!dali_fb_root_failure(&state,&ops) && !resets && !drains && state.phase==5);
mutate_after_record=false;initial();assert(arm());wrong_marker=true;
assert(!dali_fb_root_failure(&state,&ops) && resets==1 && !drains && state.phase==4);
assert(!dali_fb_root_failure(&state,&ops) && resets==1);
''')
 def test_record_delay_does_not_extend_root_reset_deadline(self):
  self.run_c('''
initial();assert(arm());expire_drain=true;
assert(!dali_fb_root_failure(&state,&ops) && !resets && !drains && state.phase==5);
expire_drain=false;
''')
 def test_native_gate_requires_context_and_single_atomic_claim(self):
  self.run_c('''
for(unsigned bad=0;bad<5;++bad){initial();assert(arm());
 irq_on=true;in_irq=false;preempt=0;dali_fastboot_ready=1;
 if(bad==1)irq_on=false;if(bad==2)in_irq=true;if(bad==3)preempt=1;if(bad==4)dali_fastboot_ready=0;
 dali_fastboot_return_root_failure();assert(resets==(bad==0?1u:0u) && !drains);
 dali_fastboot_return_root_failure();assert(resets==(bad==0?1u:0u));
}
''',native=True)

if __name__=='__main__':unittest.main(verbosity=2)
