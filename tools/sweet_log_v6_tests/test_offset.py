#!/usr/bin/env python3
"""Execute actual G4 offset admission block with missing and malformed ADT data."""
from pathlib import Path
import subprocess
import sys
import tempfile
D=Path(__file__).resolve().parents[1]
P=D.parent/'ufs-hsg4-root-20261104-v1'
sys.dont_write_bytecode=True
sys.path.insert(0,str(P/'fixtures/legacy/tools'))
import test_dali_ufs_startup as fixture
src=(D/'xnu/bsd/dev/sweet_ufs.c').read_text(encoding='latin-1')
init=fixture.source.function(src,'sweet_ufs_init')
a=init.index('\tif (su.dali) {\n\t\tsu.failure_stage = "dali-log-offset";')
b=init.index('\n\tsu.failure_stage = "hci-range";',a)
block=init[a:b]
prefix=r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#define UFS_BLOCK 4096u
#define DALI_LOG_OFFSET_BYTES (16ull<<20)
#define ENXIO 6
#define EINVAL 22
#define kSuccess 0
static struct{bool dali,hsg4_root,log_partition_configured;char log_partition_name[64];uint64_t log_offset_bytes;const char *failure_stage;}su;
static uint64_t offset_value;static int present;static unsigned offset_size;
static int output(const char *fmt,...){(void)fmt;return 0;}
#define printf output
static int SecureDTGetProperty(int node,const char *name,const void **out,unsigned *size){assert(node==1&&!strcmp(name,"log-offset"));*out=present?&offset_value:NULL;*size=offset_size;return present?0:-1;}
static int run(void){int node=1;const uint64_t *log_offset_prop=NULL;unsigned int log_offset_size=0;char log_want[64]="unixnu_logs";
'''
tail=r'''
 if(!su.log_partition_configured)assert(!log_want[0]&&!su.log_partition_name[0]);return 0;}
#undef printf
static void reset(bool g4){memset(&su,0,sizeof(su));su.dali=true;su.hsg4_root=g4;su.log_partition_configured=true;
 strcpy(su.log_partition_name,"unixnu_logs");present=1;offset_size=8;offset_value=DALI_LOG_OFFSET_BYTES;}
int main(void){
 reset(true);assert(!run()&&su.log_partition_configured);
 reset(true);present=0;assert(!run()&&!su.log_partition_configured);
 reset(true);offset_size=4;assert(!run()&&!su.log_partition_configured);
 reset(true);offset_value=0;assert(!run()&&!su.log_partition_configured);
 reset(true);offset_value=DALI_LOG_OFFSET_BYTES+1;assert(!run()&&!su.log_partition_configured);
 reset(true);offset_value=DALI_LOG_OFFSET_BYTES+UFS_BLOCK;assert(!run()&&!su.log_partition_configured);
 reset(false);present=0;assert(run()==ENXIO);
 reset(false);offset_value=1;assert(run()==EINVAL);
 reset(false);offset_value=0;assert(!run()&&su.log_partition_configured);
 puts("PASS actual offset gate: G4 invalid/missing disables logger without failing root; legacy preserved");return 0;
}
'''
with tempfile.TemporaryDirectory(prefix='sweetlog-offset-') as t:
 p=Path(t);(p/'t.c').write_text(prefix+block+tail)
 subprocess.run(['clang','-std=gnu11','-O1','-Wall','-Wextra','-Werror','-fsanitize=address,undefined','-fno-sanitize-recover=all',str(p/'t.c'),'-o',str(p/'t')],check=True)
 subprocess.run([str(p/'t')],check=True,timeout=30)
