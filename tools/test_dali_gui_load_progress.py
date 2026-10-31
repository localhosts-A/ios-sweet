#!/usr/bin/env python3
"""Exercise final0175 functions; optional SWEET_LOAD_SOURCE_DIR verifies materialized sources."""
from pathlib import Path
import os
import re
import shutil
import subprocess
import tempfile
import unittest
import test_dali_gui_user_entry as fixture
import test_dali_backboard_ipc_context as ipc
import test_dali_backboard_vm_fault as vm

ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT/'xnu/patches/0175-sweet-bounded-gui-load-progress.patch'
PATHS = ('bsd/dev/sweet_sample.c', 'bsd/dev/sweet_ufs.c', 'osfmk/kern/exception.c',
         'osfmk/kern/SweetLoadProgress.h', 'bsd/dev/SweetUFSLoadPolicy.h')
SOURCE = {p: fixture.fragment(p, PATCH) for p in PATHS}
SAMPLER, UFS, EXCEPTION, GUI_POLICY, IO_POLICY = (SOURCE[p] for p in PATHS)


def function(source, name):
    match = re.search(r'(?:static (?:inline )?)?(?:void|bool|int|uint32_t)\n'+name+r'\(', source)
    if not match:
        raise AssertionError(name)
    start = source.index('{', match.start()); end = start+1; depth = 1
    while depth:
        depth += (source[end] == '{')-(source[end] == '}'); end += 1
    return source[match.start():end]+'\n'


def old_fragment(path):
    block = next(b for b in PATCH.read_text().split('diff --git ')[1:] if f'b/{path}\n' in b)
    return ''.join(l[1:] for l in block.splitlines(keepends=True)
                   if l.startswith(('-', ' ')) and not l.startswith('---'))


def parse_record(line):
    """Strict versioned raw records: missing/busy data never becomes zero."""
    prefix, sep, rest = line.partition(': ')
    if not sep or not line.endswith('\n') or len(line.encode()) > 255:
        raise ValueError('incomplete/oversized record')
    pairs = [p.split('=', 1) for p in rest.split()]
    if any(len(p) != 2 for p in pairs):
        raise ValueError('malformed fields')
    raw = dict(pairs)
    if len(raw) != len(pairs):
        raise ValueError('duplicate field')
    patterns = {
        'SWEET LOAD': 'v s at claims ready miss final',
        'SWEET LOADP': 'v s g p id iv rg q',
        'SWEET LOADT': 'v s g j th q',
        'SWEET LOADIO STATUS': 'v s at q final pending',
        'SWEET LOADIO': 'v s g n vld d m x',
    }
    if prefix not in patterns:
        raise ValueError('not a data/status record')
    expected = set(patterns[prefix].split())
    try:
        values = {k: (v if k == 'pending' else int(v, 0)) for k, v in raw.items()}
    except ValueError as error:
        raise ValueError('invalid number') from error
    if values.get('v') != 1 or not 0 <= values.get('s', -1) < 40:
        raise ValueError('version/sample')
    q = values.get('q')
    if prefix == 'SWEET LOADP':
        if q not in (0, 1, 2): raise ValueError('process status')
        if q == 0: expected |= set('n c b z u k'.split())
    elif prefix == 'SWEET LOADT':
        if q not in (0, 1): raise ValueError('thread status')
        if q == 0: expected |= set('st h pr bp wr b z u k'.split())
    elif prefix == 'SWEET LOADIO STATUS':
        if q not in (0, 1) or values.get('pending') != 'unknown': raise ValueError('IO status')
        if q == 0: expected |= {'z', 'ov'}
    elif prefix == 'SWEET LOADIO':
        if not 0 <= values.get('g', -1) < 5: raise ValueError('IO group')
        expected |= {'b', 'e'} if values['g'] < 3 else {'w', 'wm'}
    if set(raw) != expected:
        raise ValueError('missing/unexpected field')
    signed = {'p', 'pr', 'bp', 'wr'}
    for k, value in values.items():
        if k == 'pending': continue
        if k in signed:
            if not -(1 << 31) <= value < 1 << 31: raise ValueError('signed width')
        elif not 0 <= value < 1 << 64: raise ValueError('unsigned width')
    small = {'iv', 'n', 'st', 'h'} if prefix in ('SWEET LOADP', 'SWEET LOADT') else set()
    if any(values[k] >= 1 << 32 for k in small if k in values): raise ValueError('32-bit width')
    if 'ov' in values and values['ov'] not in (0, 1): raise ValueError('overflow flag')
    if prefix in ('SWEET LOADP', 'SWEET LOADT') and not 0 <= values['g'] < 2: raise ValueError('target')
    if 'j' in values and not 0 <= values['j'] < 8: raise ValueError('thread index')
    if prefix == 'SWEET LOADP' and q == 0 and not 0 <= values['c'] <= 8: raise ValueError('thread cap')
    if 'final' in values and values['final'] != int(values['s'] == 39): raise ValueError('final')
    if prefix == 'SWEET LOAD' and (values['claims'] & ~3 or values['ready'] & ~values['claims'] or values['miss'] not in (0, 1)):
        raise ValueError('publication bits')
    return dict(kind=prefix, values=values, raw_line=line)


def accounting_delta(previous, current):
    """Callers supply same-boot stable identity; do not subtract a different generation."""
    required = ('boot', 'g', 'id', 'iv')
    if any(k not in previous or k not in current for k in required): return None
    keys = required + ('th',)
    if any(previous.get(k) != current.get(k) for k in keys): return None
    if any(k not in previous or k not in current for k in ('u', 'k', 'b', 'z')): return None
    if previous['z'] < previous['b'] or current['z'] < current['b'] or current['b'] < previous['z']: return None
    if current['u'] < previous['u'] or current['k'] < previous['k']: return None
    return (current['u']-previous['u'])+(current['k']-previous['k'])

def parse_boot_records(text):
    """Preserve boot/sample provenance, gaps and duplicates without inventing observations."""
    boots = {}; current = None
    for line in text.splitlines(keepends=True):
        match = re.search(r'SWEET BOOT SESSION UUID: ([0-9A-Fa-f]{8}(?:-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12})', line)
        if match:
            current = match[1].upper()
            boots.setdefault(current, dict(records=[], rejected=[], samples={}))
        if current is None or not any(line.startswith(prefix) for prefix in
                                      ('SWEET LOAD: ', 'SWEET LOADP: ', 'SWEET LOADT: ', 'SWEET LOADIO: ', 'SWEET LOADIO STATUS: ')):
            continue
        boot = boots[current]
        try:
            record = parse_record(line)
        except ValueError as error:
            boot['rejected'].append(dict(raw_line=line, error=str(error))); continue
        v = record['values']; sample = boot['samples'].setdefault(v['s'], dict(records=[], duplicates=[], missing=[]))
        key = (record['kind'], v.get('g'), v.get('j'))
        if any((r['kind'],r['values'].get('g'),r['values'].get('j')) == key for r in sample['records']):
            sample['duplicates'].append(record)
        else:
            sample['records'].append(record)
        boot['records'].append(record)
    for boot in boots.values():
        for sample in boot['samples'].values():
            rows = sample['records']; status = next((r['values'] for r in rows if r['kind']=='SWEET LOAD'),None)
            io = next((r['values'] for r in rows if r['kind']=='SWEET LOADIO STATUS'),None)
            if status is None: sample['missing'].append('GUI status')
            else:
                for target in range(2):
                    if not status['ready'] & (1 << target): continue
                    process = next((r['values'] for r in rows if r['kind']=='SWEET LOADP' and r['values']['g']==target),None)
                    if process is None: sample['missing'].append(f'process {target}')
                    elif process['q']==0:
                        for index in range(process['c']):
                            if not any(r['kind']=='SWEET LOADT' and r['values']['g']==target and r['values']['j']==index for r in rows):
                                sample['missing'].append(f'thread {target}/{index}')
            if io is None: sample['missing'].append('IO status')
            elif io['q']==0:
                for group in range(5):
                    if not any(r['kind']=='SWEET LOADIO' and r['values']['g']==group for r in rows):
                        sample['missing'].append(f'IO group {group}')
    return boots


PREFIX = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <stdarg.h>
#include <pthread.h>
#ifdef bzero
#undef bzero
#endif
#define bzero(p,n) memset(p,0,n)
#define os_atomic_load(p,o) __atomic_load_n((p),__ATOMIC_ACQUIRE)
#define os_atomic_store(p,v,o) __atomic_store_n((p),(v),__ATOMIC_RELEASE)
#define os_atomic_or(p,v,o) ((void)__atomic_fetch_or((p),(v),__ATOMIC_ACQ_REL))
static void (*atomic_hook)(uint32_t *,bool);
static void (*atomic_before)(uint32_t *);
static bool atomic_cas(uint32_t *p,uint32_t old,uint32_t value) {
 if(atomic_before)atomic_before(p);
 bool ok=__atomic_compare_exchange_n(p,&old,value,false,__ATOMIC_ACQ_REL,__ATOMIC_ACQUIRE);
 if(atomic_hook)atomic_hook(p,ok);return ok;
}
#define os_atomic_cmpxchg(p,o,n,m) atomic_cas((p),(o),(n))
'''
GUI_HELPERS = SAMPLER[SAMPLER.index('static bool sweet_load_configured;'):SAMPLER.index('\nvoid sweet_ff_trace(')]
GUI_MOCK = ipc.MOCK.replace('static struct process process=', 'static _Thread_local struct process process=')
GUI_MOCK += r'''
struct task {unsigned references;};typedef struct task *task_t;
#define TASK_NULL NULL
static struct task target_task;
static bool eligible=true;static unsigned task_lookups,ident_reads,collector_calls,io_calls,io_closes,io_begins;
static task_t current_task(void) {__atomic_add_fetch(&task_lookups,1,__ATOMIC_RELAXED);return &target_task;}
static void task_reference(task_t t) {__atomic_add_fetch(&t->references,1,__ATOMIC_RELAXED);}
struct proc_ident {int p_pid;uint64_t p_uniqueid;uint32_t p_idversion;};
static struct proc_ident proc_ident(proc_t p) {__atomic_add_fetch(&ident_reads,1,__ATOMIC_RELAXED);return (struct proc_ident){p->pid,123,1};}
bool sweet_ufs_load_eligible(void) {return eligible;}
void sweet_ufs_load_begin(void) {++io_begins;}
void sweet_ufs_load_close(void) {++io_closes;}
void sweet_ufs_load_dump(unsigned int s) {assert(s<40);++io_calls;}
void sweet_load_collect(task_t t,struct sweet_load_snapshot *out) {
 assert(t==&target_task);++collector_calls;bzero(out,sizeof(*out));out->captured=8;out->thread_count=10;
 for(unsigned i=0;i<8;++i)out->threads[i].tid=i+1;
}
'''
IPC_HELPERS = ipc.HELPERS
GUI_TAIL = ipc.TAIL + r'''
static void load_reset(void) {
 reset();sweet_load_configured=false;sweet_load_control=sweet_load_ready=sweet_load_missed=0;sweet_load_dumped=0;
 bzero(sweet_load_slots,sizeof(sweet_load_slots));bzero(&target_task,sizeof(target_task));
 task_lookups=ident_reads=collector_calls=io_calls=io_closes=io_begins=0;eligible=true;atomic_hook=NULL;atomic_before=NULL;
}
static void load_common(void) {
 common();load_reset();sweet_load_prepare(0);sweet_load_begin();sweet_load_register(0,&regs);sweet_load_dump(0);
 assert(!current_calls && !clock_reads && !task_lookups && !target_task.references && !io_begins);
 load_reset();
}
'''


class LoadProgressTest(unittest.TestCase):
    def compile_run(self, body, *, prefix=PREFIX, expect='PASS', compiler_error=None):
        compiler = shutil.which('clang') or shutil.which('cc'); self.assertIsNotNone(compiler)
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp); (p/'test.c').write_text(prefix+body)
            command=[compiler, '-std=c11', '-O2', '-Wall', '-Wextra', '-Werror', '-pthread',
                     str(p/'test.c'), '-o', str(p/'test')]
            if compiler_error is not None:
                failed=subprocess.run(command,capture_output=True,text=True)
                self.assertNotEqual(failed.returncode,0)
                self.assertIn(compiler_error,failed.stderr)
                return ''
            subprocess.run(command,check=True)
            r=subprocess.run([str(p/'test')], capture_output=True, text=True, timeout=30)
            self.assertEqual(r.returncode, 0, r.stderr)
            if expect is not None: self.assertEqual(r.stdout.strip(), expect)
            return r.stdout

    def gui(self, body, extra='', compiler_error=None):
        # Every exercised producer/dump comes from0175; old helpers are byte-checked below.
        code=ipc.LEGACY+ipc.POLICY+GUI_POLICY+GUI_MOCK+IPC_HELPERS+GUI_HELPERS+function(SAMPLER,'sweet_ff_trace')+function(SAMPLER,'sweet_ff_dump')+GUI_TAIL+extra+body
        return self.compile_run(code.replace('int main(void) {','int main(void) { load_common();'),compiler_error=compiler_error)

    def test_registration_modes_filters_and_first_generation(self):
        self.gui(r'''
int main(void) {
 for(unsigned i=0;i<4;++i){load_reset();sweet_load_prepare(i);if(i!=1)assert(!sweet_load_configured);}
 load_reset();eligible=false;sweet_load_prepare(1);assert(!sweet_load_configured);eligible=true;sweet_load_prepare(1);
 sweet_ff_enabled=0;sweet_ff_trace(0,5,&regs);assert(!target_task.references); /* before consumer begins */
 sweet_load_begin();sweet_load_begin();assert(io_begins==1);
 sweet_ff_trace(1,5,&regs);sweet_ff_trace(3,5,&regs);sweet_load_register(0,NULL);regs.wide=false;sweet_ff_trace(0,5,&regs);regs.wide=true;
 regs.cpsr=5;sweet_ff_trace(0,5,&regs);regs.cpsr=0;assert(!target_task.references);
 strcpy(process.p_comm,"backboarddX");sweet_ff_trace(0,5,&regs);assert(!target_task.references);
 strcpy(process.p_comm,"backboardd");sweet_ff_trace(0,5,&regs);assert(target_task.references==1 && sweet_load_ready==1 && !sweet_ff_events.total);
 sweet_ff_trace(0,5,&regs);assert(target_task.references==1);
 strcpy(process.p_comm,"SpringBoard");sweet_ff_trace(0,5,&regs);assert(target_task.references==2 && sweet_load_ready==3);
 unsigned before=current_calls,clocks=clock_reads;for(unsigned i=0;i<100;++i)sweet_ff_trace(0,5,&regs);
 assert(current_calls==before && clock_reads==clocks && target_task.references==2);
 sweet_load_dump(0);assert(collector_calls==2 && io_calls==1);puts("PASS");return 0;
}
''')

    def test_actual_new_svc_preserves_old_ring_and_ipc_when_off(self):
        old=function(ipc.SOURCE,'sweet_ff_trace').replace('sweet_ff_trace(', 'old_trace(')
        self.gui(r'''
int main(void) {
 struct sweet_ff_ring baseline;struct sweet_bbipc_buffer context;
 sweet_bbipc_prepare(1);sweet_bbipc_begin();
 for(unsigned i=0;i<80;++i)old_trace(i%3,-47,&regs);
 baseline=sweet_ff_events;context=sweet_bbipc_events;unsigned oldreads=register_reads,oldclock=clock_reads;
 load_reset();sweet_bbipc_prepare(1);sweet_bbipc_begin();
 for(unsigned i=0;i<80;++i)sweet_ff_trace(i%3,-47,&regs);
 assert(!memcmp(&baseline,&sweet_ff_events,sizeof(baseline)) && !memcmp(&context,&sweet_bbipc_events,sizeof(context)));
 assert(register_reads==oldreads && clock_reads==oldclock && !target_task.references);
 sweet_load_prepare(1);sweet_load_begin();sweet_ff_events.total=SWEET_FF_EVENT_LIMIT;
 sweet_ff_trace(0,-47,&regs);assert(target_task.references==1 && sweet_ff_events.total==SWEET_FF_EVENT_LIMIT);
 sweet_ff_enabled=2;strcpy(process.p_comm,"SpringBoard");sweet_ff_trace(0,5,&regs);assert(target_task.references==2);
 puts("PASS");return 0;
}
''',old)

    def test_closure_before_and_after_claim_and_exact_budget(self):
        self.gui(r'''
static void close_after_claim(uint32_t *p,bool ok) {
 if(ok && p==&sweet_load_control){atomic_hook=NULL;os_atomic_or(&sweet_load_control,SWEET_LOAD_CLOSED,acq_rel);assert(!sweet_load_ready);}
}
static void close_before_claim(uint32_t *p) {
 if(p==&sweet_load_control){atomic_before=NULL;os_atomic_or(p,SWEET_LOAD_CLOSED,acq_rel);}
}
int main(void) {
 sweet_load_prepare(1);sweet_load_begin();atomic_before=close_before_claim;sweet_load_register(0,&regs);
 assert(!target_task.references && !sweet_load_ready && sweet_load_missed==1);
 load_reset();sweet_load_prepare(1);sweet_load_begin();atomic_hook=close_after_claim;sweet_load_register(0,&regs);
 assert(target_task.references==1 && sweet_load_ready==1 && !sweet_load_admitting(sweet_load_control));
 strcpy(process.p_comm,"SpringBoard");sweet_load_register(0,&regs);assert(target_task.references==1);
 load_reset();sweet_load_prepare(1);sweet_load_begin();sweet_load_register(0,&regs);strcpy(process.p_comm,"SpringBoard");sweet_load_register(0,&regs);
 for(unsigned i=0;i<40;++i){sweet_load_dump(i);sweet_load_dump(i);}
 assert(sweet_load_dumped==40 && io_calls==40 && io_closes==1 && collector_calls==80);
 assert(!sweet_load_admitting(sweet_load_control) && target_task.references==2);
 sweet_load_begin();sweet_load_dump(40);assert(io_calls==40 && io_begins==1);
 unsigned count=0;for(char *p=captured;*p;++p)count+=*p=='\n';assert(count==761);
 puts("PASS");return 0;
}
''')

    def test_policy_arithmetic_and_full_width_console_roundtrip(self):
        # Reuse the real console functions from the established255-byte regression fixture.
        prefix=PREFIX[:PREFIX.index('#define os_atomic_load')]+r'''
#define SERIALMODE_SYNCDRAIN 1
static int serialmode;static char delivered[4096];static size_t delivered_size;
static void console_write(char *s,int n){assert(n>=0 && delivered_size+(size_t)n<sizeof(delivered));memcpy(delivered+delivered_size,s,(size_t)n);delivered_size+=(size_t)n;delivered[delivered_size]=0;}
'''
        body=GUI_POLICY+IO_POLICY+ipc.CONSOLE_SOURCE+r'''
static void route(const char *text){struct console_printbuf_state s;console_printbuf_state_init(&s,1,1);for(const char *p=text;*p;++p)console_printbuf_putc(*p,&s);console_printbuf_clear(&s);}
int main(void){
 struct sweet_ufs_load_counters c={0};sweet_ufs_load_record(&c,0,4096,0,10,10,20);assert(c.groups[0].elapsed==10 && c.groups[0].valid==1);
 sweet_ufs_load_record(&c,0,4096,5,20,20,19);assert(c.groups[0].errors==1 && c.groups[0].bad_clock==1 && c.groups[0].valid==1);
 sweet_ufs_load_record(&c,3,0,0,10,15,25);assert(c.groups[3].wait==5 && c.groups[3].elapsed==10);
 c.groups[0].bytes=UINT64_MAX;sweet_ufs_load_record(&c,0,1,0,0,0,0);assert(c.overflow && c.groups[0].bytes==UINT64_MAX);
 assert(sweet_ufs_load_kind(0x28)==0 && sweet_ufs_load_kind(0x2a)==1 && sweet_ufs_load_kind(0x35)==2 && sweet_ufs_load_kind(0xff)==5);
 assert(sweet_load_kind("SpringBoard")==1 && sweet_load_kind("bad")==2 && sweet_load_admitting(SWEET_LOAD_OPEN));
 char line[1024];memset(line,'x',300);line[300]='\n';line[301]=0;route(line);assert(delivered_size==255 && !strchr(delivered,'\n'));delivered_size=0;
 struct sweet_load_snapshot p;memset(&p,255,sizeof(p));p.status=0;p.captured=8;
 int n=sweet_load_process_format(line,256,39,1,INT32_MIN,UINT64_MAX,UINT32_MAX,UINT64_MAX,&p);assert(n>0);route(line);assert(delivered_size==(size_t)n);fputs(delivered,stdout);delivered_size=0;
 struct sweet_load_thread t;memset(&t,255,sizeof(t));t.status=0;t.priority=t.base_priority=t.wait_result=INT32_MIN;
 n=sweet_load_thread_format(line,256,39,1,7,&t);assert(n>0);route(line);assert(delivered_size==(size_t)n);fputs(delivered,stdout);delivered_size=0;
 struct sweet_ufs_load_group g;memset(&g,255,sizeof(g));
 for(unsigned i=0;i<5;++i){n=sweet_ufs_load_format(line,256,39,i,&g);assert(n>0);route(line);assert(delivered_size==(size_t)n);fputs(delivered,stdout);delivered_size=0;}
 p.status=1;assert(sweet_load_process_format(line,256,39,1,1,1,1,1,&p)>0);assert(!strstr(line," u="));
 t.status=1;assert(sweet_load_thread_format(line,256,39,1,7,&t)>0 && !strstr(line," st="));
 assert(!sweet_load_thread_format(line,1,39,1,7,&t));return 0;
}
'''
        output=self.compile_run(body,prefix=prefix,expect=None)
        guard='#ifdef bzero\n#undef bzero\n#endif\n#define bzero(p,n) memset(p,0,n)'
        fortified='#ifdef bzero\n#undef bzero\n#endif\n#define bzero(dest, ...) __builtin___memset_chk(dest,0,__VA_ARGS__,__builtin_object_size(dest,0))'
        self.assertEqual(self.compile_run(body,prefix=prefix.replace(guard,fortified+'\n'+guard),expect=None),output)
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp); (p/'bad.c').write_text(prefix.replace(guard,fortified+'\n#define bzero(p,n) memset(p,0,n)')+body)
            failed=subprocess.run([shutil.which('clang') or shutil.which('cc'),'-std=c11','-Wall','-Wextra','-Werror','-c',str(p/'bad.c'),'-o',str(p/'bad.o')],capture_output=True,text=True)
            self.assertNotEqual(failed.returncode,0); self.assertIn("'bzero' macro redefined",failed.stderr)
        records=[parse_record(line) for line in output.splitlines(keepends=True)]
        self.assertEqual(len(records),7)
        self.assertTrue(all(len(r['raw_line'].encode())<=255 for r in records))

    def test_parser_rejects_missing_duplicate_and_cross_generation_deltas(self):
        line='SWEET LOADT: v=1 s=39 g=1 j=0 th=0x1 q=1\n'
        self.assertNotIn('u',parse_record(line)['values'])
        for invalid in (line[:-1],line.replace('v=1','v=2'),line.replace('q=1','q=1 u=0'),line.replace('g=1','g=2'),line.replace('th=0x1','th=0x1 th=0x1')):
            with self.assertRaises(ValueError): parse_record(invalid)
        a=dict(boot='a',g=0,id=1,iv=1,th=2,u=10,k=20,b=1,z=2);b=dict(a,u=15,k=22,b=3,z=4)
        self.assertEqual(accounting_delta(a,b),7)
        self.assertIsNone(accounting_delta({k:v for k,v in a.items() if k!='boot'},b))
        for bad in (dict(b,boot='b'),dict(b,iv=2),dict(b,u=0),dict(b,z=0),dict(b,b=1)):
            self.assertIsNone(accounting_delta(a,bad))

    def test_final_source_and_unchanged_old_helpers(self):
        if os.environ.get('SWEET_LOAD_SOURCE_DIR'):
            directory=Path(os.environ['SWEET_LOAD_SOURCE_DIR'])
            for path,text in SOURCE.items(): self.assertEqual((directory/path).read_text(encoding='latin-1'),text,path)
            self.assertEqual((directory/'osfmk/kern/Makefile').read_text(),fixture.fragment('osfmk/kern/Makefile',PATCH))
        old=old_fragment(PATHS[0])
        for name in ('sweet_bbpc_trace','sweet_bbvm_enter','sweet_bbvm_leave','sweet_ff_fault_trace','sweet_ff_screen_dump','sweet_ff_dump'):
            self.assertEqual(function(SAMPLER,name),function(old,name),name)
        self.assertEqual(function(SAMPLER,'sweet_ff_trace').replace('    sweet_load_register(phase, state);\n',''),function(old,'sweet_ff_trace'))
        for name in ('ufs_scsi_on_lun_timeout','ufs_sync_cache','sweet_log_thread'):
            self.assertEqual(function(UFS,name),function(old_fragment(PATHS[1]),name),name)
        self.assertEqual(function(EXCEPTION,'sweet_sample_task'),function(old_fragment(PATHS[2]),'sweet_sample_task'))
        for word in ('proc_iterate','proc_find','vm_map_read_user','task_deallocate','thread_deallocate','printf','wait_event','runq'):
            self.assertNotIn(word,function(EXCEPTION,'sweet_load_collect'))
        self.assertNotIn('task_deallocate',GUI_HELPERS)
        self.assertEqual(PATCH.read_text().count('diff --git '),6)
        self.assertIn('SweetLoadProgress.h',fixture.fragment('osfmk/kern/Makefile',PATCH))

    def test_actual_collector_bounded_locks_lifetimes_and_unknowns(self):
        mock=r'''
#define DEVELOPMENT 1
struct recount_times_mach {uint64_t rtm_user,rtm_system;};
struct thread {struct thread *next;uint64_t tid;int sched_lock;uint32_t state,block_hint;int sched_pri,base_pri,wait_result;};
typedef struct thread *thread_t;
struct task {bool active;uint32_t thread_count;struct thread *threads;};typedef struct task *task_t;
static bool task_held,spl_held,thread_held,busy;static unsigned tries,tasktries,reads,clocks;static int thread_lck_grp;
typedef int spl_t;
static bool task_lock_try(task_t t){assert(t && !task_held && !spl_held);++tasktries;if(busy)return false;task_held=true;return true;}
static void task_unlock(task_t t){assert(t && task_held && !spl_held && !thread_held);task_held=false;}
static spl_t splsched(void){assert(task_held && !spl_held);spl_held=true;return 7;}
static void splx(spl_t s){assert(s==7 && spl_held && !thread_held);spl_held=false;}
static bool simple_lock_try(int *lock,int *group){assert(task_held && spl_held && group==&thread_lck_grp);++tries;if(*lock)return false;thread_held=true;return true;}
static void thread_unlock(thread_t t){assert(t && thread_held && task_held && spl_held);thread_held=false;}
static uint64_t thread_tid(thread_t t){assert(task_held);return t->tid;}
static uint64_t mach_absolute_time(void){assert(task_held);return ++clocks;}
static struct recount_times_mach recount_task_times(task_t t){assert(t && task_held && !spl_held);++reads;return (struct recount_times_mach){10,20};}
static struct recount_times_mach recount_thread_times(thread_t t){assert(t && task_held && spl_held && thread_held);++reads;return (struct recount_times_mach){t->tid,t->tid+1};}
#define queue_iterate(head,item,type,field) for((item)=*(head);(item);(item)=(item)->next)
'''
        self.compile_run(GUI_POLICY+mock+function(EXCEPTION,'sweet_load_collect')+r'''
int main(void){
 struct task task={.active=true,.thread_count=12};struct thread threads[12]={0};task.threads=threads;
 for(unsigned i=0;i<12;++i){threads[i].next=i==11?NULL:&threads[i+1];threads[i].tid=i+1;threads[i].state=3;threads[i].block_hint=5;}
 struct sweet_load_snapshot out;busy=true;sweet_load_collect(&task,&out);assert(out.status==1 && !out.captured && !clocks && !tries);
 busy=false;task.active=false;sweet_load_collect(&task,&out);assert(out.status==2 && !out.captured && !clocks);
 task.active=true;threads[2].sched_lock=1;sweet_load_collect(&task,&out);
 assert(out.status==0 && out.captured==8 && out.thread_count==12 && tries==8 && reads==8 && clocks==16);
 assert(out.threads[2].status==1 && out.threads[2].tid==3);assert(out.threads[0].state==3 && out.threads[0].hint==5);
 assert(!task_held && !spl_held && !thread_held);assert(out.user==10 && out.system==20);
 assert(tasktries==3 && sizeof(out)+256<8192);
 char line[256];assert(sweet_load_kind("backboardd")==0 && sweet_load_admitting(SWEET_LOAD_OPEN));
 assert(sweet_load_process_format(line,sizeof(line),0,0,1,2,3,4,&out)>0);
 assert(sweet_load_thread_format(line,sizeof(line),0,0,0,&out.threads[0])>0);
 puts("PASS");return 0;
}
''',prefix=PREFIX[:PREFIX.index('#define os_atomic_load')])

    def ufs(self, body, extra=''):
        helpers=UFS[UFS.index('static uint32_t sweet_ufs_load_state;'):UFS.index('\n#endif\n\nstatic inline uint32_t\nufs_rd')]
        mock=r'''
#define DEVELOPMENT 1
#define B_READ 1
#define EROFS 30
#define EINVAL 22
#define EIO 5
#define UFS_BLOCK 4096u
#define BOUNCE_SIZE 8192u
#define CMD_TIMEOUT_NS 5000u
#define MIN(a,b) ((a)<(b)?(a):(b))
typedef char *caddr_t;
struct buffer {uint64_t block;uint32_t count,resid;int flags,error,mapfail;char data[32768];};typedef struct buffer *buf_t;
static char bounce[BOUNCE_SIZE];static struct {bool dali,ready,writable;uint64_t nblocks,first_block;uint32_t secsize;uintptr_t dma;uint8_t lun;} su={true,true,true,1024,500,512,(uintptr_t)bounce,2};
#define DMA_BOUNCE 0
static int sweet_ufs_lock;static bool held,snapshot_busy;static unsigned tries,clocks,lines,commands;static uint64_t now;
static int error_code;static unsigned trace[1024],trace_count;static void (*command_hook)(void);
static void event(unsigned value){assert(trace_count<1024);trace[trace_count++]=value;}
static void lck_mtx_lock(int *p){assert(p==&sweet_ufs_lock && !held);event(1);held=true;now+=5;}
static void lck_mtx_unlock(int *p){assert(p==&sweet_ufs_lock && held);held=false;event(2);}
static bool lck_mtx_try_lock(int *p){assert(p==&sweet_ufs_lock);++tries;if(snapshot_busy || held)return false;held=true;return true;}
static uint64_t mach_absolute_time(void){++clocks;return now;}
static int output(const char *fmt,...){assert(!held);char text[512];va_list a;va_start(a,fmt);int n=vsnprintf(text,sizeof(text),fmt,a);va_end(a);assert(n>0 && n<=255);++lines;return n;}
#define printf output
static int ufs_scsi_on_lun_timeout(uint8_t lun,const uint8_t *cdb,unsigned size,uint32_t length,bool write,uint64_t timeout){assert(held && lun==2 && size==10 && timeout==CMD_TIMEOUT_NS);event(cdb[0]);event(length);event(write);++commands;now+=10;if(command_hook)command_hook();return error_code;}
static uint64_t buf_blkno(buf_t b){return b->block;}
static uint32_t buf_count(buf_t b){return b->count;}
static int buf_flags(buf_t b){return b->flags;}
static void buf_setresid(buf_t b,uint32_t n){b->resid=n;event(3);event(n);}
static int buf_map(buf_t b,caddr_t *p){event(4);*p=b->data;return b->mapfail;}
static void buf_unmap(buf_t b){assert(b && !held);event(5);}
static void buf_seterror(buf_t b,int error){b->error=error;event(6);event((unsigned)error);}
static void buf_biodone(buf_t b){assert(b && !held);event(7);}
'''
        io=r'''
static int ufs_read_blocks(uint64_t lba,uint32_t count){assert(lba>=su.first_block);uint8_t cdb[10]={0x28};int r=ufs_scsi(cdb,10,count*UFS_BLOCK,false);if(!r)memset(bounce,0x5a,count*UFS_BLOCK);return r;}
static int ufs_write_blocks(uint64_t lba,uint32_t count){assert(lba>=su.first_block);uint8_t cdb[10]={0x2a};return ufs_scsi(cdb,10,count*UFS_BLOCK,true);}
'''
        old=function(old_fragment(PATHS[1]),'sweet_ufs_strategy').replace('sweet_ufs_strategy(', 'old_strategy(')
        tail=r'''
#undef printf
static void reset_io(void){
 sweet_ufs_load_state=0;sweet_ufs_load_dumped=0;bzero(&sweet_ufs_load_counts,sizeof(sweet_ufs_load_counts));held=snapshot_busy=false;
 trace_count=tries=clocks=lines=commands=0;now=0;error_code=0;command_hook=NULL;atomic_hook=NULL;atomic_before=NULL;su.dali=su.ready=su.writable=true;
}
'''
        self.compile_run(IO_POLICY+mock+helpers+function(UFS,'ufs_scsi')+io+function(UFS,'sweet_ufs_strategy')+old+tail+extra+body)

    def test_actual_ufs_strategy_behavior_differential(self):
        self.ufs(r'''
int main(void){
 unsigned counts[]={0,512,4096,16384,32768};int errors[]={0,EIO,60};
 for(unsigned mode=0;mode<2;++mode)for(unsigned c=0;c<5;++c)for(unsigned e=0;e<3;++e)for(unsigned off=0;off<2;++off){
  struct buffer original={.block=off,.count=counts[c],.flags=mode?0:B_READ},a=original,b=original;
  reset_io();error_code=errors[e];old_strategy(&a);assert(!clocks);unsigned expected[1024],n=trace_count;memcpy(expected,trace,n*sizeof(*trace));
  reset_io();sweet_ufs_load_begin();trace_count=0;error_code=errors[e];sweet_ufs_strategy(&b);
  assert(n==trace_count && !memcmp(trace,expected,n*sizeof(*trace)) && !memcmp(&a,&b,sizeof(a)));
  assert(!held);if(b.count)assert(sweet_ufs_load_counts.groups[mode?4:3].count==1);
 }
 for(unsigned fail=0;fail<3;++fail){
  struct buffer a={.count=4096},b=a;reset_io();
  if(fail==0)su.writable=false;else if(fail==1)a.block=8192;else a.mapfail=1;b=a;
  old_strategy(&a);unsigned n=trace_count,expected[1024];memcpy(expected,trace,n*sizeof(*trace));trace_count=0;sweet_ufs_load_begin();sweet_ufs_strategy(&b);
  assert(n==trace_count && !memcmp(expected,trace,n*sizeof(*trace)) && !memcmp(&a,&b,sizeof(a)) && !held);
 }
 reset_io();su.dali=false;sweet_ufs_load_begin();assert(!sweet_ufs_load_state);su.dali=true;su.ready=false;sweet_ufs_load_begin();assert(!sweet_ufs_load_state);
 sweet_ufs_load_dump(0);assert(!clocks && !tries);puts("PASS");return 0;
}
''')

    def test_actual_ufs_close_races_busy_and_40_snapshot_bound(self):
        self.ufs(r'''
static void close_in_command(void){sweet_ufs_load_close();}
static void open_in_command(void){os_atomic_store(&sweet_ufs_load_state,1,release);}
int main(void){
 reset_io();struct buffer b={.count=4096,.flags=B_READ};old_strategy(&b);assert(!clocks && !sweet_ufs_load_counts.groups[0].count);
 command_hook=open_in_command;sweet_ufs_strategy(&b);assert(sweet_ufs_load_state==1 && !sweet_ufs_load_counts.groups[0].count && !sweet_ufs_load_counts.groups[3].count);
 command_hook=close_in_command;sweet_ufs_strategy(&b);assert(!sweet_ufs_load_counts.groups[0].count && !sweet_ufs_load_counts.groups[3].count);
 reset_io();sweet_ufs_load_begin();sweet_ufs_load_begin();sweet_ufs_strategy(&b);assert(sweet_ufs_load_counts.groups[0].elapsed==10 && sweet_ufs_load_counts.groups[3].wait==5);
 for(unsigned i=0;i<40;++i){snapshot_busy=i==39;sweet_ufs_load_dump(i);sweet_ufs_load_dump(i);}
 assert(tries==40 && sweet_ufs_load_state==2 && sweet_ufs_load_dumped==40 && lines==236);
 unsigned c=clocks;uint64_t count=sweet_ufs_load_counts.groups[0].count;sweet_ufs_strategy(&b);assert(clocks==c && sweet_ufs_load_counts.groups[0].count==count);
 sweet_ufs_load_begin();sweet_ufs_load_dump(40);assert(tries==40);
 reset_io();sweet_ufs_load_begin();for(unsigned i=0;i<40;++i)sweet_ufs_load_dump(i);
 assert(tries==40 && lines==241 && clocks==80);puts("PASS");return 0;
}
''')

    def test_registry_parallel_claims_and_pending_publication(self):
        self.gui(r'''
static unsigned arrived;
static void rendezvous(void){__atomic_add_fetch(&arrived,1,__ATOMIC_SEQ_CST);while(__atomic_load_n(&arrived,__ATOMIC_SEQ_CST)<3){}}
static void race_hook(uint32_t *p,bool ok){if(p==&sweet_load_control && ok)os_atomic_or(&sweet_load_control,SWEET_LOAD_CLOSED,acq_rel);}
static void *register_thread(void *arg){
 (void)arg;rendezvous();sweet_load_register(0,&regs);return NULL;
}
int main(void) {
 sweet_load_prepare(1);sweet_load_begin();
 pthread_t a,b;arrived=0;
 assert(!pthread_create(&a,NULL,register_thread,NULL) && !pthread_create(&b,NULL,register_thread,NULL));
 rendezvous();pthread_join(a,NULL);pthread_join(b,NULL);
 assert(target_task.references==1 && sweet_load_ready==1 && (sweet_load_control&3)==1);
 load_reset();sweet_load_prepare(1);sweet_load_begin();atomic_hook=race_hook;sweet_load_register(0,&regs);
 assert(sweet_load_control&SWEET_LOAD_CLOSED);assert(target_task.references==1 && sweet_load_ready==1);
 atomic_hook=NULL;sweet_load_control=SWEET_LOAD_OPEN|1; sweet_load_ready=0;
 for(unsigned i=0;i<40;++i)sweet_load_dump(i);
 assert(!collector_calls && sweet_load_dumped==40 && sweet_load_ready==0 && io_closes==1);
 /* A pre-close owner may publish after final; there is no post-window collection. */
 os_atomic_or(&sweet_load_ready,1,release);sweet_load_dump(39);assert(!collector_calls);
 puts("PASS");return 0;
}
''')

    def test_actual_start_and_40_round_consumer_no_new_worker(self):
        extra=r'''
#ifndef __unused
#define __unused __attribute__((unused))
#endif
#define DEVELOPMENT 1
#define ARM64_BOARD_CONFIG_SWEET 1
typedef void *thread_t;
static size_t fixture_strlcpy(char *d,const char *s,size_t n){size_t len=strlen(s);if(n){size_t take=len<n-1?len:n-1;memcpy(d,s,take);d[take]=0;}return len;}
#ifdef strlcpy
#undef strlcpy
#endif
#define strlcpy fixture_strlcpy
typedef int wait_result_t;
#define PZERO 0
#define PROC_ALLPROCLIST 1
#define KERN_SUCCESS 0
static char sweet_sample_names[128];static int hz=100;
static unsigned sweet_ff_screen_enabled,sweet_ff_fault_enabled;static bool creation_fail;
static unsigned iterations,sleeps,creates,deallocations,order[200],order_count;
static bool sweet_bbvm_is_configured(void){return false;}
static bool sweet_bbipc_is_configured(void);
static void sweet_bbpc_prepare(unsigned int n){(void)n;}
static void sweet_bbvm_prepare(unsigned int n){(void)n;}
static void sweet_bbpc_begin(void){}
static void sweet_bbvm_begin(void){}
static void sweet_bbpc_dump(unsigned int s){assert(s<40);order[order_count++]=1;}
static void sweet_bbvm_dump(unsigned int s){assert(s<40);order[order_count++]=2;}
static void sweet_ff_screen_dump(unsigned int s){assert(s<40);order[order_count++]=3;}
static int sweet_sample_proc(void *p,void *a){(void)p;(void)a;assert(0);return 0;}
static void proc_iterate(int flags,int (*f)(void *,void *),void *a,void *b,void *c){(void)flags;(void)f;(void)a;(void)b;(void)c;++iterations;assert(0);}
static int tsleep(void *p,int pri,const char *name,int ticks){assert(p && pri==0 && !strcmp(name,"sweetsmp") && ticks==(sleeps?3000:500));++sleeps;return 0;}
static int PE_parse_boot_argn(const char *name,void *out,unsigned bytes){
 if(!strcmp(name,"sweetsample")){assert(bytes>=2);strcpy(out,"-");return 1;}
 unsigned v=!strcmp(name,"sweetload")?1:!strcmp(name,"sweetsampledelay")?5:!strcmp(name,"sweetsampleinterval")?30:0;
 assert(bytes==sizeof(unsigned));*(unsigned *)out=v;return 1;
}
static int kernel_thread_start(void (*fn)(void *,int),void *arg,void **out){++creates;if(creation_fail)return 5;*out=&target_task;fn(arg,0);return 0;}
static void thread_deallocate(void *thread){assert(thread==&target_task);++deallocations;}
#define printf capture
'''+function(SAMPLER,'sweet_sample_thread')+function(SAMPLER,'sweet_sample_start')+r'''
#undef printf
'''
        body=r'''
int main(void) {
 creation_fail=true;sweet_sample_start();assert(creates==1 && !sweet_load_control && !io_begins && !sleeps && !deallocations);
 creation_fail=false;sweet_sample_start();assert(creates==2 && sleeps==41 && deallocations==1 && !iterations);
 assert(sweet_load_dumped==40 && io_calls==40 && io_closes==1 && io_begins==1 && (sweet_load_control&SWEET_LOAD_CLOSED));
 assert(order_count==120);for(unsigned i=0;i<120;++i)assert(order[i]==i%3+1);
 puts("PASS");return 0;
}
'''
        guard='#ifdef strlcpy\n#undef strlcpy\n#endif\n#define strlcpy fixture_strlcpy'
        fortified='#ifdef strlcpy\n#undef strlcpy\n#endif\n#define strlcpy(dest, ...) __builtin___strlcpy_chk(dest,__VA_ARGS__,__builtin_object_size(dest,0))'
        self.gui(body,extra.replace(guard,fortified+'\n#define strlcpy fixture_strlcpy'),compiler_error="'strlcpy' macro redefined")
        self.gui(body,extra)
        self.gui(body,extra.replace(guard,fortified+'\n'+guard))

    def test_boot_parser_keeps_gaps_duplicates_and_separate_sessions(self):
        a='AAAAAAAA-AAAA-AAAA-AAAA-AAAAAAAAAAAA'; b='BBBBBBBB-BBBB-BBBB-BBBB-BBBBBBBBBBBB'
        row='SWEET LOAD: v=1 s=39 at=0x1 claims=0x1 ready=0x1 miss=0 final=1\n'
        io='SWEET LOADIO STATUS: v=1 s=39 at=0x2 q=1 final=1 pending=unknown\n'
        text=f'SWEET BOOT SESSION UUID: {a}\n'+row+row+io+f'SWEET BOOT SESSION UUID: {b}\n'+row[:-1]
        boots=parse_boot_records(text)
        self.assertEqual(set(boots),{a,b}); self.assertEqual(len(boots[a]['samples'][39]['duplicates']),1)
        self.assertEqual(boots[a]['samples'][39]['missing'],['process 0'])
        self.assertEqual(len(boots[b]['rejected']),1); self.assertFalse(boots[b]['records'])
        self.assertFalse(parse_boot_records(row))
        with self.assertRaises(ValueError): parse_record('SWEET LOAD: v=1 s=39 at=0 claims=0 ready=1 miss=0 final=1\n')

    def test_ufs_all_valid_and_invalid_clock_envelopes(self):
        code=IO_POLICY+r'''
int main(void){
 struct sweet_ufs_load_counters c={0};char line[256];
 for(unsigned g=0;g<5;++g){
  sweet_ufs_load_record(&c,g,UINT64_MAX,5,0,0,UINT64_MAX);
  assert(c.groups[g].count==1 && c.groups[g].valid==1 && c.groups[g].elapsed==UINT64_MAX && c.groups[g].errors==1);
  sweet_ufs_load_record(&c,g,1,0,2,1,4);sweet_ufs_load_record(&c,g,0,0,0,4,3);
  assert(c.groups[g].count==3 && c.groups[g].bad_clock==2 && c.groups[g].valid==1);
  sweet_ufs_load_record(&c,g,0,0,0,0,1);assert(c.groups[g].elapsed==UINT64_MAX && c.overflow);
  assert(sweet_ufs_load_format(line,sizeof(line),39,g,&c.groups[g])>0);
 }
 struct sweet_ufs_load_counters copy=c;sweet_ufs_load_record(&c,5,1,1,0,0,1);assert(!memcmp(&c,&copy,sizeof(c)));
 assert(sweet_ufs_load_kind(0x35)==2 && !sweet_ufs_load_format(line,1,0,0,&c.groups[0]));puts("PASS");return 0;
}
'''
        self.compile_run(code,prefix=PREFIX[:PREFIX.index('#define os_atomic_load')])


if __name__=='__main__': unittest.main()
