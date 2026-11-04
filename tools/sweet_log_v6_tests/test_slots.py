#!/usr/bin/env python3
"""Host-execute production slot discovery, GPT checks, write limits and logger."""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

D = Path(__file__).resolve().parents[1]
P = D.parent / 'ufs-hsg4-root-20261104-v1'
sys.dont_write_bytecode = True
sys.path.insert(0, str(P / 'fixtures/legacy/tools'))
import test_dali_ufs_startup as startup

UFS = (D / 'xnu/bsd/dev/sweet_ufs.c').read_text(encoding='latin-1')
f = startup.source.function
CONSTANTS = UFS[UFS.index('#define REG_CAP'):UFS.index('struct utrd {')]
LAYOUT = UFS[UFS.index('struct utrd {'):UFS.index('#if DEVELOPMENT\n#include "SweetUFSLoadPolicy.h"')]
SLOT_PREFIX = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>
#include <sys/types.h>
#include <openssl/sha.h>
#define MIN(a,b) ((a)<(b)?(a):(b))
#define DEVELOPMENT 0
#define EINVAL 22
#define ENXIO 6
#define ENOENT 2
#define EEXIST 17
#define EIO 5
#define EPROTO 100
#define ETIMEDOUT 60
#define EPERM 1
#define NSEC_PER_USEC 1000ull
#define NSEC_PER_SEC 1000000000ull
#define LCK_GRP_DECLARE(n,s) int n
#define LCK_MTX_DECLARE(n,g) int n
#define DALI_LOG_FIRST 4096u
#define STORAGE_BLOCKS 8192u
typedef uintptr_t vm_offset_t;
typedef uint64_t addr64_t;
'''
SLOT_STUBS = r'''
static unsigned char disk[STORAGE_BLOCKS * UFS_BLOCK], dma[DMA_SIZE];
static uint32_t crc32(uint32_t old,const void *p,size_t n){uint32_t c=~old;const uint8_t *b=p;while(n--){c^=*b++;for(int i=0;i<8;i++)c=(c>>1)^((c&1)?0xedb88320u:0);}return ~c;}
static unsigned reads, writes, forced_read_error, read_before_hs;
static uint64_t time_now,time_step;
static uint64_t mach_absolute_time(void){uint64_t n=time_now;time_now+=time_step;return n;}
static bool ready_transition, lun_start;
static uint32_t logger_option,slots_option;
static bool ufs_dali_root_boot_bool(const char *key,uint32_t *value){
 if(!strcmp(key,"sweetlog"))*value=logger_option;else {assert(!strcmp(key,"sweetlogslots"));*value=slots_option;}
 return *value<=1;
}
static uint32_t secount, op;
static int ignored_log(const char *fmt,...){(void)fmt;return 0;}
#define printf ignored_log
static int ufs_scsi(const uint8_t *cdb,unsigned cdb_len,uint32_t bytes,bool write){
 (void)cdb_len; ++secount; op=cdb[0];
 assert(write && cdb[0]==0x2a && bytes<=BOUNCE_SIZE);
 uint32_t lba=(uint32_t)cdb[2]<<24|(uint32_t)cdb[3]<<16|(uint32_t)cdb[4]<<8|cdb[5];
 uint32_t n=(uint32_t)cdb[7]<<8|cdb[8];
 assert(lba>=DALI_LOG_FIRST && lba+n<=STORAGE_BLOCKS && n*UFS_BLOCK==bytes);
 ++writes; return 0;
}
static bool ufs_sense_requires_lun_start(void){return ready_transition;}
static int ufs_dali_start_lun_and_wait(void){lun_start=true;return 0;}
static int ufs_read_blocks(uint64_t lba,uint32_t n){
 ++reads;if(!su.hsg1_active && lba>=DALI_LOG_FIRST)++read_before_hs;
 if(forced_read_error && reads==forced_read_error)return EIO;
 assert(n && n<=BOUNCE_SIZE/UFS_BLOCK && lba<STORAGE_BLOCKS && n<=STORAGE_BLOCKS-lba);
 memcpy(dma+DMA_BOUNCE,disk+lba*UFS_BLOCK,n*UFS_BLOCK);return 0;
}
static void put32(unsigned char *p,uint32_t v){memcpy(p,&v,4);}
static void put64(unsigned char *p,uint64_t v){memcpy(p,&v,8);}
static void entry(unsigned index,const char *name,uint64_t first,uint64_t last){
 unsigned char *p=disk+2*UFS_BLOCK+index*128;p[0]=1;put64(p+32,first);put64(p+40,last);
 for(unsigned i=0;name[i];++i)p[56+2*i]=(unsigned char)name[i];
}
static void reset(void){
 memset(&su,0,sizeof(su));memset(disk,0,sizeof(disk));memset(dma,0,sizeof(dma));
 su.dma=(uintptr_t)dma;su.dali=true;su.hsg4_root=true;su.capacity_valid=true;
 su.capacity_blocks=STORAGE_BLOCKS;su.log_offset_bytes=DALI_LOG_OFFSET_BYTES;
 reads=writes=forced_read_error=read_before_hs=secount=op=logger_option=slots_option=0;time_now=time_step=0;ready_transition=lun_start=false;
}
static void make_gpt(void){
 unsigned char *p=disk+UFS_BLOCK;memcpy(p,"EFI PART",8);put32(p+12,92);put64(p+40,3);
 put64(p+48,STORAGE_BLOCKS-1);put64(p+72,2);put32(p+80,8);put32(p+84,128);
 entry(0,"unixnu_data",10,99);entry(1,"unixnu_logs",100,STORAGE_BLOCKS-1);
}
'''
SLOT_MAIN = r'''
#undef printf
static uint8_t digest[SHA256_DIGEST_LENGTH];
static const char fresh_id[33]="fedcba9876543210fedcba9876543210";
static const char retained_id[33]="0123456789abcdef0123456789abcdef";
static void known(unsigned slot){uint8_t *p=disk+(DALI_LOG_FIRST+slot*SWEET_LOG_BLOCKS)*UFS_BLOCK;memcpy(p,"SWEETLOG",8);put32(p+8,1);put32(p+12,SWEET_LOG_SIZE);put64(p+16,4);put64(p+24,1);}
static void extended(unsigned slot,bool v2){uint8_t *p=disk+(DALI_LOG_FIRST+slot*SWEET_LOG_BLOCKS)*UFS_BLOCK;known(slot);
 memcpy(p+32,v2?"SWIDv002":"SWIDv001",8);memcpy(p+40,"0123456789abcdef0123456789abcdef",32);
 if(v2){put64(p+72,3);put64(p+80,260);}unsigned n=v2?88:72;put32(p+n,crc32(0,p,n));}
static void prepare(void){reset();su.hsg1_active=true;su.log_first_block=DALI_LOG_FIRST;su.log_nblocks=DALI_LOG_SPAN_BLOCKS;known(0);}
static void reference(void){assert(!ufs_dali_log_pwm_digest(digest));reads=0;}
int main(void){
 /* Only a known slot0 plus explicit caller opt-in admits later empty slots. */
 prepare();reference();assert(!ufs_dali_select_log_slot(digest) && su.log_partition_ready);
 assert(su.log_first_block==DALI_LOG_FIRST+SWEET_LOG_BLOCKS && su.log_nblocks==SWEET_LOG_BLOCKS && reads==15 && !writes);
 /* Unknown/partial bytes anywhere veto all logging, even if another slot is zero. */
 for(unsigned slot=0;slot<3;slot++){prepare();disk[(DALI_LOG_FIRST+slot*SWEET_LOG_BLOCKS+900)*UFS_BLOCK+31]=0xee;
  if(slot==0)disk[DALI_LOG_FIRST*UFS_BLOCK]=0;reference();assert(!ufs_dali_select_log_slot(digest) && !su.log_partition_ready && !writes);}
 prepare();memset(disk+DALI_LOG_FIRST*UFS_BLOCK,0,8);reference();assert(!ufs_dali_select_log_slot(digest) && !su.log_partition_ready);
 prepare();known(1);reference();assert(!ufs_dali_select_log_slot(digest) && su.log_first_block==DALI_LOG_FIRST+2*SWEET_LOG_BLOCKS);
 for(unsigned ver=0;ver<2;ver++){
  prepare();extended(1,ver);reference();assert(!ufs_dali_select_log_slot(digest) && su.log_first_block==DALI_LOG_FIRST+2*SWEET_LOG_BLOCKS);
  prepare();extended(1,ver);disk[(DALI_LOG_FIRST+SWEET_LOG_BLOCKS)*UFS_BLOCK+(ver?88:72)]^=1;
  reference();assert(!ufs_dali_select_log_slot(digest) && !su.log_partition_ready && !writes);
  prepare();extended(1,ver);bool seen=false;
  assert(!ufs_dali_log_candidate_present(&seen,UINT64_MAX,retained_id) && !seen && !writes);
  prepare();extended(1,ver);reference();
  assert(!ufs_dali_select_log_slot(digest,UINT64_MAX,retained_id) && !su.log_partition_ready && !writes);
 }
 prepare();disk[DALI_LOG_FIRST*UFS_BLOCK+90]=1;reference();assert(!ufs_dali_select_log_slot(digest) && !su.log_partition_ready);
 prepare();known(1);known(2);reference();assert(!ufs_dali_select_log_slot(digest) && !su.log_partition_ready);
 prepare();su.log_nblocks=SWEET_LOG_BLOCKS;assert(!ufs_dali_select_log_slot(digest) && !su.log_partition_ready && !reads);
 /* PWM precheck avoids reading 12MiB when the log area is obviously full. */
 bool maybe=false;prepare();known(1);known(2);assert(!ufs_dali_log_candidate_present(&maybe,UINT64_MAX) && !maybe && reads==3);
 prepare();assert(!ufs_dali_log_candidate_present(&maybe,UINT64_MAX) && maybe && reads==3);
 prepare();disk[(DALI_LOG_FIRST+SWEET_LOG_BLOCKS)*UFS_BLOCK]=1;
 assert(!ufs_dali_log_candidate_present(&maybe,UINT64_MAX) && !maybe && reads==2);
 prepare();time_step=1;assert(ufs_dali_log_pwm_digest(digest,3)==ETIMEDOUT && reads==2);
 prepare();reference();time_step=1;assert(ufs_dali_select_log_slot(digest,3)==ETIMEDOUT && reads==2 && !su.log_partition_ready);
 /* Address-specific G4 false-empty: PWM recognized slot1, G4 returns all zeros. */
 prepare();known(1);reference();memset(disk+(DALI_LOG_FIRST+SWEET_LOG_BLOCKS)*UFS_BLOCK,0,SWEET_LOG_BLOCKS*UFS_BLOCK);
 assert(ufs_dali_select_log_slot(digest)==EIO && !su.log_partition_ready && !writes);
 /* Any PWM/G4 byte discrepancy refuses all writes. */
 prepare();reference();disk[(DALI_LOG_FIRST+1)*UFS_BLOCK]=1;
 assert(ufs_dali_select_log_slot(digest)==EIO && !su.log_partition_ready && !writes);
 /* PWM zero => G4 unrecognized nonzero: still finish hash, report mismatch. */
 prepare();reference();disk[(DALI_LOG_FIRST+SWEET_LOG_BLOCKS+900)*UFS_BLOCK+7]=1;
 assert(ufs_dali_select_log_slot(digest)==EIO && reads==15 && !su.log_partition_ready && !writes);
 for(unsigned failure=1;failure<=15;failure++){prepare();reference();forced_read_error=failure;
  assert(ufs_dali_select_log_slot(digest)==EIO && reads==failure && !su.log_partition_ready && !writes);}
 /* Real write helper is independent of root rw but bounded to selected slot. */
 prepare();reference();assert(!ufs_dali_select_log_slot(digest));su.writable=false;
 assert(!ufs_write_log_blocks(su.log_first_block,1) && !ufs_write_log_blocks(su.log_first_block+SWEET_LOG_BLOCKS-1,1));
 assert(ufs_write_log_blocks(su.log_first_block-1,1)==EPERM);
 assert(ufs_write_log_blocks(su.log_first_block+SWEET_LOG_BLOCKS,1)==EPERM && writes==2);
 /* Real GPT parser does not read log data before HS on this path. */
 reset();make_gpt();disk[UFS_BLOCK+56]=0x44;disk[2*UFS_BLOCK+16]=0x55;
 assert(!ufs_find_partitions("unixnu_data","unixnu_logs"));
 assert(reads==2 && !read_before_hs && !su.log_partition_ready);
 assert(su.root_gpt_first==10 && su.root_gpt_last==99 && su.root_gpt_type[0]==1 && su.root_gpt_guid[0]==0x55 && su.gpt_disk_guid[0]==0x44);
 /* Opt-in catches root/log overlap before root exposure regardless of order. */
 for(unsigned swapped=0;swapped<2;swapped++){
  reset();make_gpt();memset(disk+2*UFS_BLOCK,0,3*128);
  entry(swapped?1:0,"unixnu_data",10,99);entry(swapped?0:1,"unixnu_logs",99,STORAGE_BLOCKS-1);
  logger_option=slots_option=1;assert(ufs_find_partitions("unixnu_data","unixnu_logs")==EPROTO);
  assert(!su.first_block && !su.nblocks && !su.ready && !su.log_partition_ready && !writes);
 }
 /* A later duplicate or legacy-matched UTF-16 suffix cannot hide an overlap. */
 reset();make_gpt();entry(2,"unixnu_logs",50,70);logger_option=slots_option=1;
 assert(ufs_find_partitions("unixnu_data","unixnu_logs")==EPROTO && !su.ready && !writes);
 reset();make_gpt();entry(2,"unixnu_logs",50,70);disk[2*UFS_BLOCK+2*128+56+11*2+1]=1;
 logger_option=slots_option=1;assert(ufs_find_partitions("unixnu_data","unixnu_logs")==EPROTO && !su.ready && !writes);
 /* An unrequested log retains legacy overlap behavior; no global GPT claim. */
 reset();make_gpt();entry(1,"unixnu_logs",50,STORAGE_BLOCKS-1);
 assert(!ufs_find_partitions("unixnu_data","unixnu_logs") && su.first_block==10 && !su.log_partition_ready);
 /* In this candidate non-G4 Dali refuses legacy slot0 reuse; root discovery still succeeds. */
 reset();su.hsg4_root=false;make_gpt();assert(!ufs_find_partitions("unixnu_data","unixnu_logs"));
 assert(reads==2 && !read_before_hs && !su.log_partition_ready && !su.log_nblocks && !writes);
 /* Non-Dali keeps the legacy partition admission path (producer is global). */
 reset();su.dali=false;su.hsg4_root=false;make_gpt();
 assert(!ufs_find_partitions("unixnu_data","unixnu_logs") && su.log_partition_ready && reads==7 && !writes);
 puts("PASS actual slot admission, unknown-data refusal, PWM/G4 comparison and bounded writes");return 0;
}
'''

class TestSlots(unittest.TestCase):
    def compile_run(self, source):
        with tempfile.TemporaryDirectory(prefix='sweetlog-host-') as t:
            p = Path(t)
            (p/'test.c').write_text(source)
            cmd = ['clang', '-std=gnu11', '-O1', '-Wall', '-Wextra', '-Werror',
                   '-Wno-unused-function', '-Wno-unused-variable', '-Wno-deprecated-declarations', '-fsanitize=address,undefined',
                   '-fno-sanitize-recover=all']
            if sys.platform == 'darwin':
                prefix = Path(subprocess.check_output(['brew', '--prefix', 'openssl@3'], text=True).strip())
                cmd.extend(['-I' + str(prefix / 'include'), '-L' + str(prefix / 'lib')])
            cmd.extend([str(p/'test.c'), '-o', str(p/'test'), '-lcrypto'])
            subprocess.run(cmd, check=True)
            subprocess.run([str(p/'test')], check=True, timeout=120)

    def test_actual_slot_discovery_and_write_bounds(self):
        functions = ''.join(f(UFS, n) for n in ('ufs_log_region_available', 'ufs_gpt_name_legacy_match', 'ufs_dali_known_log_header',
                                                  'ufs_dali_log_candidate_present', 'ufs_dali_log_pwm_digest',
                                                  'ufs_dali_select_log_slot', 'ufs_write_log_blocks', 'ufs_find_partitions'))
        main = SLOT_MAIN.replace('ufs_dali_log_pwm_digest(digest)', 'ufs_dali_log_pwm_digest(digest,UINT64_MAX)')
        main = main.replace('ufs_dali_select_log_slot(digest)', 'ufs_dali_select_log_slot(digest,UINT64_MAX,fresh_id)')
        main = main.replace('ufs_dali_select_log_slot(digest,3)', 'ufs_dali_select_log_slot(digest,3,fresh_id)')
        main = main.replace('ufs_dali_log_candidate_present(&maybe,UINT64_MAX)', 'ufs_dali_log_candidate_present(&maybe,UINT64_MAX,fresh_id)')
        self.compile_run(SLOT_PREFIX + CONSTANTS + LAYOUT + SLOT_STUBS + functions + main)

    def test_g4_logger_real_code_command_stages(self):
        # Reuse the prior driver harness but replace old source with this actual G4 source.
        startup.UFS = UFS
        startup.FUNCTIONS = tuple(n for n in startup.FUNCTIONS if n not in
            ('ufs_query_current_power_mode', 'ufs_query_fdeviceinit_flag'))
        code = startup.fixture()
        support = r'''
#define MAX(a,b) ((a)>(b)?(a):(b))
#define acquire 0
#define release 0
#define os_atomic_store(p,v,o) (*(p)=(v))
#define os_atomic_cmpxchg(p,o,n,m) (*(p)==(o)?(*(p)=(n),true):false)
#define SWEET_LOG_BATCH (64u<<10)
static uint32_t sweet_log_busy;
static int sweet_log_fair_chan;
static uint64_t sweet_log_dropped_chunks,sweet_log_dropped_bytes;
static char sweet_log_snapshot[SWEET_LOG_BATCH];
static bool ufs_dali_run_id(char out[33]){memcpy(out,"0123456789abcdef0123456789abcdef",33);return true;}
static uint32_t crc32(uint32_t old,const void *p,size_t n){uint32_t c=~old;const uint8_t *b=p;while(n--){c^=*b++;for(int i=0;i<8;i++)c=(c>>1)^((c&1)?0xedb88320u:0);}return ~c;}
'''
        code = code.replace('static void\nsweet_log_thread(', support + f(UFS,'sweet_log_retry_next') + '\nstatic void\nsweet_log_thread(', 1)
        code = code.replace('static int ufs_pwm_reentry_error(void){++hs_reentry_calls;return EIO;}',
                            'static int ufs_pwm_reentry_error(void){++hs_reentry_calls;return 0;}')
        code = code.replace('static int ufs_dali_hs_platform_validate(void){++hs_validate_calls;return EIO;}',
                            'static int ufs_dali_hs_platform_validate(void){++hs_validate_calls;return 0;}')
        code = code.replace('assert(!hs_reentry_calls && !hs_validate_calls && !hs_failure_calls);',
                            'hs_reentry_calls=hs_validate_calls=hs_failure_calls=0;')
        code = code.replace('(void)&ufs_query_current_power_mode;', '').replace('(void)&ufs_query_fdeviceinit_flag;', '')
        body = r'''
int main(void){
 (void)command;(void)unchanged_after_poison;
 for(unsigned failed=0;failed<=4;++failed){
  reset();su.hsg4_requested=su.hsg4_root=su.hsg4_root_verified=su.hsg1_active=true;
  sweet_log_dropped_chunks=3;sweet_log_dropped_bytes=260;
  fail_command=failed;logger(false);assert(!held && unlocks==1 && commands==(int)(failed?failed:4));
  assert(hs_reentry_calls && hs_validate_calls && (hs_failure_calls==(failed?1u:0u)));
  unsigned cmds[4],n=0;for(unsigned j=0;j<operation_count;++j)
   if(operations[j]==0x2a || operations[j]==0x35)cmds[n++]=operations[j];
  assert(n==(failed?failed:4));assert(cmds[0]==0x2a);
  if(n>1)assert(cmds[1]==0x35);if(n>2)assert(cmds[2]==0x2a);if(n>3)assert(cmds[3]==0x35);
  if(!failed){struct sweet_log_header *h=(void *)(dma+DMA_BOUNCE);
   assert(h->total==4096 && h->seq==1 && !memcmp(h->identity,"SWIDv002",8));
   assert(!memcmp(h->run_id,"0123456789abcdef0123456789abcdef",32));
   assert(h->dropped_chunks==3 && h->dropped_bytes==260);
   assert(h->header_crc==crc32(0,h,offsetof(struct sweet_log_header,header_crc)));}
 }
 /* Partial payload requires a read before the same four commit stages. */
 for(unsigned failed=0;failed<=5;++failed){
  reset();su.hsg4_requested=su.hsg4_root=su.hsg4_root_verified=su.hsg1_active=true;
  sweet_log_total=4095;fail_command=failed;logger(false);
  assert(!held && unlocks==1 && commands==(int)(failed?failed:5));
  unsigned cmds[5],n=0;for(unsigned j=0;j<operation_count;++j)
   if(operations[j]==0x28 || operations[j]==0x2a || operations[j]==0x35)cmds[n++]=operations[j];
  assert(n==(failed?failed:5) && cmds[0]==0x28);
  if(n>1)assert(cmds[1]==0x2a);if(n>2)assert(cmds[2]==0x35);
  if(n>3)assert(cmds[3]==0x2a);if(n>4)assert(cmds[4]==0x35);
 }
 reset();su.hsg4_requested=su.hsg4_root=su.hsg4_root_verified=su.hsg1_active=true;
 su.command_poisoned=true;memcpy(saved_dma,dma,sizeof(dma));logger(false);
 assert(!commands && !memcmp(saved_dma,dma,sizeof(dma)));
 check_counters();puts("PASS real G4 logger payload/sync/header/sync and error stops");return 0;
}
'''
        self.compile_run(code + body)

    def test_gate_order_and_offset(self):
        init = f(UFS, 'sweet_ufs_init')
        discovery = f(UFS, 'ufs_find_partitions')
        self.assertLess(init.index('ufs_find_partitions('), init.index('ufs_dali_prepare_hsg4('))
        self.assertLess(init.index('ufs_dali_log_pwm_digest('), init.index('ufs_dali_prepare_hsg4('))
        self.assertLess(init.index('su.hsg4_root_verified = true'), init.index('ufs_dali_select_log_slot('))
        self.assertLess(init.index('ufs_dali_select_log_slot('), init.index('lck_mtx_unlock(&sweet_ufs_lock)'))
        self.assertLess(init.index('lck_mtx_unlock(&sweet_ufs_lock)'), init.index('su.ready = true'))
        self.assertIn('log_slot_optin && su.log_nblocks >= SWEET_LOG_BLOCKS', init)
        self.assertIn('!su.hsg4_root || su.log_offset_bytes == DALI_LOG_OFFSET_BYTES', init)
        self.assertIn('else if (su.dali && su.hsg4_root)', discovery)
        self.assertIn('non-G4 Dali persistent logging disabled', discovery)
        self.assertEqual(UFS.count('ufs_dali_select_log_slot(log_pwm_digest, log_scan_until, log_run_id);'), 1)

if __name__ == '__main__':
    unittest.main(verbosity=2)
