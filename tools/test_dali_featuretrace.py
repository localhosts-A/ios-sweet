#!/usr/bin/env python3
"""Exercise actual bounded FeatureFlags event-ring/helper code without a phone."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / 'xnu/patches/0164-sweet-bounded-featureflags-svc-ring.patch'


def added(path):
    for block in PATCH.read_text().split('diff --git ')[1:]:
        if f'b/{path}\n' in block:
            return ''.join(line[1:] for line in block.splitlines(keepends=True)
                           if line.startswith('+') and not line.startswith('+++'))
    raise AssertionError(path)


class FeatureTraceTest(unittest.TestCase):
    def compile_run(self, source):
        compiler = shutil.which('clang') or shutil.which('cc')
        self.assertIsNotNone(compiler)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'trace.c').write_text(source)
            subprocess.run([compiler, '-std=c11', '-O2', '-Wall', '-Wextra', '-Werror',
                            '-pthread', str(root / 'trace.c'), '-o', str(root / 'trace')],
                           check=True)
            result = subprocess.run([str(root / 'trace')], capture_output=True, text=True,
                                    timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), 'PASS')

    def test_actual_ring_wrap_drain_filter_and_exhaustion(self):
        self.compile_run('''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
''' + added('bsd/dev/SweetFeatureTrace.h') + '''
int main(void) {
    struct sweet_ff_ring ring={0};
    struct sweet_ff_event out[SWEET_FF_RECORDS+1]={0};
    assert(!sweet_ff_target(0,"init_featureflag"));
    assert(!sweet_ff_target(2,"init_featureflag"));
    assert(!sweet_ff_target(1,NULL));
    assert(!sweet_ff_target(1,"init_featureflags"));
    assert(!sweet_ff_target(1,"init_featurefla"));
    assert(!sweet_ff_target(1,"SpringBoard"));
    assert(sweet_ff_target(1,"init_featureflag"));
    uint32_t last_drain=0;
    for(uint32_t seq=1;seq<=SWEET_FF_EVENT_LIMIT;++seq) {
        struct sweet_ff_event event={.pid=42,.tid=77,.call=(int32_t)seq,
            .phase=seq%4,.pc=seq*16u,.lr=seq*32u,.sp=seq*64u,.x0=seq*128u};
        struct sweet_ff_event before=event;
        assert(sweet_ff_record(&ring,&event));
        assert(!memcmp(&event,&before,sizeof(event)));
        if(seq%113==0 || seq==SWEET_FF_EVENT_LIMIT) {
            out[SWEET_FF_RECORDS].seq=0xaabbccdd;
            uint32_t expected=seq-last_drain;
            if(expected>SWEET_FF_RECORDS)expected=SWEET_FF_RECORDS;
            uint32_t count=sweet_ff_snapshot(&ring,out);
            assert(count==expected && !ring.count && ring.total==seq);
            for(uint32_t i=0;i<count;++i) {
                uint32_t n=seq-count+i+1;
                assert(out[i].seq==n && out[i].call==(int32_t)n && out[i].phase==n%4);
                assert(out[i].pc==n*16u && out[i].lr==n*32u && out[i].sp==n*64u);
                assert(out[i].x0==n*128u && out[i].pid==42 && out[i].tid==77);
            }
            assert(out[SWEET_FF_RECORDS].seq==0xaabbccdd);
            assert(!sweet_ff_snapshot(&ring,out));last_drain=seq;
        }
    }
    struct sweet_ff_event extra={0};struct sweet_ff_ring before=ring;
    for(unsigned int i=0;i<100;++i)assert(!sweet_ff_record(&ring,&extra));
    assert(!memcmp(&ring,&before,sizeof(ring)));
    puts("PASS");return 0;
}
''')

    def test_actual_runtime_helper_is_read_only_trylocked_and_bounded(self):
        source = added('bsd/dev/sweet_sample.c')
        start = source.index('static unsigned int sweet_ff_enabled;')
        helper = source[start:source.index('\n#endif', start)]
        self.compile_run('''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <stdarg.h>
#include <pthread.h>
''' + added('bsd/dev/SweetFeatureTrace.h') + '''
typedef uint32_t UInt32;
struct process { char p_comm[32]; int pid; };
typedef struct process *proc_t;
#define PROC_NULL NULL
struct arm_saved_state { uint64_t pc,lr,sp,x0;uint32_t cpsr;bool is64; };
static struct process process={"init_featureflag",42};
static struct arm_saved_state regs={0x1234,0x5678,0x8000,0x99,0,true};
static bool absent;
static unsigned int outputs;
static proc_t current_proc(void) { return absent ? NULL : &process; }
static int proc_pid(proc_t p) { return p->pid; }
static void *current_thread(void) { return NULL; }
static uint64_t thread_tid(void *p) { (void)p;return 77; }
static bool is_saved_state64(struct arm_saved_state *s) { return s->is64; }
static uint64_t get_saved_state_pc(struct arm_saved_state *s) { return s->pc; }
static uint64_t get_saved_state_lr(struct arm_saved_state *s) { return s->lr; }
static uint64_t get_saved_state_sp(struct arm_saved_state *s) { return s->sp; }
static uint64_t get_saved_state_reg(struct arm_saved_state *s,unsigned int n) { assert(n==0);return s->x0; }
static uint32_t get_saved_state_cpsr(struct arm_saved_state *s) { return s->cpsr; }
static bool OSCompareAndSwap(UInt32 old,UInt32 value,volatile UInt32 *p) {
    return __atomic_compare_exchange_n(p,&old,value,false,__ATOMIC_SEQ_CST,__ATOMIC_SEQ_CST);
}
static int trace_output(const char *format,...) {
    assert(strstr(format,"SWEET FFSVC:"));++outputs;return 0;
}
#define printf trace_output
''' + helper + '''
#undef printf
static void *worker(void *p) {
    (void)p;for(unsigned int i=0;i<40000;++i)sweet_ff_trace(i%4,31,&regs);
    return NULL;
}
int main(void) {
    struct arm_saved_state before=regs;struct process pbefore=process;
    sweet_ff_trace(0,31,&regs);sweet_ff_dump(0);assert(!sweet_ff_events.total && !outputs);
    sweet_ff_enabled=2;sweet_ff_trace(0,31,&regs);assert(!sweet_ff_events.total);
    sweet_ff_enabled=1;absent=true;sweet_ff_trace(0,31,&regs);absent=false;
    strcpy(process.p_comm,"not-selected");sweet_ff_trace(0,31,&regs);
    process=pbefore;regs.is64=false;sweet_ff_trace(0,31,&regs);regs=before;
    assert(!sweet_ff_events.total);
    sweet_ff_guard=1;sweet_ff_trace(0,31,&regs);assert(sweet_ff_dropped==1);
    sweet_ff_dump(0);assert(outputs==1 && !sweet_ff_events.total);
    sweet_ff_guard=0;
    for(unsigned int i=0;i<4;++i)sweet_ff_trace(i,31,&regs);
    assert(sweet_ff_events.total==4 && outputs==1);
    sweet_ff_dump(1);assert(outputs==6 && !sweet_ff_events.count && !sweet_ff_dropped);
    assert(!memcmp(&regs,&before,sizeof(regs)) && !memcmp(&process,&pbefore,sizeof(process)));
    pthread_t threads[4];
    for(unsigned int i=0;i<4;++i)assert(!pthread_create(&threads[i],NULL,worker,NULL));
    for(unsigned int i=0;i<4;++i)assert(!pthread_join(threads[i],NULL));
    assert(sweet_ff_events.total<=SWEET_FF_EVENT_LIMIT && sweet_ff_events.count<=SWEET_FF_RECORDS);
    while(sweet_ff_events.total<SWEET_FF_EVENT_LIMIT)sweet_ff_trace(0,31,&regs);
    uint32_t total=sweet_ff_events.total;
    for(unsigned int i=0;i<100;++i)sweet_ff_trace(0,31,&regs);
    assert(sweet_ff_events.total==total && !sweet_ff_guard);
    assert(!memcmp(&regs,&before,sizeof(regs)) && !memcmp(&process,&pbefore,sizeof(process)));
    sweet_ff_dump(2);assert(outputs==71 && !sweet_ff_events.count);
    puts("PASS");return 0;
}
''')

    def test_normal_and_both_continuation_paths_are_covered(self):
        sleh = added('osfmk/arm64/sleh.c')
        bsd = added('bsd/dev/arm/systemcalls.c')
        sampler = added('bsd/dev/sweet_sample.c')
        self.assertIn('sweet_ff_trace(0, sweet_ff_call, state)', sleh)
        self.assertIn('sweet_ff_trace(1, sweet_ff_call, state)', sleh)
        self.assertIn('sweet_ff_trace(2, code, regs)', bsd)
        self.assertIn('sweet_ff_trace(3, get_saved_state_svc_number(state), state)', sleh)
        self.assertIn('trap_no == 0 ? get_saved_state_reg(state, 0) : trap_no', sleh)
        self.assertIn('trap_no != MACH_ARM_TRAP_ABSTIME', sleh)
        self.assertIn('trap_no != MACH_ARM_TRAP_CONTTIME', sleh)
        self.assertIn('"sweetfftrace"', sampler)
        self.assertIn('sweet_ff_enabled = enabled == 1 ? 1u : 0u', sampler)
        self.assertIn('&& sweet_ff_enabled != 1', sampler)
        self.assertNotIn('vm_map_read_user', sampler)
        self.assertNotIn('task_lock(', sampler)


if __name__ == '__main__':
    unittest.main()
