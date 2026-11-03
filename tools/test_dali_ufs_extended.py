#!/usr/bin/env python3
"""Host-only tests of actual candidate helpers and control blocks; no device I/O."""
from pathlib import Path
import os
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import test_dali_gui_load_progress as sources
PATCH = ROOT / 'xnu/patches/0206-dali-ufs-extended-read-probe.patch'
PATHS = ('bsd/dev/sweet_ufs.c', 'bsd/dev/SweetUFSSpeedRead.h')
expected = {path: sources.apply_exact_hunks(sources.SOURCE[path], path, PATCH) for path in PATHS}
for path in PATHS:
    try:
        sources.apply_exact_hunks('\n' + sources.SOURCE[path], path, PATCH)
    except AssertionError:
        pass
    else:
        raise AssertionError('offset patch accepted: ' + path)
if len(sys.argv) > 1:
    view = Path(sys.argv[1])
    for path in PATHS:
        assert (view / path).read_text(encoding='latin-1') == expected[path], path
work = tempfile.TemporaryDirectory(prefix='dali-ufs-extended-')
BUILD = Path(work.name)
HEADER = BUILD / 'SweetUFSSpeedRead.h'
HEADER.write_text(expected[PATHS[1]], encoding='latin-1')
text = expected[PATHS[0]]


def function(name):
    m = re.search(r'(?:static )?(?:bool|int)\n' + name + r'\(', text)
    assert m, name
    start = text.index('{', m.start())
    pos, depth = start + 1, 1
    while depth:
        depth += (text[pos] == '{') - (text[pos] == '}')
        pos += 1
    return text[m.start():pos] + '\n'


def compile_and_run(name, content):
    source = BUILD / (name + '.c')
    exe = BUILD / name
    source.write_text(content)
    args = ['clang', '-std=gnu11', '-O1', '-g', '-Wall', '-Wextra', '-Werror',
            '-Wno-unused-function', '-fsanitize=address,undefined', str(source), '-o', str(exe)]
    subprocess.run(args, cwd=BUILD, check=True)
    subprocess.run([str(exe)], cwd=BUILD, check=True)


READ_PREFIX = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <string.h>
#include <strings.h>
#include <stdlib.h>
#include <stdio.h>
#include <errno.h>
#define UFS_BLOCK 4096u
#define BOUNCE_SIZE (1u<<20)
#define DMA_BOUNCE 0u
#define DALI_ROOT_MAX_BLOCKS (16ull*1024*1024*1024/UFS_BLOCK)
static struct {bool dali,ready,hsg1_active,command_poisoned,capacity_valid;
 uint64_t nblocks,first_block,capacity_blocks; uintptr_t dma;} su;
static uint64_t clock_now=1, clock_step, expected_lba;
static unsigned reads, fail_read, corrupt_read, backwards_read;
static bool poison, stable_corruption, check_lba;
static uint64_t mach_absolute_time(void){return clock_now;}
static int ufs_read_blocks(uint64_t lba, uint32_t count){
 assert(count==BOUNCE_SIZE/UFS_BLOCK);
 assert(lba>=su.first_block && lba-su.first_block+count<=su.nblocks);
 assert(lba<su.capacity_blocks && count<=su.capacity_blocks-lba);
 if(check_lba){assert(lba==expected_lba);expected_lba+=count;}
 ++reads; clock_now+=clock_step;
 if(reads==backwards_read)clock_now=0;
 if(reads==fail_read){su.command_poisoned=poison;return ETIMEDOUT;}
 uint8_t *b=(void *)su.dma;
 memset(b,(int)((lba ^ (lba>>8)) & 255),BOUNCE_SIZE);
 b[0]=(uint8_t)(lba>>16); b[BOUNCE_SIZE/2]=(uint8_t)lba;
 b[BOUNCE_SIZE-1]=(uint8_t)(lba>>3);
 if(reads==corrupt_read || stable_corruption)b[BOUNCE_SIZE-1]^=1;
 return 0;
}
'''
READ_TEST = r'''
static void reset(void){
 uintptr_t dma=su.dma;memset(&su,0,sizeof(su));su.dma=dma;
 su.dali=su.hsg1_active=su.capacity_valid=true;su.first_block=4096;
 su.nblocks=DALI_ROOT_MAX_BLOCKS;su.capacity_blocks=su.first_block+su.nblocks;
 clock_now=1;clock_step=0;reads=fail_read=corrupt_read=backwards_read=0;
 poison=stable_corruption=check_lba=false;expected_lba=0;
}
int main(void){
 su.dma=(uintptr_t)malloc(BOUNCE_SIZE);assert(su.dma);
 uint8_t *p=malloc((size_t)DALI_EXT_WINDOW_BYTES+2);assert(p);p[0]=9;p[DALI_EXT_WINDOW_BYTES+1]=7;
 struct dali_speed_sample s;
 reset();
 for(unsigned w=0;w<DALI_EXT_WINDOWS;w++)for(unsigned pass=0;pass<2;pass++){
  check_lba=true;expected_lba=su.first_block+DALI_SPEED_BLOCKS+(uint64_t)w*DALI_EXT_WINDOW_BLOCKS;
  assert(!ufs_dali_extended_read_pass(p+1,DALI_EXT_WINDOW_BYTES,w,pass,1000,&s));
  assert(s.bytes==DALI_EXT_WINDOW_BYTES && s.commands==64 && s.error==0);
  assert(expected_lba==su.first_block+DALI_SPEED_BLOCKS+(uint64_t)(w+1)*DALI_EXT_WINDOW_BLOCKS);
 }
 assert(reads==512 && p[0]==9 && p[DALI_EXT_WINDOW_BYTES+1]==7);
 for(unsigned n=0;n<14;n++){
  reset();if(n==0)su.dali=false;if(n==1)su.ready=true;if(n==2)su.hsg1_active=false;
  if(n==3)su.command_poisoned=true;if(n==4)su.capacity_valid=false;
  if(n==5)su.nblocks=DALI_EXT_TOTAL_BLOCKS-1;if(n==6)su.nblocks=DALI_ROOT_MAX_BLOCKS+1;
  if(n==7)su.first_block=(uint64_t)UINT32_MAX+1;
  if(n==8){su.first_block=UINT32_MAX-DALI_EXT_TOTAL_BLOCKS+2;su.capacity_blocks=(uint64_t)UINT32_MAX+1;}
  if(n==9)su.capacity_blocks=su.first_block;
  if(n==10)su.capacity_blocks=su.first_block+DALI_EXT_TOTAL_BLOCKS-1;
  void *ref=n==11?NULL:p+1;size_t size=n==12?DALI_EXT_WINDOW_BYTES-1:DALI_EXT_WINDOW_BYTES;
  unsigned window=n==13?DALI_EXT_WINDOWS:0;
  assert(ufs_dali_extended_read_pass(ref,size,window,false,1000,&s)==EINVAL && !reads);
 }
 reset(); assert(ufs_dali_extended_read_pass(p+1,DALI_EXT_WINDOW_BYTES,0,false,1,&s)==ETIMEDOUT && !reads);
 reset();clock_step=10;assert(ufs_dali_extended_read_pass(p+1,DALI_EXT_WINDOW_BYTES,0,false,25,&s)==ETIMEDOUT);
 assert(reads==3 && s.commands==3 && s.bytes==3*BOUNCE_SIZE);
 reset();backwards_read=2;assert(ufs_dali_extended_read_pass(p+1,DALI_EXT_WINDOW_BYTES,0,false,1000,&s)==ETIMEDOUT && reads==2);
 const unsigned points[]={1,2,32,64};
 for(unsigned i=0;i<sizeof(points)/sizeof(points[0]);i++){
  reset();fail_read=points[i];poison=true;
  assert(ufs_dali_extended_read_pass(p+1,DALI_EXT_WINDOW_BYTES,3,false,1000,&s)==ETIMEDOUT);
  assert(reads==fail_read && s.commands==fail_read && s.bytes==(uint64_t)(fail_read-1)*BOUNCE_SIZE && su.command_poisoned);
 }
 for(unsigned i=0;i<sizeof(points)/sizeof(points[0]);i++){
  reset();assert(!ufs_dali_extended_read_pass(p+1,DALI_EXT_WINDOW_BYTES,1,false,1000,&s));
  corrupt_read=64+points[i];assert(ufs_dali_extended_read_pass(p+1,DALI_EXT_WINDOW_BYTES,1,true,1000,&s)==EIO);
  assert(reads==corrupt_read && s.commands==points[i] && s.bytes==(uint64_t)(points[i]-1)*BOUNCE_SIZE);
 }
 /* Repeated, identical wrong data is intentionally not detected: no oracle. */
 reset();stable_corruption=true;
 assert(!ufs_dali_extended_read_pass(p+1,DALI_EXT_WINDOW_BYTES,0,false,1000,&s));
 assert(!ufs_dali_extended_read_pass(p+1,DALI_EXT_WINDOW_BYTES,0,true,1000,&s));
 reset();su.first_block=UINT32_MAX-DALI_EXT_TOTAL_BLOCKS+1;
 su.capacity_blocks=(uint64_t)UINT32_MAX+1;su.nblocks=DALI_EXT_TOTAL_BLOCKS;
 assert(!ufs_dali_extended_read_pass(p+1,DALI_EXT_WINDOW_BYTES,3,false,1000,&s));
 assert(s.commands==64);
 assert(ufs_dali_extended_read_pass(p+1,DALI_EXT_WINDOW_BYTES,0,false,1000,NULL)==EINVAL);
 free(p);free((void *)su.dma);
 puts("PASS actual extended READ helper: 512 mock commands, bounds, exact mismatch, deadline, poison; consistent corruption limitation confirmed");
 return 0;
}
'''

start = text.index('\tif (!error) {\n\t\tsu.failure_stage = "read-capacity";')
end = text.index('\n\tsu.failure_stage = "bsd-block-device";', start)
flow = text[start:end]
FLOW_PREFIX = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>
#define DALI_SPEED_BYTES (4u<<20)
#define DALI_EXT_WINDOW_BYTES (64u<<20)
#define DALI_EXT_WINDOWS 4u
struct dali_speed_sample{uint64_t bytes,begin,end;unsigned commands;int error;};
static struct{bool dali,ready,command_poisoned,capacity_valid,hsg1_requested,hsg1_active,speed_probe,speed_probe_done,speed_extended;const char *failure_stage;}su;
static unsigned cap,parts,shorts,transitions,extendeds,latches,unlocks, fail, normal, calls;
static bool held;
static int sweet_ufs_lock;
static void absolutetime_to_nanoseconds(uint64_t ticks,uint64_t *ns){*ns=ticks*77;}
static int ufs_read_capacity(void){assert(held);++cap;return fail==1?EIO:0;}
static int ufs_find_partitions(const char *r,const char *l){assert(held && !strcmp(r,"unixnu_data") && !*l);++parts;return fail==2?EIO:0;}
static int ufs_dali_speed_read_pass(void *p,size_t n,bool compare,uint64_t until,struct dali_speed_sample *s){
 assert(held && p==(void *)1 && n==DALI_SPEED_BYTES && until==900 && !su.ready);
 ++shorts;assert(compare==(shorts==2));s->bytes=n;s->commands=4;s->begin=1;s->end=2;
 return fail==(compare?5u:3u)?EIO:0;}
static int ufs_dali_prepare_hsg1(bool requested){assert(held && requested && !su.ready && !su.hsg1_active);++transitions;return fail==4?EIO:0;}
static int ufs_pwm_failure(int error){assert(error);++latches;return error;}
static int ufs_dali_extended_read_pass(void *p,size_t n,unsigned window,bool compare,uint64_t until,struct dali_speed_sample *s){
 assert(held && p==(void *)1 && n==DALI_EXT_WINDOW_BYTES && until==900 && !su.ready && su.hsg1_active);
 assert(window==extendeds/2 && compare==(extendeds%2!=0));++extendeds;
 s->bytes=n;s->commands=64;s->begin=1;s->end=2;
 return fail==5+extendeds?EIO:0;}
static void lck_mtx_unlock(int *p){assert(p==&sweet_ufs_lock && held);held=false;++unlocks;}
static int flow(void){
 int error=0;const char *want="unixnu_data",*log_want="";uintptr_t speed_reference=1;uint64_t speed_until=900;
 struct dali_speed_sample speed_before={0},speed_after={0};++calls;
'''
FLOW_TEST = r'''
 ++normal;return 0;}
static void reset(bool ext){memset(&su,0,sizeof(su));su.dali=su.speed_probe=su.hsg1_requested=true;su.speed_extended=ext;
 cap=parts=shorts=transitions=extendeds=latches=unlocks=fail=normal=calls=0;held=true;}
int main(void){
 reset(true);assert(flow()==ECANCELED && !su.ready && su.speed_probe_done && !held && unlocks==1);
 assert(cap==1 && parts==1 && shorts==2 && transitions==1 && extendeds==8 && !latches && !normal);
 for(unsigned f=1;f<=13;f++){
  reset(true);fail=f;assert(flow()==EIO && !su.ready && !su.speed_probe_done && !held && unlocks==1 && !normal);
  if(f<=5)assert(!extendeds);else assert(extendeds==f-5);
 }
 reset(false);assert(flow()==ECANCELED && su.speed_probe_done && !extendeds && !normal);
 reset(false);su.speed_probe=su.hsg1_requested=false;assert(flow()==0 && normal==1 && !shorts && !transitions);
 reset(false);su.speed_probe=false;assert(flow()==0 && normal==1 && !shorts && transitions==1 && su.hsg1_active);
 puts("PASS actual pre-root flow: all 8 extended stages, short/default paths, every injected error stops; no log writer linked");return 0;
}
'''

ENTRY_PREFIX = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <string.h>
#include <stdio.h>
#include <errno.h>
typedef int test_dev_t;
#define dev_t test_dev_t
#define SWEET_PWM_PLATFORM_CAP 2
#define kSuccess 0
typedef void *DTEntry;
static struct{bool dali,ready,pwm_two_lanes,hsg1_requested,speed_probe,speed_probe_done,speed_extended;const char *failure_stage;dev_t bdev;}su;
static uint32_t hs,probe,ext,pwm,automatic,rw,logger,terminal,claim;
static bool two,present[3],dali=true,run_missing,run_null;static unsigned run_size=32,init_calls,ramoops,returns,failures;
static char run_value[33]="0123456789abcdef0123456789abcdef";
static int PE_parse_boot_argn(const char *key,void *out,int size){
 assert(size==4);uint32_t v=0;bool ok=true;
 if(!strcmp(key,"ufspwm"))v=pwm;else if(!strcmp(key,"dalifastboot")){v=automatic;ok=present[0];}
 else if(!strcmp(key,"ufsrw")){v=rw;ok=present[1];}else if(!strcmp(key,"sweetlog")){v=logger;ok=present[2];}else assert(0);
 if(ok)memcpy(out,&v,4);return ok;
}
static int SecureDTLookupEntry(void *p,const char *name,DTEntry *out){assert(!p && !strcmp(name,"/sweet-ufs"));*out=(void *)1;return 0;}
static int SecureDTGetProperty(DTEntry node,const char *name,const void **out,unsigned *size){
 static const char yes[]="mediatek,mt6991-ufshc",no[]="qcom,ufshc";assert(node==(void *)1);
 if(!strcmp(name,"compatible")){*out=dali?yes:no;*size=dali?sizeof(yes):sizeof(no);return 0;}
 if(!strcmp(name,"dali-ufs-run-id")){*out=run_null?NULL:run_value;*size=run_size;return run_missing?-1:0;}
 *size=4;if(!strcmp(name,"dali-hs-g1"))*out=&hs;else if(!strcmp(name,"dali-speed-probe"))*out=&probe;
 else if(!strcmp(name,"dali-speed-extended"))*out=&ext;else assert(0);return 0;
}
static bool ufs_dali_two_lane_requested(void){return two;}
static int ufs_pwm_reentry_error(void){return terminal;}
static int ufs_pwm_root_enter(bool guard){if(!guard)return 0;if(claim)return EBUSY;claim=1;return 0;}
static void ufs_pwm_root_leave(bool guard,int error){if(guard){if(error)terminal=error;else claim=2;}}
static int sweet_ramoops_start(void){++ramoops;return 0;}
static int sweet_ufs_init(void){++init_calls;if(su.speed_probe){su.speed_probe_done=true;return ECANCELED;}su.ready=true;su.bdev=9;return 0;}
static void dali_fastboot_return_ufs_probe(void){++returns;assert(!automatic && !su.ready && su.speed_probe_done);}
static void dali_fastboot_return_root_failure(void){++failures;}
'''
ENTRY_TEST = r'''
static void reset(void){memset(&su,0,sizeof(su));hs=probe=ext=pwm=automatic=rw=logger=terminal=claim=0;
 two=run_missing=run_null=false;dali=true;present[0]=present[1]=present[2]=true;run_size=32;
 memcpy(run_value,"0123456789abcdef0123456789abcdef",33);init_calls=ramoops=returns=failures=0;}
int main(void){dev_t dev=0;
 reset();hs=probe=ext=1;assert(sweet_ufs_rootdev(&dev)==ECANCELED && !su.ready && su.speed_extended && init_calls==1 && returns==1 && !dev);
 assert(sweet_ufs_rootdev(&dev)==EBUSY && init_calls==1 && returns==1);
 for(unsigned n=0;n<12;n++){
  reset();hs=probe=ext=1;if(n==0)probe=0;if(n==1)hs=0;if(n==2)automatic=1;
  if(n==3)rw=1;if(n==4)logger=1;if(n>=5 && n<=7)present[n-5]=false;
  if(n==8)run_missing=true;if(n==9)run_null=true;if(n==10)run_size=31;if(n==11)run_value[5]='G';
  assert(sweet_ufs_rootdev(&dev)==EINVAL && !init_calls && !ramoops && !returns && !claim);
 }
 reset();assert(!sweet_ufs_rootdev(&dev) && dev==9 && !su.speed_probe && !su.speed_extended);
 reset();hs=probe=1;dev=0;assert(sweet_ufs_rootdev(&dev)==ECANCELED && !su.speed_extended && !dev);
 puts("PASS actual ADT, zero-argument, run-id and root-entry guards; initialization/reset mocked");return 0;
}
'''

compile_and_run('extended_read', READ_PREFIX + HEADER.read_text() + READ_TEST)
compile_and_run('extended_flow', FLOW_PREFIX + flow + FLOW_TEST)
compile_and_run('extended_entry', ENTRY_PREFIX + function('ufs_dali_speed_flag') + function('ufs_dali_extended_configuration') + function('sweet_ufs_rootdev') + ENTRY_TEST)
# These source checks cover safety gates outside the isolated function fixtures.
assert 'if (su.speed_probe) log_want[0] = \'\\0\';' in text
assert 'ufs_dali_speed_save_log(' not in text
assert '#include "SweetUFSSpeedLog.h"' not in text
assert text.index('return ECANCELED; /* Stop before device nodes') < text.index('su.bmajor = bdevsw_add')
assert 'nanoseconds_to_absolutetime(90ull * NSEC_PER_SEC, &span)' in text
print('PASS source integration checks: no direct UFS probe-log writer; ECANCELED precedes device nodes; 90s deadline')
