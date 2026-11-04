#!/usr/bin/env python3
"""Compile and execute actual optional-log GPT validation with hostile GPTs."""
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
f=fixture.source.function
prefix=r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>
#define UFS_BLOCK 4096u
#define BOUNCE_SIZE (1u<<20)
#define DMA_BOUNCE 0
#define DALI_LOG_LUN 2u
#define DALI_LOG_FIRST_BLOCK 0x3d92ff8ull
#define DALI_LOG_PART_BLOCKS 8192u
#define DALI_LOG_OFFSET_BYTES (16ull<<20)
#define DALI_ROOT_MAX_BLOCKS ((16ull<<30)/UFS_BLOCK)
#define MIN(a,b) ((a)<(b)?(a):(b))
#define EIO 5
#define EPROTO 100
#define ETIMEDOUT 60
#define SWEET_LOG_BLOCKS 1025u
#define DALI_LOG_SCAN_TIMEOUT_NS 90u
#define DALI_SPEED_BYTES (4u<<20)
#define bzero(p,n) memset(p,0,n)
static uint8_t dma[BOUNCE_SIZE],hdr[UFS_BLOCK],table[UFS_BLOCK];
static struct{uintptr_t dma;uint64_t log_first_block,log_nblocks,first_block,nblocks,capacity_blocks,root_gpt_first,root_gpt_last;
 uint8_t root_gpt_type[16],root_gpt_guid[16],gpt_disk_guid[16],lun;
 bool capacity_valid,log_partition_ready,hsg4_root,hsg4_root_verified;const char *failure_stage;}su;
static unsigned reads,fail_at;
static int output(const char *fmt,...){(void)fmt;return 0;}
#define printf output
static uint32_t crc32(uint32_t old,const void *p,size_t n){
 uint32_t c=~old;const uint8_t *b=p;while(n--){c^=*b++;for(int i=0;i<8;i++)c=(c>>1)^((c&1)?0xedb88320u:0);}return ~c;}
static int ufs_read_blocks(uint64_t lba,uint32_t n){++reads;if(fail_at==reads)return EIO;assert(n==1 && (lba==1||lba==2));memcpy(dma,lba==1?hdr:table,UFS_BLOCK);return 0;}
static void p32(uint8_t *p,uint32_t v){memcpy(p,&v,4);}
static void p64(uint8_t *p,uint64_t v){memcpy(p,&v,8);}
static void ent(unsigned i,const char *s,uint64_t first,uint64_t last){uint8_t *e=table+i*128;memset(e,0,128);e[0]=1;p64(e+32,first);p64(e+40,last);for(unsigned j=0;s[j];j++)e[56+j*2]=s[j];}
static void crc(void){p32(hdr+88,crc32(0,table,8*128));p32(hdr+16,0);p32(hdr+16,crc32(0,hdr,92));}
static void setup(void){
 memset(&su,0,sizeof(su));memset(hdr,0,sizeof(hdr));memset(table,0,sizeof(table));
 su.dma=(uintptr_t)dma;su.capacity_blocks=DALI_LOG_FIRST_BLOCK+10000;su.capacity_valid=true;su.lun=2;
 su.first_block=1000;su.log_first_block=DALI_LOG_FIRST_BLOCK+4096;su.log_nblocks=4096;su.hsg4_root=true;reads=fail_at=0;
 memcpy(hdr,"EFI PART",8);p32(hdr+8,0x00010000);p32(hdr+12,92);p64(hdr+24,1);p64(hdr+32,su.capacity_blocks-1);
 p64(hdr+40,3);p64(hdr+48,su.capacity_blocks-2);p64(hdr+72,2);p32(hdr+80,8);p32(hdr+84,128);
 ent(0,"unixnu_data",1000,2000);ent(1,"unixnu_logs",DALI_LOG_FIRST_BLOCK,DALI_LOG_FIRST_BLOCK+8191);
 su.root_gpt_first=1000;su.root_gpt_last=2000;su.nblocks=1001;
 memcpy(su.root_gpt_type,table,16);memcpy(su.root_gpt_guid,table+16,16);memcpy(su.gpt_disk_guid,hdr+56,16);crc();
}
'''
post_stubs=r'''
static unsigned latches,selects;
static bool ufs_dali_deadline_after(uint64_t n,uint64_t *p){assert(n==90);*p=100;return true;}
static uint64_t mach_absolute_time(void){return 1;}
static int ufs_dali_select_log_slot(const uint8_t *p,uint64_t deadline,const char *id){assert(p && deadline==100 && id);++selects;return 0;}
static int ufs_pwm_failure(int e){assert(e);++latches;return e;}
static int post_flow(bool has_digest){int error=0;bool log_slot_optin=true,log_gpt_validated=true,log_digest_valid=has_digest;
 uint64_t log_scan_until=0;uint8_t log_pwm_digest[32]={0};char log_run_id[33]={0};
'''
tail=r'''
#undef printf
static void bad(void){assert(!ufs_dali_validate_log_gpt() && !su.log_nblocks && !su.log_first_block && !su.log_partition_ready);}
int main(void){
 assert(DALI_LOG_FIRST_BLOCK==64565240ull && DALI_LOG_FIRST_BLOCK+8191==64573431ull);
 setup();assert(!ufs_dali_validate_log_gpt() && su.log_nblocks==4096 && reads==2);
 setup();hdr[24]^=1;bad();
 setup();table[128+30]^=1;bad();
 setup();ent(1,"unixnu_logs",DALI_LOG_FIRST_BLOCK+1,DALI_LOG_FIRST_BLOCK+8192);crc();bad();
 setup();su.lun=1;bad();
 setup();su.log_first_block--;bad();
 setup();p64(hdr+24,2);crc();bad();
 setup();p32(hdr+8,0);crc();bad();
 setup();p32(hdr+20,1);crc();bad();
 setup();p64(hdr+32,su.capacity_blocks-2);crc();bad();
 setup();p64(hdr+72,3);crc();bad();
 setup();p32(hdr+80,0);crc();bad();
 setup();p32(hdr+84,256);crc();bad();
 setup();table[128+56+11*2]=0x80;crc();bad();
 setup();table[128+56+11*2+1]=1;crc();bad();
 setup();table[128+56+12*2]='x';crc();bad();
 setup();ent(2,"unixnu_logs",DALI_LOG_FIRST_BLOCK,DALI_LOG_FIRST_BLOCK+8191);crc();bad();
 setup();ent(2,"other",DALI_LOG_FIRST_BLOCK-1,DALI_LOG_FIRST_BLOCK);crc();bad();
 setup();ent(2,"other",DALI_LOG_FIRST_BLOCK+8191,DALI_LOG_FIRST_BLOCK+8192);crc();bad();
 setup();ent(2,"other",DALI_LOG_FIRST_BLOCK+8192,DALI_LOG_FIRST_BLOCK+8193);crc();assert(!ufs_dali_validate_log_gpt() && su.log_nblocks);
 /* A valid root overlapping any used entry must stop root publication. */
 setup();ent(2,"unrelated",1500,2500);crc();assert(ufs_dali_validate_log_gpt()==EPROTO && !su.log_nblocks);
 setup();ent(2,"unrelated",999,1000);crc();assert(ufs_dali_validate_log_gpt()==EPROTO && !su.log_nblocks);
 /* Overlap not involving root is only an optional-logger admission failure. */
 setup();ent(2,"first",3000,4000);ent(3,"second",4000,5000);crc();bad();
 /* A root overlap stays fatal even if the log name itself is missing. */
 setup();memset(table+128,0,128);ent(2,"other",1500,2500);crc();assert(ufs_dali_validate_log_gpt()==EPROTO);
 setup();ent(2,"bad",90,89);crc();bad();
 /* CRC-valid root or disk identity contradictions are fatal, not optional-log failures. */
 setup();ent(0,"unixnu_data",1001,2000);crc();assert(ufs_dali_validate_log_gpt()==EPROTO && !su.log_nblocks);
 setup();ent(0,"unixnu_data",1000,1999);crc();assert(ufs_dali_validate_log_gpt()==EPROTO && !su.log_nblocks);
 setup();table[0]^=2;crc();assert(ufs_dali_validate_log_gpt()==EPROTO && !su.log_nblocks);
 setup();table[16]^=2;crc();assert(ufs_dali_validate_log_gpt()==EPROTO && !su.log_nblocks);
 setup();hdr[56]^=2;crc();assert(ufs_dali_validate_log_gpt()==EPROTO && !su.log_nblocks);
 setup();su.nblocks--;assert(ufs_dali_validate_log_gpt()==EPROTO && !su.log_nblocks);
 setup();table[56+11*2]=0x80;crc();assert(ufs_dali_validate_log_gpt()==EPROTO && !su.log_nblocks);
 for(unsigned i=1;i<=2;i++){setup();fail_at=i;assert(ufs_dali_validate_log_gpt()==EIO && reads==i);}
 /* Real validator + real post-G4 control block: PWM valid, HS CRC invalid. */
 setup();latches=selects=0;assert(!ufs_dali_validate_log_gpt());table[128+30]^=1;
 assert(post_flow(true)==EIO && latches==1 && !selects && !su.log_partition_ready);
 setup();latches=selects=0;assert(!ufs_dali_validate_log_gpt());hdr[24]^=1;
 assert(post_flow(true)==EIO && latches==1 && !selects && !su.log_partition_ready);
 setup();latches=selects=0;assert(!ufs_dali_validate_log_gpt());ent(0,"unixnu_data",1000,1999);crc();
 assert(post_flow(true)==EPROTO && latches==1 && !selects && !su.log_partition_ready);
 setup();latches=selects=0;assert(!ufs_dali_validate_log_gpt());assert(!post_flow(true) && !latches && selects==1);
 /* An already validated GPT is reread even when no slot digest was produced. */
 setup();latches=selects=0;assert(!ufs_dali_validate_log_gpt());table[128+30]^=1;
 assert(post_flow(false)==EIO && latches==1 && !selects && !su.log_partition_ready);
 setup();latches=selects=0;assert(!ufs_dali_validate_log_gpt());
 assert(!post_flow(false) && !latches && !selects && !su.log_nblocks && !su.log_partition_ready);
 puts("PASS actual GPT CRC, root identity, overlaps and PWM-valid/HS-invalid fatal flow");return 0;
}
'''
start=src.index('if (!error && su.hsg4_root) {', src.index('hsg4-root-hs-read'))
brace=src.index('{',start);level=1;end=brace+1
while level:
 level+=(src[end]=='{')-(src[end]=='}');end+=1
post=src[start:end]
code=prefix+f(src,'ufs_gpt_name_exact')+f(src,'ufs_dali_validate_log_gpt')+post_stubs+post+'\nreturn error;}\n'+tail
with tempfile.TemporaryDirectory(prefix='sweetlog-gpt-') as t:
 p=Path(t);(p/'t.c').write_text(code)
 subprocess.run(['clang','-std=gnu11','-O1','-Wall','-Wextra','-Werror','-fsanitize=address,undefined','-fno-sanitize-recover=all',str(p/'t.c'),'-o',str(p/'t')],check=True)
 subprocess.run([str(p/'t')],check=True,timeout=30)
