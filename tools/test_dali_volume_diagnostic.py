#!/usr/bin/env python3
"""Replay the Dali volume diagnostic delta and test its actual code on host mocks.

This does not exercise physical GPIO, validate MMIO, or prove HID delivery.
"""
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'tools/dali_input_tests/base/iokit/Kernel/arm/hid/SweetHID.cpp'
PATCHES = (
    ROOT / 'xnu/patches/0209-dali-volume-up-readonly-gpio.patch',
    ROOT / 'xnu/patches/0210-sweet-dali-legacy-touch-guard.patch',
    ROOT / 'xnu/patches/0211-dali-volume-up-rejection-diagnostics.patch',
)
EXPECTED = {
    'SweetHID.cpp': '0e05a5c761379480dffe826676abbc04cc06d6e9338c67df87fc25ac6e935d98',
    'DaliVolumeKey.h': 'a6e52fd1e7e8416fa61a6cdd72ca27b22d2afae1585e5bd6ff57c74e22728e5d',
}


def run(command, **kwargs):
    subprocess.run(command, check=True, **kwargs)


def main():
    compiler = shutil.which('clang++')
    if not compiler:
        raise SystemExit('clang++ is required')
    with tempfile.TemporaryDirectory(prefix='dali-volume-diagnostic-') as name:
        work = Path(name)
        hid = work / 'iokit/Kernel/arm/hid'
        hid.mkdir(parents=True)
        shutil.copyfile(BASE, hid / 'SweetHID.cpp')
        touch = work / 'bsd/dev/sweet_touch.c'
        touch.parent.mkdir(parents=True)
        shutil.copyfile(ROOT / 'tools/dali_input_tests/base/bsd/dev/sweet_touch.c', touch)
        for patch in PATCHES:
            result = subprocess.run(['patch', '--batch', '--fuzz=0', '-p1', '-i', str(patch)],
                                    cwd=work, text=True, capture_output=True, check=True)
            if 'offset' in result.stdout.lower() or 'fuzz' in result.stdout.lower():
                raise AssertionError(f'patch did not apply at exact location: {result.stdout}')
        for filename, expected in EXPECTED.items():
            assert hashlib.sha256((hid / filename).read_bytes()).hexdigest() == expected, filename
        source = (hid / 'SweetHID.cpp').read_text()
        start = source.index('struct DaliVolumeDiagnosticState {')
        end = source.index('\nvoid\nSweetHIDButtons::stop(IOService', start)
        backend = source[start:end]
        barrier = '__asm__ volatile("dmb oshld" ::: "memory");'
        assert backend.count(barrier) == 1
        (work / 'actual_backend.inc').write_text(backend.replace(barrier, '(void)0; /* host mock */'))
        shutil.copyfile(hid / 'DaliVolumeKey.h', work / 'DaliVolumeKey.h')
        env = dict(os.environ, ASAN_OPTIONS='detect_leaks=1:abort_on_error=1',
                   UBSAN_OPTIONS='halt_on_error=1')
        for test in ('test_volume', 'test_backend'):
            src = ROOT / 'tools/dali_volume_diagnostic_tests' / (test + '.cpp')
            shutil.copyfile(src, work / src.name)
            run([compiler, '-std=c++17', '-Wall', '-Wextra', '-Werror',
                 '-fsanitize=address,undefined', '-fno-omit-frame-pointer',
                 str(work / src.name), '-o', str(work / test)], cwd=work)
            run([str(work / test)], cwd=work, env=env)
    print('PASS exact 0209/0210/0211 replay and actual volume diagnostic host tests')
    print('MMIO and HID are mocked; physical behavior remains unverified.')


if __name__ == '__main__':
    main()
