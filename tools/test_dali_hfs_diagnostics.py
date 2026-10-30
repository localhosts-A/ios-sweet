#!/usr/bin/env python3
"""Compile and exercise the actual opt-in HFS diagnostic policy header."""

from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / 'xnu/patches/0160-sweet-bounded-hfs-exchange-diagnostics.patch'
HEADER = 'bsd/sweet_hfs/core/sweet_hfs_diag.h'


def added_header():
    active = False
    lines = []
    for line in PATCH.read_text(encoding='latin-1').splitlines(keepends=True):
        if line.startswith('+++ '):
            active = line.strip() == '+++ b/' + HEADER
        elif line.startswith('--- '):
            active = False
        elif active and line.startswith('+'):
            lines.append(line[1:])
    if not lines:
        raise AssertionError('new HFS policy header missing from patch')
    return ''.join(lines)


class HfsDiagnosticPolicyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        root = Path(cls.temp.name)
        (root / 'libkern').mkdir()
        (root / 'pexpert').mkdir()
        (root / 'sweet_hfs_diag.h').write_text(added_header())
        (root / 'libkern/OSAtomic.h').write_text('''
#include <stdint.h>
typedef uint32_t UInt32;
typedef int32_t SInt32;
static inline SInt32 OSAddAtomic(SInt32 n, volatile UInt32 *p) {
    return (SInt32)__atomic_fetch_add(p, (UInt32)n, __ATOMIC_RELAXED);
}
static inline int OSCompareAndSwap(UInt32 old, UInt32 next, volatile UInt32 *p) {
    return __atomic_compare_exchange_n(p, &old, next, 0, __ATOMIC_SEQ_CST, __ATOMIC_SEQ_CST);
}
''')
        (root / 'pexpert/pexpert.h').write_text('''
#include <stdbool.h>
bool PE_parse_boot_argn(const char *, void *, int);
''')
        source = root / 'policy.c'
        source.write_text('''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <pthread.h>
#include <stdio.h>
#include <string.h>
#define ARM64_BOARD_CONFIG_SWEET 1
#include "sweet_hfs_diag.h"
static unsigned int enabled;
static bool override;
static uint32_t target;
static unsigned int seen[SWEET_HFS_DIAG_EVENTS + 1];
bool PE_parse_boot_argn(const char *name, void *out, int size) {
    assert(size == 4);
    if (!strcmp(name, "sweethfsdiag")) {
        memcpy(out, &enabled, 4);
        return true;
    }
    if (!strcmp(name, "sweethfsblock") && override) {
        memcpy(out, &target, 4);
        return true;
    }
    return false;
}
static void *worker(void *unused) {
    (void)unused;
    for (unsigned int i = 0; i < 1024; ++i) {
        uint32_t n = sweet_hfs_diag_take(false);
        if (n) {
            assert(n <= SWEET_HFS_DIAG_EVENTS);
            __atomic_fetch_add(&seen[n], 1, __ATOMIC_RELAXED);
        }
    }
    return NULL;
}
int main(int argc, char **argv) {
    assert(argc == 2);
    if (!strcmp(argv[1], "disabled")) {
        for (enabled = 0; enabled <= 2; enabled += 2) {
            assert(!sweet_hfs_diag_enabled());
            assert(!sweet_hfs_diag_take(false));
            assert(!sweet_hfs_diag_take(true));
            assert(!sweet_hfs_diag_range(3059250, 1));
        }
        assert(!sweet_hfs_diag_events && !sweet_hfs_diag_errors);
    } else if (!strcmp(argv[1], "bounds")) {
        enabled = 1;
        for (unsigned int i = 1; i <= SWEET_HFS_DIAG_EVENTS; ++i)
            assert(sweet_hfs_diag_take(false) == i);
        assert(!sweet_hfs_diag_take(false));
        assert(sweet_hfs_diag_events == SWEET_HFS_DIAG_EVENTS);
        for (unsigned int i = 1; i <= SWEET_HFS_DIAG_ERRORS; ++i)
            assert(sweet_hfs_diag_take(true) == i);
        assert(!sweet_hfs_diag_take(true));
        assert(sweet_hfs_diag_errors == SWEET_HFS_DIAG_ERRORS);
    } else if (!strcmp(argv[1], "concurrent")) {
        enabled = 1;
        pthread_t threads[8];
        for (unsigned int i = 0; i < 8; ++i)
            assert(!pthread_create(&threads[i], NULL, worker, NULL));
        for (unsigned int i = 0; i < 8; ++i)
            assert(!pthread_join(threads[i], NULL));
        for (unsigned int i = 1; i <= SWEET_HFS_DIAG_EVENTS; ++i)
            assert(seen[i] == 1);
        assert(sweet_hfs_diag_events == SWEET_HFS_DIAG_EVENTS);
        assert(sweet_hfs_diag_errors == 0);
        assert(sweet_hfs_diag_take(true) == 1);
    } else if (!strcmp(argv[1], "ranges")) {
        assert(!sweet_hfs_diag_contains(10, 0, 10));
        assert(!sweet_hfs_diag_contains(10, 3, 9));
        assert(sweet_hfs_diag_contains(10, 3, 10));
        assert(sweet_hfs_diag_contains(10, 3, 12));
        assert(!sweet_hfs_diag_contains(10, 3, 13));
        assert(sweet_hfs_diag_contains(UINT32_MAX - 1, 3, UINT32_MAX));
        assert(!sweet_hfs_diag_contains(UINT32_MAX - 1, 3, 0));
        enabled = 1;
        assert(sweet_hfs_diag_range(3059250, 1));
        assert(!sweet_hfs_diag_range(1966891, 1));
        override = true;
        target = 1966891;
        assert(sweet_hfs_diag_range(1966891, 1));
        assert(!sweet_hfs_diag_range(3059250, 1));
    } else {
        return 2;
    }
    puts("PASS");
    return 0;
}
''')
        cls.binary = root / 'policy'
        compiler = shutil.which('clang') or shutil.which('cc')
        if not compiler:
            raise RuntimeError('a host C compiler is required')
        flags = ['-std=c11', '-Wall', '-Wextra', '-Werror']
        if sys.platform != 'darwin':
            flags.append('-pthread')
        subprocess.run([compiler, *flags, '-I', str(root), str(source),
                        '-o', str(cls.binary)], check=True)

    def run_case(self, case):
        result = subprocess.run([str(self.binary), case], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), 'PASS')

    def test_disabled_is_inert(self):
        self.run_case('disabled')

    def test_error_budget_survives_normal_budget(self):
        self.run_case('bounds')

    def test_concurrent_saturating_tickets(self):
        self.run_case('concurrent')

    def test_range_boundaries_and_target_override(self):
        self.run_case('ranges')

    def test_instrumentation_has_expected_contexts(self):
        text = PATCH.read_text(encoding='latin-1')
        for marker in ('bitmap-mismatch', 'exchange-before-from', 'exchange-after-to',
                       'truncate-tail-after', 'truncate-following-after', 'extent-release-after'):
            self.assertIn(marker, text)
        self.assertIn('sweet_hfs_diag_take(true)', text)


if __name__ == '__main__':
    unittest.main()
