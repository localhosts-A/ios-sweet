#!/usr/bin/env python3
"""Execute final0177 UFS functions, including real polling/blocking and DMA callers."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest
import test_dali_gui_load_progress as source

ROOT = Path(__file__).resolve().parents[1]
UFS = source.UFS
OLD = source.fixture.fragment('bsd/dev/sweet_ufs.c', source.PATCH)
FUNCTIONS = ('ufs_be32', 'ufs_scsi_on_lun_timeout', 'ufs_scsi',
             'ufs_query_current_power_mode', 'ufs_query_fdeviceinit_flag',
             'ufs_read_blocks', 'ufs_write_blocks', 'ufs_write_log_blocks',
             'ufs_sync_cache', 'sweet_ufs_strategy', 'sweet_ufs_ioctl', 'sweet_log_thread')
PREFIX = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>
#include <stdarg.h>
#include <setjmp.h>
#include <sys/types.h>
#ifdef bzero
#undef bzero
#endif
#define bzero(p,n) memset(p,0,n)
#define DEVELOPMENT 0
#ifdef __unused
#undef __unused
#endif
#define __unused __attribute__((unused))
#define MIN(a,b) ((a)<(b)?(a):(b))
#define NSEC_PER_USEC 1000ull
#define NSEC_PER_SEC 1000000000ull
#define ETIMEDOUT 60
#define EIO 5
#define EINVAL 22
#define EPERM 1
#define ENODEV 19
#define ENOTTY 25
#define EROFS 30
#define TRUE 1
#define B_READ 1
#define THREAD_UNINT 7
#define THREAD_WAITING 8
#define THREAD_CONTINUE_NULL 0
#define TIMEOUT_URGENCY_SYS_CRITICAL 1
#define PZERO 0
#define relaxed 0
#define os_atomic_load(p,o) (*(p))
#define LCK_GRP_DECLARE(n,s) int n
#define LCK_MTX_DECLARE(n,g) int n
#define DKIOCGETBLOCKSIZE 1
#define DKIOCSETBLOCKSIZE 2
#define DKIOCGETBLOCKCOUNT 3
#define DKIOCGETMAXBLOCKCOUNTREAD 4
#define DKIOCGETMAXBLOCKCOUNTWRITE 5
#define DKIOCISWRITABLE 6
#define DKIOCSYNCHRONIZECACHE 7
#define DKIOCGETFEATURES 8
#define DKIOCGETPHYSICALBLOCKSIZE 9
#define DKIOCSYNCHRONIZE 12
#define DKIOCGETMAXSEGMENTCOUNTREAD 10
#define DKIOCGETMAXSEGMENTCOUNTWRITE 11
#define DEV_BSIZE 512
#define DK_FEATURE_BARRIER 1
#define DK_FEATURE_PRIORITY 2
#define DK_FEATURE_FORCE_UNIT_ACCESS 4
#define __builtin_arm_dsb(x) barrier(x)
typedef uintptr_t vm_offset_t;
typedef uint64_t addr64_t;
typedef char *caddr_t;
typedef uintptr_t event_t;
typedef int wait_result_t;
typedef unsigned long u_long;
struct proc;
struct buffer {uint64_t block;uint32_t count,resid;int flags,error,mapfail;unsigned char data[3*(1u<<20)];};
typedef struct buffer *buf_t;
'''
MOCK = r'''
static unsigned char dma[DMA_SIZE],saved_dma[DMA_SIZE],sweet_log_ring[SWEET_LOG_SIZE];
static uint64_t sweet_log_total,now,complete_after,wake_at;
static unsigned sweet_log_secs=30;static int sweet_log_chan,hz=100;
static bool held,irq_on,in_irq,timeout_mode,early_wake,reject_wait;
static int preempt,wait_calls,block_calls,delay_calls,polls,commands,clear_calls,cache_calls;
static int unlocks,maps,unmaps,biodones,sleeps,fail_command,desync_command,timeout_command;
static bool ocs_error;
static unsigned operations[1024],operation_count;static jmp_buf logger_exit;
static uint64_t submitted_at;static unsigned char tag_at_submit;
static void event(unsigned n){assert(operation_count<1024);operations[operation_count++]=n;}
static int output(const char *fmt,...){(void)fmt;return 0;}
#define printf output
static void barrier(unsigned n){assert(n==15);event(10);}
static uint64_t mach_absolute_time(void){return now;}
static void nanoseconds_to_absolutetime(uint64_t n,uint64_t *a){*a=n/1000;}
static bool ml_get_interrupts_enabled(void){return irq_on;}
static bool ml_at_interrupt_context(void){return in_irq;}
static int get_preemption_level(void){return preempt;}
static void delay(unsigned n){++delay_calls;now+=n;}
static void dma_sync(uint32_t off,uint32_t len){assert(off+len<=DMA_SIZE);++cache_calls;event(20);}
static void dma_clean(uint32_t off,uint32_t len){assert(off+len<=DMA_SIZE);++cache_calls;event(21);}
static uint32_t ufs_rd(uint32_t reg){
 assert(held);if(reg==REG_IS)return 1;
 if(reg!=REG_UTRLDBR)return 0;
 ++polls;if(timeout_mode || commands==timeout_command || now-submitted_at<complete_after)return 1;
 struct utrd *t=(void *)(dma+DMA_UTRL);struct ucd *u=(void *)(dma+DMA_UCD);
 t->dw2=ocs_error?1:0;bzero(u->resp,sizeof(u->resp));u->resp[0]=u->cmd[0]==0x16?0x36:0x21;
 u->resp[2]=u->cmd[2];u->resp[3]=u->cmd[3];
 if(u->cmd[0]==0x16){u->resp[12]=u->cmd[12];u->resp[13]=u->cmd[13];u->resp[5]=u->cmd[5];u->resp[23]=0x11;if(u->cmd[13]==1)u->resp[23]=0;}
 if(commands==fail_command)u->resp[7]=2;
 if(commands==desync_command)++u->resp[3];
 if(u->cmd[0]!=0x16 && u->cmd[16]==0x28){uint32_t n=((uint32_t)u->cmd[12]<<24)|((uint32_t)u->cmd[13]<<16)|((uint32_t)u->cmd[14]<<8)|u->cmd[15];memset(dma+DMA_BOUNCE,0x5a,n);}
 event(30);return 0;
}
static void ufs_wr(uint32_t reg,uint32_t value){
 assert(held);if(reg==REG_UTRLDBR){assert(value==1);++commands;submitted_at=now;tag_at_submit=su.tag;struct ucd *u=(void *)(dma+DMA_UCD);event(u->cmd[0]==0x16?0x100+u->cmd[12]:u->cmd[16]);}
 else if(reg==REG_UTRLCLR){assert(value==~1u);++clear_calls;event(40);}
 else assert(reg==REG_IS);
}
static int assert_wait_deadline_with_leeway(event_t e,int interruptible,int urgency,uint64_t wake,int leeway){
 assert(held && e==(event_t)&su.wait && interruptible==THREAD_UNINT && urgency==TIMEOUT_URGENCY_SYS_CRITICAL && leeway==0);
 assert(su.wait && su.ready && irq_on && !in_irq && preempt==0 && now-submitted_at>=50);
 assert(wake>=now && wake-now<=200);wake_at=wake;++wait_calls;
 /* At exact deadline the unchanged driver may request an already-due timer. */
 if(wake==now){++now;return 0;}
 if(reject_wait){now=wake;return 0;}return THREAD_WAITING;
}
static void thread_block(int continuation){
 assert(held && continuation==THREAD_CONTINUE_NULL && su.tag==tag_at_submit);
 struct utrd t=*(struct utrd *)(dma+DMA_UTRL);struct ucd u=*(struct ucd *)(dma+DMA_UCD);
 unsigned char first=dma[DMA_BOUNCE],last=dma[DMA_SIZE-1];
 ++block_calls;now=early_wake?now+1:wake_at;
 assert(held && su.tag==tag_at_submit && !memcmp(&t,dma+DMA_UTRL,sizeof(t)) && !memcmp(&u,dma+DMA_UCD,sizeof(u)));
 assert(dma[DMA_BOUNCE]==first && dma[DMA_SIZE-1]==last);
}
static void lck_mtx_lock(int *l){assert(l==&sweet_ufs_lock && !held);held=true;event(1);}
static void lck_mtx_unlock(int *l){assert(l==&sweet_ufs_lock && held);held=false;++unlocks;event(2);}
static uint64_t buf_blkno(buf_t b){return b->block;}
static uint32_t buf_count(buf_t b){return b->count;}
static int buf_flags(buf_t b){return b->flags;}
static void buf_setresid(buf_t b,uint32_t n){b->resid=n;}
static int buf_map(buf_t b,caddr_t *p){++maps;*p=(char *)b->data;return b->mapfail;}
static void buf_unmap(buf_t b){assert(!held && b);++unmaps;}
static void buf_seterror(buf_t b,int e){b->error=e;}
static void buf_biodone(buf_t b){assert(!held && b);++biodones;}
static int tsleep(void *e,int priority,const char *name,int ticks){assert(!held && e==&sweet_log_chan && priority==PZERO && !strcmp(name,"sweetlog") && ticks==hz*30);if(++sleeps>1)longjmp(logger_exit,1);return 0;}
'''
TAIL = r'''
#undef printf
static struct buffer b;
static void keep(void){
 (void)&ufs_be32;(void)&ufs_scsi_on_lun_timeout;(void)&ufs_scsi;(void)&ufs_query_current_power_mode;
 (void)&ufs_query_fdeviceinit_flag;(void)&ufs_read_blocks;(void)&ufs_write_blocks;(void)&ufs_write_log_blocks;
 (void)&ufs_sync_cache;(void)&sweet_ufs_strategy;(void)&sweet_ufs_ioctl;(void)&sweet_log_thread;
 (void)&old_strategy;(void)&old_logger;(void)&sweet_ufs_grp;
}
static void reset(void){
 keep();memset(&su,0,sizeof(su));memset(dma,0xa5,sizeof(dma));memset(&b,0,sizeof(b));
 su.dali=su.ready=su.writable=su.log_partition_configured=su.log_partition_ready=true;
 su.dma=(uintptr_t)dma;su.dma_pa=0x100000000ull;su.lun=2;su.first_block=500;su.nblocks=4096;su.secsize=512;
 su.log_first_block=10000;su.log_nblocks=2048;strcpy(su.log_partition_name,"unixnu_logs");
 memset(sweet_log_ring,0x33,sizeof(sweet_log_ring));sweet_log_total=4096;
 now=complete_after=wake_at=submitted_at=0;held=in_irq=timeout_mode=early_wake=reject_wait=false;irq_on=true;preempt=0;
 wait_calls=block_calls=delay_calls=polls=commands=clear_calls=cache_calls=0;
 unlocks=maps=unmaps=biodones=sleeps=fail_command=desync_command=timeout_command=0;ocs_error=false;operation_count=0;
}
static void check_counters(void){assert(wait_calls>=block_calls && block_calls>=0 && delay_calls>=0 && polls>=commands && clear_calls<=commands && cache_calls>=0 && unmaps<=maps && biodones>=unmaps);}
static int command(uint64_t timeout){uint8_t c[10]={0x28};held=true;int e=ufs_scsi_on_lun_timeout(2,c,10,4096,false,timeout);held=false;return e;}
static void logger(bool old){if(!setjmp(logger_exit)){if(old)old_logger(NULL,0);else sweet_log_thread(NULL,0);}assert(!held);}
static void unchanged_after_poison(bool write,bool partial){
 memcpy(saved_dma,dma,sizeof(dma));b.block=partial?1:0;b.count=4096;b.flags=write?0:B_READ;memset(b.data,0x77,b.count);
 unsigned submitted=commands;sweet_ufs_strategy(&b);assert(b.error==ETIMEDOUT && b.resid==b.count && !held);
 assert(commands==(int)submitted && !memcmp(saved_dma,dma,sizeof(dma)) && unmaps==1 && biodones==1 && unlocks==1);
 memcpy(saved_dma,dma,sizeof(dma));int before=commands;logger(false);assert(commands==before && !memcmp(saved_dma,dma,sizeof(dma)) && sleeps==1);
 uint64_t ignored=0;assert(sweet_ufs_ioctl(0,DKIOCSYNCHRONIZECACHE,(char *)&ignored,0,NULL,0)==ETIMEDOUT);
 assert(commands==before && !memcmp(saved_dma,dma,sizeof(dma)));
}
'''


def fixture():
    constants = UFS[UFS.index('#define REG_CAP'):UFS.index('struct utrd {')]
    layout = UFS[UFS.index('struct utrd {'):UFS.index('#if DEVELOPMENT\n#include "SweetUFSLoadPolicy.h"')]
    log_header = UFS[UFS.index('struct sweet_log_header {'):UFS.index('\nstatic void\nsweet_log_thread')]
    policy = source.fixture.fragment('bsd/dev/SweetUFSCompletion.h', ROOT/'xnu/patches/0166-sweet-dali-validate-scsi-completions.patch')
    actual = ''.join(source.function(UFS, n) for n in FUNCTIONS)
    old = source.function(OLD, 'sweet_ufs_strategy').replace('sweet_ufs_strategy(', 'old_strategy(')
    old += source.function(OLD, 'sweet_log_thread').replace('sweet_log_thread(', 'old_logger(')
    return PREFIX+constants+layout+policy+MOCK+log_header+actual+old+TAIL


class UFSStartupTest(unittest.TestCase):
    def run_c(self, body):
        compiler = shutil.which('clang') or shutil.which('cc')
        self.assertIsNotNone(compiler)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            body = body.replace('int main(void){', 'int main(void){(void)command;(void)unchanged_after_poison;')
            c = root/'ufs.c'; c.write_text(fixture()+body.replace('puts("PASS");','check_counters();puts("PASS");'))
            subprocess.run([compiler, '-std=gnu11', '-O2', '-Wall', '-Wextra', '-Werror', str(c), '-o', str(root/'ufs')], check=True)
            p = subprocess.run([str(root/'ufs')], capture_output=True, text=True, timeout=30)
            self.assertEqual(p.returncode, 0, p.stdout+p.stderr)
            self.assertEqual(p.stdout.strip(), 'PASS')

    def test_final_source_and_only_two_guards(self):
        if os.environ.get('SWEET_LOAD_SOURCE_DIR'):
            self.assertEqual((Path(os.environ['SWEET_LOAD_SOURCE_DIR'])/'bsd/dev/sweet_ufs.c').read_text(encoding='latin-1'), UFS)
        self.assertEqual(source.function(UFS,'ufs_scsi_on_lun_timeout'), source.function(OLD,'ufs_scsi_on_lun_timeout'))
        for name in ('ufs_scsi','ufs_sync_cache','ufs_query_current_power_mode','ufs_query_fdeviceinit_flag'):
            self.assertEqual(source.function(UFS,name), source.function(OLD,name))
        strategy_guard = ('\t/* A poisoned command may still own DMA; do not touch its bounce buffer. */\n'
                          '\tif (su.dali && su.command_poisoned) {\n\t\terror = ETIMEDOUT;\n\t}\n')
        logger_guard = ('\t\t/* Do not overwrite DMA memory until hardware ownership is known. */\n'
                        '\t\tif (su.dali && su.command_poisoned) {\n\t\t\terror = ETIMEDOUT;\n\t\t}\n')
        self.assertEqual(UFS.count(strategy_guard),1); self.assertEqual(UFS.count(logger_guard),1)
        normalized=UFS.replace(strategy_guard,'').replace(logger_guard,'').replace('done < count && !error;','done < count;')
        self.assertEqual(normalized,OLD)

    def test_old_overwrite_negative_and_new_poison_isolation(self):
        self.run_c(r'''
int main(void){
 reset();su.command_poisoned=true;memset(b.data,0x77,4096);b.count=4096;memcpy(saved_dma,dma,sizeof(dma));old_strategy(&b);
 assert(b.error==ETIMEDOUT && memcmp(saved_dma,dma,sizeof(dma)) && commands==0);
 reset();su.command_poisoned=true;memcpy(saved_dma,dma,sizeof(dma));logger(true);assert(memcmp(saved_dma,dma,sizeof(dma)) && commands==0);
 for(unsigned write=0;write<2;++write)for(unsigned partial=0;partial<2;++partial){reset();su.command_poisoned=true;unchanged_after_poison(write,partial);}
 puts("PASS");return 0;
}
''')

    def test_real_timeout_and_desync_then_callers_remain_isolated(self):
        self.run_c(r'''
int main(void){
 for(unsigned kind=0;kind<4;++kind){
  reset();if(kind==0){timeout_mode=true;assert(command(50000)==ETIMEDOUT && clear_calls==1);}
  if(kind==1){desync_command=1;assert(command(50000)==EIO && !clear_calls);}
  if(kind==2){uint32_t value=0;held=true;timeout_mode=true;assert(ufs_query_current_power_mode(&value)==ETIMEDOUT);held=false;}
  if(kind==3){uint32_t value=0;held=true;timeout_mode=true;assert(ufs_query_fdeviceinit_flag(0x05,&value)==ETIMEDOUT);held=false;}
  assert(su.command_poisoned);unchanged_after_poison(true,false);
 }
 puts("PASS");return 0;
}
''')

    def test_real_wait_contexts_and_early_wake(self):
        self.run_c(r'''
int main(void){
 for(unsigned mode=0;mode<8;++mode){
  reset();su.wait=true;complete_after=600;
  if(mode==0)su.wait=false;if(mode==1)su.ready=false;if(mode==2)irq_on=false;if(mode==3)in_irq=true;if(mode==4)preempt=1;
  if(mode==5)early_wake=true;if(mode==6)reject_wait=true;
  assert(command(1000000)==0 && !su.command_poisoned && commands==1 && !clear_calls);
  if(mode<5)assert(wait_calls==0 && block_calls==0);else assert(wait_calls>0 && (mode==6?block_calls==0:block_calls==wait_calls));
 }
 reset();su.wait=true;assert(command(1000000)==0 && !wait_calls);
 reset();su.wait=true;complete_after=49;assert(command(1000000)==0 && !wait_calls);
 reset();su.wait=true;complete_after=51;assert(command(1000000)==0 && wait_calls==1 && block_calls==1 && now==250);
 reset();su.wait=true;timeout_mode=true;assert(command(51000)==ETIMEDOUT && su.command_poisoned && now==52);
 puts("PASS");return 0;
}
''')

    def test_real_strategy_success_partial_and_multiblock_error(self):
        self.run_c(r'''
int main(void){
 for(unsigned dali=0;dali<2;++dali)for(unsigned write=0;write<2;++write)for(unsigned partial=0;partial<2;++partial){
  reset();su.dali=dali;su.wait=true;complete_after=51;b.count=4096;b.block=partial?1:0;b.flags=write?0:B_READ;memset(b.data,0x77,4096);
  sweet_ufs_strategy(&b);assert(!b.error && !b.resid && maps==1 && unmaps==1 && biodones==1 && unlocks==1 && !held);
  assert(commands==(write && partial?2:1));if(!write)assert(b.data[0]==0x5a);
 }
 reset();b.count=BOUNCE_SIZE+4096;b.flags=B_READ;fail_command=2;sweet_ufs_strategy(&b);
 assert(b.error==EIO && b.resid==4096 && !su.command_poisoned && commands==2 && unmaps==1 && biodones==1);
 reset();su.dali=false;su.command_poisoned=true;b.count=4096;memset(b.data,0x77,4096);sweet_ufs_strategy(&b);assert(!b.error && !b.resid && commands==1);
 puts("PASS");return 0;
}
''')

    def test_strategy_error_priority_and_cleanup(self):
        self.run_c(r'''
int main(void){
 for(unsigned kind=0;kind<4;++kind){reset();su.command_poisoned=true;b.count=4096;
  if(kind==0)b.count=0;if(kind==1)su.writable=false;if(kind==2)b.block=UINT32_MAX;if(kind==3)b.mapfail=1;
  memcpy(saved_dma,dma,sizeof(dma));sweet_ufs_strategy(&b);assert(!memcmp(saved_dma,dma,sizeof(dma)) && !commands && biodones==1 && !held);
  assert(b.error==(kind==0?0:kind==1?EROFS:kind==2?EINVAL:EIO));assert(unmaps==0 && unlocks==0);
 }
 puts("PASS");return 0;
}
''')

    def test_completion_cache_and_descriptor_order(self):
        self.run_c(r'''
int main(void){
 reset();assert(command(100000)==0);unsigned expected[]={20,20,20,10,0x28,30,10,20,20,20};
 assert(operation_count==sizeof(expected)/sizeof(*expected) && !memcmp(operations,expected,sizeof(expected)));
 struct utrd *t=(void *)(dma+DMA_UTRL);struct ucd *u=(void *)(dma+DMA_UCD);
 assert(t->ucdba==DMA_UCD && t->ucdbau==1 && u->prdt[0].addr_lo==DMA_BOUNCE && u->prdt[0].addr_hi==1);
 assert(u->prdt[0].dbc==4095 && su.tag==1);
 reset();ocs_error=true;assert(command(100000)==EIO && !su.command_poisoned && !clear_calls);
 puts("PASS");return 0;
}
''')

    def test_mid_request_poison_and_logger_each_poison_stage(self):
        self.run_c(r'''
int main(void){
 for(unsigned write=0;write<2;++write){
  reset();b.count=BOUNCE_SIZE+4096;b.flags=write?0:B_READ;desync_command=2;
  sweet_ufs_strategy(&b);assert(b.error==EIO && b.resid==4096 && commands==2 && su.command_poisoned && !held);
  maps=unmaps=biodones=unlocks=0;unchanged_after_poison(true,false);
 }
 for(unsigned stage=1;stage<=4;++stage){
  reset();desync_command=stage;logger(false);assert(su.command_poisoned && commands==(int)stage && !held);
  sleeps=0;maps=unmaps=biodones=unlocks=0;unchanged_after_poison(true,false);
 }
 reset();su.wait=true;b.count=BOUNCE_SIZE+4096;timeout_command=2;
 sweet_ufs_strategy(&b);assert(b.error==ETIMEDOUT && b.resid==4096 && su.command_poisoned && clear_calls==1);
 maps=unmaps=biodones=unlocks=0;unchanged_after_poison(true,false);
 puts("PASS");return 0;
}
''')

    def test_logger_success_and_every_failure_stage(self):
        self.run_c(r'''
int main(void){
 for(unsigned failed=0;failed<=4;++failed){reset();fail_command=failed;logger(false);assert(!held && unlocks==1 && commands==(int)(failed?failed:4));
  unsigned cmds[4],n=0;for(unsigned j=0;j<operation_count;++j)if(operations[j]==0x2a || operations[j]==0x35)cmds[n++]=operations[j];
  assert(n==(failed?failed:4));assert(cmds[0]==0x2a);if(n>1)assert(cmds[1]==0x35);if(n>2)assert(cmds[2]==0x2a);if(n>3)assert(cmds[3]==0x35);
 }
 puts("PASS");return 0;
}
''')


if __name__ == '__main__':
    unittest.main()
