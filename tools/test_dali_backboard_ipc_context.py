#!/usr/bin/env python3
"""Execute final0174 IPC/VM formats, SVC snapshots and the real console path."""
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest
import test_dali_gui_user_entry as fixture
import test_dali_backboard_vm_fault as vm

ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT/'xnu/patches/0174-sweet-bounded-backboard-ipc-context.patch'
POLICY = fixture.fragment('bsd/dev/SweetBackboardIPCPolicy.h', PATCH)
SOURCE = fixture.fragment('bsd/dev/sweet_sample.c', PATCH)
VM_POLICY = fixture.fragment('bsd/dev/SweetBackboardVMPolicy.h', PATCH)
LEGACY = vm.LEGACY
HELPERS = SOURCE[SOURCE.index('static volatile UInt32 sweet_bbipc_configured,'):SOURCE.index('\nvoid sweet_ff_trace(')]
PRODUCER = vm.function(SOURCE, 'sweet_ff_trace')
DUMP = vm.function(SOURCE, 'sweet_ff_dump')


def fields(line, prefix, expected):
    if not line.startswith(prefix) or not line.endswith('\n') or len(line.encode())>255:
        raise ValueError('incomplete or oversized versioned record')
    pairs = [word.split('=',1) for word in line[len(prefix):].split()]
    if any(len(pair)!=2 for pair in pairs):
        raise ValueError('malformed fields')
    result = dict(pairs)
    if len(result)!=len(pairs) or set(result)!=set(expected):
        raise ValueError('missing, duplicate or unexpected field')
    return result


def parse_ipc(line):
    raw=fields(line,'SWEET BBIPC: ',('v','s','n','p','th','at','o','b','p2','vi','d','r','t'))
    values={k:int(v,0) for k,v in raw.items()}
    if values['v']!=1 or not 0<=values['s']<40 or not 1<=values['n']<=65536:
        raise ValueError('unknown version/sample/sequence')
    if not -(1<<31)<=values['p']<(1<<31) or any(not 0<=values[k]<(1<<64) for k in ('th','at','o','b','p2','vi','d','r','t')):
        raise ValueError('invalid scalar width')
    return values


def decode_ipc(e):
    """Requested raw intent only: no validated IPC, peer identity or ready claim."""
    low=lambda x:x&0xffffffff
    high=lambda x:x>>32
    o=e['o'];vector=bool(o&(1<<32))
    return dict(send_requested=bool(o&1),receive_requested=bool(o&2),vector_requested=vector,
                send_timeout_requested=bool(o&0x10),receive_timeout_requested=bool(o&0x100),
                bits=low(e['b']),send_size=None if vector else high(e['b']),send_vector_count=high(e['b']) if vector else None,
                remote_client_name=low(e['p2']),local_client_name=high(e['p2']),
                voucher_client_name=low(e['vi']),raw_message_id=high(e['vi']),
                descriptor_count=low(e['d']),receive_client_name=high(e['d']),
                receive_size=None if vector else low(e['r']),receive_vector_count=low(e['r']) if vector else None,
                priority=high(e['r']),timeout_low32=low(e['t']),validated=False,peer_pid=None)


def parse_compact_vm(line):
    base=('v','t','k','p','th','b')
    is_entry=' k=E ' in line
    expected=base+('pc','lr','sp','c','e','f','a','pr') if is_entry else base+('z','r','d','vld')
    raw=fields(line,'SWEET BBVM: ',expected)
    if raw['k']!=('E' if is_entry else 'R'):
        raise ValueError('unknown VM kind')
    values={k:int(v,0) if k!='k' else v for k,v in raw.items()}
    if values['v']!=2 or not 1<=values['t']<=40:
        raise ValueError('unknown VM version/token')
    if not is_entry and (values['vld'] not in (0,1) or values['d']!=(values['z']-values['b'] if values['vld'] else 0) or values['vld']!=int(values['z']>=values['b'])):
        raise ValueError('invalid duration')
    return values


def parse_vm_record(line):
    """Keep legacy missing fields absent; compact returns join only by explicit keys."""
    if line.startswith('SWEET BBVM: v=2 '):
        e=parse_compact_vm(line)
        aliases=dict(t='token',p='pid',th='tid',b='start_abs',pc='pc',lr='lr',sp='sp',c='cpsr',e='esr',f='far',a='address',pr='prot',z='end_abs',r='result',d='duration_abs',vld='duration_valid')
        return dict(format_version=2,kind='entry' if e['k']=='E' else 'return',numeric={aliases[k]:v for k,v in e.items() if k in aliases},missing=[],complete=True,raw_line=line)
    if not line.startswith('SWEET BBVM: token='):
        raise ValueError('not a VM data record')
    raw=dict(re.findall(r'(\w+)=([^\s]+)',line));kind=raw.pop('kind',None)
    if kind not in ('entry','return'):
        raise ValueError('unknown legacy kind')
    expected=set('token pid tid pc lr sp cpsr esr far address prot start_abs end_abs result duration_abs duration_valid'.split())
    numeric={}
    for key in expected&set(raw):
        try:numeric[key]=int(raw[key],0)
        except ValueError:pass
    missing=sorted(expected-set(numeric))
    return dict(format_version=1,kind=kind,numeric=numeric,missing=missing,complete=not missing and line.endswith('\n'),raw_line=line)


# Exact XNU console implementation from verified CI41181923386; no SYNCDRAIN.
CONSOLE_SOURCE = "#define SERIAL_CONS_BUF_SIZE  256\nstruct console_printbuf_state {\n\tint pos;\n\tint total;\n\tint flags;\n#define CONS_PB_WRITE_NEWLINE  0x1\n#define CONS_PB_CANBLOCK       0x2\n\tchar str[SERIAL_CONS_BUF_SIZE];\n};\n\nvoid\nconsole_printbuf_state_init(struct console_printbuf_state * data, int write_on_newline, int can_block)\n{\n\tif (data == NULL) {\n\t\treturn;\n\t}\n\tbzero(data, sizeof(struct console_printbuf_state));\n\tif (write_on_newline) {\n\t\tdata->flags |= CONS_PB_WRITE_NEWLINE;\n\t}\n\tif (can_block) {\n\t\tdata->flags |= CONS_PB_CANBLOCK;\n\t}\n}\n\nvoid\nconsole_printbuf_putc(int ch, void * arg)\n{\n\tstruct console_printbuf_state * info = (struct console_printbuf_state *)arg;\n\tinfo->total += 1;\n\tif (info->pos < (SERIAL_CONS_BUF_SIZE - 1)) {\n\t\tinfo->str[info->pos] = (char)ch;\n\t\tinfo->pos += 1;\n\t} else {\n\t\t/*\n\t\t * when len(line) > SERIAL_CONS_BUF_SIZE, we truncate the message\n\t\t * if boot-arg 'drain_uart_sync=1' is set, then\n\t\t * drain all the buffer right now and append new ch\n\t\t */\n\t\tif (serialmode & SERIALMODE_SYNCDRAIN) {\n\t\t\tinfo->str[info->pos] = '\\0';\n\t\t\tconsole_write(info->str, info->pos);\n\t\t\tinfo->pos            = 0;\n\t\t\tinfo->str[info->pos] = (char)ch;\n\t\t\tinfo->pos += 1;\n\t\t}\n\t}\n\n\tinfo->str[info->pos] = '\\0';\n\t/* if newline, then try output to console */\n\tif (ch == '\\n' && info->flags & CONS_PB_WRITE_NEWLINE) {\n\t\tconsole_write(info->str, info->pos);\n\t\tinfo->pos            = 0;\n\t\tinfo->str[info->pos] = '\\0';\n\t}\n}\n\nvoid\nconsole_printbuf_clear(struct console_printbuf_state * info)\n{\n\tif (info->pos != 0) {\n\t\tconsole_write(info->str, info->pos);\n\t}\n\tinfo->pos = 0;\n\tinfo->str[info->pos] = '\\0';\n\tinfo->total = 0;\n}\n"


MOCK=r'''
typedef uint32_t UInt32;
struct process {char p_comm[32];int pid;};typedef struct process *proc_t;
#define PROC_NULL NULL
struct arm_saved_state {uint64_t pc,lr,sp,x[8];uint32_t cpsr;bool wide;};
#define PSR64_IS_USER64(c) (((c)&31u)==0)
static struct process process={"backboardd",56};
static _Thread_local uint64_t tid=316,now;
static bool absent,pc_progress;
static unsigned sweet_ff_enabled,current_calls,clock_reads,register_reads,outputs,ipc_lines,svc_lines;
static struct sweet_ff_ring sweet_ff_events;
static volatile UInt32 sweet_ff_guard,sweet_ff_dropped;
static _Thread_local bool owns_guard;
static volatile UInt32 *guard_address;
static char captured[1048576];static size_t captured_size;
static proc_t current_proc(void) {__atomic_add_fetch(&current_calls,1,__ATOMIC_RELAXED);return absent?NULL:&process;}
static int proc_pid(proc_t p) {return p->pid;}
static void *current_thread(void) {return &process;}
static uint64_t thread_tid(void *p) {assert(p==&process);return tid;}
static bool is_saved_state64(struct arm_saved_state *s) {return s->wide;}
static uint32_t get_saved_state_cpsr(struct arm_saved_state *s) {return s->cpsr;}
static uint64_t get_saved_state_pc(struct arm_saved_state *s) {return s->pc;}
static uint64_t get_saved_state_lr(struct arm_saved_state *s) {return s->lr;}
static uint64_t get_saved_state_sp(struct arm_saved_state *s) {return s->sp;}
static uint64_t get_saved_state_reg(struct arm_saved_state *s,unsigned i) {assert(i<8);__atomic_add_fetch(&register_reads,1,__ATOMIC_RELAXED);return s->x[i];}
static bool OSCompareAndSwap(UInt32 old,UInt32 value,volatile UInt32 *p) {
 bool ok=__atomic_compare_exchange_n(p,&old,value,false,__ATOMIC_ACQ_REL,__ATOMIC_ACQUIRE);
 if(ok && p==guard_address) {if(old==0 && value==1)owns_guard=true;if(old==1 && value==0)owns_guard=false;}
 return ok;
}
static uint64_t mach_absolute_time(void) {__atomic_add_fetch(&clock_reads,1,__ATOMIC_RELAXED);return now;}
static bool sweet_bbpc_is_configured(void) {return pc_progress;}
static int capture(const char *,...);
#define printf capture
'''
TAIL=r'''
#undef printf
static int capture(const char *fmt,...) {
 assert(!owns_guard);char line[512];va_list ap;va_start(ap,fmt);int n=vsnprintf(line,sizeof(line),fmt,ap);va_end(ap);
 assert(n>0 && n<=255 && captured_size+(size_t)n<sizeof(captured));memcpy(captured+captured_size,line,(size_t)n+1);captured_size+=(size_t)n;++outputs;
 if(strstr(line,"SWEET BBIPC: v=1 s=") && strstr(line," n="))++ipc_lines;
 if(strstr(line,"SWEET FFSVC: n="))++svc_lines;
 return n;
}
static struct arm_saved_state regs={.pc=0x1234,.lr=0x5678,.sp=0x9000,.x={0xa000,0x100000103,0x1100000022,0x3300000044,0x5500000066,0x7700000088,0x99000000aa,0xbb000000cc},.wide=true};
static void reset(void) {
 memset(&sweet_ff_events,0,sizeof(sweet_ff_events));memset(&sweet_bbipc_events,0,sizeof(sweet_bbipc_events));
 sweet_ff_guard=sweet_ff_dropped=sweet_bbipc_configured=sweet_bbipc_open=0;guard_address=&sweet_ff_guard;owns_guard=false;
 current_calls=clock_reads=register_reads=outputs=ipc_lines=svc_lines=0;captured_size=0;captured[0]=0;
 absent=pc_progress=false;now=0;tid=316;sweet_ff_enabled=3;strcpy(process.p_comm,"backboardd");
}
static void common(void) {reset();sweet_bbipc_prepare(0);sweet_bbipc_begin();sweet_ff_dump(0);assert(!strstr(captured,"BBIPC"));reset();}
'''


class IPCContextTest(unittest.TestCase):
    run_c=fixture.GUIUserEntryTest.run_c

    def runtime(self, body, extra=''):
        code=LEGACY+POLICY+MOCK+HELPERS+PRODUCER+DUMP+TAIL+extra+body
        self.run_c(code.replace('int main(void) {','int main(void) { common();'))

    def test_actual_policy_latest_four_tag_owner_and_no_replay(self):
        self.run_c(LEGACY+POLICY+r'''
int main(void) {
 struct sweet_ff_ring ring={0};struct sweet_bbipc_buffer metadata={0};struct sweet_ff_event svc={.call=-47,.pid=56,.tid=316},snapshot[64];struct sweet_bbipc_event e={.pid=56,.tid=316},out[5]={0};
 assert(sweet_ff_svc_mode(3)==3 && sweet_ff_mode(3)==0 && sweet_ff_target(3,"backboardd"));
 for(unsigned i=1;i<=8;++i) {assert(sweet_ff_record(&ring,&svc));e.seq=ring.total;e.words[0]=i;sweet_bbipc_record(&metadata,&e);}
 uint32_t n=sweet_ff_snapshot(&ring,snapshot);out[4].seq=0xaabbccdd;
 struct sweet_bbipc_coverage c=sweet_bbipc_select(&metadata,snapshot,n,out);
 assert(c.eligible==8 && c.matched==8 && c.selected==4 && c.limited==4 && !c.missing);
 for(unsigned i=0;i<4;++i)assert(out[i].seq==i+5 && out[i].words[0]==i+5);assert(out[4].seq==0xaabbccdd);
 n=sweet_ff_snapshot(&ring,snapshot);assert(!n && !sweet_bbipc_select(&metadata,snapshot,n,out).selected);
 snapshot[0]=(struct sweet_ff_event){.seq=1,.call=-47,.pid=99,.tid=316};c=sweet_bbipc_select(&metadata,snapshot,1,out);assert(c.missing==1 && !c.selected);
 snapshot[0].pid=56;snapshot[0].tid=999;c=sweet_bbipc_select(&metadata,snapshot,1,out);assert(c.missing==1);
 snapshot[0].tid=316;metadata.events[0].seq=65;c=sweet_bbipc_select(&metadata,snapshot,1,out);assert(c.missing==1);
 snapshot[0].phase=3;assert(!sweet_bbipc_select(&metadata,snapshot,1,out).eligible);
 char line[256];e.seq=65536;assert(sweet_bbipc_format(line,sizeof(line),39,&e)>0);puts("PASS");return 0;
}
''')

    def test_actual_producer_filters_arguments_and_legacy_registers(self):
        self.runtime(r'''
int main(void) {
 sweet_bbipc_prepare(1);assert(sweet_bbipc_configured && !sweet_bbipc_open);
 struct arm_saved_state before=regs;sweet_ff_trace(0,-47,&regs);assert(sweet_ff_events.total==1 && register_reads==1 && !clock_reads && !sweet_bbipc_events.events[0].seq);
 sweet_bbipc_begin();sweet_ff_trace(0,-47,&regs);assert(sweet_ff_events.total==2 && register_reads==9 && clock_reads==1);
 struct sweet_bbipc_event *e=&sweet_bbipc_events.events[1];assert(e->seq==2 && e->pid==56 && e->tid==316);
 for(unsigned i=0;i<7;++i)assert(e->words[i]==regs.x[i+1]);assert(!memcmp(&before,&regs,sizeof(regs)));
 unsigned read=register_reads,clocks=clock_reads;
 sweet_ff_trace(1,-47,&regs);sweet_ff_trace(3,-47,&regs);sweet_ff_trace(0,47,&regs);assert(register_reads==read+3 && clock_reads==clocks);
 regs.cpsr=4;sweet_ff_trace(0,-47,&regs);assert(clock_reads==clocks);regs=before;
 regs.wide=false;unsigned total=sweet_ff_events.total;sweet_ff_trace(0,-47,&regs);assert(sweet_ff_events.total==total);regs=before;
 strcpy(process.p_comm,"SpringBoard");sweet_ff_trace(0,-47,&regs);assert(sweet_ff_events.total==total);strcpy(process.p_comm,"backboardd");
 sweet_ff_guard=1;sweet_ff_trace(0,-47,&regs);assert(sweet_ff_dropped && sweet_ff_events.total==total);sweet_ff_guard=0;
 sweet_ff_dump(39);assert(!sweet_bbipc_open && strstr(captured," n=2 "));
 puts("PASS");return 0;
}
''')

    def test_actual_defaults_modes_and_closed_capture_keeps_old_svc(self):
        self.runtime(r'''
int main(void) {
 for(unsigned mode=0;mode<4;++mode) {reset();sweet_ff_enabled=mode;sweet_bbipc_prepare(2);assert(!sweet_bbipc_configured);sweet_bbipc_prepare(1);assert(sweet_bbipc_configured==(mode==3));}
 reset();sweet_bbipc_prepare(1);sweet_bbipc_begin();sweet_ff_trace(0,-47,&regs);
 sweet_ff_guard=1;sweet_ff_dump(39);assert(!sweet_bbipc_open && strstr(captured,"snapshot incomplete"));sweet_ff_guard=0;
 unsigned reads=register_reads,clocks=clock_reads;uint32_t old=sweet_ff_events.total;sweet_ff_trace(0,-47,&regs);
 assert(sweet_ff_events.total==old+1 && register_reads==reads+1 && clock_reads==clocks && !sweet_bbipc_events.events[1].seq);
 puts("PASS");return 0;
}
''')

    def test_actual_ring_wrap_quota_and_snapshot_burst(self):
        self.runtime(r'''
int main(void) {
 sweet_bbipc_prepare(1);sweet_bbipc_begin();
 for(unsigned i=0;i<65;++i)sweet_ff_trace(0,-47,&regs);
 assert(sweet_ff_events.total==65 && sweet_bbipc_events.events[0].seq==65 && sweet_bbipc_events.events[63].seq==64);
 sweet_ff_dump(0);assert(ipc_lines==4 && strstr(captured,"eligible=64 matched=64 selected=4 missing_tag=0 limited=60"));
 unsigned exported=ipc_lines;sweet_ff_dump(1);assert(ipc_lines==exported);
 for(unsigned i=65;i<65536;++i)sweet_ff_trace(0,-47,&regs);
 assert(sweet_ff_events.total==65536);unsigned reads=register_reads,clocks=clock_reads;struct sweet_bbipc_event last=sweet_bbipc_events.events[63];
 sweet_ff_trace(0,-47,&regs);assert(sweet_ff_events.total==65536 && clock_reads==clocks && register_reads==reads+1 && !memcmp(&last,&sweet_bbipc_events.events[63],sizeof(last)));
 sweet_ff_dump(39);assert(ipc_lines==8 && strstr(captured," n=65536 "));
 puts("PASS");return 0;
}
''')

    def test_actual_snapshot_drops_overwritten_context_and_selects_last_burst(self):
        self.runtime(r'''
int main(void) {
 sweet_bbipc_prepare(1);sweet_bbipc_begin();sweet_ff_trace(0,-47,&regs);
 for(unsigned i=0;i<64;++i)sweet_ff_trace(1,-47,&regs);
 sweet_ff_dump(0);assert(!ipc_lines && strstr(captured,"eligible=0"));
 for(unsigned i=0;i<7;++i) {sweet_ff_trace(0,-47,&regs);sweet_ff_trace(1,-47,&regs);}
 sweet_ff_dump(39);assert(ipc_lines==4 && strstr(captured,"limited=3"));
 assert(strstr(captured," n=72 ") && strstr(captured," n=78 "));
 puts("PASS");return 0;
}
''')

    def test_actual_concurrent_svc_and_normal_snapshot(self):
        self.runtime(r'''
int main(void) {
 sweet_bbipc_prepare(1);sweet_bbipc_begin();
 for(unsigned sample=0;sample<40;++sample) {
  pthread_t a,b;assert(!pthread_create(&a,NULL,produce,(void *)(uintptr_t)101));assert(!pthread_create(&b,NULL,produce,(void *)(uintptr_t)102));
  sweet_ff_dump(sample);assert(!pthread_join(a,NULL) && !pthread_join(b,NULL));
 }
 assert(ipc_lines<=160 && !sweet_bbipc_open);
 for(unsigned i=0;i<64;++i) {
  const struct sweet_bbipc_event *e=&sweet_bbipc_events.events[i];
  if(!e->seq)continue;
  assert((e->tid==101 || e->tid==102) && e->abstime==e->tid && e->words[0]==regs.x[1] && e->words[6]==regs.x[7]);
 }
 puts("PASS");return 0;
}
''',extra=r'''
#include <pthread.h>
static void *produce(void *arg) {
 tid=(uint64_t)(uintptr_t)arg;now=tid;
 for(unsigned i=0;i<100;++i)sweet_ff_trace(0,-47,&regs);
 return NULL;
}
''')

    def test_actual_startup_failure_and_finite_ipc_lifecycle(self):
        extra=r"""
#define DEVELOPMENT 1
#define ARM64_BOARD_CONFIG_SWEET 1
#ifndef __unused
#define __unused __attribute__((__unused__))
#endif
#ifdef strlcpy
#undef strlcpy
#endif
#define strlcpy fixture_ipc_strlcpy
#define PZERO 0
#define PROC_ALLPROCLIST 0
#define KERN_SUCCESS 0
typedef int wait_result_t;typedef void *thread_t;
static unsigned sweet_ff_fault_enabled,sweet_ff_screen_enabled;
static unsigned requested=1,svc_requested=3,starts,deallocated,walks,sleeps,elapsed;
static int hz=100;static bool fail_thread;static char sweet_sample_names[128];static void (*callback)(void *,int);
static size_t fixture_ipc_strlcpy(char *d,const char *s,size_t n) {size_t len=strlen(s);assert(len<n);memcpy(d,s,len+1);return len;}
static int PE_parse_boot_argn(const char *key,void *out,unsigned bytes) {
 if(!strcmp(key,"sweetsample")){assert(bytes==128);strcpy(out,"-");return 1;}
 assert(bytes==sizeof(unsigned));unsigned value=0;
 if(!strcmp(key,"sweetfftrace"))value=svc_requested;
 else if(!strcmp(key,"sweetbbipc"))value=requested;
 else if(!strcmp(key,"sweetsampledelay"))value=5;
 else if(!strcmp(key,"sweetsampleinterval"))value=30;
 else assert(!strcmp(key,"sweetffpc") || !strcmp(key,"sweetbbvm") || !strcmp(key,"sweetheartbeat") || !strcmp(key,"sweetfffault") || !strcmp(key,"sweetffscreen"));
 *(unsigned *)out=value;return 1;
}
static int kernel_thread_start(void (*fn)(void *,int),void *arg,thread_t *out) {assert(!arg);++starts;callback=fn;*out=&process;return fail_thread?1:0;}
static void thread_deallocate(thread_t t) {assert(t==&process);++deallocated;}
static void sweet_bbpc_prepare(unsigned x) {assert(!x);}
static void sweet_bbpc_begin(void) { }
static void sweet_bbpc_dump(unsigned sample) {assert(sample<40);}
static void sweet_bbvm_prepare(unsigned x) {assert(!x);}
static bool sweet_bbvm_is_configured(void) {return false;}
static void sweet_bbvm_begin(void) { }
static void sweet_bbvm_dump(unsigned sample) {assert(sample<40);}
static void sweet_ff_screen_dump(unsigned sample) {assert(sample<40);}
static void sweet_sample_proc(void) { }
static void proc_iterate(int kind,void (*fn)(void),void *a,void *b,void *c) {assert(!kind && fn==sweet_sample_proc && !a && !b && !c);++walks;}
static int tsleep(void *p,int pri,const char *name,int ticks) {
 assert(p && !pri && !strcmp(name,"sweetsmp") && ticks==(sleeps?30:5)*hz);
 elapsed+=(unsigned)(ticks/hz);now=elapsed;
 for(unsigned i=0;i<8;++i)sweet_ff_trace(0,-47,&regs);
 ++sleeps;return 0;
}
#define printf capture
"""+vm.function(SOURCE,'sweet_sample_thread')+vm.function(SOURCE,'sweet_sample_start')+r"""
#undef printf
"""
        self.runtime(r"""
int main(void) {
 fail_thread=true;sweet_sample_start();assert(starts==1 && !deallocated && sweet_bbipc_configured && !sweet_bbipc_open);
 sweet_ff_trace(0,-47,&regs);assert(!clock_reads);reset();fail_thread=false;sweet_sample_start();assert(starts==2 && deallocated==1 && !sweet_bbipc_open);
 callback(NULL,0);assert(sleeps==41 && elapsed==1205 && !walks && !sweet_bbipc_open && ipc_lines==160);
 unsigned all_ipc=0;for(const char *p=captured;(p=strstr(p,"SWEET BBIPC"))!=NULL;++p)++all_ipc;
 assert(all_ipc==201);
 assert(!sweet_ff_fault_enabled && !sweet_ff_screen_enabled && sweet_ff_events.total==41*8);
 reset();requested=0;svc_requested=0;sweet_sample_start();callback(NULL,0);assert(sleeps==41 && !sweet_bbipc_configured && !walks);
 puts("PASS");return 0;
}
""",extra)

    def test_actual_new_capture_does_not_change_mode_one_two_or_user_fields(self):
        self.runtime(r"""
int main(void) {
 for(unsigned mode=1;mode<=2;++mode) {
  reset();sweet_ff_enabled=mode;strcpy(process.p_comm,mode==1?"init_featureflag":"SpringBoard");
  sweet_bbipc_prepare(1);sweet_bbipc_begin();assert(!sweet_bbipc_configured);
  sweet_ff_trace(0,-47,&regs);assert(sweet_ff_events.total==1 && !clock_reads && register_reads==1);
  sweet_ff_dump(0);assert(!strstr(captured,"BBIPC") && svc_lines==1);
 }
 puts("PASS");return 0;
}
""")

    def test_decoder_raw_vector_timeout_and_no_peer_claim(self):
        line='SWEET BBIPC: v=1 s=39 n=65536 p=56 th=0x13c at=0x123 o=0x100000113 b=0x200000022 p2=0x3300000044 vi=0x5500000066 d=0x7700000088 r=0x99000000aa t=0xbb000000cc\n'
        raw=parse_ipc(line);d=decode_ipc(raw)
        self.assertTrue(d['send_requested'] and d['receive_requested'] and d['vector_requested'])
        self.assertTrue(d['send_timeout_requested'] and d['receive_timeout_requested'])
        self.assertEqual((d['send_vector_count'],d['receive_vector_count'],d['timeout_low32']),(2,0xaa,0xcc))
        self.assertEqual((d['remote_client_name'],d['local_client_name'],d['raw_message_id'],d['receive_client_name']),(0x44,0x33,0x55,0x77))
        self.assertIsNone(d['send_size']);self.assertFalse(d['validated']);self.assertIsNone(d['peer_pid'])
        raw['o']=2;d=decode_ipc(raw);self.assertTrue(d['receive_requested']);self.assertFalse(d['send_requested']);self.assertEqual(d['send_size'],2)
        for bad in (line[:-1],line.replace('v=1','v=2'),line.replace('t=0xbb000000cc','t=SWEET'),line.replace('p=56','p=56 p=56')):
            with self.assertRaises(ValueError):parse_ipc(bad)

    def test_real_console_old_truncation_and_compact_format_parser_roundtrip(self):
        old_policy=fixture.fragment('bsd/dev/SweetBackboardVMPolicy.h',ROOT/'xnu/patches/0173-sweet-bounded-backboard-vm-fault.patch')
        old_format=old_policy[old_policy.index('static inline int\nsweet_bbvm_format'):old_policy.rindex('#endif')].replace('sweet_bbvm_format','old_vm_format')
        prefix=r"""
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#define bzero(p,n) memset(p,0,n)
#define SERIALMODE_SYNCDRAIN 1
static int serialmode;
static char delivered[2048];static size_t delivered_size;
static void console_write(char *text,int size) {assert(size>=0 && delivered_size+(size_t)size<sizeof(delivered));memcpy(delivered+delivered_size,text,(size_t)size);delivered_size+=(size_t)size;delivered[delivered_size]=0;}
"""
        # Put the real functions after their prototype-bearing struct and sink.
        body=prefix+LEGACY+POLICY+VM_POLICY+CONSOLE_SOURCE+old_format+r"""
static void route(const char *line) {
 struct console_printbuf_state state;console_printbuf_state_init(&state,1,1);
 for(const char *p=line;*p;++p)console_printbuf_putc(*p,&state);
 console_printbuf_clear(&state);
}
int main(void) {
 assert(!serialmode && SWEET_FF_RECORDS==64 && SWEET_FF_EVENT_LIMIT==65536 && sweet_ff_mode(3)==0 && sweet_ff_svc_mode(3)==3 && sweet_ff_target(3,"backboardd"));
 struct sweet_ff_ring ring={0};struct sweet_ff_event svc={0},snapshot[64];assert(sweet_ff_record(&ring,&svc) && sweet_ff_snapshot(&ring,snapshot)==1);
 struct sweet_bbipc_buffer metadata={0};struct sweet_bbipc_event e={.seq=65536,.pid=INT32_MIN,.tid=UINT64_MAX,.abstime=UINT64_MAX};
 for(unsigned i=0;i<7;++i)e.words[i]=UINT64_MAX;
 sweet_bbipc_record(&metadata,&e);struct sweet_bbipc_event selected[4];svc=(struct sweet_ff_event){.seq=e.seq,.pid=e.pid,.tid=e.tid,.call=-47};
 assert(sweet_bbipc_select(&metadata,&svc,1,selected).selected==1);
 struct sweet_bbvm_buffer vm_buffer={0};struct sweet_bbvm_event event;memset(&event,255,sizeof(event));event.token=40;event.version=2;event.pid=INT32_MIN;event.result=INT32_MIN;event.duration_valid=true;
 assert(sweet_bbvm_ceil_ticks(2,SWEET_BBVM_INTERVAL_NS)==2);struct sweet_bbvm_event vout[80];
 event.start=0;assert(sweet_bbvm_enter_record(&vm_buffer,30,&event)==1);assert(sweet_bbvm_leave_record(&vm_buffer,1,event.tid,30,0));assert(sweet_bbvm_snapshot(&vm_buffer,vout)==2);
 char line[1024];event.start=event.end=UINT64_MAX;event.duration=0;event.duration_valid=true;
 int old=old_vm_format(line,sizeof(line),&event);assert(old>255);route(line);assert(delivered_size==255 && !strchr(delivered,'\n'));delivered_size=0;delivered[0]=0;
 int n=sweet_bbipc_format(line,256,39,&e);assert(n>0 && n<=255 && line[n-1]=='\n');route(line);assert(delivered_size==(size_t)n && !strcmp(delivered,line));fputs(delivered,stdout);delivered_size=0;delivered[0]=0;
 event.version=1;n=sweet_bbvm_format(line,256,&event);assert(n>0 && n<=255 && line[n-1]=='\n');route(line);assert(delivered_size==(size_t)n && !strcmp(delivered,line));fputs(delivered,stdout);delivered_size=0;delivered[0]=0;
 event.version=2;n=sweet_bbvm_format(line,256,&event);assert(n>0 && n<=255 && line[n-1]=='\n');route(line);assert(delivered_size==(size_t)n && !strcmp(delivered,line));fputs(delivered,stdout);
 assert(!sweet_bbipc_format(line,1,39,&e) && !sweet_bbvm_format(line,1,&event));
 assert(sizeof(metadata)+sizeof(selected)+8<=8192);return 0;
}
"""
        compiler=shutil.which('clang') or shutil.which('cc');self.assertIsNotNone(compiler)
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source=root/'roundtrip.c';source.write_text(body)
            subprocess.run([compiler,'-std=c11','-O2','-Wall','-Wextra','-Werror',str(source),'-o',str(root/'test')],check=True)
            result=subprocess.run([str(root/'test')],capture_output=True,text=True,timeout=30)
            self.assertEqual(result.returncode,0,result.stderr)
        lines=result.stdout.splitlines(keepends=True);self.assertEqual(len(lines),3)
        ipc=parse_ipc(lines[0]);entry=parse_compact_vm(lines[1]);returned=parse_compact_vm(lines[2])
        self.assertEqual(ipc['t'],(1<<64)-1);self.assertEqual(ipc['n'],65536)
        self.assertEqual(entry['t'],returned['t']);self.assertEqual(entry['th'],returned['th']);self.assertEqual(entry['b'],returned['b'])
        self.assertEqual(returned['vld'],1);self.assertTrue(all(len(line.encode())<=255 for line in lines))

    def test_disabled_ipc_preserves_exact_old_svc_snapshot_and_text(self):
        old=fixture.fragment('bsd/dev/sweet_sample.c',ROOT/'xnu/patches/0173-sweet-bounded-backboard-vm-fault.patch')
        extra='#define printf capture\n'+vm.function(old,'sweet_ff_trace').replace('sweet_ff_trace(','old_ff_trace(')+vm.function(old,'sweet_ff_dump').replace('sweet_ff_dump(','old_ff_dump(')+'\n#undef printf\n'
        self.runtime(r"""
int main(void) {
 sweet_ff_trace(0,-47,&regs);sweet_ff_trace(1,-47,&regs);sweet_ff_trace(3,-47,&regs);sweet_ff_dump(0);
 char expected[4096];assert(captured_size<sizeof(expected));strcpy(expected,captured);struct sweet_ff_ring final=sweet_ff_events;
 reset();old_ff_trace(0,-47,&regs);old_ff_trace(1,-47,&regs);old_ff_trace(3,-47,&regs);old_ff_dump(0);
 assert(!strcmp(expected,captured) && !memcmp(&final,&sweet_ff_events,sizeof(final)) && !clock_reads);
 absent=true;old_ff_trace(0,-47,&regs);sweet_ff_trace(0,-47,&regs);assert(sweet_ff_events.total==3);
 puts("PASS");return 0;
}
""",extra)

    def test_vm_parser_preserves_old_partial_and_new_return_identity(self):
        old='SWEET BBVM: token=2 kind=return pid=56 tid=316 pc=0x100 lr=0x104 sp=0x200 cpsr=0x0 esr=0x92000007 far=0x300 address=0x300 prot=0x1 start_abs=0x0 end_abs=0x10 result=0 duration_abs=0x10 duration_valid=1\n'
        self.assertTrue(parse_vm_record(old)['complete'])
        partial=parse_vm_record(old.replace('duration_valid=1','duration_valid=SWEET BOOTHEART: sample=26'))
        self.assertFalse(partial['complete']);self.assertEqual(partial['missing'],['duration_valid']);self.assertNotIn('duration_valid',partial['numeric'])
        new='SWEET BBVM: v=2 t=2 k=R p=56 th=0x13c b=0x0 z=0x10 r=0 d=0x10 vld=1\n'
        r=parse_vm_record(new);self.assertEqual(r['numeric']['tid'],316);self.assertNotIn('pc',r['numeric']);self.assertTrue(r['complete'])
        for bad in (new[:-1],new.replace('v=2','v=3'),new.replace('vld=1','vld=0'),new.replace('t=2','t=41')):
            with self.assertRaises(ValueError):parse_vm_record(bad)

    def test_source_scope_and_unchanged_old_svc_format(self):
        patch=PATCH.read_text();self.assertEqual(patch.count('diff --git '),3)
        old=fixture.fragment('bsd/dev/sweet_sample.c',ROOT/'xnu/patches/0173-sweet-bounded-backboard-vm-fault.patch')
        for name in ('sweet_bbpc_trace','sweet_bbvm_enter','sweet_bbvm_leave','sweet_ff_fault_trace','sweet_ff_screen_dump'):
            self.assertEqual(vm.function(SOURCE,name),vm.function(old,name))
        fmt=r'printf\("SWEET FFSVC: n=.*?e->cpsr\);'
        self.assertEqual(re.search(fmt,DUMP,re.S)[0],re.search(fmt,vm.function(old,'sweet_ff_dump'),re.S)[0])
        self.assertLess(PRODUCER.index('bool accepted = sweet_ff_record'),PRODUCER.index('context.words[i]'))
        for forbidden in ('copyin','ipc_port','ipc_space','task_lock','proc_find','printf','sweet_ramoops','tsleep','kalloc'):
            self.assertNotIn(forbidden,PRODUCER)
        self.assertIn('!sweet_bbipc_is_configured()',vm.function(SOURCE,'sweet_sample_thread'))
        self.assertIn('sweet_bbipc_prepare(enabled)',vm.function(SOURCE,'sweet_sample_start'))


if __name__=='__main__':unittest.main()
