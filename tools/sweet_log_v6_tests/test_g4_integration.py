#!/usr/bin/env python3
"""Run the existing actual-G4 integration suite against the isolated logger source."""
from pathlib import Path
import os
import subprocess
import sys
import tempfile

D = Path(__file__).resolve().parents[1]
P = D.parent/'ufs-hsg4-root-20261104-v1'
original = (P/'tests/test_integration.py').read_text()
old = "text = (D / 'xnu/bsd/dev/sweet_ufs.c').read_text(encoding='latin-1')"
new = f"text = Path({str(D/'xnu/bsd/dev/sweet_ufs.c')!r}).read_text(encoding='latin-1')"
assert original.count(old) == 1
# Keep D rooted in the baseline fixture tree, change only the production source under test.
original = original.replace("D = Path(__file__).resolve().parents[1]", f'D = Path({str(P)!r})', 1)
original = original.replace(old, new, 1)
# The unchanged allocation fixture now calls the real extracted deadline helper.
marker_alloc = "alloc_tests = r'''"
assert original.count(marker_alloc) == 1
original = original.replace(marker_alloc, """alloc_prefix = rep(alloc_prefix, 'static int alloc_flow(void){',
    '#define DALI_ROOT_COMPARE_TIMEOUT_NS (90ull*NSEC_PER_SEC)\\nstatic bool log_slot_optin;\\n' +
    f(text,'ufs_dali_deadline_after') + '\\nstatic int alloc_flow(void){')
alloc_tests = r'''""", 1)
# Extend the existing flow double with logger-selection controls. Execute the
# candidate's real pre-root block, rather than suppressing the new call.
old_flow = "flow_prefix = constants['FLOW_PREFIX']"
new_flow = """flow_prefix = constants['FLOW_PREFIX']
flow_prefix = flow_prefix.replace('const char *failure_stage;}su;', 'const char *failure_stage;unsigned long long log_nblocks,log_first_block;bool log_partition_ready;}su;', 1)
flow_prefix = flow_prefix.replace('static int ufs_dali_speed_read_pass(', '''
static bool log_slot_optin,log_gpt_validated,log_digest_valid;static char log_run_id[33];
static uint64_t fake_now,compare_deadline,scan_elapsed,log_scan_until;static uint8_t log_pwm_digest[32];
static int ufs_dali_speed_read_pass(''', 1)
flow_prefix = flow_prefix.replace('until==900 && !su.ready', 'until==(log_slot_optin?compare_deadline:900) && !su.ready', 1)
flow_prefix = flow_prefix.replace('return fail==2?EIO:0;', 'return fail==2?EIO:fail==14?EPROTO:0;', 1)
flow_prefix = flow_prefix.replace('static int flow(void){', '''
#define SWEET_LOG_BLOCKS 1025u
#define DALI_LOG_SCAN_TIMEOUT_NS 90u
#define DALI_ROOT_COMPARE_TIMEOUT_NS 90u
static unsigned slot_select_calls,slot_select_error,digest_calls,gpt_calls,gpt_bad_second,gpt_first_invalid,gpt_first_error,candidate_error,candidate_unavailable,sticky;
static bool ufs_dali_deadline_after(uint64_t ns,uint64_t *p){*p=fake_now+ns;if(!su.hsg1_active)compare_deadline=*p;return true;}
static uint64_t mach_absolute_time(void){return fake_now;}
static int ufs_pwm_reentry_error(void){return sticky;}
static int ufs_dali_validate_log_gpt(void){assert(held && !su.ready);if(su.hsg1_active)assert(su.hsg4_root_verified);++gpt_calls;assert(gpt_calls);
 if(gpt_calls==1 && gpt_first_error)return gpt_first_error;
 if((gpt_calls==1 && gpt_first_invalid)||(gpt_calls==2 && gpt_bad_second))su.log_nblocks=0;return 0;}
static int ufs_dali_log_candidate_present(bool *maybe,uint64_t deadline,const char *id){assert(held && fake_now<deadline && id==log_run_id);*maybe=!candidate_unavailable;return candidate_error;}
static int ufs_dali_log_pwm_digest(uint8_t *p,uint64_t deadline){assert(p==log_pwm_digest && held && !su.hsg1_active);++digest_calls;assert(digest_calls);fake_now+=scan_elapsed;if(fake_now>=deadline)return ETIMEDOUT;return 0;}
static int ufs_dali_select_log_slot(const uint8_t *p,uint64_t deadline,const char *id){assert(p==log_pwm_digest && id==log_run_id && fake_now<deadline && held && su.hsg1_active && su.hsg4_root_verified && !su.ready);++slot_select_calls;assert(slot_select_calls);return slot_select_error;}
static int flow(void){log_gpt_validated=false;log_digest_valid=false;''', 1)
"""
assert original.count(old_flow) == 1
original = original.replace(old_flow, new_flow, 1)
# Registration double obtains all of flow_prefix; append fields without dropping log_nblocks.
original = original.replace("'const char *failure_stage;}su;',\n                   'const char *failure_stage;unsigned long long nblocks,first_block,dma_pa;unsigned lun;int bmajor,cmajor,bdev;}su;')",
                            "'const char *failure_stage;unsigned long long log_nblocks,log_first_block;bool log_partition_ready;}su;',\n                   'const char *failure_stage;unsigned long long log_nblocks,log_first_block,nblocks,first_block,dma_pa;bool log_partition_ready;unsigned lun;int bmajor,cmajor,bdev;}su;')", 1)
# Test actual selection gate for off/on/read-error and its effect on root publication.
marker = ' puts("PASS actual normal init tail: verified G4 precedes BSD nodes and ready/log threads; failures prevent publication");return 0;'
insert = '''
 reset(1);su.log_nblocks=SWEET_LOG_BLOCKS;log_slot_optin=false;slot_select_calls=slot_select_error=digest_calls=gpt_calls=0;
 assert(!flow() && su.ready && !slot_select_calls && !digest_calls && !gpt_calls);
 /* EPROTO from real discovery's opt-in overlap guard stops actual init flow. */
 reset(1);su.log_nblocks=SWEET_LOG_BLOCKS;log_slot_optin=true;fail=14;
 assert(flow()==EPROTO && !su.ready && !badds && !logs && !shorts && !held);
 /* A slow 80/90s PWM log scan is followed by a fresh 90s comparison budget. */
 reset(1);su.log_nblocks=SWEET_LOG_BLOCKS;log_slot_optin=true;slot_select_calls=slot_select_error=digest_calls=gpt_calls=0;
 fake_now=0;scan_elapsed=80;assert(!flow() && su.ready && slot_select_calls==1 && digest_calls==1 && gpt_calls==2);
 assert(compare_deadline==170 && shorts==2);
 /* An expired scan itself stops before any speed comparison/root publication. */
 reset(1);su.log_nblocks=SWEET_LOG_BLOCKS;slot_select_calls=slot_select_error=digest_calls=gpt_calls=0;
 fake_now=0;scan_elapsed=90;assert(flow()==ETIMEDOUT && !su.ready && !badds && !logs && !shorts && !held);
 /* A completed nonpoisoned optional PWM EIO disables logging, not root. */
 reset(1);su.log_nblocks=SWEET_LOG_BLOCKS;slot_select_calls=slot_select_error=digest_calls=gpt_calls=0;
 fake_now=scan_elapsed=0;gpt_first_error=EIO;assert(!flow() && su.ready && !slot_select_calls && !su.log_nblocks);
 reset(1);su.log_nblocks=SWEET_LOG_BLOCKS;slot_select_calls=slot_select_error=digest_calls=gpt_calls=0;
 gpt_first_error=0;candidate_error=EIO;assert(!flow() && su.ready && !slot_select_calls && !su.log_nblocks && gpt_calls==2);candidate_error=0;
 /* Full/unknown/reused slot disables logger but cannot skip HS GPT recheck. */
 reset(1);su.log_nblocks=SWEET_LOG_BLOCKS;slot_select_calls=slot_select_error=digest_calls=gpt_calls=0;candidate_unavailable=1;
 assert(!flow() && su.ready && !slot_select_calls && !digest_calls && gpt_calls==2 && !su.log_nblocks);
 reset(1);su.log_nblocks=SWEET_LOG_BLOCKS;slot_select_calls=slot_select_error=digest_calls=gpt_calls=0;gpt_bad_second=1;
 assert(flow()==EIO && !su.ready && !badds && !logs && !slot_select_calls && !digest_calls && gpt_calls==2 && latches==1);
 candidate_unavailable=gpt_bad_second=0;
 /* Even completed nonpoisoned precheck EIO preserves the validated GPT gate. */
 reset(1);su.log_nblocks=SWEET_LOG_BLOCKS;slot_select_calls=slot_select_error=digest_calls=gpt_calls=0;candidate_error=EIO;gpt_bad_second=1;
 assert(flow()==EIO && !su.ready && !slot_select_calls && !digest_calls && gpt_calls==2 && latches==1);candidate_error=gpt_bad_second=0;
 reset(1);su.log_nblocks=SWEET_LOG_BLOCKS;slot_select_calls=slot_select_error=digest_calls=gpt_calls=0;
 gpt_first_invalid=1;assert(!flow() && su.ready && !slot_select_calls && !su.log_nblocks);gpt_first_invalid=0;gpt_first_error=EIO;
 /* Poisoned, sticky, or protocol-contradictory errors remain fatal. */
 reset(1);su.log_nblocks=SWEET_LOG_BLOCKS;su.command_poisoned=true;slot_select_calls=slot_select_error=digest_calls=gpt_calls=0;
 assert(flow()==EIO && !su.ready && !badds && !logs && !held);
 reset(1);su.log_nblocks=SWEET_LOG_BLOCKS;sticky=EIO;slot_select_calls=slot_select_error=digest_calls=gpt_calls=0;
 assert(flow()==EIO && !su.ready && !badds && !logs && !held);sticky=0;
 reset(1);su.log_nblocks=SWEET_LOG_BLOCKS;gpt_first_error=EPROTO;slot_select_calls=slot_select_error=digest_calls=gpt_calls=0;
 assert(flow()==EPROTO && !su.ready && !badds && !logs && !held);gpt_first_error=0;
 /* PWM-valid, HS-invalid GPT is fatal before slot selection or root publication. */
 reset(1);su.log_nblocks=SWEET_LOG_BLOCKS;gpt_bad_second=1;slot_select_calls=slot_select_error=digest_calls=gpt_calls=0;
 assert(flow()==EIO && !su.ready && !badds && !logs && !slot_select_calls && latches==1 && !held);gpt_bad_second=0;
 reset(1);su.log_nblocks=SWEET_LOG_BLOCKS;slot_select_calls=0;slot_select_error=EIO;digest_calls=gpt_calls=0;
 assert(flow()==EIO && !su.ready && !badds && !logs && slot_select_calls==1 && !held);
 log_slot_optin=false;slot_select_calls=slot_select_error=digest_calls=gpt_calls=0;
'''
assert original.count(marker) == 1
original = original.replace(marker, insert + marker, 1)
with tempfile.TemporaryDirectory(prefix='sweetlog-integration-') as t:
    script=Path(t)/'test.py'
    script.write_text(original)
    subprocess.run([sys.executable, '-I', str(script)], check=True, timeout=120)
