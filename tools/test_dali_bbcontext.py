#!/usr/bin/env python3
"""Execute exact 0181 bounded context code against synthetic kernel stubs."""
from pathlib import Path
import hashlib, os, shutil, subprocess, tempfile, unittest
import test_dali_gui_user_entry as fixture
import dali_bbcontext_parser as parser

ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / 'xnu/patches/0181-sweet-bounded-backboard-context.patch'
HEADERS = {name: fixture.fragment('bsd/dev/' + name, PATCH) for name in
           ('SweetBBContextPolicy.h', 'SweetBBContextRuntime.h')}
INTERFACE = fixture.fragment('bsd/sys/SweetBBContext.h', PATCH)

PREFIX=r'''
#include <assert.h>
#include <errno.h>
#include <pthread.h>
#include <stdarg.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define DEVELOPMENT 1
#define ARM64_BOARD_CONFIG_SWEET 1
#define MAXPATHLEN 1024
#undef ERESTART
#undef EJUSTRETURN
#define ERESTART (-1)
#define EJUSTRETURN (-2)
#define TASK_NULL NULL
#define PROC_NULL NULL
#define SWEET_LOAD_OPEN 4u
#define SWEET_LOAD_CLOSED 8u
#define SWEET_LOAD_TARGETS 2u
#define PSR64_IS_USER64(c) (((c)&31u)==0)
typedef void *task_t;
struct mock_proc { int32_t pid; uint32_t version; uint64_t uniqueid; };
typedef struct mock_proc *proc_t;
struct proc_ident { int32_t p_pid; uint32_t p_idversion; uint64_t p_uniqueid; };
struct arm_saved_state { bool wide; uint32_t cpsr; uint64_t x[8]; };
static struct mock_proc mock_proc={42,7,55};
static _Thread_local uint64_t mock_tid=100;
static _Thread_local bool mock_irq;
static task_t mock_task=(task_t)(uintptr_t)0x1000;
static bool sweet_load_configured;
static unsigned int sweet_ff_enabled;
static uint32_t sweet_load_control,sweet_load_ready;
struct sweet_load_slot {task_t task; int32_t pid; uint32_t version; uint64_t uniqueid,registered;};
static struct sweet_load_slot sweet_load_slots[2];
static uint32_t sweet_bbctx_stat_guard,sweet_bbctx_mach_guard;
static _Thread_local unsigned int fixture_guards;
static bool fixture_cas(uint32_t *p,uint32_t old,uint32_t next) {
 bool ok=__atomic_compare_exchange_n(p,&old,next,false,__ATOMIC_SEQ_CST,__ATOMIC_SEQ_CST);
 if(ok && next==1 && (p==&sweet_bbctx_stat_guard||p==&sweet_bbctx_mach_guard))++fixture_guards;
 return ok;
}
static void fixture_store(uint32_t *p,uint32_t value) {
 if(value==0 && (p==&sweet_bbctx_stat_guard||p==&sweet_bbctx_mach_guard)){assert(fixture_guards>0);--fixture_guards;}
 __atomic_store_n(p,value,__ATOMIC_SEQ_CST);
}
#define os_atomic_load(p,order) __atomic_load_n((p),__ATOMIC_SEQ_CST)
#define os_atomic_store(p,value,order) fixture_store((p),(value))
#define os_atomic_cmpxchg(p,old,next,order) fixture_cas((p),(old),(next))
static bool sweet_load_admitting(uint32_t v){return (v&(SWEET_LOAD_OPEN|SWEET_LOAD_CLOSED))==SWEET_LOAD_OPEN;}
static bool ml_at_interrupt_context(void){return mock_irq;}
static task_t current_task(void){return mock_task;}
static proc_t current_proc(void){return &mock_proc;}
static struct proc_ident proc_ident(proc_t p){struct proc_ident x={p->pid,p->version,p->uniqueid};return x;}
static void *current_thread(void){return (void *)(uintptr_t)mock_tid;}
static uint64_t thread_tid(void *p){return (uint64_t)(uintptr_t)p;}
static uint64_t fixture_clock;
static uint64_t mach_absolute_time(void){return __atomic_add_fetch(&fixture_clock,1,__ATOMIC_RELAXED);}
static bool is_saved_state64(struct arm_saved_state *s){return s->wide;}
static uint32_t get_saved_state_cpsr(struct arm_saved_state *s){return s->cpsr;}
static uint64_t get_saved_state_reg(struct arm_saved_state *s,unsigned int i){assert(i<8);return s->x[i];}
static char captured[400000];static size_t captured_len;
static int fixture_printf(const char *format,...) {
 assert(fixture_guards==0);
 va_list ap;va_start(ap,format);
 int n=vsnprintf(captured+captured_len,sizeof(captured)-captured_len,format,ap);
 va_end(ap);assert(n>=0 && (size_t)n<sizeof(captured)-captured_len);captured_len+=(size_t)n;return n;
}
#define printf fixture_printf
#include "SweetBBContextRuntime.h"
#undef printf
static void ready(void) {
 sweet_load_configured=true;sweet_ff_enabled=3;sweet_load_control=SWEET_LOAD_OPEN;sweet_load_ready=1;
 sweet_load_slots[0]=(struct sweet_load_slot){mock_task,42,7,55,1};
 sweet_bbctx_prepare(1);sweet_bbctx_window_begin();
}
'''

class FixedContextTests(unittest.TestCase):
    def run_c(self,body,extra=''):
        compiler=shutil.which('clang') or shutil.which('cc');self.assertIsNotNone(compiler)
        with tempfile.TemporaryDirectory(prefix='bbctx-runtime-') as td:
            p=Path(td);(p/'sys').mkdir()
            for name, content in HEADERS.items():
                (p/name).write_text(content)
            (p/'sys/SweetBBContext.h').write_text(INTERFACE)
            source=p/'test.c';source.write_text(PREFIX+'\n'+extra+'\nint main(void){(void)&ready;(void)&sweet_bbctx_trace_mach;(void)&sweet_bbctx_dump;\n'+body+'\nreturn 0;}\n')
            result=subprocess.run([compiler,'-std=gnu11','-O1','-g','-Wall','-Wextra','-Werror','-pthread','-fsanitize=address,undefined','-I',str(p),str(source),'-o',str(p/'test')],capture_output=True,text=True,timeout=45)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            result=subprocess.run([str(p/'test')],capture_output=True,text=True,timeout=30)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            return result.stdout

    def test_normal_paths_and_continuations_stay_unpaired(self):
        self.run_c(r'''
ready();struct sweet_bbctx_identity id={55,100,42,7};
uint64_t e=sweet_bbctx_stat_enter();assert(e);char p[]="/x";
assert(sweet_bbctx_namei_enter(p,sizeof(p))==e);sweet_bbctx_namei_return(e,2);
sweet_bbctx_stat_continuation_return(2);
struct sweet_bbctx_record *r=sweet_bbctx_find(&sweet_bbctx_stat_book,&id,e);
assert(r && r->state==SWEET_BBCTX_PENDING && !r->result_known && !sweet_bbctx_stat_book.has_last_enoent);
assert(sweet_bbctx_stat_book.has_unpaired && sweet_bbctx_stat_book.unpaired_result==2);
sweet_bbctx_dump(0);assert(strstr(captured,"SWEET BBCTXU:") && strstr(captured,"link=unknown"));
uint64_t next=sweet_bbctx_stat_enter();assert(next && next!=e);
assert(sweet_bbctx_stat_book.retired_unknown==1 && !sweet_bbctx_find(&sweet_bbctx_stat_book,&id,e));
sweet_bbctx_stat_return(next,2);assert(sweet_bbctx_stat_book.has_last_enoent);
''')
    def test_mach_stack_token_and_unpaired_continuation(self):
        self.run_c(r'''
ready();struct arm_saved_state s={.wide=true};for(unsigned i=1;i<8;++i)s.x[i]=i;
struct sweet_bbctx_identity id={55,100,42,7};uint64_t e=sweet_bbctx_mach_enter(-47,&s);assert(e);
s.x[0]=0x10004004;sweet_bbctx_trace_mach(3,-47,&s);
struct sweet_bbctx_record *r=sweet_bbctx_find(&sweet_bbctx_mach_book,&id,e);
assert(r && r->state==SWEET_BBCTX_PENDING && !r->result_known);
assert(sweet_bbctx_mach_book.has_unpaired && sweet_bbctx_mach_book.unpaired_result==0x10004004);
uint64_t n=sweet_bbctx_mach_enter(-47,&s);assert(n && n!=e && sweet_bbctx_mach_book.retired_unknown==1);
s.x[0]=0;sweet_bbctx_mach_return(e,-47,&s);assert(sweet_bbctx_mach_book.stale>=2);
r=sweet_bbctx_find(&sweet_bbctx_mach_book,&id,n);assert(r && !r->result_known);
sweet_bbctx_mach_return(n,-47,&s);assert(r->state==SWEET_BBCTX_COMPLETE && r->result==0);
''')
    def test_other_pending_slots_are_not_retired(self):
        self.run_c(r'''
ready();mock_tid=101;uint64_t a=sweet_bbctx_stat_enter();assert(a);
mock_tid=102;uint64_t b=sweet_bbctx_stat_enter();assert(b);
mock_tid=101;uint64_t c=sweet_bbctx_stat_enter();assert(c && c!=a);
struct sweet_bbctx_identity other={55,102,42,7};
struct sweet_bbctx_record *r=sweet_bbctx_find(&sweet_bbctx_stat_book,&other,b);
assert(r && r->state==SWEET_BBCTX_PENDING && sweet_bbctx_stat_book.retired_unknown==1);
''')
    def test_disabled_and_close(self):
        self.run_c(r'''
struct arm_saved_state s={.wide=true};assert(!sweet_bbctx_mach_enter(-47,&s));
sweet_bbctx_mach_return(0,-47,&s);assert(!sweet_bbctx_mach_book.has_unpaired);
ready();uint64_t e=sweet_bbctx_stat_enter();assert(e);sweet_bbctx_dump(39);
sweet_bbctx_stat_continuation_return(2);assert(!sweet_bbctx_stat_book.has_unpaired);
''')
    def test_path_bounds_and_identity_fail_closed(self):
        self.run_c(r'''
ready();mock_proc.version++;assert(!sweet_bbctx_stat_enter());
assert(!sweet_bbctx_namei_enter((const char *)(uintptr_t)1,4));mock_proc.version--;
uint64_t e=sweet_bbctx_stat_enter();assert(e);char path[194];memset(path,'A',193);path[193]=0;
assert(sweet_bbctx_namei_enter(path,sizeof(path))==e);sweet_bbctx_namei_return(e,2);sweet_bbctx_stat_return(e,2);
assert(sweet_bbctx_stat_book.last_enoent.path_state==SWEET_BBCTX_PATH_TRUNCATED);
assert(sweet_bbctx_stat_book.last_enoent.path_size==192);
''')
    def test_full_slots_exhaustion_and_saturation(self):
        self.run_c(r'''
ready();for(unsigned i=0;i<8;++i){mock_tid=200+i;assert(sweet_bbctx_stat_enter());}
mock_tid=300;assert(!sweet_bbctx_stat_enter());assert(sweet_bbctx_stat_book.rejected_full==1);
sweet_bbctx_mach_book.next_epoch=UINT64_MAX;struct arm_saved_state s={.wide=true};
assert(!sweet_bbctx_mach_enter(-47,&s));assert(sweet_bbctx_mach_book.exhausted);
sweet_bbctx_stat_book.unpaired_count=UINT64_MAX;sweet_bbctx_stat_continuation_return(2);
assert(sweet_bbctx_stat_book.unpaired_count==UINT64_MAX);
sweet_bbctx_dump(0);assert(strstr(captured,"full=1") && strstr(captured,"exhausted=1"));
''')
    def test_mach_raw_words_and_bsd_special_returns(self):
        self.run_c(r'''
ready();struct arm_saved_state s={.wide=true};for(unsigned i=1;i<8;++i)s.x[i]=0x100000000ULL+i;
struct sweet_bbctx_identity id={55,100,42,7};uint64_t e=sweet_bbctx_mach_enter(-47,&s);assert(e);
s.x[0]=2;sweet_bbctx_mach_return(e,-47,&s);struct sweet_bbctx_record *r=sweet_bbctx_find(&sweet_bbctx_mach_book,&id,e);
assert(r && r->result==2 && r->return_kind==SWEET_BBCTX_RETURN_MACH && !sweet_bbctx_mach_book.has_last_enoent);
for(unsigned i=0;i<7;++i)assert(r->mach_words[i]==0x100000001ULL+i);
e=sweet_bbctx_stat_enter();assert(e);sweet_bbctx_stat_return(e,ERESTART);r=sweet_bbctx_find(&sweet_bbctx_stat_book,&id,e);assert(r && r->return_kind==SWEET_BBCTX_RETURN_BSD_RESTART);
e=sweet_bbctx_stat_enter();assert(e);sweet_bbctx_stat_return(e,EJUSTRETURN);r=sweet_bbctx_find(&sweet_bbctx_stat_book,&id,e);assert(r && r->return_kind==SWEET_BBCTX_RETURN_BSD_JUST_RETURN);
''')
    def test_worst_case_log_budget(self):
        self.run_c(r'''
ready();struct sweet_bbctx_book *books[2]={&sweet_bbctx_stat_book,&sweet_bbctx_mach_book};
for(unsigned b=0;b<2;++b){struct sweet_bbctx_book *book=books[b];
 book->begun=book->completed=book->rejected_full=book->reentered=book->stale=book->retired_unknown=UINT64_MAX;
 book->exhausted=true;book->has_unpaired=true;book->unpaired_count=book->unpaired_time=UINT64_MAX;
 book->unpaired_identity=(struct sweet_bbctx_identity){UINT64_MAX,UINT64_MAX,UINT32_MAX,UINT32_MAX};book->unpaired_result=INT32_MIN;
 for(unsigned i=0;i<8;++i){struct sweet_bbctx_record *r=&book->slots[i];
  r->identity=book->unpaired_identity;r->epoch=r->start=r->end=UINT64_MAX;r->state=SWEET_BBCTX_COMPLETE;
  r->result=r->namei_error=INT32_MIN;r->result_known=r->namei_result_known=true;
  r->return_kind=SWEET_BBCTX_RETURN_MACH;r->path_state=SWEET_BBCTX_PATH_TRUNCATED;
  r->path_size=192;r->namei_calls=UINT32_MAX;memset(r->path,255,192);
  for(unsigned j=0;j<7;++j)r->mach_words[j]=UINT64_MAX;
 }}
sweet_bbctx_stat_book.has_last_enoent=true;sweet_bbctx_stat_book.last_enoent=sweet_bbctx_stat_book.slots[0];
for(unsigned i=0;i<40;++i)sweet_bbctx_dump(i);
assert(captured_len<327680);const char *drop=captured;
while((drop=strstr(drop," dropped="))!=NULL){assert(!strncmp(drop," dropped=0\n",11));drop+=11;}
size_t start=0;for(size_t i=0;i<captured_len;++i)if(captured[i]=='\n'){
 assert(i-start+1<=255);start=i+1;}assert(start==captured_len);
''')
    def test_nested_and_unobserved_namei_never_claim_unique_path(self):
        self.run_c(r'''
ready();struct sweet_bbctx_identity id={55,100,42,7};char path[]="/first";
uint64_t e=sweet_bbctx_stat_enter();assert(e);assert(sweet_bbctx_namei_enter(path,sizeof(path))==e);
sweet_bbctx_namei_unobserved_event();sweet_bbctx_namei_return(e,2);sweet_bbctx_stat_return(e,2);
struct sweet_bbctx_record *r=sweet_bbctx_find(&sweet_bbctx_stat_book,&id,e);
assert(r && r->path_state==SWEET_BBCTX_PATH_AMBIGUOUS && !r->namei_result_known && r->namei_calls==2);
mock_tid=101;id.tid=101;e=sweet_bbctx_stat_enter();assert(e);sweet_bbctx_namei_unobserved_event();
assert(!sweet_bbctx_namei_enter(path,sizeof(path)));sweet_bbctx_stat_return(e,2);
r=sweet_bbctx_find(&sweet_bbctx_stat_book,&id,e);
assert(r && r->path_state==SWEET_BBCTX_PATH_AMBIGUOUS && !r->namei_result_known && r->namei_calls==2);
''')

    def test_actual_runtime_round_parses_and_detects_missing_output(self):
        text = self.run_c(r'''
ready();uint64_t e=sweet_bbctx_stat_enter();assert(e);char name[]="/synthetic";
assert(sweet_bbctx_namei_enter(name,sizeof(name))==e);sweet_bbctx_namei_return(e,2);sweet_bbctx_stat_return(e,2);
struct arm_saved_state s={.wide=true};for(unsigned i=1;i<8;++i)s.x[i]=i;
e=sweet_bbctx_mach_enter(-47,&s);assert(e);s.x[0]=0;sweet_bbctx_mach_return(e,-47,&s);
e=sweet_bbctx_stat_enter();assert(e);sweet_bbctx_stat_continuation_return(2);sweet_bbctx_dump(0);
assert(fwrite(captured,1,captured_len,stdout)==captured_len);
''')
        lines = [line+'\n' for line in text.splitlines() if not line.startswith('SWEET BBCTX CONFIG:')]
        parsed = parser.parse_round(lines)
        names = {name for name, _ in parsed}
        self.assertTrue({'SWEET BBCTXS', 'SWEET BBCTXC', 'SWEET BBCTX', 'SWEET BBCTXM',
                         'SWEET BBCTXP', 'SWEET BBCTXU', 'SWEET BBCTXEND'} <= names)
        self.assertTrue(any(row['validity'] == 'recorded' for name, row in parsed if name == 'SWEET BBCTX'))
        self.assertTrue(all(row['link'] == 'unknown' for name, row in parsed if name == 'SWEET BBCTXU'))
        for incomplete in (lines[:-1], lines[:1]+lines[2:],
                           lines[:-1]+[lines[-1].replace('dropped=0','dropped=1')]):
            with self.assertRaises(ValueError): parser.parse_round(incomplete)

    def test_parser_rejects_loss_and_false_association(self):
        row = ('SWEET BBCTX: v=1 s=0 k=S p=42 id=0x37 iv=0x7 th=0x64 e=0x1 '
               'q=2 vld=0 b=0x1 z=0x2 rk=2 r=2 kn=1 ps=1 pn=1 nc=1 nk=1 ne=2\n')
        _, data = parser.parse(row)
        self.assertEqual(data['validity'], 'loss')
        for bad in (row.rstrip('\n'), row.replace('vld=0','vld=2'), row.replace('q=2','q=1'),
                    row.replace('v=1','v=2'), row.replace('vld=0','vld=0 vld=1'),
                    'SWEET BBCTXU: v=1 s=0 k=S n=1 p=42 id=0x37 iv=0x7 th=0x64 t=0x1 r=2 link=paired\n',
                    'SWEET BBCTXM: v=1 s=0 e=0x1 hex=1,2,3\n',
                    'SWEET BBCTXP: v=1 s=0 e=0x1 off=191 n=2 ps=1 hex=abcd\n'):
            with self.subTest(bad=bad), self.assertRaises(ValueError): parser.parse(bad)

    def test_exact_materialized_sources_if_available(self):
        directory = os.environ.get('SWEET_BBCTX_SOURCE_DIR')
        if not directory:
            self.skipTest('pre-build: no materialized XNU source tree')
        # Final source also includes 0197 lsd selection and 0198 CPU activity.
        # The historical 0181 runtime fixture above remains intentionally fixed;
        # test_dali_lsd_context executes the actual final mode4 implementation.
        expected = {
            'bsd/dev/sweet_sample.c': '47a4b8c966c3d156a596da1e0181d046bc09ddd7eb788a8892944380e0cfe7e2',
            'bsd/dev/arm/systemcalls.c': '236d1c88f4b21c3bfa6ebf6515b48ea47514c119f1aed2886b26fbadcedd71f6',
            'bsd/vfs/vfs_lookup.c': 'f358de08b87531aaac31b049722284d93519f6932615cbc9d059db3e7d7b18b7',
            'osfmk/arm64/sleh.c': 'af9ea337637806b4dad91ba65da83ec809e1ba01f4b8351323eb0f63aa191c3b',
            'bsd/sys/Makefile': '4f33af1f3768241e92d90755f54fc277469fd88f5b911fa32e3c123befef6b57',
            'bsd/dev/SweetBBContextPolicy.h': 'fa67dc3baebf5daabc19d601bca832d439a9fa25a0099666e875a29ffdf52d17',
            'bsd/dev/SweetBBContextRuntime.h': '0c062280dbf83e9f2f735f1ec4359777d43c15b41b9d5d156dfa735ed1512a18',
            'bsd/sys/SweetBBContext.h': '76c3fc654418553aad42d05f86be9d568eff403c8ecfd829c721726368e21d4c',
        }
        for path, digest in expected.items():
            self.assertEqual(hashlib.sha256((Path(directory)/path).read_bytes()).hexdigest(), digest, path)

    def test_patch_hooks_and_nonlocal_returns_are_scoped(self):
        patch = PATCH.read_text()
        self.assertEqual(patch.count('diff --git '), 8)
        self.assertEqual(patch.count('new file mode 100644'), 3)
        self.assertNotIn('diff --git a/bsd/sys/user.h ', patch)
        self.assertNotIn('diff --git a/bsd/kern/kern_fork.c ', patch)
        sleh = fixture.fragment('osfmk/arm64/sleh.c', PATCH)
        self.assertLess(sleh.index('sweet_bbctx_mach_enter(trap_no, state)'), sleh.index('mach_syscall(state)'))
        self.assertLess(sleh.index('mach_syscall(state)'), sleh.index('sweet_bbctx_mach_return(sweet_bbctx_mach_epoch, trap_no, state)'))
        syscall = fixture.fragment('bsd/dev/arm/systemcalls.c', PATCH)
        self.assertIn('sweet_bbctx_stat_continuation_return(error)', syscall)
        namei = fixture.fragment('bsd/vfs/vfs_lookup.c', PATCH)
        self.assertIn('sweet_bbctx_namei_enter(cnp->cn_pnbuf, bytes_copied)', namei)
        self.assertIn('sweet_bbctx_namei_unobserved_event()', namei)
        runtime = HEADERS['SweetBBContextRuntime.h']
        self.assertIn('if (phase == 3) sweet_bbctx_mach_return(0, call, state);', runtime)
        for operation in ('copyinstr(', 'copyin(', 'copyout(', 'ml_io_map(', 'ufs_wr('):
            self.assertNotIn(operation, runtime)

    def test_new_file_mode_is_required_by_git(self):
        blocks = PATCH.read_text().split('diff --git ')[1:]
        target = [block for block in blocks if block.startswith(
            'a/bsd/dev/SweetBBContextPolicy.h b/bsd/dev/SweetBBContextPolicy.h\n')]
        self.assertEqual(len(target), 1)
        good = 'diff --git '+target[0]
        self.assertEqual(good.count('new file mode 100644\n'), 1)
        with tempfile.TemporaryDirectory(prefix='bbctx-git-') as temp:
            root = Path(temp)
            subprocess.run(['git', 'init', '-q', str(root)], check=True)
            patch = root/'input.patch'; patch.write_text(good)
            subprocess.run(['git', '-C', str(root), 'apply', '--check', str(patch)], check=True)
            patch.write_text(good.replace('new file mode 100644\n', '', 1))
            bad = subprocess.run(['git', '-C', str(root), 'apply', '--check', str(patch)],
                                 capture_output=True, text=True)
            self.assertNotEqual(bad.returncode, 0)

    def test_parallel_guard_contention_never_publishes_false_pairs(self):
        extra = r'''
static void *producer(void *arg){mock_tid=(uint64_t)(uintptr_t)arg;assert(!sweet_bbctx_stat_enter());return NULL;}
'''
        self.run_c(r'''
ready();uint64_t e=sweet_bbctx_stat_enter();assert(e);
__atomic_store_n(&sweet_bbctx_stat_guard,1,__ATOMIC_SEQ_CST);
pthread_t threads[32];for(unsigned i=0;i<32;++i)assert(!pthread_create(&threads[i],NULL,producer,(void *)(uintptr_t)(200+i)));
for(unsigned i=0;i<32;++i)assert(!pthread_join(threads[i],NULL));
assert(sweet_bbctx_stat_loss==1 && sweet_bbctx_stat_book.begun==1);
__atomic_store_n(&sweet_bbctx_stat_guard,0,__ATOMIC_SEQ_CST);sweet_bbctx_stat_return(e,2);
assert(!sweet_bbctx_stat_book.has_last_enoent);sweet_bbctx_dump(0);assert(strstr(captured,"vld=0"));
''',extra=extra)

    def test_guard_contention_is_sticky(self):
        self.run_c(r'''
ready();uint64_t e=sweet_bbctx_stat_enter();assert(e);
__atomic_store_n(&sweet_bbctx_stat_guard,1,__ATOMIC_SEQ_CST);
sweet_bbctx_stat_continuation_return(2);assert(sweet_bbctx_stat_loss==1);
__atomic_store_n(&sweet_bbctx_stat_guard,0,__ATOMIC_SEQ_CST);
sweet_bbctx_stat_return(e,2);assert(!sweet_bbctx_stat_book.has_last_enoent);
sweet_bbctx_dump(0);assert(strstr(captured,"sloss=1") && strstr(captured,"vld=0"));
''')

if __name__ == '__main__': unittest.main(verbosity=2)
