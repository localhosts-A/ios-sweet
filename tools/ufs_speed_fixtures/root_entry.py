#!/usr/bin/env python3
"""Actual proposed root entry/ADT flag functions; initialization remains a mock."""
import pathlib
import subprocess
import sys
import tempfile

repo, view = map(pathlib.Path, sys.argv[1:3])
sys.path.insert(0, str(repo / 'tools'))
import test_dali_ufs_pwm as base
f = base.source.function
source = (view / 'bsd/dev/sweet_ufs.c').read_text(encoding='latin-1')
c = base.fixture()
old_root = f(base.UFS, 'sweet_ufs_rootdev')
# The mock init in the existing fixture ends on the same line.
start = c.index('static int sweet_ufs_init(void){')
end = c.index('su.bdev=9;return 0;}', start) + len('su.bdev=9;return 0;}')
c = c[:start] + r'''
static int init_error;
static int sweet_ufs_init(void){++init_calls;
 if(init_error)return init_error;
 ++capacity_calls;
 if(su.speed_probe){su.speed_probe_done=true;return ECANCELED;}
 su.ready=true;su.bdev=9;return 0;}
''' + c[end:]
c = c.replace('bool dali,ready,command_poisoned;', 'bool dali,ready,command_poisoned,pwm_two_lanes,hsg1_requested,hsg1_active,speed_probe,speed_probe_done;')
c = '#define ECANCELED 89\n#define ENOTSUP 45\n' + c
prefix = r'''
typedef void *DTEntry;
#define kSuccess 0
static uint32_t hs_flag,probe_flag;
static unsigned dt_bytes=4,probe_returns;
static bool two_lane,dt_present=true,dt_dali=true,dt_null;
static bool ufs_dali_two_lane_requested(void){return two_lane;}
static int SecureDTLookupEntry(void *p,const char *path,DTEntry *entry){assert(!p && !strcmp(path,"/sweet-ufs"));*entry=(void *)1;return 0;}
static int SecureDTGetProperty(DTEntry node,const char *key,const void **value,unsigned *size){
 static const char dali[]="mediatek,mt6991-ufshc",other[]="qcom,ufshc";assert(node==(void *)1);
 if(!strcmp(key,"compatible")){*value=dt_dali?(const void *)dali:(const void *)other;*size=dt_dali?sizeof(dali):sizeof(other);return 0;}
 assert(!strcmp(key,"dali-hs-g1") || !strcmp(key,"dali-speed-probe"));
 *value=dt_null?NULL:(!strcmp(key,"dali-hs-g1")?&hs_flag:&probe_flag);*size=dt_bytes;return dt_present?0:-1;
}
static void dali_fastboot_return_ufs_probe(void){assert(!su.ready && su.speed_probe_done && sweet_ufs_pwm_root_control==1 && !sweet_ufs_pwm_terminal);++probe_returns;}
'''
assert c.count(old_root) == 1
c = c.replace(old_root, prefix + f(source, 'ufs_dali_speed_flag') + f(source, 'sweet_ufs_rootdev'))
test = r'''
static void speed_reset(void){reset();hs_flag=probe_flag=0;dt_bytes=4;probe_returns=0;two_lane=dt_null=false;dt_present=dt_dali=true;init_error=0;}
int main(void){(void)check_dma;
 speed_reset();assert(ufs_pwm_clock_cap()==1);assert(!ufs_dali_prepare_pwm(0,true,2));
 dev_t dev=0;speed_reset();assert(!sweet_ufs_rootdev(&dev) && dev==9 && init_calls==1 && !reads && !writes && !sweet_ufs_pwm_root_control);
 speed_reset();hs_flag=1;assert(!sweet_ufs_rootdev(&dev) && su.hsg1_requested && !su.speed_probe && sweet_ufs_pwm_root_control==2);
 for(unsigned which=0;which<4;which++){
  speed_reset();if(which==0)probe_flag=1;
  if(which==1){hs_flag=1;two_lane=true;}if(which==2){hs_flag=1;requested=1;}
  if(which==3){two_lane=true;requested=1;}
  assert(sweet_ufs_rootdev(&dev)==EINVAL && !init_calls && !ramoops_calls && !reads && !writes);
 }
 speed_reset();hs_flag=probe_flag=1;dev=0;
 assert(sweet_ufs_rootdev(&dev)==ECANCELED && dev==0 && !su.ready && probe_returns==1 && !early_returns);
 assert(!sweet_ufs_pwm_terminal && sweet_ufs_pwm_root_control==1);
 assert(sweet_ufs_rootdev(&dev)==EBUSY && init_calls==1 && probe_returns==1);
 speed_reset();hs_flag=probe_flag=1;init_error=ENOTSUP;
 assert(sweet_ufs_rootdev(&dev)==ENOTSUP && early_returns==1 && !probe_returns && sweet_ufs_pwm_terminal);
 unsigned count=init_calls;assert(sweet_ufs_rootdev(&dev)==ENOTSUP && init_calls==count && early_returns==1);
 for(unsigned bad=0;bad<5;bad++){
  speed_reset();hs_flag=1;if(bad==0)dt_bytes=3;if(bad==1)dt_null=true;
  if(bad==2)dt_present=false;if(bad==3)dt_dali=false;if(bad==4)hs_flag=2;
  assert(!sweet_ufs_rootdev(&dev) && !su.hsg1_requested && !su.speed_probe && !sweet_ufs_pwm_root_control);
 }
 puts("PASS actual short-probe root entry/ADT conflicts/once claim; init remains mocked");return 0;}
'''
with tempfile.TemporaryDirectory(prefix='ufs-speed-root-') as tmp:
    p = pathlib.Path(tmp)
    (p / 'SweetUFSPWMPolicy.h').write_text(base.POLICY)
    (p / 'SweetUFSPWMClock.h').write_text(base.source.SOURCE['bsd/dev/SweetUFSPWMClock.h'])
    (p / 'test.c').write_text(c + test)
    subprocess.run(['clang','-std=gnu11','-O1','-Wall','-Wextra','-Werror','-fsanitize=address,undefined',str(p/'test.c'),'-o',str(p/'test')],check=True)
    subprocess.run([str(p/'test')],check=True)
