#!/usr/bin/env python3
"""Run 0189's actual ulock header and hook blocks without device access."""
from pathlib import Path
import hashlib
import os
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / 'xnu/patches/0189-sweet-springboard-ulock.patch'


def section(path, old=None):
    text = PATCH.read_text()
    marker = f'--- {old or "a/" + path}\n+++ b/{path}\n'
    if text.count(marker) != 1:
        raise AssertionError(path)
    start = text.index(marker) + len(marker)
    end = text.find('\n--- ', start)
    return text[start:] if end < 0 else text[start:end + 1]


def additions(hunk):
    return ''.join(line[1:] for line in hunk.splitlines(keepends=True)
                   if line.startswith('+') and not line.startswith('+++'))


def added_hook(needle):
    hunks = re.split(r'(?m)^@@ [^\n]*\n', section('bsd/kern/sys_ulock.c'))[1:]
    matches = [additions(hunk) for hunk in hunks if needle in additions(hunk)]
    if len(matches) != 1:
        raise AssertionError(needle)
    return matches[0]


HEADER = additions(section('bsd/kern/SweetUlockTrace.h', '/dev/null'))
ENTER = added_hook('sweet_trace_token = 0;')
LEAVE = added_hook('sweet_ulock_record_end(&sweet_ulock_trace,')
EXPECTED = {
    'bsd/kern/sys_ulock.c': '89a84e38cb79a79feec904f907b3dd20ec5ed2d7dbda1d3a2a7ba32a9dfdee06',
    'bsd/sys/user.h': '6b39bb8ac79b31d7fada34da238d39d2e609baa8332ead7d2ce5e96af0e736e2',
    'bsd/dev/sweet_sample.c': '021bf0b0c785abd81cbc358722fa3a72965a5ca1206b36eb04500cf52aae3b16',
    'bsd/kern/SweetUlockTrace.h': '9c21081f70ea5970a48a36f9b78cf8122a3590ab51c3b1f1d5941ecdeeb20542',
}

PREFIX = r'''
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#define DEVELOPMENT 1
#define ARM64_BOARD_CONFIG_SWEET 1
#define SWEET_ULOCK_CAS(p, old, val) __extension__ ({ __typeof__(*(p)) want_ = (old); __atomic_compare_exchange_n((p), &want_, (val), 0, __ATOMIC_ACQ_REL, __ATOMIC_ACQUIRE); })
#define SWEET_ULOCK_LOAD(p) __atomic_load_n((p), __ATOMIC_ACQUIRE)
#define SWEET_ULOCK_STORE(p, v) __atomic_store_n((p), (v), __ATOMIC_RELEASE)
#define SWEET_ULOCK_INC(p) __atomic_add_fetch((p), 1, __ATOMIC_RELAXED)
#include "SweetUlockTrace.h"
struct ull { int unused; };
struct fake_thread;
typedef struct fake_thread *thread_t;
struct fake_wait { struct ull *ull; thread_t owner_thread, old_owner; int32_t *retval; unsigned int flags; uint64_t sweet_trace_token; };
struct fake_uthread { struct { struct fake_wait uus_ulock_wait_data; } uu_save; };
typedef struct fake_uthread *uthread_t;
struct fake_regs { int valid; uint64_t pc, lr; };
typedef struct fake_regs arm_saved_state_t;
struct fake_thread { uint64_t tid; struct fake_uthread uth; struct fake_regs regs; };
struct fake_args { uint64_t addr; };
#define THREAD_NULL ((thread_t)0)
#define THREAD_WAITING 1
static unsigned int selected;
static struct sweet_ulock_trace sweet_ulock_trace;
static void *current_task(void) { return (void *)1; }
static uint32_t sweet_load_rw_target(void *task) { assert(task == (void *)1); return selected; }
static void *get_bsdthread_info(thread_t t) { return &t->uth; }
static uint64_t thread_tid(thread_t t) { return t->tid; }
static arm_saved_state_t *find_user_regs(thread_t t) { return &t->regs; }
static int is_saved_state64(arm_saved_state_t *r) { return r->valid; }
static uint64_t get_saved_state_pc(arm_saved_state_t *r) { return r->pc; }
static uint64_t get_saved_state_lr(arm_saved_state_t *r) { return r->lr; }
static uint64_t mach_absolute_time(void) { return 9999; }
static void enter_real(int wr, thread_t self, thread_t owner_thread, thread_t old_owner,
    struct ull *ull, int32_t *retval, unsigned int flags, unsigned int opcode,
    struct fake_args *args, uint64_t value, uint64_t timeout)
{
    (void)old_owner; (void)ull; (void)retval;
'''
MID = r'''
}
static void leave_real(uthread_t uthread)
{
'''
SUFFIX = r'''
}
int main(void)
{
    struct fake_thread self = {.tid=221, .regs={1,0xabc,0xdef}};
    struct fake_thread owner = {.tid=555};
    struct fake_args args = {.addr=0x1000};
    struct sweet_ulock_snapshot out;
    struct ull u; int32_t result=0;
    selected=1;
    enter_real(THREAD_WAITING, &self, &owner, 0, &u, &result, 0x10000, 2, &args, 0x77, 0);
    assert(self.uth.uu_save.uus_ulock_wait_data.sweet_trace_token == 0);
    selected=2;
    enter_real(0, &self, &owner, 0, &u, &result, 0x10000, 2, &args, 0x77, 0);
    assert(sweet_ulock_record_snapshot(&sweet_ulock_trace, &out) == 0 && out.count == 0);
    enter_real(THREAD_WAITING, &self, &owner, 0, &u, &result, 0x10000, 2, &args, 0x77, 0);
    assert(sweet_ulock_record_snapshot(&sweet_ulock_trace, &out) == 0 && out.count == 1);
    assert(out.records[0].tid == 221 && out.records[0].owner_tid == 555 && out.records[0].opcode == 2);
    assert(out.records[0].address == 0x1000 && out.records[0].value == 0x77);
    assert(out.records[0].flags == 0x10000 && out.records[0].begin == 9999);
    assert(out.records[0].pc == 0xabc && out.records[0].lr == 0xdef);
    leave_real(&self.uth);
    assert(sweet_ulock_record_snapshot(&sweet_ulock_trace, &out) == 0 && out.count == 0);
    self.regs.valid=0;
    enter_real(THREAD_WAITING, &self, 0, 0, &u, &result, 0, 1, &args, 1, 42);
    assert(sweet_ulock_record_snapshot(&sweet_ulock_trace, &out) == 0 && out.count == 1);
    assert(out.records[0].owner_tid == 0 && out.records[0].opcode == 1 && out.records[0].pc == 0);
    assert(out.records[0].timeout_ns == 42);
    leave_real(&self.uth);
    sweet_ulock_record_close(&sweet_ulock_trace);
    enter_real(THREAD_WAITING, &self, &owner, 0, &u, &result, 0, 2, &args, 1, 0);
    assert(!self.uth.uu_save.uus_ulock_wait_data.sweet_trace_token);
    puts("ulock production-hook fixture: PASS");
    return 0;
}
'''


class UlockTests(unittest.TestCase):
    def compile_run(self, source, expected):
        compiler = shutil.which('clang') or shutil.which('cc')
        self.assertIsNotNone(compiler)
        with tempfile.TemporaryDirectory(prefix='dali-ulock-') as temp:
            directory = Path(temp)
            (directory / 'SweetUlockTrace.h').write_text(HEADER)
            source_path = directory / 'fixture.c'
            source_path.write_text(source)
            command = [compiler, '-std=gnu11', '-O1', '-g', '-Wall', '-Wextra', '-Werror',
                       '-fsanitize=address,undefined', '-fno-omit-frame-pointer', '-pthread',
                       '-I', str(directory), str(source_path), '-o', str(directory / 'fixture')]
            compiled = subprocess.run(command, capture_output=True, text=True, timeout=45)
            self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
            result = subprocess.run([str(directory / 'fixture')], capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(result.stdout.strip(), expected)

    def test_real_header_bounded_reuse_close_and_concurrency(self):
        self.assertEqual(hashlib.sha256(HEADER.encode()).hexdigest(), EXPECTED['bsd/kern/SweetUlockTrace.h'])
        self.compile_run((ROOT / 'tools/test_dali_ulock_trace.c').read_text(), 'ulock trace fixture: PASS')

    def test_actual_wait_and_continuation_hooks(self):
        directory = os.environ.get('SWEET_LOAD_SOURCE_DIR')
        if directory:
            source = (Path(directory) / 'bsd/kern/sys_ulock.c').read_text()
            start = source.index('\tif (wr == THREAD_WAITING) {\n\t\tuthread_t uthread = (uthread_t)get_bsdthread_info(self);')
            end = source.index('\n\n\tull_unlock(ull);', start)
            enter = source[start:end]
            cont = source.index('ulock_wait_continue(__unused void * parameter, wait_result_t wr)')
            start = source.index('\n#if DEVELOPMENT && defined(ARM64_BOARD_CONFIG_SWEET)', cont)
            end = source.index('\n\tret = wait_result_to_return_code(wr);', start)
            leave = source[start:end] + '\n'
            self.assertEqual(enter.count(ENTER), 1)
            self.assertEqual(leave.count(LEAVE), 1)
            self.assertLess(source.index('/* owner_thread has a +1 reference */'), source.index('waitq_assert_wait64('))
            self.assertLess(source.index('waitq_assert_wait64('), source.index(enter))
        else:
            # Pre-build has only the patch: compile its actual inserted hook bytes.
            enter = ('\tif (wr == THREAD_WAITING) {\n'
                     '\t\tuthread_t uthread = (uthread_t)get_bsdthread_info(self);\n' + ENTER + '\t}\n')
            leave = LEAVE
        self.assertIn('sweet_load_rw_target(current_task()) == 2', enter)
        self.assertNotIn('port_name_to_thread', enter)
        self.assertNotIn('ulock_resolve_owner', enter)
        self.compile_run(PREFIX + enter + MID + leave + SUFFIX, 'ulock production-hook fixture: PASS')

    def test_exact_materialized_sources_if_available(self):
        directory = os.environ.get('SWEET_LOAD_SOURCE_DIR')
        if not directory:
            self.skipTest('pre-build: no materialized XNU source tree')
        for path, digest in EXPECTED.items():
            data = (Path(directory) / path).read_bytes()
            self.assertEqual(hashlib.sha256(data).hexdigest(), digest, path)
        self.assertEqual((Path(directory) / 'bsd/kern/SweetUlockTrace.h').read_text(), HEADER)
        source = (Path(directory) / 'bsd/kern/sys_ulock.c').read_text()
        self.assertIn('#include "SweetUlockTrace.h"', source)
        self.assertIn('sweet_ulock_dump(sample, sample == SWEET_LOAD_SAMPLES - 1)',
                      (Path(directory) / 'bsd/dev/sweet_sample.c').read_text())


if __name__ == '__main__':
    unittest.main()
