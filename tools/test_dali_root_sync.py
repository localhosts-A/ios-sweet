#!/usr/bin/env python3
"""Compile actual root-sync and ordinary-thread return-poll functions on mocks.
These tests are not a device durability or writer-quiescence proof.
"""
from pathlib import Path
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / 'xnu/patches/0199-dali-root-sync-before-fastboot.patch'
SOURCE = Path(sys.argv.pop()) if len(sys.argv) > 1 and not sys.argv[-1].startswith('-') else None


def functions_from_patch():
    text = PATCH.read_text()
    added = ''.join(line[1:] for line in text.splitlines(keepends=True) if line.startswith('+') and not line.startswith('+++'))
    return added


def function(text, name):
    match = re.search(r'(?m)^(?:static )?(?:int|void|bool)\n' + re.escape(name) + r'\(', text)
    if not match:
        raise AssertionError('Missing actual function ' + name)
    start = text.index('{', match.start())
    depth = 1
    end = start + 1
    while depth:
        depth += (text[end] == '{') - (text[end] == '}')
        end += 1
    return text[match.start():end] + '\n'


ADDED = functions_from_patch()
if SOURCE:
    VFS = (SOURCE / 'bsd/vfs/vfs_syscalls.c').read_text()
    NATIVE = (SOURCE / 'pexpert/arm/pe_dali_watchdog.c').read_text()
else:
    VFS = ADDED
    # Patch additions do not contain the complete changed poll function; replay
    # onto the exact baseline already reconstructed by the existing fixture.
    spec_name = str(ROOT / 'tools')
    sys.path.insert(0, spec_name)
    import test_dali_fastboot_return as previous
    NATIVE = previous.NATIVE

VFS_MOCK = r'''
#include <assert.h>
#include <stdint.h>
#include <stdbool.h>
#include <string.h>
#include <stdarg.h>
#include <stdio.h>
#define DEVELOPMENT 1
#define ARM64_BOARD_CONFIG_SWEET 1
#define MFSNAMELEN 16
#define MNT_ROOTFS 1
#define MNT_RDONLY 2
#define MNT_ASYNC 4
#define MNT_WAIT 1
#define LK_NOWAIT 1
#define VFS_RETURNED 0
#define VFS_RETURNED_DONE 1
#define ENOTSUP 45
#define ENOENT 2
struct mount {unsigned flags;const char *name;int busy_error,sync_error;};
typedef struct mount *mount_t;
static struct mount mounts[3];
static unsigned mount_count,iterrefs,busy,syncs,unbusies,reports;
static int iteration_error;
static int fixture_printf(const char *format,...){assert(format);++reports;return 0;}
#define printf fixture_printf
static unsigned vfs_flags(mount_t mp){assert(iterrefs);return mp->flags;}
static int vfs_busy(mount_t mp,int flags){assert(iterrefs && flags==LK_NOWAIT && !busy);if(!mp->busy_error)++busy;return mp->busy_error;}
static void vfs_unbusy(mount_t mp){(void)mp;assert(busy==1);--busy;++unbusies;}
static void vfs_name(mount_t mp,char *out){assert(busy);strcpy(out,mp->name);}
static void *vfs_context_kernel(void){assert(busy);return (void*)1;}
static int VFS_SYNC(mount_t mp,int wait,void *ctx){assert(busy && wait==MNT_WAIT && ctx==(void*)1);++syncs;return mp->sync_error;}
static int vfs_iterate(int flags,int(*fn)(mount_t,void*),void *ctx){assert(flags==0 && !iterrefs && !busy);if(iteration_error)return iteration_error;for(unsigned i=0;i<mount_count;i++){++iterrefs;int result=fn(&mounts[i],ctx);--iterrefs;if(result==VFS_RETURNED_DONE)break;assert(result==VFS_RETURNED);}assert(!busy);return 0;}
static void reset(void){memset(mounts,0,sizeof mounts);mount_count=1;mounts[0]=(struct mount){MNT_ROOTFS,"hfs",0,0};iterrefs=busy=syncs=unbusies=reports=0;iteration_error=0;}
'''
NATIVE_MOCK = r'''
#include <assert.h>
#include <stdint.h>
#include <stdbool.h>
#include <stdarg.h>
#include <stdio.h>
#define DEVELOPMENT 1
struct dali_fb_state {uint32_t phase,original,mode,length,after,lease;uint64_t started,frequency;};
struct dali_fb_ops {int unused;};
static struct dali_fb_state dali_fastboot_state;
static struct dali_fb_ops dali_fastboot_ops;
static uint32_t dali_fastboot_ready,dali_fastboot_sync_claimed;
static bool interrupts,in_irq;static int preemption;
static uint64_t now;static unsigned sync_calls,step_calls,order,last_sync_order,last_step_order;
static int fixture_printf(const char *fmt,...){assert(fmt);return 0;}
#define printf fixture_printf
#define os_atomic_load(p,o) (*(p))
static bool fixture_cas(uint32_t*p,uint32_t a,uint32_t b){if(*p!=a)return false;*p=b;return true;}
#define os_atomic_cmpxchg(p,a,b,o) fixture_cas(p,a,b)
static bool ml_get_interrupts_enabled(void){return interrupts;}
static bool ml_at_interrupt_context(void){return in_irq;}
static int get_preemption_level(void){return preemption;}
static uint64_t mach_absolute_time(void){return now;}
static int sync_result;
static int sweet_fastboot_sync_root(void){++sync_calls;last_sync_order=++order;now+=100;return sync_result;}
static bool dali_fb_step(struct dali_fb_state*s,const struct dali_fb_ops*ops){assert(s==&dali_fastboot_state && ops==&dali_fastboot_ops);++step_calls;last_step_order=++order;return false;}
static void reset(void){dali_fastboot_state=(struct dali_fb_state){.phase=1,.after=2280,.lease=2400,.frequency=13000000,.started=1000};dali_fastboot_ready=1;dali_fastboot_sync_claimed=0;interrupts=true;in_irq=false;preemption=0;now=1000;sync_calls=step_calls=order=last_sync_order=last_step_order=0;sync_result=0;}
'''


class RootSyncTests(unittest.TestCase):
    def execute(self, text):
        compiler = shutil.which('clang') or shutil.which('cc')
        self.assertIsNotNone(compiler)
        with tempfile.TemporaryDirectory(prefix='dali-root-sync-') as temp:
            source = Path(temp) / 'fixture.c'
            source.write_text(text)
            compiled = subprocess.run([compiler,'-std=c11','-O1','-g','-Wall','-Wextra','-Werror','-fsanitize=address,undefined',str(source),'-o',str(Path(temp)/'fixture')],capture_output=True,text=True,timeout=45)
            self.assertEqual(compiled.returncode,0,compiled.stdout+compiled.stderr)
            result = subprocess.run([str(Path(temp)/'fixture')],capture_output=True,text=True,timeout=20)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            self.assertEqual(result.stdout.strip(),'PASS')

    def test_actual_mount_callback_lifetime_scope_and_errors(self):
        for name in ('sweet_root_sync_callback', 'sweet_fastboot_sync_root'):
            self.assertEqual(function(VFS, name), function(ADDED, name))
        struct = re.search(r'struct sweet_root_sync_pass \{.*?\};',VFS,re.S).group(0)
        self.execute(VFS_MOCK+struct+'\n'+function(VFS,'sweet_root_sync_callback')+function(VFS,'sweet_fastboot_sync_root')+r'''
int main(void){
 reset();assert(!sweet_fastboot_sync_root());assert(syncs==1&&unbusies==1&&!busy&&!iterrefs&&reports==1);
 reset();mount_count=0;assert(sweet_fastboot_sync_root()==ENOENT&&!syncs&&!unbusies);
 reset();mounts[0].flags=0;assert(sweet_fastboot_sync_root()==ENOENT&&!syncs&&!unbusies);
 reset();mounts[0].name="apfs";assert(sweet_fastboot_sync_root()==ENOTSUP&&!syncs&&unbusies==1);
 reset();mounts[0].flags|=MNT_RDONLY;assert(sweet_fastboot_sync_root()==ENOTSUP&&!syncs&&unbusies==1);
 reset();mounts[0].flags|=MNT_ASYNC;assert(sweet_fastboot_sync_root()==ENOTSUP&&!syncs&&unbusies==1);
 reset();mounts[0].busy_error=16;assert(sweet_fastboot_sync_root()==16&&!syncs&&!unbusies);
 reset();mounts[0].sync_error=5;assert(sweet_fastboot_sync_root()==5&&syncs==1&&unbusies==1);
 reset();iteration_error=12;assert(sweet_fastboot_sync_root()==12&&!syncs&&!unbusies);
 reset();mount_count=2;mounts[1]=(struct mount){MNT_ROOTFS,"hfs",0,0};assert(!sweet_fastboot_sync_root()&&syncs==1);
 puts("PASS");}
''')

    def test_actual_poll_opt_in_context_once_and_before_return(self):
        self.execute(NATIVE_MOCK+function(NATIVE,'dali_fastboot_return_poll')+r'''
int main(void){
 reset();dali_fastboot_ready=0;now+=2280ULL*13000000;dali_fastboot_return_poll();assert(!sync_calls&&!step_calls);
 reset();interrupts=false;dali_fastboot_return_poll();assert(!sync_calls&&!step_calls);
 reset();in_irq=true;dali_fastboot_return_poll();assert(!sync_calls&&!step_calls);
 reset();preemption=1;dali_fastboot_return_poll();assert(!sync_calls&&!step_calls);
 reset();now+=2099ULL*13000000;dali_fastboot_return_poll();assert(!sync_calls&&step_calls==1);
 now+=13000000;dali_fastboot_return_poll();assert(sync_calls==1&&last_sync_order<last_step_order);
 for(int i=0;i<40;i++){now+=13000000;dali_fastboot_return_poll();}assert(sync_calls==1&&step_calls==42);
 reset();sync_result=5;now+=2100ULL*13000000;dali_fastboot_return_poll();assert(sync_calls==1&&step_calls==1);
 reset();now=999;dali_fastboot_return_poll();assert(!sync_calls&&step_calls==1);
 reset();now+=2390ULL*13000000;dali_fastboot_return_poll();assert(!sync_calls&&step_calls==1);
 reset();dali_fastboot_state.phase=5;now+=2200ULL*13000000;dali_fastboot_return_poll();assert(!sync_calls&&step_calls==1);
 reset();dali_fastboot_state.after=120;dali_fastboot_state.lease=180;now+=60ULL*13000000;dali_fastboot_return_poll();assert(sync_calls==1&&step_calls==1);
 puts("PASS");}
''')

    def test_final_sync_precedes_original_logger_drain_and_keeps_result(self):
        leaf = r'''
static unsigned drain_calls,drain_order;static bool drain_result;
static bool sweet_log_fastboot_drain(uint64_t deadline){assert(now<deadline);++drain_calls;drain_order=++order;return drain_result;}
'''
        self.execute(NATIVE_MOCK+leaf+function(NATIVE,'dali_fastboot_return_poll')+function(NATIVE,'dali_fastboot_drain')+r'''
int main(void){
 reset();drain_result=true;drain_calls=0;assert(dali_fastboot_drain(0,now+10000));assert(sync_calls==1&&drain_calls==1&&last_sync_order<drain_order);
 reset();drain_calls=0;assert(!dali_fastboot_drain(0,now));assert(!sync_calls&&!drain_calls);
 reset();sync_result=5;drain_calls=0;drain_result=true;assert(dali_fastboot_drain(0,now+10000)&&sync_calls==1&&drain_calls==1);
 reset();drain_calls=0;drain_result=false;assert(!dali_fastboot_drain(0,now+10000)&&sync_calls==1&&drain_calls==1);
 puts("PASS");}
''')


if __name__ == '__main__':
    unittest.main()
