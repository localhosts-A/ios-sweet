#!/usr/bin/env python3
"""Host-execute the exact trial-linked G4-root ADT admission path."""
import ast
from pathlib import Path
import hashlib
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import test_dali_gui_load_progress as source

UFS = source.TRIAL_SOURCE['bsd/dev/sweet_ufs.c']
EXPECTED_UFS = 'c87a315f99d56348017db3d93fbc2e5466b8b3c7f83438ac8f13c6767fe629bb'
assert hashlib.sha256(UFS.encode('latin-1')).hexdigest() == EXPECTED_UFS

fixture_tree = ast.parse((ROOT / 'tools/test_dali_ufs_extended.py').read_text())
entry_prefix = next(ast.literal_eval(node.value) for node in fixture_tree.body
                    if isinstance(node, ast.Assign) and len(node.targets) == 1
                    and isinstance(node.targets[0], ast.Name)
                    and node.targets[0].id == 'ENTRY_PREFIX')


def rep(text, old, new):
    assert text.count(old) == 1, old[:100]
    return text.replace(old, new, 1)


entry = entry_prefix
entry = rep(entry, 'hsg1_requested,speed_probe',
            'hsg1_requested,hsg4_requested,hsg4_root,speed_probe')
entry = rep(entry, 'static uint32_t hs,probe,ext,pwm,automatic,rw,logger,terminal,claim;',
            'static uint32_t hs,probe,ext,pwm,automatic,rw,logger,terminal,claim,target,root,bad_value;\n'
            'static bool target_present,target_null,root_present,root_null,pwm_present,bad_null;'
            'static unsigned target_size=4,root_size=4,init_error,bad_size;'
            'static const char *raw[3],*raw_pwm,*bad_name;')
entry = rep(entry, ' *size=4;if(!strcmp(name,"dali-hs-g1"))',
            ' if(!strcmp(name,"dali-hs-target-gear")){*out=target_null?NULL:&target;*size=target_size;return target_present?0:-1;}\n'
            ' if(!strcmp(name,"dali-hs-g4-root")){*out=root_null?NULL:&root;*size=root_size;return root_present?0:-1;}\n'
            ' if(bad_name && !strcmp(name,bad_name)){*out=bad_null?NULL:&bad_value;*size=bad_size;return 0;}\n'
            ' if(!strcmp(name,"dali-pwm-two-lanes"))return -1;\n'
            ' *size=4;if(!strcmp(name,"dali-hs-g1"))')
entry = rep(entry, 'static int SecureDTLookupEntry(', r'''
static int PE_parse_boot_arg_str(const char *key,char *out,int size){
 if(!strcmp(key,"ufspwm")){if(!pwm_present)return 0;snprintf(out,(size_t)size,"%s",raw_pwm?raw_pwm:(pwm?"1":"0"));return 1;}
 unsigned i=!strcmp(key,"dalifastboot")?0:!strcmp(key,"ufsrw")?1:!strcmp(key,"sweetlog")?2:99;
 assert(i<3 && size==4);if(!present[i])return 0;
 const char *s=raw[i]?raw[i]:(i==0?(automatic?"1":"0"):i==1?(rw?"1":"0"):(logger?"1":"0"));
 snprintf(out,(size_t)size,"%s",s);return 1;
}
static int SecureDTLookupEntry(''')
entry = rep(entry, 'static int sweet_ufs_init(void){++init_calls;if(su.speed_probe)',
            'static int sweet_ufs_init(void){++init_calls;if(init_error){su.dali=dali;return (int)init_error;}if(su.speed_probe)')

tests = r'''
static void reset(void){memset(&su,0,sizeof(su));hs=probe=ext=pwm=automatic=rw=logger=terminal=claim=target=root=0;
 target_present=target_null=root_present=root_null=pwm_present=bad_null=false;target_size=root_size=bad_size=4;two=run_missing=run_null=false;dali=true;
 present[0]=present[1]=present[2]=true;run_size=32;raw[0]=raw[1]=raw[2]=raw_pwm=bad_name=NULL;init_error=bad_value=0;
 memcpy(run_value,"0123456789abcdef0123456789abcdef",33);init_calls=ramoops=returns=failures=0;}
static void g4(void){target_present=true;target=4;probe=1;}
static void root_g4(void){target_present=root_present=true;target=4;root=1;}
int main(void){dev_t dev=0;
 reset();assert(!sweet_ufs_rootdev(&dev) && dev==9 && !su.hsg4_requested);
 reset();g4();dev=0;assert(sweet_ufs_rootdev(&dev)==ECANCELED && !dev && su.hsg4_requested && su.speed_probe && !su.ready);
 assert(returns==1 && init_calls==1 && claim==1);assert(sweet_ufs_rootdev(&dev)==EBUSY && init_calls==1);
 for(unsigned n=0;n<18;n++){
  reset();g4();if(n==0)target=5;if(n==1)target=0;if(n==2)target=3;if(n==3)target=6;
  if(n==4)target_size=3;if(n==5)target_null=true;if(n==6)probe=0;if(n==7)hs=1;
  if(n==8)two=true;if(n==9)pwm=1;if(n==10)automatic=1;if(n==11)rw=1;if(n==12)logger=1;
  if(n==13)run_missing=true;if(n==14)run_null=true;if(n==15)run_size=31;
  if(n==16)run_value[0]='Z';if(n==17)present[0]=false;
  dev=0;int e=sweet_ufs_rootdev(&dev);assert(e==(n==0?ENOTSUP:EINVAL));
  assert(!ramoops && !init_calls && !returns && !claim && !dev);
 }
 reset();g4();ext=1;dev=0;assert(sweet_ufs_rootdev(&dev)==ECANCELED && su.speed_extended && su.hsg4_requested);
 reset();hs=probe=1;dev=0;assert(sweet_ufs_rootdev(&dev)==ECANCELED && su.hsg1_requested && !su.hsg4_requested);
 reset();target_present=true;target=5;dev=0;assert(sweet_ufs_rootdev(&dev)==ENOTSUP && !init_calls);
 for(unsigned w=0;w<2;w++)for(unsigned l=0;l<2;l++){
  reset();root_g4();rw=w;logger=l;dev=0;assert(!sweet_ufs_rootdev(&dev) && dev==9);
  assert(su.ready && su.hsg4_requested && su.hsg4_root && !su.speed_probe && !su.speed_extended && init_calls==1 && claim==2 && !returns);
 }
 reset();root_g4();init_error=EIO;dev=0;assert(sweet_ufs_rootdev(&dev)==EIO && !dev && !su.ready && init_calls==1);
 assert(sweet_ufs_rootdev(&dev)==EIO && init_calls==1 && claim==1 && failures==1);
 for(unsigned n=0;n<25;n++){
  reset();root_g4();if(n==0)target_present=false;if(n==1)target=5;if(n==2)root=2;
  if(n==3)root_null=true;if(n==4)root_size=3;if(n==5)probe=1;if(n==6)ext=1;
  if(n==7)hs=1;if(n==8)two=true;if(n==9)pwm=1;if(n==10)automatic=1;
  if(n>=11 && n<=13)present[n-11]=false;if(n==14)run_missing=true;if(n==15)run_null=true;
  if(n==16)run_size=31;if(n==17)run_value[0]='Z';if(n==18)raw[0]="";
  if(n==19)raw[0]="00";if(n==20)raw[1]="0foo";if(n==21)raw[2]="2";
  if(n==22)rw=2;if(n==23)logger=2;if(n==24)root=0;
  dev=0;int e=sweet_ufs_rootdev(&dev);assert(e==(n==1?ENOTSUP:EINVAL));
  assert(!ramoops && !init_calls && !returns && !claim && !dev);
 }
 reset();root_g4();root=0;target_present=false;dev=0;assert(!sweet_ufs_rootdev(&dev) && !su.hsg4_requested);
 const char *names[]={"dali-hs-g1","dali-speed-probe","dali-speed-extended","dali-pwm-two-lanes"};
 for(unsigned i=0;i<4;i++)for(unsigned n=0;n<3;n++){
  reset();root_g4();bad_name=names[i];if(n==0)bad_value=2;if(n==1)bad_size=3;if(n==2)bad_null=true;
  dev=0;assert(sweet_ufs_rootdev(&dev)==EINVAL && !init_calls && !ramoops && !claim && !dev);
 }
 reset();root_g4();pwm_present=true;raw_pwm="";dev=0;assert(sweet_ufs_rootdev(&dev)==EINVAL && !init_calls);
 reset();root_g4();pwm_present=true;raw_pwm="0x0";dev=0;assert(sweet_ufs_rootdev(&dev)==EINVAL && !init_calls);
 reset();root_g4();pwm_present=true;dev=0;assert(!sweet_ufs_rootdev(&dev) && su.ready);
 puts("PASS current root admission: G1/probe compatibility, G4 root flags/options/run-id, duplicate init and terminal errors");return 0;}
'''

compiler = shutil.which('clang')
if not compiler:
    raise SystemExit('clang is required for the root admission harness')
with tempfile.TemporaryDirectory(prefix='dali-g4-root-admission-') as tmp:
    build = Path(tmp)
    harness = build / 'test_root.c'
    harness.write_text(entry + ''.join(source.function(UFS, name) for name in (
        'ufs_dali_speed_flag', 'ufs_dali_hs_target', 'ufs_dali_hsg4_root_requested',
        'ufs_dali_hsg4_root_flag_off', 'ufs_dali_hsg4_root_no_conflicts',
        'ufs_dali_run_id', 'ufs_dali_extended_configuration', 'ufs_dali_root_boot_bool',
        'ufs_dali_hsg4_root_configuration', 'sweet_ufs_rootdev')) + tests)
    executable = build / 'test_root'
    subprocess.run([compiler, '-std=gnu11', '-O1', '-Wall', '-Wextra', '-Werror',
                    '-Wno-unused-function', '-fsanitize=address,undefined',
                    '-fno-sanitize-recover=all', str(harness), '-o', str(executable)], check=True)
    subprocess.run([str(executable)], check=True, timeout=120)
print('PASS exact trial-linked root-entry fixture; host stubs do not demonstrate physical UFS or boot success.')
