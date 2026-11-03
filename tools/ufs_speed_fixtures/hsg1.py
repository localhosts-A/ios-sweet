#!/usr/bin/env python3
"""Exercise private HS-G1 function with existing actual UIC/PMC fixture.
Platform callbacks remain explicit mocks; this is not voltage/hardware proof.
"""
import pathlib
import subprocess
import sys
import tempfile

repo = pathlib.Path(sys.argv[1]).resolve()
source = pathlib.Path(sys.argv[2]).resolve()
sys.path.insert(0, str(repo / 'tools'))
import test_dali_ufs_pwm as base

c = base.fixture()
if len(sys.argv) > 3:
    current = (pathlib.Path(sys.argv[3]) / 'bsd/dev/sweet_ufs.c').read_text(encoding='latin-1')
    f = base.source.function
    for name in ('ufs_pwm_idle', 'ufs_pwm_get', 'ufs_pwm_set', 'ufs_pwm_pmc'):
        previous = f(base.source.UFS_PRE_SPEED, name)
        assert c.count(previous) == 1
        replacement = f(current, name)
        if name == 'ufs_pwm_idle':
            replacement = (f(current, 'ufs_pwm_idle_expect') + replacement +
                           f(current, 'ufs_pwm_observe_error') + f(current, 'ufs_pwm_observe_guard') +
                           f(current, 'ufs_pwm_pmc_observe'))
        c = c.replace(previous, replacement)
else:
    old = 'if (mode != 0x22 && mode != 0x55) return EINVAL;'
    new = 'if (mode != 0x22 && mode != 0x55 && mode != 0x44) return EINVAL;'
    assert c.count(old) == 1
    c = c.replace(old, new)
platform = r'''
#define ENOTSUP 45
static unsigned adopt_calls,validate_calls,fail_validate;
static int adopt_error;
static int ufs_dali_hs_platform_adopt(void){++adopt_calls;return adopt_error;}
static int ufs_dali_hs_platform_validate(void){++validate_calls;return validate_calls==fail_validate?EIO:0;}
static void hs_reset(void){reset();adopt_calls=validate_calls=fail_validate=0;adopt_error=0;attrs[0x1587]=5;peer_max=5;}
'''
tests = r'''
int main(void) {
 (void)check_dma;
 hs_reset();assert(!ufs_dali_prepare_hsg1(false) && !reads && !writes && !adopt_calls);
 hs_reset();su.dali=false;assert(!ufs_dali_prepare_hsg1(true) && !reads && !writes && !adopt_calls);
 hs_reset();adopt_error=ENOTSUP;assert(ufs_dali_prepare_hsg1(true)==ENOTSUP && !commands && !reads && !writes);
 hs_reset();assert(!ufs_dali_prepare_hsg1(true));assert(sets==17 && pmcs==1 && commands==39);
 assert(attrs[0x1571]==0x44 && attrs[0x156a]==2 && attrs[0x15d4]==3);
 assert(attrs[0x1583]==1 && attrs[0x1568]==1 && attrs[0x1580]==1 && attrs[0x1560]==1);
 assert(attrs[0x1584]==1 && attrs[0x1569]==1 && adopt_calls==1 && validate_calls==20);
 for(unsigned i=0;i<commands;i++)if(trace_op[i]==2 && trace_attr[i]==0x1571)assert(trace_value[i]==0x44);
 for(unsigned bad=0;bad<4;bad++) {
  hs_reset();if(bad==0)attrs[0x1587]=0;if(bad==1)peer_max=0;
  if(bad==2)attrs[0x1587]=6;if(bad==3)peer_max=6;
  int e=ufs_dali_prepare_hsg1(true);assert(e==(bad<2?ENOTSUP:EIO) && !sets && !pmcs);
 }
 for(unsigned failure=1;failure<=39;failure++) {
  hs_reset();if(failure==29)upms_after=-1;else fail_command=failure;
  assert(ufs_dali_prepare_hsg1(true)!=0 && sweet_ufs_pwm_terminal);
  unsigned count=commands;assert(ufs_dali_prepare_hsg1(true)!=0 && commands==count);
 }
 for(unsigned f=1;f<=20;f++) {
  hs_reset();fail_validate=f;assert(ufs_dali_prepare_hsg1(true)==EIO);
  if(f==1)assert(!sets && !pmcs);else assert(sweet_ufs_pwm_terminal);
 }
 hs_reset();corrupt_readback=true;assert(ufs_dali_prepare_hsg1(true)==EIO && sweet_ufs_pwm_terminal && pmcs==1);
 hs_reset();upmcrs=0;assert(ufs_dali_prepare_hsg1(true)==EIO && sweet_ufs_pwm_terminal);
 hs_reset();pmc_ack_stuck=true;assert(ufs_dali_prepare_hsg1(true)==EIO && sweet_ufs_pwm_terminal);
 hs_reset();freeze_clock=true;upms_after=uccs_after=-1;
 assert(ufs_dali_prepare_hsg1(true)==ETIMEDOUT && sweet_ufs_pwm_terminal);
 puts("PASS private actual HS-G1 sequence with actual UIC/PMC bodies; platform remains mocked");return 0;
}
'''
with tempfile.TemporaryDirectory(prefix='ufs-hsg1-host-') as name:
    p = pathlib.Path(name)
    (p / 'SweetUFSPWMPolicy.h').write_text(base.POLICY)
    (p / 'SweetUFSPWMClock.h').write_text(base.source.SOURCE['bsd/dev/SweetUFSPWMClock.h'])
    (p / 'test.c').write_text(c + platform + source.read_text() + tests)
    subprocess.run(['clang', '-std=gnu11', '-O1', '-Wall', '-Wextra', '-Werror',
                    '-fsanitize=address,undefined', str(p / 'test.c'), '-o', str(p / 'test')], check=True)
    subprocess.run([str(p / 'test')], check=True)
