#!/usr/bin/env python3
"""Execute exact speed-integration block from real mutable sweet_ufs_init.
Read/HS/log helpers are separately tested; here mocks verify control flow.
"""
import pathlib
import subprocess
import sys
import tempfile

repo, view = map(pathlib.Path, sys.argv[1:3])
sys.path.insert(0, str(repo / 'tools'))
import test_dali_gui_load_progress as source
text = (view / 'bsd/dev/sweet_ufs.c').read_text(encoding='latin-1')
init = source.function(text, 'sweet_ufs_init')
start = init.index('\tif (!error) {\n\t\tsu.failure_stage = "read-capacity";')
end = init.index('\n\tsu.failure_stage = "bsd-block-device";', start)
block = init[start:end]
prefix = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>
#define DALI_SPEED_BYTES (4u<<20)
struct dali_speed_sample {uint64_t bytes,begin,end;unsigned commands;int error;};
static struct {bool dali,ready,command_poisoned,capacity_valid,hsg1_requested,hsg1_active,speed_probe,speed_probe_done;const char *failure_stage;}su;
static unsigned capacity,partitions,readpasses,hstransitions,saves,latches,unlocks,fail;
static bool held;
static int sweet_ufs_lock;
static int ufs_read_capacity(void){assert(held);++capacity;return fail==1?EIO:0;}
static int ufs_find_partitions(const char *r,const char *l){assert(held && !strcmp(r,"unixnu_data") && !strcmp(l,"unixnu_logs"));++partitions;return fail==2?EIO:0;}
static int ufs_dali_speed_read_pass(void *p,size_t n,bool cmp,uint64_t until,struct dali_speed_sample *s){
 assert(held && p==(void *)1 && n==DALI_SPEED_BYTES && until==900 && !su.ready);++readpasses;
 assert(cmp==su.hsg1_active);assert((readpasses==1 && !cmp)||(readpasses==2 && cmp));
 s->bytes=n;s->begin=1;s->end=2;s->commands=4;
 return fail==(cmp?5u:3u)?EIO:0;}
static int ufs_dali_prepare_hsg1(bool requested){assert(held && requested && !su.ready && !su.hsg1_active);++hstransitions;return fail==4?EIO:0;}
static int ufs_pwm_failure(int error){assert(error);++latches;return error;}
static int ufs_dali_speed_save_log(bool verified){assert(held && verified && su.hsg1_active && readpasses==2 && !su.ready);++saves;return fail==6?EIO:0;}
static void lck_mtx_unlock(int *p){assert(p==&sweet_ufs_lock && held);held=false;++unlocks;}
static int flow(void){
 int error=0;const char *want="unixnu_data",*log_want="unixnu_logs";
 uintptr_t speed_reference=1;uint64_t speed_until=900;
 struct dali_speed_sample speed_before={0},speed_after={0};
'''
tests = r'''
 return 0;}
static void reset(void){memset(&su,0,sizeof(su));su.dali=true;su.speed_probe=su.hsg1_requested=true;capacity=partitions=readpasses=hstransitions=saves=latches=unlocks=fail=0;held=true;}
int main(void){
 reset();assert(flow()==ECANCELED && !su.ready && su.speed_probe_done && !held && unlocks==1);
 assert(capacity==1 && partitions==1 && readpasses==2 && hstransitions==1 && saves==1 && !latches);
 for(unsigned f=1;f<=6;f++){
  reset();fail=f;assert(flow()==EIO && !su.ready && !su.speed_probe_done && !held && unlocks==1);
  if(f<3)assert(!readpasses && !hstransitions && !saves);
  if(f==3)assert(readpasses==1 && !hstransitions && !saves);
  if(f==4)assert(readpasses==1 && hstransitions==1 && !saves && !su.hsg1_active);
  if(f==5)assert(readpasses==2 && hstransitions==1 && !saves && latches==1);
  if(f==6)assert(readpasses==2 && hstransitions==1 && saves==1);
 }
 reset();su.speed_probe=su.hsg1_requested=false;assert(flow()==0 && !readpasses && !hstransitions && !saves && !held);
 reset();su.speed_probe=false;assert(flow()==0 && !readpasses && hstransitions==1 && su.hsg1_active && !saves && !held);
 puts("PASS exact mutable init integration block: default no probe, errors stop, successful probe stops before mount");return 0;}
'''
with tempfile.TemporaryDirectory(prefix='ufs-speed-init-') as tmp:
    p=pathlib.Path(tmp)
    (p/'test.c').write_text(prefix+block+tests)
    subprocess.run(['clang','-std=gnu11','-O1','-Wall','-Wextra','-Werror','-fsanitize=address,undefined',str(p/'test.c'),'-o',str(p/'test')],check=True)
    subprocess.run([str(p/'test')],check=True)
