#!/usr/bin/env python3
"""Run actual Dali two-lane PMC/root functions on a bounded host mock."""
import os
import pathlib
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import test_dali_ufs_pwm as base
candidate = base.source.UFS
if os.environ.get('SWEET_LOAD_SOURCE_DIR'):
    assert (pathlib.Path(os.environ['SWEET_LOAD_SOURCE_DIR'])/'bsd/dev/sweet_ufs.c').read_text(encoding='latin-1') == candidate
f = base.source.function
before = base.fixture()
for name in ('ufs_mtk_uic','ufs_pwm_idle','ufs_pwm_get','ufs_pwm_snapshot','ufs_pwm_set'):
    assert f(candidate,name) == f(base.UFS,name), name
old_pmc=f(base.UFS,'ufs_pwm_pmc'); new_pmc=f(candidate,'ufs_pwm_pmc')
assert new_pmc.replace('mode != 0x22 && mode != 0x55', 'mode != 0x22') == old_pmc
assert before.count(new_pmc) == 1
old_root=f(base.UFS,'sweet_ufs_rootdev'); root=f(candidate,'sweet_ufs_rootdev')
assert before.count(old_root)==1
before=before.replace(old_root,root,1)
before=before.replace('bool dali,ready,command_poisoned;', 'bool dali,ready,command_poisoned,pwm_two_lanes;')
before=before.replace('static uint32_t regs[4096],attrs[65536],peer_max;', 'static uint32_t regs[4096],attrs[65536],peer_max,peer_rx,peer_tx;static bool corrupt_peer;')
before=before.replace('uint32_t val=v==3?peer_max:attrs[attr];', 'uint32_t val=v==3?(attr==0x1586?peer_max:attr==0x1581?peer_rx:attr==0x1561?peer_tx:attr==0x1580?attrs[0x1560]:attr==0x1560?attrs[0x1580]:0):attrs[attr];')
before=before.replace('if(corrupt_readback && pmc_completed && attr==0x1583)++val;', 'if(corrupt_readback && pmc_completed && attr==0x1583)++val; if(corrupt_peer && pmc_completed && v==3 && attr==0x1580)val=1;')
before=before.replace('attrs[0x1586]=peer_max=4;', 'attrs[0x1586]=peer_max=4;peer_rx=peer_tx=2;corrupt_peer=false;')
# Mock ADT preserves actual byte-size, missing/null, compatible, and flag cases.
dt=r'''
typedef void *DTEntry;
#define kSuccess 0
static unsigned dt_calls,dt_size=4;
static bool dt_node=true,dt_dali=true,dt_present=true,dt_null;
static uint32_t dt_value=1;
static int SecureDTLookupEntry(void *p,const char *path,DTEntry *node){
 assert(!p && !strcmp(path,"/sweet-ufs"));++dt_calls;*node=(DTEntry)1;return dt_node?0:-1;
}
static int SecureDTGetProperty(DTEntry n,const char *key,const void **v,unsigned *size){
 static const char good[]="mediatek,mt6991-ufshc";static const char other[]="qcom,ufshc";
 assert(n==(DTEntry)1);++dt_calls;
 if(!strcmp(key,"compatible")){*v=dt_dali?(const void *)good:(const void *)other;*size=dt_dali?sizeof(good):sizeof(other);return 0;}
 assert(!strcmp(key,"dali-pwm-two-lanes"));*v=dt_null?NULL:&dt_value;*size=dt_size;return dt_present?0:-1;
}
'''
helper=f(candidate,'ufs_dali_two_lane_requested')
lane=f(candidate,'ufs_dali_prepare_two_lanes')
# The real root entry is compiled, while this mock initialization executes the
# actual explicit two-lane init call at the same point after PWM init.
init = f(candidate,'sweet_ufs_init')
start = init.index('\tif (!error && su.pwm_two_lanes) {')
end = init.index('\tif (!error) {\n\t\tsu.failure_stage = "read-capacity";', start)
call = init[start:end]
assert call.count('ufs_dali_prepare_two_lanes(true)') == 1
before=before.replace('static int sweet_ufs_init(void){', 'static int ufs_dali_prepare_two_lanes(bool);\nstatic int sweet_ufs_init(void){',1)
before=before.replace(' if(error)return error;++capacity_calls;su.ready=true;su.bdev=9;return 0;}',call+' if(error)return error;++capacity_calls;su.ready=true;su.bdev=9;return 0;}',1)
assert before.count(root)==1
before=before.replace(root,dt+helper+lane+root,1)
test=r'''
int main(void){
 (void)check_dma;
 reset();assert(ufs_pwm_pmc(0x11,500000)==EINVAL && !reads && !writes);
 reset();assert(!ufs_pwm_pmc(0x22,500000) && pmcs==1 && attrs[0x1571]==0x22);
 reset();assert(!ufs_pwm_pmc(0x55,500000) && pmcs==1 && attrs[0x1571]==0x55);
 reset();assert(ufs_dali_prepare_two_lanes(false)==0 && !reads && !writes);
 reset();su.dali=false;assert(ufs_dali_prepare_two_lanes(true)==0 && !reads && !writes);
 reset();attrs[0x1581]=attrs[0x1561]=2;attrs[0x1586]=peer_max=1;
 assert(!ufs_dali_prepare_two_lanes(true));assert(sets==2 && pmcs==1 && commands==27);
 assert(attrs[0x1583]==1 && attrs[0x1568]==1 && attrs[0x1580]==2 && attrs[0x1560]==2);
 assert(attrs[0x1584]==0 && attrs[0x1569]==0 && attrs[0x1571]==0x55);
 assert(trace_op[13]==2 && trace_attr[13]==0x1580 && trace_value[13]==2);
 assert(trace_op[14]==2 && trace_attr[14]==0x1560 && trace_value[14]==2);
 assert(trace_op[15]==2 && trace_attr[15]==0x1571 && trace_value[15]==0x55);
 assert(trace_op[9]==1 && trace_attr[9]==0x1586);
 assert(trace_op[10]==3 && trace_attr[10]==0x1586);
 assert(trace_op[11]==3 && trace_attr[11]==0x1581);
 assert(trace_op[12]==3 && trace_attr[12]==0x1561);
 for(unsigned i=0;i<commands;i++){
  assert(trace_attr[i]!=0x156a && trace_attr[i]!=0x15d4); /* no HS fields */
  if(trace_op[i]==2)assert(trace_attr[i]==0x1580 || trace_attr[i]==0x1560 || trace_attr[i]==0x1571);
 }
 for(unsigned scope=0;scope<7;scope++){
  reset();attrs[0x1581]=attrs[0x1561]=2;attrs[0x1586]=peer_max=1;
  if(scope==0)attrs[0x1581]=1;if(scope==1)peer_rx=1;if(scope==2)peer_tx=1;
  if(scope==3){attrs[0x1583]=2;attrs[0x1586]=2;}if(scope==4)attrs[0x1580]=2;
  if(scope==5){attrs[0x1571]=0x11;attrs[0x1584]=attrs[0x1569]=1;}
  if(scope==6)attrs[0x1571]=0x22;
  assert(!ufs_dali_prepare_two_lanes(true) && sets==0 && pmcs==0 && !sweet_ufs_pwm_terminal);
 }
 reset();attrs[0x1581]=attrs[0x1561]=2;peer_rx=0;
 assert(ufs_dali_prepare_two_lanes(true)==EIO && sweet_ufs_pwm_terminal && sets==0);
 reset();attrs[0x1581]=attrs[0x1561]=2;attrs[0x1583]=2;attrs[0x1586]=1;
 assert(ufs_dali_prepare_two_lanes(true)==EIO && sweet_ufs_pwm_terminal && sets==0);
 for(unsigned failure=1;failure<=27;failure++){
  reset();attrs[0x1581]=attrs[0x1561]=2;attrs[0x1586]=peer_max=1;
  if(failure==16)upms_after=-1;else fail_command=failure;
  assert(ufs_dali_prepare_two_lanes(true)!=0 && sweet_ufs_pwm_terminal);
  unsigned was=commands;assert(ufs_dali_prepare_two_lanes(true)!=0 && commands==was);
 }
 reset();attrs[0x1581]=attrs[0x1561]=2;attrs[0x1586]=peer_max=1;corrupt_readback=true;
 assert(ufs_dali_prepare_two_lanes(true)==EIO && sweet_ufs_pwm_terminal && pmcs==1);
 reset();attrs[0x1581]=attrs[0x1561]=2;attrs[0x1586]=peer_max=1;corrupt_peer=true;
 assert(ufs_dali_prepare_two_lanes(true)==EIO && sweet_ufs_pwm_terminal && pmcs==1);
 dt_node=true;dt_dali=true;dt_present=true;dt_null=false;dt_size=4;dt_value=1;dt_calls=0;
 assert(ufs_dali_two_lane_requested() && dt_calls==3);dt_value=0;assert(!ufs_dali_two_lane_requested());
 dt_value=2;assert(!ufs_dali_two_lane_requested());dt_value=1;dt_size=3;assert(!ufs_dali_two_lane_requested());
 dt_size=4;dt_null=true;assert(!ufs_dali_two_lane_requested());dt_null=false;dt_present=false;assert(!ufs_dali_two_lane_requested());
 dt_present=true;dt_dali=false;assert(!ufs_dali_two_lane_requested());dt_dali=true;dt_node=false;assert(!ufs_dali_two_lane_requested());
 dt_node=true;dt_present=false;reset();dev_t dev=0;
 assert(!sweet_ufs_rootdev(&dev) && dev==9 && capacity_calls==1 && !reads && !writes && !pmcs && !su.pwm_two_lanes);
 reset();attrs[0x1581]=attrs[0x1561]=2;attrs[0x1586]=peer_max=1;dt_present=true;dt_value=1;
 reenter_on_init=true;dev=0;assert(!sweet_ufs_rootdev(&dev) && dev==9 && capacity_calls==1 && pmcs==1);
 reset();attrs[0x1581]=attrs[0x1561]=2;attrs[0x1586]=peer_max=1;fail_command=1;dev=0;
 assert(sweet_ufs_rootdev(&dev)==EIO && early_returns==1 && capacity_calls==0 && sweet_ufs_pwm_terminal);
 unsigned was=commands;assert(sweet_ufs_rootdev(&dev)==EIO && commands==was && early_returns==1);
 reset();requested=1;dev=0;assert(sweet_ufs_rootdev(&dev)==EINVAL && !ramoops_calls && !init_calls && !reads && !writes);
 puts("PASS actual two-lane UIC/PMC, ADT, no-retry and root early-return host mocks");return 0;
}
'''
with tempfile.TemporaryDirectory() as tmp:
    p=pathlib.Path(tmp); (p/'test.c').write_text(before+test)
    (p/'SweetUFSPWMPolicy.h').write_text(base.POLICY)
    (p/'SweetUFSPWMClock.h').write_text(base.source.SOURCE['bsd/dev/SweetUFSPWMClock.h'])
    subprocess.run(['clang','-std=gnu11','-O1','-Wall','-Wextra','-Werror','-fsanitize=address,undefined',str(p/'test.c'),'-o',str(p/'test')],check=True)
    subprocess.run([str(p/'test')],check=True)
