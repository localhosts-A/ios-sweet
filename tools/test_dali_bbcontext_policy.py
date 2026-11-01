#!/usr/bin/env python3
"""Execute exact 0181 BB context policy with synthetic data only."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

import test_dali_gui_user_entry as fixture
ROOT=Path(__file__).resolve().parents[1]
PATCH=ROOT/'xnu/patches/0181-sweet-bounded-backboard-context.patch'
POLICY=fixture.fragment('bsd/dev/SweetBBContextPolicy.h', PATCH)
PREFIX=r'''
#include <assert.h>
#include <stdint.h>
#include <string.h>
#include <pthread.h>
#include "SweetBBContextPolicy.h"
static struct sweet_bbctx_identity ident(uint64_t tid) {
    struct sweet_bbctx_identity x={55,tid,42,7}; return x;
}
'''

class ContextPolicyTests(unittest.TestCase):
    def run_c(self,body,cxx=False,extra=''):
        compiler=shutil.which('clang++' if cxx else 'clang') or shutil.which('c++' if cxx else 'cc')
        self.assertIsNotNone(compiler)
        with tempfile.TemporaryDirectory(prefix='bbctx-host-') as td:
            p=Path(td);(p/'SweetBBContextPolicy.h').write_bytes(POLICY.encode())
            source=p/('test.cpp' if cxx else 'test.c')
            source.write_text(PREFIX+'\n'+extra+'\nint main(void){(void)&ident;\n'+body+'\nreturn 0;}\n')
            result=subprocess.run([compiler,'-std=gnu++17' if cxx else '-std=gnu11','-O2','-Wall','-Wextra','-Werror','-pthread',str(source),'-o',str(p/'test')],capture_output=True,text=True,timeout=30)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            result=subprocess.run([str(p/'test')],capture_output=True,text=True,timeout=20)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_disabled_and_invalid_identity_do_not_create_records(self):
        self.run_c(r'''
struct sweet_bbctx_book b={0}; struct sweet_bbctx_identity id=ident(100); uint64_t e=99;
assert(sweet_bbctx_begin(&b,&id,1,0,&e)==SWEET_BBCTX_CLOSED && e==0 && b.begun==0);
b.open=true;id.tid=0;e=99;
assert(sweet_bbctx_begin(&b,&id,1,0,&e)==SWEET_BBCTX_INVALID && e==0 && b.begun==0);
id=ident(100);id.uniqueid=0;
assert(sweet_bbctx_begin(&b,&id,1,0,&e)==SWEET_BBCTX_INVALID && e==0);
assert(sweet_bbctx_begin(&b,0,1,0,&e)==SWEET_BBCTX_INVALID);
''')

    def test_stat_complete_preserves_raw_result_and_real_namei_operand(self):
        self.run_c(r'''
struct sweet_bbctx_book b={0};b.open=true;struct sweet_bbctx_identity id=ident(100);uint64_t e=0;
unsigned char name[]={'a','/','b',0};
assert(sweet_bbctx_begin(&b,&id,10,0,&e)==SWEET_BBCTX_OK && e==1);
assert(sweet_bbctx_namei_begin(&b,&id,e,name,sizeof(name))==SWEET_BBCTX_OK);
name[0]='z'; /* observer copied kernel-owned bytes synchronously */
assert(sweet_bbctx_namei_end(&b,&id,e,2)==SWEET_BBCTX_OK);
assert(sweet_bbctx_complete(&b,&id,e,20,2,SWEET_BBCTX_RESULT_BSD)==SWEET_BBCTX_OK);
assert(b.has_last_enoent && b.last_enoent.epoch==e && b.last_enoent.path_size==3);
assert(memcmp(b.last_enoent.path,"a/b",3)==0 && b.last_enoent.path_state==SWEET_BBCTX_PATH_COMPLETE);
assert(b.last_enoent.namei_result_known && b.last_enoent.namei_error==2 && b.last_enoent.result==2);
''')

    def test_empty_exact_limit_truncated_and_invalid_path(self):
        self.run_c(r'''
struct sweet_bbctx_book b={0};b.open=true;struct sweet_bbctx_identity id=ident(1);uint64_t e;
unsigned char s[1025];memset(s,'q',sizeof(s));
assert(sweet_bbctx_begin(&b,&id,1,0,&e)==SWEET_BBCTX_OK);
s[0]=0;assert(sweet_bbctx_namei_begin(&b,&id,e,s,1)==SWEET_BBCTX_OK);
struct sweet_bbctx_record *r=sweet_bbctx_find(&b,&id,e);
assert(r->path_state==SWEET_BBCTX_PATH_COMPLETE && r->path_size==0);
assert(sweet_bbctx_namei_end(&b,&id,e,2)==SWEET_BBCTX_OK);
assert(sweet_bbctx_complete(&b,&id,e,2,2,SWEET_BBCTX_RESULT_BSD)==SWEET_BBCTX_OK);
assert(sweet_bbctx_begin(&b,&id,3,0,&e)==SWEET_BBCTX_OK);memset(s,'q',sizeof(s));s[192]=0;
assert(sweet_bbctx_namei_begin(&b,&id,e,s,193)==SWEET_BBCTX_OK);r=sweet_bbctx_find(&b,&id,e);
assert(r->path_state==SWEET_BBCTX_PATH_COMPLETE && r->path_size==192);
assert(sweet_bbctx_namei_end(&b,&id,e,2)==SWEET_BBCTX_OK);
assert(sweet_bbctx_complete(&b,&id,e,4,2,SWEET_BBCTX_RESULT_BSD)==SWEET_BBCTX_OK);
assert(sweet_bbctx_begin(&b,&id,5,0,&e)==SWEET_BBCTX_OK);memset(s,'q',sizeof(s));s[1023]=0;
assert(sweet_bbctx_namei_begin(&b,&id,e,s,1024)==SWEET_BBCTX_OK);r=sweet_bbctx_find(&b,&id,e);
assert(r->path_state==SWEET_BBCTX_PATH_TRUNCATED && r->path_size==192);
assert(sweet_bbctx_namei_end(&b,&id,e,2)==SWEET_BBCTX_OK);
assert(sweet_bbctx_complete(&b,&id,e,6,2,SWEET_BBCTX_RESULT_BSD)==SWEET_BBCTX_OK);
assert(sweet_bbctx_begin(&b,&id,7,0,&e)==SWEET_BBCTX_OK);
assert(sweet_bbctx_namei_begin(&b,&id,e,s,1025)==SWEET_BBCTX_INVALID);
assert(sweet_bbctx_namei_begin(&b,&id,e,0,1)==SWEET_BBCTX_INVALID);
assert(sweet_bbctx_namei_begin(&b,&id,e,s,0)==SWEET_BBCTX_INVALID);
assert(sweet_bbctx_namei_begin(&b,&id,e,s,3)==SWEET_BBCTX_INVALID);
assert(sweet_bbctx_find(&b,&id,e)->path_state==SWEET_BBCTX_PATH_UNOBSERVED);
''')

    def test_namei_bounded_local_view_not_full_kernel_buffer(self):
        self.run_c(r'''
struct sweet_bbctx_book b={0};b.open=true;struct sweet_bbctx_identity id=ident(1);uint64_t e;
unsigned char scratch[192];memset(scratch,'a',sizeof(scratch));
assert(sweet_bbctx_begin(&b,&id,1,0,&e)==SWEET_BBCTX_OK);
assert(sweet_bbctx_namei_begin_view(&b,&id,e,scratch,191,1024)==SWEET_BBCTX_INVALID);
assert(sweet_bbctx_namei_begin_view(&b,&id,e,scratch,193,1024)==SWEET_BBCTX_INVALID);
assert(sweet_bbctx_namei_begin_view(&b,&id,e,scratch,192,1025)==SWEET_BBCTX_INVALID);
assert(sweet_bbctx_namei_begin_view(&b,&id,e,scratch,192,1024)==SWEET_BBCTX_OK);
struct sweet_bbctx_record *r=sweet_bbctx_find(&b,&id,e);
assert(r && r->path_size==192 && r->path_state==SWEET_BBCTX_PATH_TRUNCATED);
''')

    def test_nested_or_retried_namei_never_reports_pair(self):
        self.run_c(r'''
struct sweet_bbctx_book b={0};b.open=true;struct sweet_bbctx_identity id=ident(2);uint64_t e;
const unsigned char path[]="/synthetic";
assert(sweet_bbctx_begin(&b,&id,1,0,&e)==SWEET_BBCTX_OK);
assert(sweet_bbctx_namei_begin(&b,&id,e,path,sizeof(path))==SWEET_BBCTX_OK);
assert(sweet_bbctx_namei_begin(&b,&id,e,path,sizeof(path))==SWEET_BBCTX_REENTERED);
assert(sweet_bbctx_namei_end(&b,&id,e,2)==SWEET_BBCTX_STALE);
assert(sweet_bbctx_complete(&b,&id,e,2,2,SWEET_BBCTX_RESULT_BSD)==SWEET_BBCTX_OK);
assert(b.last_enoent.path_state==SWEET_BBCTX_PATH_AMBIGUOUS && !b.last_enoent.namei_result_known && b.last_enoent.path_size==0);
''')

    def test_pending_namei_on_completion_stays_unknown(self):
        self.run_c(r'''
struct sweet_bbctx_book b={0};b.open=true;struct sweet_bbctx_identity id=ident(1);uint64_t e;
const unsigned char path[]="/x";assert(sweet_bbctx_begin(&b,&id,1,0,&e)==SWEET_BBCTX_OK);
assert(sweet_bbctx_namei_begin(&b,&id,e,path,sizeof(path))==SWEET_BBCTX_OK);
assert(sweet_bbctx_complete(&b,&id,e,2,2,SWEET_BBCTX_RESULT_BSD)==SWEET_BBCTX_OK);
assert(b.last_enoent.path_state==SWEET_BBCTX_PATH_AMBIGUOUS && !b.last_enoent.namei_result_known);
''')

    def test_stale_wrong_identity_and_reentry_never_pair(self):
        self.run_c(r'''
struct sweet_bbctx_book b={0};b.open=true;struct sweet_bbctx_identity id=ident(1),other=ident(1);uint64_t e,e2;
assert(sweet_bbctx_begin(&b,&id,1,0,&e)==SWEET_BBCTX_OK);
other.idversion++;assert(sweet_bbctx_complete(&b,&other,e,2,2,SWEET_BBCTX_RESULT_BSD)==SWEET_BBCTX_STALE);
assert(sweet_bbctx_complete(&b,&id,e+1,2,2,SWEET_BBCTX_RESULT_BSD)==SWEET_BBCTX_STALE);
assert(sweet_bbctx_begin(&b,&id,3,0,&e2)==SWEET_BBCTX_REENTERED && e2==0);
assert(sweet_bbctx_complete(&b,&id,e,4,2,SWEET_BBCTX_RESULT_BSD)==SWEET_BBCTX_STALE);
assert(!b.has_last_enoent && sweet_bbctx_find(&b,&id,e)->state==SWEET_BBCTX_AMBIGUOUS);
''')

    def test_eight_pending_slots_cannot_be_overwritten(self):
        self.run_c(r'''
struct sweet_bbctx_book b={0};b.open=true;uint64_t es[8];
for(unsigned i=0;i<8;++i){struct sweet_bbctx_identity id=ident(i+1);assert(sweet_bbctx_begin(&b,&id,i,0,&es[i])==SWEET_BBCTX_OK);}
struct sweet_bbctx_identity extra=ident(9);uint64_t e=9;
assert(sweet_bbctx_begin(&b,&extra,9,0,&e)==SWEET_BBCTX_FULL && !e && b.rejected_full==1);
for(unsigned i=0;i<8;++i){struct sweet_bbctx_identity id=ident(i+1);assert(sweet_bbctx_find(&b,&id,es[i])->state==SWEET_BBCTX_PENDING);}
struct sweet_bbctx_identity id=ident(3);assert(sweet_bbctx_complete(&b,&id,es[2],10,0,SWEET_BBCTX_RESULT_MACH)==SWEET_BBCTX_OK);
assert(sweet_bbctx_begin(&b,&extra,11,0,&e)==SWEET_BBCTX_OK && e==9);
assert(sweet_bbctx_find(&b,&id,es[2])==0);
''')

    def test_late_enoent_replaces_only_prior_enoent(self):
        self.run_c(r'''
struct sweet_bbctx_book b={0};b.open=true;struct sweet_bbctx_identity a=ident(1),p=ident(2);uint64_t pending,e;
assert(sweet_bbctx_begin(&b,&p,1,0,&pending)==SWEET_BBCTX_OK);
for(unsigned i=0;i<100;++i){assert(sweet_bbctx_begin(&b,&a,i+2,0,&e)==SWEET_BBCTX_OK);
 assert(sweet_bbctx_complete(&b,&a,e,i+3,2,SWEET_BBCTX_RESULT_BSD)==SWEET_BBCTX_OK);}
assert(b.last_enoent.epoch==101 && b.has_last_enoent);
assert(sweet_bbctx_find(&b,&p,pending)->state==SWEET_BBCTX_PENDING);
''')

    def test_mach_results_are_not_bsd_errno_and_words_retained(self):
        self.run_c(r'''
struct sweet_bbctx_book b={0};b.open=true;struct sweet_bbctx_identity id=ident(4);uint64_t e;
uint64_t words[7]={0x100000003ULL,0x1122334455667788ULL,2,3,4,5,6};
assert(sweet_bbctx_begin(&b,&id,1,words,&e)==SWEET_BBCTX_OK);
struct sweet_bbctx_record *r=sweet_bbctx_find(&b,&id,e);assert(r->mach_words[0]==words[0] && r->mach_words[6]==6);
assert(sweet_bbctx_complete(&b,&id,e,2,2,SWEET_BBCTX_RESULT_MACH)==SWEET_BBCTX_OK);
assert(!b.has_last_enoent && r->result==2 && r->result_known);
''')

    def test_raw_bsd_sentinels_and_mach_domain_are_distinct(self):
        self.run_c(r'''
struct sweet_bbctx_book b={0};b.open=true;struct sweet_bbctx_identity id=ident(1);uint64_t e;
const int32_t results[]={0,2,-1,-2,-3};
const enum sweet_bbctx_return_kind kinds[]={SWEET_BBCTX_RETURN_BSD_SUCCESS,SWEET_BBCTX_RETURN_BSD_ERRNO,
 SWEET_BBCTX_RETURN_BSD_RESTART,SWEET_BBCTX_RETURN_BSD_JUST_RETURN,SWEET_BBCTX_RETURN_BSD_OTHER_NEGATIVE};
for(unsigned i=0;i<5;++i){assert(sweet_bbctx_begin(&b,&id,i,0,&e)==SWEET_BBCTX_OK);
 assert(sweet_bbctx_complete(&b,&id,e,i+1,results[i],SWEET_BBCTX_RESULT_BSD)==SWEET_BBCTX_OK);
 struct sweet_bbctx_record *r=sweet_bbctx_find(&b,&id,e);assert(r && r->return_kind==kinds[i] && r->result==results[i]);}
assert(b.has_last_enoent && b.last_enoent.result==2);
assert(sweet_bbctx_begin(&b,&id,6,0,&e)==SWEET_BBCTX_OK);
assert(sweet_bbctx_complete(&b,&id,e,7,-1,SWEET_BBCTX_RESULT_MACH)==SWEET_BBCTX_OK);
assert(sweet_bbctx_find(&b,&id,e)->return_kind==SWEET_BBCTX_RETURN_MACH);
''')

    def test_close_keeps_pending_and_rejects_late_producers(self):
        self.run_c(r'''
struct sweet_bbctx_book b={0};b.open=true;struct sweet_bbctx_identity id=ident(1);uint64_t e,e2;
assert(sweet_bbctx_begin(&b,&id,1,0,&e)==SWEET_BBCTX_OK);sweet_bbctx_close(&b);
assert(sweet_bbctx_begin(&b,&id,2,0,&e2)==SWEET_BBCTX_CLOSED);
assert(sweet_bbctx_complete(&b,&id,e,3,0,SWEET_BBCTX_RESULT_MACH)==SWEET_BBCTX_CLOSED);
assert(sweet_bbctx_find(&b,&id,e)->state==SWEET_BBCTX_PENDING && !sweet_bbctx_find(&b,&id,e)->result_known);
''')

    def test_epoch_saturation_and_invalid_domain_fail_closed(self):
        self.run_c(r'''
struct sweet_bbctx_book b={0};b.open=true;struct sweet_bbctx_identity id=ident(1);uint64_t e;
b.next_epoch=UINT64_MAX-1;assert(sweet_bbctx_begin(&b,&id,1,0,&e)==SWEET_BBCTX_OK && e==UINT64_MAX);
assert(sweet_bbctx_complete(&b,&id,e,2,2,(enum sweet_bbctx_result_domain)0)==SWEET_BBCTX_INVALID);
assert(sweet_bbctx_find(&b,&id,e)->state==SWEET_BBCTX_PENDING);
assert(sweet_bbctx_begin(&b,&id,3,0,&e)==SWEET_BBCTX_EXHAUSTED && b.exhausted);
''')

    def test_hex_encoding_is_bounded_and_rejects_ambiguous_bytes(self):
        self.run_c(r'''
struct sweet_bbctx_book b={0};b.open=true;struct sweet_bbctx_identity id=ident(1);uint64_t e;
unsigned char name[]={0x41,0x0a,0x1b,0xff,0x20,0x2f,0};
assert(sweet_bbctx_begin(&b,&id,1,0,&e)==SWEET_BBCTX_OK);
assert(sweet_bbctx_namei_begin(&b,&id,e,name,sizeof(name))==SWEET_BBCTX_OK);
struct sweet_bbctx_record copy=*sweet_bbctx_find(&b,&id,e);char out[97];uint32_t n=999;
assert(sweet_bbctx_path_hex_chunk(&copy,0,out,sizeof(out),&n));
assert(n==6 && strcmp(out,"410a1bff202f")==0);
assert(!sweet_bbctx_path_hex_chunk(&copy,0,out,12,&n) && n==0);
assert(sweet_bbctx_path_hex_chunk(&copy,6,out,1,&n) && n==0 && out[0]==0);
assert(!sweet_bbctx_path_hex_chunk(&copy,7,out,sizeof(out),&n));
copy.path_state=SWEET_BBCTX_PATH_AMBIGUOUS;
assert(!sweet_bbctx_path_hex_chunk(&copy,0,out,sizeof(out),&n));
copy.path_state=SWEET_BBCTX_PATH_COMPLETE;copy.path_size=193;
assert(!sweet_bbctx_path_hex_chunk(&copy,0,out,sizeof(out),&n));
''')

    def test_long_path_chunk_bounds_and_snapshot(self):
        self.run_c(r'''
struct sweet_bbctx_book b={0};b.open=true;struct sweet_bbctx_identity id=ident(1);uint64_t e;
unsigned char name[202];for(unsigned i=0;i<201;++i)name[i]=(unsigned char)(1+i%254);name[201]=0;
assert(sweet_bbctx_begin(&b,&id,1,0,&e)==SWEET_BBCTX_OK);
assert(sweet_bbctx_namei_begin(&b,&id,e,name,sizeof(name))==SWEET_BBCTX_OK);
struct sweet_bbctx_record snap=*sweet_bbctx_find(&b,&id,e);assert(snap.path_state==SWEET_BBCTX_PATH_TRUNCATED);
for(unsigned off=0;off<192;off+=48){char out[97];uint32_t n=0;
 assert(sweet_bbctx_path_hex_chunk(&snap,off,out,sizeof(out),&n) && n==48 && strlen(out)==96);
 for(unsigned j=0;j<96;++j)assert((out[j]>='0'&&out[j]<='9')||(out[j]>='a'&&out[j]<='f'));
}
''')

    def test_guarded_concurrent_producers_and_snapshots(self):
        extra=r'''
static struct sweet_bbctx_book shared;
static pthread_mutex_t guard=PTHREAD_MUTEX_INITIALIZER;
static void *worker(void *arg) {
 uint64_t tid=(uint64_t)(uintptr_t)arg;struct sweet_bbctx_identity id=ident(tid);
 for(unsigned i=0;i<100;++i){uint64_t e=0;pthread_mutex_lock(&guard);
  enum sweet_bbctx_result r=sweet_bbctx_begin(&shared,&id,i,0,&e);pthread_mutex_unlock(&guard);
  if(r==SWEET_BBCTX_FULL)continue;
  assert(r==SWEET_BBCTX_OK && e);
  pthread_mutex_lock(&guard);assert(sweet_bbctx_complete(&shared,&id,e,i+1,0,SWEET_BBCTX_RESULT_MACH)==SWEET_BBCTX_OK);pthread_mutex_unlock(&guard);
 }
 return 0;
}
'''
        self.run_c(r'''
shared.open=true;pthread_t t[32];
for(uintptr_t i=0;i<32;++i)assert(!pthread_create(&t[i],0,worker,(void *)(i+1)));
for(unsigned j=0;j<500;++j){pthread_mutex_lock(&guard);struct sweet_bbctx_book copy=shared;pthread_mutex_unlock(&guard);
 for(unsigned i=0;i<SWEET_BBCTX_SLOTS;++i){struct sweet_bbctx_record *r=&copy.slots[i];
  if(r->state==SWEET_BBCTX_PENDING)assert(r->epoch && r->identity.tid && !r->result_known);
  if(r->state==SWEET_BBCTX_COMPLETE)assert(r->epoch && r->identity.tid && r->result_known);
 }}
for(unsigned i=0;i<32;++i)assert(!pthread_join(t[i],0));
assert(shared.begun==shared.completed && shared.begun+shared.rejected_full==3200);
assert(!shared.has_last_enoent && shared.stale==0 && shared.reentered==0);
''',extra=extra)

    def test_cxx_strict_header(self):
        self.run_c(r'''
struct sweet_bbctx_book b={};b.open=true;struct sweet_bbctx_identity id=ident(1);uint64_t e=0;
assert(sweet_bbctx_begin(&b,&id,0,nullptr,&e)==SWEET_BBCTX_OK);
assert(sweet_bbctx_complete(&b,&id,e,1,0,SWEET_BBCTX_RESULT_MACH)==SWEET_BBCTX_OK);
''',cxx=True)

    def test_later_user_entry_retires_only_same_incarnation_as_unknown(self):
        self.run_c(r'''
struct sweet_bbctx_book b={0};b.open=true;struct sweet_bbctx_identity a=ident(1),other=ident(2),wrong=a;uint64_t first,other_epoch,next;
assert(sweet_bbctx_begin(&b,&a,1,0,&first)==SWEET_BBCTX_OK);
assert(sweet_bbctx_begin(&b,&other,1,0,&other_epoch)==SWEET_BBCTX_OK);
wrong.idversion++;sweet_bbctx_retire_on_user_entry(&b,&wrong);
assert(sweet_bbctx_find(&b,&a,first) && b.retired_unknown==0);
sweet_bbctx_retire_on_user_entry(&b,&a);assert(!sweet_bbctx_find(&b,&a,first) && b.retired_unknown==1);
assert(sweet_bbctx_find(&b,&other,other_epoch)->state==SWEET_BBCTX_PENDING);
assert(sweet_bbctx_begin(&b,&a,2,0,&next)==SWEET_BBCTX_OK && next!=first);
assert(sweet_bbctx_complete(&b,&a,first,3,2,SWEET_BBCTX_RESULT_BSD)==SWEET_BBCTX_STALE && !b.has_last_enoent);
assert(sweet_bbctx_find(&b,&a,next)->state==SWEET_BBCTX_PENDING);
''')

    def test_policy_has_no_user_copy_logging_or_hardware(self):
        text=POLICY
        for x in ('copyinstr(', 'copyin(', 'copyout(', 'ml_io_map(', 'ufs_rd(', 'ufs_wr(', 'PE_parse_boot_argn(', 'printf(', 'IOLog('):
            self.assertNotIn(x,text)

if __name__=='__main__':unittest.main(verbosity=2)
