#!/usr/bin/env python3
"""Replay 0209/0210 exactly and host-test final volume/touch source."""
from pathlib import Path
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'tools/dali_input_tests/base'
PATCH_0209 = ROOT / 'xnu/patches/0209-dali-volume-up-readonly-gpio.patch'
PATCH_0210 = ROOT / 'xnu/patches/0210-sweet-dali-legacy-touch-guard.patch'
EXPECTED = {
    'base/bsd/dev/sweet_touch.c': '54363943add43be82956541f099ccfb45296dae7674888e3725c562250645c06',
    'base/iokit/Kernel/arm/hid/SweetHID.cpp': '269e94ae1d9d6e42a33390f4499bbdd6801141691f45294a9d4cef3a0fb1868d',
    'xnu/bsd/dev/sweet_touch.c': '90ec1634c1836d16e9d1810055796f14b31a8b320878b069d82027c6f6ce5a71',
    'xnu/bsd/dev/SweetLegacyTouchPolicy.h': 'cc79805619d4726775512998bc2e3eafea98fa7da42a8a5012449f63592108fc',
    'xnu/iokit/Kernel/arm/hid/SweetHID.cpp': '61a5671cd01f834e42f6374432f394049d59c766ab52bfd76b1666dddc3f5c36',
    'xnu/iokit/Kernel/arm/hid/DaliVolumeKey.h': '42da45b453b3f9bebd34f29bf434d6a88786b673ae0c6bf8c98520a4f67a1837',
}
PATCH_HASHES = {
    PATCH_0209: '2c80c29cbf1433922efa864b964cd57a71bed67b5764facf6c586f78ab0335d9',
    PATCH_0210: '82810df5f999bf56e454e3d4ccb89d9a568d3b81de7c60714a9e371b37c15c1a',
}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def function(source, pattern, name):
    match = re.search(pattern, source)
    assert match, f'missing source function {name}'
    start = source.index('{', match.start())
    depth = 0
    for end in range(start, len(source)):
        if source[end] == '{':
            depth += 1
        elif source[end] == '}':
            depth -= 1
            if depth == 0:
                return source[match.start():end + 1]
    raise AssertionError(f'unterminated source function {name}')


def run(cmd, **kwargs):
    subprocess.run(cmd, check=True, **kwargs)


def main():
    for path, expected in PATCH_HASHES.items():
        assert sha(path) == expected, f'patch identity mismatch: {path.name}'
    for rel, expected in EXPECTED.items():
        if rel.startswith('base/'):
            assert sha(BASE / rel.removeprefix('base/')) == expected, rel

    compiler = shutil.which('clang') or shutil.which('cc')
    cxx = shutil.which('clang++')
    if not compiler or not cxx:
        raise SystemExit('clang and clang++ are required')
    env = dict(os.environ, ASAN_OPTIONS=('detect_leaks=0:abort_on_error=1' if sys.platform == 'darwin' else 'detect_leaks=1:abort_on_error=1'),
               UBSAN_OPTIONS='halt_on_error=1')

    with tempfile.TemporaryDirectory(prefix='dali-input-patches-') as tmp:
        work = Path(tmp)
        for rel in ('bsd/dev/sweet_touch.c', 'iokit/Kernel/arm/hid/SweetHID.cpp'):
            target = work / 'xnu' / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(BASE / rel, target)
        patch_root = work / 'xnu'
        for patch_file in (PATCH_0209, PATCH_0210):
            run(['patch', '--batch', '--fuzz=0', '-p1', '-i', str(patch_file)],
                cwd=patch_root, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        final = {key: patch_root / key.removeprefix('xnu/') for key in EXPECTED if key.startswith('xnu/')}
        for key, path in final.items():
            assert sha(path) == EXPECTED[key], f'patched source identity mismatch: {key}'
        hid = final['xnu/iokit/Kernel/arm/hid/SweetHID.cpp'].read_text(encoding='latin-1')
        assert '#include "SweetHIDWaitPolicy.h"' in hid
        assert 'PE_parse_boot_argn("sweethidwait"' in hid
        assert 'self->fSwipeTicks >= self->fSwipeLimit' in hid

        # Run the actual new policy header and the extracted production backend.
        vol_dir = work / 'volume'
        vol_dir.mkdir()
        shutil.copyfile(final['xnu/iokit/Kernel/arm/hid/DaliVolumeKey.h'], vol_dir / 'DaliVolumeKey.h')
        for name in ('test_volume.cpp', 'test_backend.cpp'):
            shutil.copyfile(ROOT / 'tools/dali_volume_tests' / name, vol_dir / name)
        start = hid.index('UInt32\nSweetHIDButtons::daliVolumeRead')
        end = hid.index('\nvoid\nSweetHIDButtons::stop(IOService', start)
        backend = hid[start:end]
        barrier = '__asm__ volatile("dmb oshld" ::: "memory");'
        assert backend.count(barrier) == 1
        (vol_dir / 'actual_backend.inc').write_text(backend.replace(barrier, '(void)0; /* host mock only */'))
        for name in ('test_volume', 'test_backend'):
            run([cxx, '-std=c++17', '-Wall', '-Wextra', '-Werror',
                 '-fsanitize=address,undefined', '-fno-omit-frame-pointer',
                 str(vol_dir / (name + '.cpp')), '-o', str(vol_dir / name)], cwd=vol_dir)
            run([str(vol_dir / name)], cwd=vol_dir, env=env)

        # Run the actual sweet_touch_init() gate with minimal DT/thread stubs.
        touch_dir = work / 'touch'
        touch_dir.mkdir()
        touch_header = final['xnu/bsd/dev/SweetLegacyTouchPolicy.h']
        shutil.copyfile(touch_header, touch_dir / 'SweetLegacyTouchPolicy.h')
        shutil.copyfile(ROOT / 'tools/dali_touch_guard_tests/test_legacy_touch_policy.c',
                        touch_dir / 'test_legacy_touch_policy.c')
        body = function(final['xnu/bsd/dev/sweet_touch.c'].read_text(encoding='latin-1'),
                        r'(?m)^void\s+sweet_touch_init\(void\)\s*\{', 'sweet_touch_init')
        prefix = r'''#include <assert.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>
#include "SweetLegacyTouchPolicy.h"
typedef int thread_t;
typedef int wait_result_t;
typedef int kern_return_t;
typedef void *DTEntry;
#define KERN_SUCCESS 0
#define KERN_FAILURE 5
#define kSuccess 0
static struct { bool started; int irq_hint; } st;
static bool arg_present, irq_present, lookup_ok, property_ok, property_null;
static int arg_value, irq_value, thread_result, thread_starts, thread_releases, lookups, properties;
static const void *compatible_data;
static unsigned compatible_size;
static void touch_thread(void *arg, wait_result_t result) { (void)arg; (void)result; }
static int PE_parse_boot_argn(const char *key, void *out, int size) {
    if (!strcmp(key, "sweettouch")) {
        assert(size == (int)sizeof(int)); if (arg_present) memcpy(out, &arg_value, sizeof(int)); return arg_present;
    }
    if (!strcmp(key, "sweettouchirq")) {
        assert(size == (int)sizeof(int)); if (irq_present) memcpy(out, &irq_value, sizeof(int)); return irq_present;
    }
    assert(0); return 0;
}
static int SecureDTLookupEntry(void *parent, const char *path, DTEntry *out) {
    assert(parent == NULL && !strcmp(path, "/")); ++lookups;
    if (!lookup_ok) return -1; *out = (void *)1; return kSuccess;
}
static int SecureDTGetProperty(DTEntry entry, const char *name, const void **out, unsigned int *size) {
    assert(entry == (void *)1 && !strcmp(name, "compatible")); ++properties;
    if (!property_ok) return -1; *out = property_null ? NULL : compatible_data; *size = compatible_size; return kSuccess;
}
static kern_return_t kernel_thread_start(void (*thread)(void *, wait_result_t), void *arg, thread_t *out) {
    assert(thread == touch_thread && arg == NULL); ++thread_starts; *out = 37; return thread_result;
}
static void thread_deallocate(thread_t thread) { assert(thread == 37); ++thread_releases; }
static void reset(const void *data, unsigned size) {
    st.started = false; st.irq_hint = -1; arg_present = irq_present = false; arg_value = irq_value = 0;
    lookup_ok = property_ok = true; property_null = false; compatible_data = data; compatible_size = size;
    thread_result = 0; thread_starts = thread_releases = lookups = properties = 0;
}
'''
        main = r'''
int main(void) {
    static const char dali[] = "xiaomi,sweet\0xiaomi,dali\0mediatek,mt6991";
    static const char other[] = "xiaomi,sweet\0qcom,sm6150";
    static const char malformed[] = {'x','i','a','o','m','i',',','d','a','l','i'};
    reset(dali, sizeof(dali)); sweet_touch_init(); assert(st.started && !thread_starts);
    reset(dali, sizeof(dali)); arg_present=true; arg_value=1; sweet_touch_init(); assert(!thread_starts);
    reset(other, sizeof(other)); arg_present=true; arg_value=0; sweet_touch_init(); assert(!thread_starts);
    reset(other, sizeof(other)); lookup_ok=false; sweet_touch_init(); assert(!thread_starts && lookups==1);
    reset(other, sizeof(other)); property_ok=false; sweet_touch_init(); assert(!thread_starts && properties==1);
    reset(other, sizeof(other)); property_null=true; sweet_touch_init(); assert(!thread_starts);
    reset(malformed, sizeof(malformed)); sweet_touch_init(); assert(!thread_starts);
    reset(other, sizeof(other)); irq_present=true; irq_value=9; sweet_touch_init();
    assert(thread_starts==1 && thread_releases==1 && st.irq_hint==9);
    sweet_touch_init(); assert(thread_starts==1); /* init is one-shot */
    reset(other, sizeof(other)); thread_result=KERN_FAILURE; sweet_touch_init();
    assert(thread_starts==1 && !thread_releases);
    puts("actual sweet_touch_init() source gate: Dali/unknown boards blocked; non-Dali legacy launch preserved");
    return 0;
}
'''
        init_test = touch_dir / 'test_actual_touch_init.c'
        init_test.write_text(prefix + '\n' + body + '\n' + main)
        for name, source in (('test_legacy_touch_policy', touch_dir / 'test_legacy_touch_policy.c'),
                             ('test_actual_touch_init', init_test)):
            run([compiler, '-std=c11', '-Wall', '-Wextra', '-Werror',
                 '-fsanitize=address,undefined', '-fno-omit-frame-pointer',
                 str(source), '-o', str(touch_dir / name)], cwd=touch_dir)
            run([str(touch_dir / name)], cwd=touch_dir, env=env)

    print('PASS exact zero-fuzz 0209/0210 replay, volume policy/backend, and actual touch initializer host tests')
    print('Limit: MMIO is mocked/read-only; policy and initializer tests do not prove GPIO electrical behavior or physical touch.')


if __name__ == '__main__':
    main()
