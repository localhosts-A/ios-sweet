#!/usr/bin/env python3
"""Execute proposed short-probe return using the existing actual arm policy."""
import pathlib
import subprocess
import sys
import tempfile

header = pathlib.Path(sys.argv[1]).read_text()
probe = pathlib.Path(sys.argv[2]).read_text()
fixture = r'''
#include <assert.h>
#include <string.h>
#include <stdio.h>
static uint32_t registers[10],writes,resets,drains,records,fences,reads;
static uint64_t ticks;
static unsigned mutate,rollback_bad;
static uint32_t rd(void *c,uint32_t off){(void)c;++reads;return registers[off/4];}
static void wr(void *c,uint32_t off,uint32_t v){(void)c;assert(off==0x24);++writes;registers[off/4]=rollback_bad?v^1:v;}
static void fence(void *c){(void)c;++fences;}
static void record(void *c,const char *s,uint32_t v,uint32_t m){(void)c;(void)v;(void)m;++records;
 if(!strcmp(s,"ufs-probe-prepared")){if(mutate==1)registers[0]^=8;if(mutate==2)ticks=13000000ULL*170+1000;}}
static uint64_t now(void *c){(void)c;return ticks;}
static bool drain(void *c,uint64_t deadline){(void)c;(void)deadline;++drains;return true;}
static uint32_t reset(void *c){(void)c;++resets;return (uint32_t)-1;}
static const struct dali_fb_ops ops={0,rd,wr,fence,record,now,drain,reset};
static struct dali_fb_state state;
static void initial(void){memset(&state,0,sizeof(state));memset(registers,0,sizeof(registers));registers[0]=0x17d;registers[1]=0xf000;registers[9]=0x40002000;writes=resets=drains=records=fences=reads=mutate=rollback_bad=0;ticks=1000;}
static void arm(void){assert(dali_fb_arm(&state,&ops,true,true,1000,13000000,120,180));assert(state.phase==1);}
int main(void){
 assert(dali_fb_contract_valid(0x1c010000ULL,0x1000ULL,0x24,15,3));
 initial();assert(!dali_fb_step(&state,&ops));assert(!dali_fb_root_failure(&state,&ops));
 assert(!reads && !writes && !resets && !drains);
 initial();assert(!dali_fb_ufs_probe_return(&state,&ops) && !reads && !writes && !resets && !drains);
 initial();arm();ticks+=13000000ULL*30;assert(!dali_fb_ufs_probe_return(&state,&ops));
 assert(resets==1 && !drains && state.phase==3 && registers[0]==0x17d && registers[1]==0xf000);
 assert((registers[9]&15)==0);unsigned count=reads;
 for(unsigned i=0;i<5;i++)assert(!dali_fb_ufs_probe_return(&state,&ops));assert(resets==1 && reads==count);
 for(unsigned bad=0;bad<4;bad++){
  initial();arm();if(bad==0)registers[9]^=1;if(bad==1)registers[0]^=8;
  if(bad==2)registers[1]^=32;if(bad==3)ticks+=13000000ULL*170;
  assert(!dali_fb_ufs_probe_return(&state,&ops) && !resets && !drains && state.phase==5);
 }
 for(unsigned m=1;m<=2;m++){initial();arm();mutate=m;
  assert(!dali_fb_ufs_probe_return(&state,&ops) && !resets && !drains && state.phase==5);}
 initial();arm();rollback_bad=1;assert(!dali_fb_ufs_probe_return(&state,&ops) && resets==1 && state.phase==4);
 initial();arm();ticks=999;assert(!dali_fb_ufs_probe_return(&state,&ops) && !resets && state.phase==5);
 puts("PASS actual arm plus short UFS-probe single-return, no drain/VFS/mode-length write");return 0;
}
'''
with tempfile.TemporaryDirectory(prefix='ufs-probe-return-') as tmp:
    p = pathlib.Path(tmp)
    (p / 'test.c').write_text(header + probe + fixture)
    subprocess.run(['clang', '-std=gnu11', '-O1', '-Wall', '-Wextra', '-Werror',
                    '-fsanitize=address,undefined', str(p / 'test.c'), '-o', str(p / 'test')], check=True)
    subprocess.run([str(p / 'test')], check=True)
