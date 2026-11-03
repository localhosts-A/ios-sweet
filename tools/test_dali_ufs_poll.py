#!/usr/bin/env python3
"""Run actual command function under a deterministic, simulated scheduler."""
import os
import pathlib
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import test_dali_ufs_startup as base
source = base.UFS
if os.environ.get('SWEET_LOAD_SOURCE_DIR'):
    assert (pathlib.Path(os.environ['SWEET_LOAD_SOURCE_DIR'])/'bsd/dev/sweet_ufs.c').read_text(encoding='latin-1') == source

actual = base.source.function(source, 'ufs_scsi_on_lun_timeout')
pre_speed = base.source.function(base.source.UFS_PRE_SPEED, 'ufs_scsi_on_lun_timeout')
old = base.source.function(base.source.UFS_PRE_POLL, 'ufs_scsi_on_lun_timeout')
changed = 'nanoseconds_to_absolutetime((uint64_t)su.spin_us * NSEC_PER_USEC, &spin_until);'
original = 'nanoseconds_to_absolutetime(CMD_SPIN_NS, &spin_until);'
if pre_speed.replace(changed, original) != old or pre_speed.count(changed) != 1:
    raise ValueError('unexpected pre-0200 polling function difference')
assert base.source.apply_exact_hunks(base.source.UFS_PRE_SPEED, 'bsd/dev/sweet_ufs.c', base.source.UFS_SPEED_PATCH) == source
assert '#define DALI_CMD_SPIN_MAX_US 1000u' in source
init = base.source.function(source, 'sweet_ufs_init')
assert init.count('su.spin_us = ufs_dali_spin_us(node, su.dali);') == 1
assert init.index('su.spin_us = ufs_dali_spin_us(node, su.dali);') < init.index('ufs_take_over_host()')
fixture = base.fixture()
assert '#define CMD_SPIN_US     50u' in fixture
helper = base.source.function(source, 'ufs_dali_spin_us')
dt_stub = r'''
typedef void *DTEntry;
#define kSuccess 0
static uint32_t prop_value;
static unsigned prop_size, prop_calls;
static int prop_result;
static bool prop_null;
static int SecureDTGetProperty(DTEntry node, const char *name, const void **prop, unsigned *size) {
 assert(node && !strcmp(name,"dali-poll-spin-us")); ++prop_calls;
 *prop=prop_null ? NULL : &prop_value; *size=prop_size; return prop_result;
}
'''
assert fixture.count(actual) == 1
fixture = fixture.replace(actual,dt_stub + helper + actual,1)
# Add a test-controlled scheduler dispatch delay: this models, not measures, XNU.
fixture = fixture.replace('static uint64_t submitted_at;static unsigned char tag_at_submit;',
                          'static uint64_t submitted_at, dispatch_delay;static unsigned char tag_at_submit;')
fixture = fixture.replace('++block_calls;now=early_wake?now+1:wake_at;',
                          '++block_calls;now=(early_wake?now+1:wake_at)+dispatch_delay;')
fixture = fixture.replace('now=complete_after=wake_at=submitted_at=0;',
                          'now=complete_after=wake_at=submitted_at=dispatch_delay=0; su.spin_us=500;')
test = r'''
int main(void){
 (void)unchanged_after_poison; (void)logger; (void)check_counters;
 DTEntry node=(DTEntry)1; prop_size=4; prop_value=500; prop_result=0; prop_null=false; prop_calls=0;
 assert(ufs_dali_spin_us(node,true)==500 && prop_calls==1);
 assert(ufs_dali_spin_us(node,false)==50 && prop_calls==1);
 prop_value=49;assert(ufs_dali_spin_us(node,true)==50);
 prop_value=50;assert(ufs_dali_spin_us(node,true)==50);
 prop_value=1000;assert(ufs_dali_spin_us(node,true)==1000);
 prop_value=1001;assert(ufs_dali_spin_us(node,true)==50);
 prop_value=UINT32_MAX;assert(ufs_dali_spin_us(node,true)==50);
 prop_size=3;assert(ufs_dali_spin_us(node,true)==50);
 prop_size=4;prop_null=true;assert(ufs_dali_spin_us(node,true)==50);
 prop_null=false;prop_result=-1;assert(ufs_dali_spin_us(node,true)==50);
 for (unsigned n=0;n<5;++n){
  static const unsigned t[]={0,49,50,300,500}; reset(); su.wait=true;complete_after=t[n];
  assert(command(5000000)==0 && now==t[n] && !wait_calls && !block_calls && !su.command_poisoned);
 }
 reset();su.wait=true;complete_after=501;
 assert(command(5000000)==0 && now==700 && wait_calls==1 && block_calls==1);
 reset();su.wait=true;complete_after=1001;
 assert(command(5000000)==0 && now==1100 && wait_calls==3 && block_calls==3 && delay_calls==500);
 for(unsigned mode=0;mode<5;++mode){
  reset();su.wait=true;complete_after=600;
  if(mode==0)su.wait=false;if(mode==1)su.ready=false;if(mode==2)irq_on=false;
  if(mode==3)in_irq=true;if(mode==4)preempt=1;
  assert(command(5000000)==0 && now==600 && !wait_calls && !block_calls);
 }
 reset();su.wait=true;complete_after=701;early_wake=true;
 assert(command(5000000)==0 && now==701 && block_calls>1 && !su.command_poisoned);
 reset();su.wait=true;complete_after=701;reject_wait=true;
 assert(command(5000000)==0 && now==900 && wait_calls==2 && !block_calls);
 reset();su.wait=true;timeout_mode=true;
 assert(command(800000)==ETIMEDOUT && su.command_poisoned && now==801 && clear_calls==1 && block_calls==2);
 unsigned before=commands;assert(command(5000000)==ETIMEDOUT && commands==(int)before);
 reset();su.wait=true;timeout_mode=true;
 assert(command(5000000000ull)==ETIMEDOUT && now==5000001 && su.command_poisoned && clear_calls==1);
 assert(block_calls>24000 && block_calls<25001);
 reset();su.wait=true;desync_command=1;
 assert(command(5000000)==EIO && su.command_poisoned && !clear_calls);
 reset();su.wait=true;complete_after=300;dispatch_delay=1000;
 assert(command(5000000)==0 && now==300 && !block_calls);
 reset();su.dali=false;su.spin_us=ufs_dali_spin_us(node,false);su.wait=true;complete_after=300;dispatch_delay=1000;
 assert(command(5000000)==0 && now==1250 && block_calls==1);
 reset();su.spin_us=50;su.wait=true;complete_after=300;dispatch_delay=1000;
 assert(command(5000000)==0 && now==1250 && block_calls==1);
 reset();su.spin_us=1000;su.wait=true;complete_after=1001;
 assert(command(5000000)==0 && now==1200 && block_calls==1 && delay_calls==1000);
 check_counters();puts("PASS actual function: completion boundaries, bounded sleeping, contexts, timeout, poison, scheduler model");
 return 0;
}
'''
with tempfile.TemporaryDirectory() as tmp:
    c = pathlib.Path(tmp) / 'wait.c'
    exe = pathlib.Path(tmp) / 'wait'
    c.write_text(fixture + test)
    subprocess.run(['clang','-std=gnu11','-O1','-Wall','-Wextra','-Werror','-fsanitize=address,undefined',str(c),'-o',str(exe)],check=True)
    subprocess.run([str(exe)],check=True)
