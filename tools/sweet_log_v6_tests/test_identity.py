#!/usr/bin/env python3
"""Execute actual ADT run-id and optional log opt-in parsing."""
from pathlib import Path
import subprocess,sys,tempfile
D=Path(__file__).resolve().parents[1]; P=D.parent/'ufs-hsg4-root-20261104-v1'
sys.dont_write_bytecode=True;sys.path.insert(0,str(P/'fixtures/legacy/tools'))
import test_dali_ufs_startup as fixture
src=(D/'xnu/bsd/dev/sweet_ufs.c').read_text(encoding='latin-1');f=fixture.source.function
init=f(src,'sweet_ufs_init');a=init.index('\tif (su.dali && su.hsg4_root) {',init.index('su.log_partition_configured'))
b=init.index('\n\tsu.spin_us =',a);block=init[a:b]
prefix=r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#define kSuccess 0
typedef int DTEntry;
static struct{bool dali,hsg4_root;}su;
static char run_value[33];static bool has_run,has_log,has_slots;static uint32_t logger,slots;static unsigned run_size;
static int output(const char *fmt,...){(void)fmt;return 0;}
#define printf output
static int SecureDTLookupEntry(void *p,const char *name,DTEntry *node){assert(!p && !strcmp(name,"/sweet-ufs"));*node=1;return 0;}
static int SecureDTGetProperty(DTEntry node,const char *name,const void **data,unsigned *size){assert(node==1 && !strcmp(name,"dali-ufs-run-id"));*size=run_size;*data=has_run?run_value:NULL;return has_run?0:-1;}
static bool ufs_dali_root_boot_bool(const char *key,uint32_t *value){
 if(!strcmp(key,"sweetlog")){if(!has_log)return false;*value=logger;return logger<=1;}
 assert(!strcmp(key,"sweetlogslots"));if(!has_slots)return false;*value=slots;return slots<=1;
}
'''
tail=r'''
static bool check(void){bool log_slot_optin=false;char log_run_id[33]={0};
'''
end=r'''
 return log_slot_optin;}
#undef printf
static void reset(void){su.dali=su.hsg4_root=true;has_run=has_log=has_slots=true;logger=slots=1;run_size=32;memcpy(run_value,"0123456789abcdef0123456789abcdef",33);}
int main(void){char id[33];
 reset();assert(ufs_dali_log_run_id(id) && check());
 reset();memset(run_value,'0',32);assert(ufs_dali_run_id(id) && !ufs_dali_log_run_id(id) && !check());
 reset();run_value[31]='z';assert(!ufs_dali_log_run_id(id) && !check());
 reset();has_run=false;assert(!ufs_dali_log_run_id(id) && !check());
 reset();run_size=31;assert(!ufs_dali_log_run_id(id) && !check());
 reset();logger=0;assert(!check());reset();slots=0;assert(!check());
 reset();logger=2;assert(!check());reset();slots=2;assert(!check());
 reset();has_log=false;assert(!check());reset();has_slots=false;assert(!check());
 reset();su.dali=false;assert(!check());reset();su.hsg4_root=false;assert(!check());
 puts("PASS actual run ID and opt-in: all-zero disallowed for logging, root ID parser unchanged");return 0;}
'''
code=prefix+f(src,'ufs_dali_run_id')+f(src,'ufs_dali_log_run_id')+tail+block+end
with tempfile.TemporaryDirectory(prefix='sweetlog-identity-') as t:
 p=Path(t);(p/'t.c').write_text(code);subprocess.run(['clang','-std=gnu11','-O1','-Wall','-Wextra','-Werror','-fsanitize=address,undefined','-fno-sanitize-recover=all',str(p/'t.c'),'-o',str(p/'t')],check=True);subprocess.run([str(p/'t')],check=True,timeout=30)
