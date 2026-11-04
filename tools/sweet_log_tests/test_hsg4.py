#!/usr/bin/env python3
"""Run candidate's actual UIC/PMC/HSG4 functions with simulated registers.

The model deliberately does not simulate a PHY, electrical margins or firmware.
"""
from pathlib import Path
import difflib
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True
D = Path(__file__).resolve().parents[1]
P = D.parents[1]
repo = D / 'fixtures/legacy'
sys.path.insert(0, str(repo / 'tools'))
import test_dali_ufs_pwm as base

text = (D / 'xnu/bsd/dev/sweet_ufs.c').read_text(encoding='latin-1')
header = (D / 'xnu/bsd/dev/SweetUFSHSG1.h').read_text(encoding='latin-1')
original = P / '.tmp/ufs-hsg4-diag-20261104/xnu/bsd/dev'
old_header = (original / 'SweetUFSHSG1.h').read_text(encoding='latin-1')
f = base.source.function
c = base.fixture()
# The common MMIO/UIC/failure primitives and register constants in the inherited
# fixture must be byte-identical to this candidate, not just similarly named.
for name in ('ufs_mtk_log_uic_error', 'ufs_mtk_ack_uic', 'ufs_mtk_link_reset_event',
             'ufs_mtk_uic', 'ufs_pwm_latch', 'ufs_pwm_failure', 'ufs_pwm_reentry_error',
             'ufs_pwm_root_enter', 'ufs_pwm_snapshot'):
    assert f(base.UFS, name) == f(text, name), name
def constants(s):
    block = s[s.index('#define REG_CAP'):s.index('struct utrd {')]
    # Slot-v6 adds independent logger bounds here; compare the shared UFSHCI
    # register constants without hiding or normalizing any other lines.
    return ''.join(line for line in block.splitlines(keepends=True)
                   if not line.startswith('#define DALI_LOG_') and
                   not line.startswith('#define DALI_ROOT_COMPARE_TIMEOUT_NS') and
                   not line.startswith('/* On the G4 root path only, preserve the older logs') and
                   not line.startswith('/* Independently observed Dali LUN 2 unixnu_logs GPT extent'))
if constants(base.UFS) != constants(text):
    print(''.join(difflib.unified_diff(constants(base.UFS).splitlines(True),
                                       constants(text).splitlines(True),
                                       fromfile='0206-fixture', tofile='trial-source')))
    raise AssertionError('register constants changed outside logger-specific definitions')
assert (D/'xnu/bsd/dev/SweetUFSPWMPolicy.h').read_text() == base.POLICY
assert (D/'xnu/bsd/dev/SweetUFSPWMClock.h').read_text() == base.source.SOURCE['bsd/dev/SweetUFSPWMClock.h']

def swap(old, new):
    global c
    assert c.count(old) == 1, old[:100]
    c = c.replace(old, new, 1)

# Compile the actual probe state declaration and actual updated PMC functions.
diag_start = text.index('/* HSG4_DIAG_BEGIN')
diag_end = text.index('/* HSG4_DIAG_END */', diag_start) + len('/* HSG4_DIAG_END */')
swap('static uint32_t sweet_ufs_pwm_terminal,sweet_ufs_pwm_root_control;',
     'static uint32_t sweet_ufs_pwm_terminal,sweet_ufs_pwm_root_control;\n' + text[diag_start:diag_end])
swap('bool dali,ready,command_poisoned;', 'bool dali,ready,command_poisoned,hsg4_requested;')

for name in ('ufs_pwm_idle', 'ufs_pwm_get', 'ufs_pwm_set', 'ufs_pwm_pmc', 'ufs_pwm_root_leave'):
    old = f(base.source.UFS_PRE_SPEED, name)
    new = f(text, name)
    if name == 'ufs_pwm_root_leave':
        new = 'static void ufs_hsg4_report(int);\n' + new
    if name == 'ufs_pwm_idle':
        new = (f(text, 'ufs_pwm_idle_expect') + new +
               f(text, 'ufs_pwm_observe_error') + f(text, 'ufs_pwm_observe_guard') +
               f(text, 'ufs_pwm_pmc_observe'))
    if name == 'ufs_pwm_set':
        new += f(text, 'ufs_pwm_peer_set')
    swap(old, new)

swap('static uint32_t regs[4096],attrs[65536],peer_max;',
     '''static uint32_t regs[4096],attrs[65536],peer_max,peerattrs[65536],pending[65536];
static bool staged[65536],mutate_peer;
static unsigned corrupt_command,suppress_command,bad_pmc_ack,mutate_pmc,mutate_attr;
/* This active/pending split is an explicit mock model, not a hardware claim. */
static bool stage_attr(uint32_t a){
 switch(a){case 0x1583:case 0x1580:case 0x1584:case 0x1568:
 case 0x1560:case 0x1569:case 0x156a:case 0x15b0:case 0x15b1:case 0x15b2:
 case 0x15b3:case 0x15b4:case 0x15b5:case 0xd041:case 0xd042:case 0xd043:return true;
 default:return false;}
}''')
swap('trace_attr[64],trace_value[64],trace_op[64]',
     'trace_attr[1024],trace_value[1024],trace_op[1024]')
swap('assert(commands<=64);', 'assert(commands<=1024);')
swap('if(v==2 && attr==0x1571){++pmcs;pmc_active=true;if(pmc_ack_stuck)ack_stuck=true;return;}',
     'if(v==2 && attr==0x1571){++pmcs;pmc_active=true;if(commands==fail_command)command_result=1;if(pmc_ack_stuck || bad_pmc_ack==pmcs)ack_stuck=true;return;}')
swap('uint32_t val=v==3?peer_max:attrs[attr];',
     'uint32_t val=v==3?peerattrs[attr]:attrs[attr];if(commands==corrupt_command)val^=1;')
swap('}else{assert(v==2);++sets;attrs[attr]=regs[0x9c/4];}',
     '''}else{assert(v==2 || v==4);++sets;if(commands!=suppress_command){
       if(v==4)peerattrs[attr]=regs[0x9c/4];
       else if(stage_attr(attr)){pending[attr]=regs[0x9c/4];staged[attr]=true;}
       else attrs[attr]=regs[0x9c/4];}}''')
swap('attrs[0x1571]=regs[0x9c/4];pmc_active=false;pmc_completed=true;',
     '''attrs[0x1571]=regs[0x9c/4];pmc_active=false;pmc_completed=true;
       for(unsigned i=0;i<65536;i++)if(staged[i]){attrs[i]=pending[i];staged[i]=false;}
       peerattrs[0x1571]=attrs[0x1571];peerattrs[0x1583]=attrs[0x1568];
       peerattrs[0x1568]=attrs[0x1583];peerattrs[0x1580]=attrs[0x1560];
       peerattrs[0x1560]=attrs[0x1580];peerattrs[0x1584]=attrs[0x1569];
       peerattrs[0x1569]=attrs[0x1584];peerattrs[0x156a]=attrs[0x156a];
       if(pmcs==mutate_pmc){assert(mutate_attr<65536);if(mutate_peer)peerattrs[mutate_attr]=1;else attrs[mutate_attr]=1;}''')
swap('memset(regs,0,sizeof(regs));memset(attrs,0,sizeof(attrs));',
     '''memset(regs,0,sizeof(regs));memset(attrs,0,sizeof(attrs));memset(peerattrs,0,sizeof(peerattrs));
     memset(pending,0,sizeof(pending));memset(staged,0,sizeof(staged));''')

platform = r'''
#define ENOTSUP 45
#include "SweetUFSHSFloor.h"
static unsigned adopt_calls,validate_calls,fail_validate,floor_reads,floor_clocks;
static unsigned floor_at_commands[1024],floor_at_pmcs[1024];
static int adopt_error;
static bool floor_clock_bad;
static uint32_t floor_reg[0x1000/4];
static struct {struct hsf_state floor;bool started;int failure;} dali_hs_platform;
#define floor_state dali_hs_platform.floor
static int floor_read(void *unused,uint32_t offset,uint32_t *out){
 (void)unused;assert(!(offset&3) && offset<0x1000);++floor_reads;*out=floor_reg[offset/4];return 0;
}
static int floor_clock(void *unused){(void)unused;++floor_clocks;return floor_clock_bad;}
/* Adopt is a test double; subsequent checks execute actual production hsf_validate. */
static int ufs_dali_hs_platform_adopt(void){
 ++adopt_calls;if(adopt_error)return adopt_error;dali_hs_platform.started=true;
 floor_state.phase=HSF_ACTIVE;floor_state.opp_count=6;floor_state.request=2;
 floor_state.completed=2;floor_state.calibrated[2]=650000;
 floor_state.ops.read=floor_read;floor_state.ops.clock_ok=floor_clock;return 0;
}
/* PRODUCTION_PLATFORM_VALIDATE */
static int ufs_dali_hs_platform_validate(void){
 assert(validate_calls<1024);floor_at_commands[validate_calls]=commands;floor_at_pmcs[validate_calls]=pmcs;
 ++validate_calls;if(validate_calls==fail_validate)floor_clock_bad=true;
 return ufs_real_hs_platform_validate();
}
static void hs_reset(void){
 reset();su.hsg4_requested=true;sweet_hsg4_diag=(struct sweet_hsg4_diagnostic){0};
 adopt_calls=validate_calls=fail_validate=floor_reads=floor_clocks=0;adopt_error=0;
 memset(&dali_hs_platform,0,sizeof(dali_hs_platform));memset(floor_reg,0,sizeof(floor_reg));
 floor_reg[HSF_REQUEST/4]=2u<<4;floor_reg[HSF_GEAR/4]=2u<<8;floor_clock_bad=false;
 corrupt_command=suppress_command=bad_pmc_ack=mutate_pmc=mutate_attr=0;mutate_peer=false;
 attrs[0x1581]=attrs[0x1561]=peerattrs[0x1581]=peerattrs[0x1561]=2;
 peerattrs[0x1571]=0x55;peerattrs[0x1583]=peerattrs[0x1568]=1;
 peerattrs[0x1580]=peerattrs[0x1560]=1;
 attrs[0x1520]=attrs[0x1540]=peerattrs[0x1520]=peerattrs[0x1540]=2;
 attrs[0x1587]=peerattrs[0x1587]=5;attrs[0x15a9]=peerattrs[0x15a9]=6;
 attrs[0x156a]=peerattrs[0x156a]=1;
 attrs[0x1554]=0x60;peerattrs[0x15d6]=0x50;
}
'''
platform_source = (D / 'xnu/bsd/dev/SweetUFSHSPlatform.h').read_text(encoding='latin-1')
actual_validate = f(platform_source, 'ufs_dali_hs_platform_validate')
assert platform.count('/* PRODUCTION_PLATFORM_VALIDATE */') == 1
platform = platform.replace('/* PRODUCTION_PLATFORM_VALIDATE */',
    '#define ufs_dali_hs_platform_validate ufs_real_hs_platform_validate\n' + actual_validate +
    '\n#undef ufs_dali_hs_platform_validate')
tests = r'''
static unsigned find_trace(const uint32_t *ops,const uint32_t *attrs,unsigned begin,unsigned end,
                           uint32_t op,uint32_t attr){
 for(unsigned i=begin;i<end;i++)if(ops[i]==op && attrs[i]==attr)return i;
 assert(!"required command not found");return 0;
}
static void check_diag(const char *phase,const char *operation,const char *endpoint,
                       uint32_t attr,bool expected_valid,uint32_t expected,
                       bool observed_valid,uint32_t observed){
 assert(sweet_hsg4_diag.phase && !strcmp(sweet_hsg4_diag.phase,phase));
 assert(sweet_hsg4_diag.operation && !strcmp(sweet_hsg4_diag.operation,operation));
 assert(sweet_hsg4_diag.endpoint && !strcmp(sweet_hsg4_diag.endpoint,endpoint));
 assert(sweet_hsg4_diag.attribute==attr && sweet_hsg4_diag.expected_valid==expected_valid);
 if(expected_valid)assert(sweet_hsg4_diag.expected_min==expected && sweet_hsg4_diag.expected_max==expected);
 assert(sweet_hsg4_diag.observed_valid==observed_valid);
 if(observed_valid)assert(sweet_hsg4_diag.observed==observed);
}
int main(void){
 (void)check_dma;(void)ufs_dali_prepare_hsg1;
 hs_reset();assert(!ufs_dali_prepare_hsg4(false) && !reads && !writes && !adopt_calls);
 hs_reset();su.dali=false;assert(!ufs_dali_prepare_hsg4(true) && !reads && !writes && !adopt_calls);
 hs_reset();adopt_error=EIO;assert(ufs_dali_prepare_hsg4(true)==EIO && !commands && sweet_ufs_pwm_terminal);
 /* Explicit hypothetical active/pending mock: successful SET is not yet GET-visible. */
 hs_reset();uint32_t lane=0;
 assert(!ufs_pwm_set(0x1580,2,300000) && staged[0x1580] && pending[0x1580]==2);
 assert(!ufs_pwm_get(false,0x1580,&lane,300000) && lane==1 && !pmcs);
 assert(!ufs_pwm_pmc(0x44,300000) && !staged[0x1580]);
 assert(!ufs_pwm_get(false,0x1580,&lane,300000) && lane==2 && pmcs==1);
 hs_reset();assert(!ufs_dali_prepare_hsg4(true));
 unsigned total=commands,total_validates=validate_calls;assert(total>90 && total_validates>30);
 unsigned saved_floor_commands[1024],saved_floor_pmcs[1024];
 memcpy(saved_floor_commands,floor_at_commands,total_validates*sizeof(*saved_floor_commands));
 memcpy(saved_floor_pmcs,floor_at_pmcs,total_validates*sizeof(*saved_floor_pmcs));
 (void)ufs_hsg4_post_probe_error;
 assert(pmcs==2 && attrs[0x1571]==0x11 && attrs[0x1583]==4 && attrs[0x1568]==4);
 assert(attrs[0x1580]==2 && attrs[0x1560]==2 && attrs[0x15d4]==1 && attrs[0x156a]==2);
 assert(peerattrs[0x1580]==2 && peerattrs[0x1560]==2 && peerattrs[0x1552]==0x48);
 assert(attrs[0x1554]==0x60 && peerattrs[0x15d6]==0x50);
 unsigned pmc_seen=0,peer_sets=0,first_pmc=0,second_pmc=0;
 uint32_t saved_op[1024],saved_attr[1024],saved_value[1024];
 memcpy(saved_op,trace_op,total*sizeof(*saved_op));memcpy(saved_attr,trace_attr,total*sizeof(*saved_attr));
 memcpy(saved_value,trace_value,total*sizeof(*saved_value));
 for(unsigned i=0;i<total;i++){
  if(trace_op[i]==4){++peer_sets;assert(trace_attr[i]!=0x1571 && trace_value[i]==0x48);}
  if(trace_attr[i]==0x1571 && trace_op[i]==2){
   assert(trace_value[i]==(pmc_seen?0x11:0x44));if(pmc_seen)second_pmc=i;else first_pmc=i;++pmc_seen;
  }
  if(trace_op[i]==2 && trace_attr[i]==0x15d4)
   assert(trace_value[i]==(pmc_seen?1u:3u));
 }
 assert(pmc_seen==2 && peer_sets==4 && first_pmc>=17 && second_pmc>first_pmc+17);
 const uint32_t stage_attrs[]={0x15d4,0x1583,0x1580,0x1584,0x1568,0x1560,0x1569,
   0x156a,0x15b0,0x15b1,0x15b2,0x15b3,0x15b4,0x15b5,0xd041,0xd042,0xd043};
 for(unsigned phase=0;phase<2;phase++){
  unsigned pmc=phase?second_pmc:first_pmc;
  for(unsigned i=0;i<17;i++)assert(saved_op[pmc-17+i]==2 && saved_attr[pmc-17+i]==stage_attrs[i]);
 }
 assert(sweet_hsg4_diag.pmc_submitted==3 && sweet_hsg4_diag.pmc_completed==3 &&
        sweet_hsg4_diag.pmc_acknowledged==3 && sweet_hsg4_diag.pmc_idle_ok==3);
 assert(sweet_hsg4_diag.avail_rx[0]==2 && sweet_hsg4_diag.avail_tx[0]==2 &&
        sweet_hsg4_diag.avail_rx[1]==2 && sweet_hsg4_diag.avail_tx[1]==2 &&
        sweet_hsg4_diag.unipro_version[0]==6 && sweet_hsg4_diag.unipro_version[1]==6);
 unsigned sync_local=find_trace(saved_op,saved_attr,0,first_pmc,2,0x1552);
 unsigned sync_peer=find_trace(saved_op,saved_attr,0,first_pmc,4,0x1552);
 unsigned lane_set=find_trace(saved_op,saved_attr,0,first_pmc,2,0x1580);
 unsigned peer_series=find_trace(saved_op,saved_attr,first_pmc+1,second_pmc,3,0x156a);
 unsigned gear_set=find_trace(saved_op,saved_attr,first_pmc+1,second_pmc,2,0x1583);
 unsigned peer_lane=find_trace(saved_op,saved_attr,second_pmc+1,total,3,0x1580);
 unsigned final_cap=find_trace(saved_op,saved_attr,second_pmc+1,total,1,0x1540);
 unsigned max_get=find_trace(saved_op,saved_attr,second_pmc+1,total,1,0x1587);
 unsigned sync_final=find_trace(saved_op,saved_attr,second_pmc+1,total,3,0x15d6);
 hs_reset();attrs[0x1580]=0;assert(ufs_dali_prepare_hsg4(true)==EIO);
 check_diag("before-snapshot","compare","local",0x1580,true,1,true,0);
 assert(!sets && !pmcs);
 hs_reset();peerattrs[0x1520]=1;assert(ufs_dali_prepare_hsg4(true)==ENOTSUP);
 check_diag("before-capability","compare","peer",0x1520,true,2,true,1);
 assert(!sets && !pmcs);
 hs_reset();peerattrs[0x1571]=0x22;assert(ufs_dali_prepare_hsg4(true)==ENOTSUP);
 check_diag("before-admission","compare","peer",0x1571,true,0x55,true,0x22);
 assert(!sets && !pmcs && !(sweet_ufs_pwm_terminal&1));
 /* Floor failure identifies the next intended UIC attribute without reading it. */
 hs_reset();fail_validate=2;assert(ufs_dali_prepare_hsg4(true)==EIO);
 check_diag("sync-prepare","floor-before","platform",0x1552,false,0,false,0);
 assert(!pmcs && !sets);
 /* Silent local/peer sync SET mismatch must identify the readback and stop before PMC. */
 hs_reset();suppress_command=sync_local+1;assert(ufs_dali_prepare_hsg4(true)==EIO);
 check_diag("sync-prepare","compare","local",0x1552,true,0x48,true,0);
 assert(commands==sync_local+2 && !pmcs && !sweet_hsg4_diag.pmc_submitted);
 assert(sweet_hsg4_diag.first_error_valid && sweet_hsg4_diag.first_error==EIO);
 unsigned was=commands,old_reads=reads,old_writes=writes;
 puts("DIAG_REPEAT_BEGIN");
 ufs_pwm_root_leave(true,EIO);ufs_pwm_root_leave(true,EIO);
 puts("DIAG_REPEAT_END");
 assert(sweet_hsg4_diag.terminal_reemitted && commands==was && reads==old_reads && writes==old_writes);
 assert(ufs_dali_prepare_hsg4(true)==EIO && commands==was);
 check_diag("sync-prepare","compare","local",0x1552,true,0x48,true,0);
 /* Suppress all success printf output; a later probe failure still carries
  * the completed masks and never claims the transition itself failed. */
 hs_reset();puts("DROP_SUCCESS_BEGIN");assert(!ufs_dali_prepare_hsg4(true));puts("DROP_SUCCESS_END");
 su.failure_stage="speed-probe-hs-read";was=commands;old_reads=reads;old_writes=writes;
 ufs_hsg4_post_probe_error(EIO);
 check_diag("post-transition-probe","speed-probe-hs-read","probe",0,false,0,false,0);
 assert(sweet_hsg4_diag.first_error==EIO && sweet_hsg4_diag.pmc_idle_ok==3);
 puts("POST_REPEAT_BEGIN");ufs_pwm_root_leave(true,EIO);puts("POST_REPEAT_END");
 assert(sweet_hsg4_diag.terminal_reemitted && commands==was && reads==old_reads && writes==old_writes);
 hs_reset();suppress_command=sync_peer+1;assert(ufs_dali_prepare_hsg4(true)==EIO);
 check_diag("sync-prepare","compare","peer",0x1552,true,0x48,true,0);
 assert(commands==sync_peer+2 && !pmcs);
 /* Failed stage SET has no invented observed value and must not submit PMC. */
 hs_reset();fail_command=lane_set+1;assert(ufs_dali_prepare_hsg4(true)==EIO);
 check_diag("stage-g1","set","local",0x1580,true,2,false,0);
 assert(commands==lane_set+1 && !pmcs && !sweet_hsg4_diag.pmc_submitted);
 was=commands;old_reads=reads;old_writes=writes;
 assert(ufs_dali_prepare_hsg4(true)==EIO && commands==was && reads==old_reads && writes==old_writes);
 check_diag("stage-g1","set","local",0x1580,true,2,false,0);
 /* A silently ignored SET is detected from actual post-PMC state, not pre-PMC. */
 hs_reset();suppress_command=lane_set+1;assert(ufs_dali_prepare_hsg4(true)==EIO);
 check_diag("verify-g1","compare","local",0x1580,true,2,true,1);
 assert(pmcs==1 && sweet_hsg4_diag.pmc_submitted==1 && sweet_hsg4_diag.pmc_idle_ok==1);
 hs_reset();corrupt_command=peer_series+1;assert(ufs_dali_prepare_hsg4(true)==EIO);
 check_diag("verify-g1","compare","peer",0x156a,true,2,true,3);
 assert(pmcs==1 && sweet_hsg4_diag.pmc_submitted==1 && sweet_hsg4_diag.pmc_idle_ok==1);
 hs_reset();suppress_command=gear_set+1;assert(ufs_dali_prepare_hsg4(true)==EIO);
 check_diag("verify-g4","compare","local",0x1583,true,4,true,1);
 assert(pmcs==2 && sweet_hsg4_diag.pmc_submitted==3 && sweet_hsg4_diag.pmc_acknowledged==3);
 /* Inject real post-completion mock state mismatches, not altered GET results. */
 for(unsigned phase=1;phase<=2;phase++)for(unsigned peer=0;peer<2;peer++){
  hs_reset();mutate_pmc=phase;mutate_attr=0x1580;mutate_peer=peer!=0;
  assert(ufs_dali_prepare_hsg4(true)==EIO && pmcs==phase);
  assert((peer?peerattrs[0x1580]:attrs[0x1580])==1 && !corrupt_command);
  check_diag(phase==1?"verify-g1":"verify-g4","compare",peer?"peer":"local",0x1580,true,2,true,1);
  assert(sweet_hsg4_diag.pmc_completed==(phase==1?1u:3u) &&
         sweet_hsg4_diag.pmc_acknowledged==(phase==1?1u:3u));
  was=commands;old_reads=reads;old_writes=writes;
  assert(ufs_dali_prepare_hsg4(true)==EIO && commands==was && reads==old_reads && writes==old_writes);
 }
 hs_reset();corrupt_command=peer_lane+1;assert(ufs_dali_prepare_hsg4(true)==EIO);
 check_diag("verify-g4","compare","peer",0x1580,true,2,true,3);
 assert(pmcs==2 && sweet_hsg4_diag.pmc_submitted==3 && sweet_hsg4_diag.pmc_idle_ok==3);
 hs_reset();corrupt_command=max_get+1;assert(ufs_dali_prepare_hsg4(true)==EIO);
 check_diag("final-capability","compare","local",0x1587,true,5,true,4);
 assert(pmcs==2 && sweet_hsg4_diag.pmc_acknowledged==3);
 hs_reset();corrupt_command=sync_final+1;assert(ufs_dali_prepare_hsg4(true)==EIO);
 check_diag("sync-final","compare","peer",0x15d6,true,0x50,true,0x51);
 assert(commands==sync_final+1 && pmcs==2);
 /* Every GET in both completed-PMC verification phases is mandatory. */
 unsigned verified_corruptions=0;
 for(unsigned phase=0;phase<2;phase++){
  unsigned begin=(phase?second_pmc:first_pmc)+1,end=phase?final_cap:second_pmc-17;
  assert(end-begin==21);
  for(unsigned i=begin;i<end;i++){
   assert(saved_op[i]==1 || saved_op[i]==3);hs_reset();corrupt_command=i+1;
   assert(ufs_dali_prepare_hsg4(true)==EIO && sweet_ufs_pwm_terminal && pmcs==phase+1);
   assert(sweet_hsg4_diag.pmc_submitted==(phase?3u:1u) &&
          sweet_hsg4_diag.pmc_acknowledged==(phase?3u:1u));++verified_corruptions;
  }
 }
 assert(verified_corruptions==42);
 unsigned corruptions=0,silent_sets=0;
 for(unsigned i=0;i<18;i++){
  assert(saved_op[i]==1 || saved_op[i]==3);hs_reset();corrupt_command=i+1;
  assert(ufs_dali_prepare_hsg4(true)!=0 && sweet_ufs_pwm_terminal && !sets && !pmcs);++corruptions;
 }
 for(unsigned i=first_pmc-8;i<total;i++){
  if(saved_op[i]!=1 && saved_op[i]!=3)continue;
  hs_reset();corrupt_command=i+1;assert(ufs_dali_prepare_hsg4(true)!=0 && sweet_ufs_pwm_terminal);++corruptions;
 }
 for(unsigned i=0;i<total;i++){
  uint32_t a=saved_attr[i];bool sync=a==0x1552 || a==0x1554 || a==0x1556 || a==0x15d0 || a==0x15d6;
  bool changing=(i<first_pmc && (a==0x15d4 || a==0x1580 || a==0x1560 || a==0x1584 || a==0x1569 || a==0x156a)) ||
      (i>first_pmc && i<second_pmc && (a==0x15d4 || a==0x1583 || a==0x1568));
  if((saved_op[i]!=2 && saved_op[i]!=4) || (!sync && !changing))continue;
  hs_reset();suppress_command=i+1;assert(ufs_dali_prepare_hsg4(true)!=0 && sweet_ufs_pwm_terminal);++silent_sets;
 }
 assert(corruptions>50 && silent_sets>10);
 hs_reset();bad_pmc_ack=2;assert(ufs_dali_prepare_hsg4(true)!=0 && pmcs==2 && sweet_ufs_pwm_terminal);
 check_diag("stage-g4","pmc","local",0x1571,true,0x11,false,0);
 assert(sweet_hsg4_diag.pmc_submitted==3 && sweet_hsg4_diag.pmc_completed==3 &&
        sweet_hsg4_diag.pmc_acknowledged==1 && sweet_hsg4_diag.pmc_idle_ok==1 &&
        sweet_hsg4_diag.pmc_sampled && sweet_hsg4_diag.pmc_arg2_valid &&
        sweet_hsg4_diag.pmc_ack_status_valid && sweet_hsg4_diag.pmc_ack_status==0x410 &&
        !strcmp(sweet_hsg4_diag.pmc_failure_stage,"ack") && sweet_hsg4_diag.pmc_error==EIO);
 unsigned bad_count=commands;assert(ufs_dali_prepare_hsg4(true)!=0 && commands==bad_count);
 hs_reset();uccs_after=0;upms_after=20;assert(!ufs_dali_prepare_hsg4(true) && pmcs==2);
 hs_reset();uccs_after=20;upms_after=0;assert(!ufs_dali_prepare_hsg4(true) && pmcs==2);
 for(unsigned n=0;n<15;n++){
  hs_reset();switch(n){
   case 0:attrs[0x1520]=1;break;case 1:attrs[0x1540]=1;break;
   case 2:peerattrs[0x1520]=1;break;case 3:peerattrs[0x1540]=1;break;
   case 4:attrs[0x1581]=1;break;case 5:attrs[0x1561]=1;break;
   case 6:peerattrs[0x1581]=1;break;case 7:peerattrs[0x1561]=1;break;
   case 8:attrs[0x1587]=3;break;case 9:peerattrs[0x1587]=3;break;
   case 10:attrs[0x1587]=6;break;case 11:peerattrs[0x1587]=6;break;
   case 12:attrs[0x15a9]=4;break;case 13:peerattrs[0x15a9]=4;break;
   case 14:peerattrs[0x1571]=0x22;break;
  }
  assert(ufs_dali_prepare_hsg4(true)!=0 && sweet_ufs_pwm_terminal && !sets && !pmcs);
 }
 unsigned failed_staged_sets=0;
 for(unsigned failure=1;failure<=total;failure++){
  hs_reset();fail_command=failure;assert(ufs_dali_prepare_hsg4(true)!=0 && sweet_ufs_pwm_terminal);
  unsigned idx=failure-1;
  if((idx>=first_pmc-17 && idx<first_pmc) || (idx>=second_pmc-17 && idx<second_pmc)){
   bool g4=idx>=second_pmc-17;
   check_diag(g4?"stage-g4":"stage-g1","set","local",saved_attr[idx],true,saved_value[idx],false,0);
   assert(commands==failure && pmcs==(g4?1u:0u));++failed_staged_sets;
  }
  unsigned was=commands,old_reads=reads,old_writes=writes,adopted=adopt_calls;int first=ufs_pwm_reentry_error();
  const char *phase=sweet_hsg4_diag.phase,*operation=sweet_hsg4_diag.operation;
  uint32_t attr=sweet_hsg4_diag.attribute;int diag_error=sweet_hsg4_diag.first_error;
  assert(sweet_hsg4_diag.first_error_valid && diag_error==first);
  assert(ufs_dali_prepare_hsg4(true)==first && commands==was && adopt_calls==adopted &&
         reads==old_reads && writes==old_writes);
  assert(sweet_hsg4_diag.phase==phase && sweet_hsg4_diag.operation==operation &&
         sweet_hsg4_diag.attribute==attr && sweet_hsg4_diag.first_error==diag_error);
 }
 assert(failed_staged_sets==34);
 for(unsigned v=1;v<=total_validates;v++){
  hs_reset();fail_validate=v;assert(ufs_dali_prepare_hsg4(true)!=0 && sweet_ufs_pwm_terminal);
  assert(commands==saved_floor_commands[v-1] && pmcs==saved_floor_pmcs[v-1]);
  assert(floor_state.phase==HSF_FAILED && floor_state.failure==HSF_CLOCK);
  assert(!strcmp(sweet_hsg4_diag.operation,"floor-before") &&
         !strcmp(sweet_hsg4_diag.endpoint,"platform") && !sweet_hsg4_diag.observed_valid);
  unsigned was=commands,old_reads=reads,old_writes=writes,old_floor=floor_reads;
  assert(ufs_dali_prepare_hsg4(true)!=0 && commands==was && reads==old_reads &&
         writes==old_writes && floor_reads==old_floor);
 }
 /* Execute the real retained-floor validator with independently injected losses. */
 for(unsigned n=0;n<7;n++){
  hs_reset();assert(!ufs_dali_hs_platform_adopt() && !ufs_dali_hs_platform_validate());
  switch(n){
   case 0:floor_reg[HSF_REQUEST/4]=1u<<4;break;
   case 1:floor_reg[HSF_GEAR/4]=1u<<8;break;
   case 2:floor_reg[HSF_TARGET/4]=1u<<16;break;
   case 3:floor_reg[HSF_FORCE_REQUEST/4]=1;break;
   case 4:floor_reg[HSF_HALT/4]=2;break;
   case 5:floor_clock_bad=true;break;
   case 6:floor_state.calibrated[2]=649999;break;
  }
  assert(ufs_dali_hs_platform_validate()==EIO && floor_state.phase==HSF_FAILED);
  unsigned old_floor=floor_reads,old_clocks=floor_clocks;
  assert(ufs_dali_hs_platform_validate()==EIO && floor_reads==old_floor && floor_clocks==old_clocks);
 }
 hs_reset();upmcrs=0;assert(ufs_dali_prepare_hsg4(true)!=0 && sweet_ufs_pwm_terminal && pmcs==1);
 hs_reset();pmc_ack_stuck=true;assert(ufs_dali_prepare_hsg4(true)!=0 && sweet_ufs_pwm_terminal && pmcs==1);
 hs_reset();freeze_clock=true;upms_after=uccs_after=-1;
 assert(ufs_dali_prepare_hsg4(true)==ETIMEDOUT && sweet_ufs_pwm_terminal && pmcs==1);
 hs_reset();corrupt_readback=true;assert(ufs_dali_prepare_hsg4(true)!=0 && sweet_ufs_pwm_terminal && pmcs==1);
 hs_reset();assert(ufs_pwm_peer_set(0x1571,0x44,300000)==EINVAL && !reads && !writes);
 hs_reset();assert(ufs_hsg4_stage(300000,5,0x11)==EINVAL && !reads && !writes && !validate_calls);
 hs_reset();assert(ufs_hsg4_stage(300000,4,0x44)==EINVAL && !reads && !writes && !validate_calls);
 hs_reset();assert(ufs_pwm_pmc(0x33,300000)==EINVAL && !reads && !writes);
 hs_reset();now=300000;assert(ufs_pwm_pmc(0x44,300000)==ETIMEDOUT && !reads && !writes);
 assert(!strcmp(sweet_hsg4_diag.pmc_failure_stage,"deadline-pre") &&
        sweet_hsg4_diag.pmc_error==ETIMEDOUT && !sweet_hsg4_diag.pmc_submitted);
 hs_reset();regs[REG_HCE/4]=0;assert(ufs_pwm_pmc(0x44,300000)==EIO);
 assert(!strcmp(sweet_hsg4_diag.pmc_failure_stage,"guard-pre") &&
        sweet_hsg4_diag.pmc_error==EIO && !sweet_hsg4_diag.pmc_submitted);
 hs_reset();assert(!ufs_pwm_pmc(0x11,300000) && pmcs==1);
 printf("PASS production UIC/PMC/G4 + hsf_validate: %u command failures (%u staged SETs), %u floor-gate losses, %u corrupted GETs (%u post-PMC checks), 4 actual mock endpoint-state mismatches, %u ignored SETs, 7 floor-loss modes; mock hardware only\n",total,failed_staged_sets,total_validates,corruptions,verified_corruptions,silent_sets);
 return 0;
}
'''

# A negative control executes the exact diagnostic-base production header in
# the SAME mock. It must reproduce early EIO; the new header succeeds above.
old_tests = r'''
int main(void){
 (void)check_dma;(void)ufs_dali_prepare_hsg1;(void)ufs_hsg4_post_probe_error;(void)adopt_calls;
 hs_reset();assert(ufs_dali_prepare_hsg4(true)==EIO && sweet_ufs_pwm_terminal);
 assert(!pmcs && !sweet_hsg4_diag.pmc_submitted);
 assert(!strcmp(sweet_hsg4_diag.phase,"stage-g1") && !strcmp(sweet_hsg4_diag.operation,"compare"));
 assert(sweet_hsg4_diag.attribute==0x1580 && sweet_hsg4_diag.expected_min==2 &&
        sweet_hsg4_diag.observed_valid && sweet_hsg4_diag.observed==1);
 assert(staged[0x1580] && pending[0x1580]==2 && attrs[0x1580]==1);
 puts("PASS diagnostic-base negative control: active/pending mock stops on staged GET before PMC");
 return 0;
}
'''
with tempfile.TemporaryDirectory(prefix='test-', dir=D / 'artifacts') as tmp:
    folder = Path(tmp)
    (folder / 'SweetUFSPWMPolicy.h').write_bytes((D / 'xnu/bsd/dev/SweetUFSPWMPolicy.h').read_bytes())
    (folder / 'SweetUFSPWMClock.h').write_bytes((D / 'xnu/bsd/dev/SweetUFSPWMClock.h').read_bytes())
    (folder / 'SweetUFSHSFloor.h').write_bytes((D / 'xnu/bsd/dev/SweetUFSHSFloor.h').read_bytes())
    (folder / 'test.c').write_text(c + platform + header + tests)
    subprocess.run(['clang', '-std=gnu11', '-O1', '-Wall', '-Wextra', '-Werror',
                    '-fsanitize=address,undefined', '-fno-sanitize-recover=all', str(folder / 'test.c'), '-o', str(folder / 'test')], check=True)
    p = subprocess.run([str(folder / 'test')], text=True, capture_output=True, timeout=120)
    if p.returncode:
        print(p.stdout[-6000:]); print(p.stderr, file=sys.stderr)
        raise SystemExit(p.returncode)
    assert not p.stderr, p.stderr
    for fragment in (
        'phase=sync-prepare op=compare endpoint=local attr=0x1552 expect=eq expected-valid=1 expected-min=0x48 expected-max=0x48 observed-valid=1 observed=0x0',
        'phase=sync-prepare op=compare endpoint=peer attr=0x1552',
        'phase=stage-g1 op=set endpoint=local attr=0x1580 expect=eq expected-valid=1 expected-min=0x2 expected-max=0x2 observed-valid=0',
        'phase=verify-g1 op=compare endpoint=local attr=0x1580',
        'phase=verify-g1 op=compare endpoint=peer attr=0x156a',
        'phase=verify-g4 op=compare endpoint=local attr=0x1583',
        'phase=verify-g4 op=compare endpoint=peer attr=0x1580',
        'phase=final-capability op=compare endpoint=local attr=0x1587',
        'phase=sync-final op=compare endpoint=peer attr=0x15d6',
        'SWEET UFS HSG4 PMC: submit=0x3 complete=0x3 ack=0x1 idle=0x1 mode=0x11',
        'SWEET UFS HSG4 CAP: local-rx=2 local-tx=2 peer-rx=2 peer-tx=2 local-ver=6 peer-ver=6',
    ):
        assert fragment in p.stdout, 'missing rendered diagnostic: ' + fragment
    repeat = p.stdout.split('DIAG_REPEAT_BEGIN\n', 1)[1].split('DIAG_REPEAT_END\n', 1)[0]
    assert repeat.count('SWEET UFS HSG4 FAIL:') == 1 and repeat.count('SWEET UFS HSG4 PMC:') == 1
    assert 'phase=sync-prepare op=compare endpoint=local attr=0x1552' in repeat
    assert 'observed=0x0 repeat=1' in repeat
    dropped = p.stdout.split('DROP_SUCCESS_BEGIN\n', 1)[1].split('DROP_SUCCESS_END\n', 1)[0]
    assert 'SWEET UFS HSG4: verified' in dropped
    post = p.stdout.split('POST_REPEAT_BEGIN\n', 1)[1].split('POST_REPEAT_END\n', 1)[0]
    assert 'phase=post-transition-probe op=speed-probe-hs-read endpoint=probe attr=0x0' in post
    assert 'submit=0x3 complete=0x3 ack=0x3 idle=0x3' in post and 'repeat=1' in post
    assert 'phase=stage-' not in post and 'phase=verify-' not in post
    print(p.stdout.splitlines()[-1])
    print('PASS first-error attribution and bounded terminal re-emission without additional MMIO')
    source_file = folder / 'diagnostic-base.c'
    binary = folder / 'diagnostic-base'
    source_file.write_text(c + platform + old_header + old_tests)
    subprocess.run(['clang', '-std=gnu11', '-O1', '-Wall', '-Wextra', '-Werror',
                    '-fsanitize=address,undefined', '-fno-sanitize-recover=all', str(source_file), '-o', str(binary)], check=True)
    result = subprocess.run([str(binary)], text=True, capture_output=True, timeout=120)
    if result.returncode:
        print(result.stdout[-6000:]); print(result.stderr, file=sys.stderr)
        raise SystemExit(result.returncode)
    assert not result.stderr, result.stderr
    print(result.stdout.splitlines()[-1])
