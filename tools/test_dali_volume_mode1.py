#!/usr/bin/env python3
"""Replay mode-1 GPIO patch and host-test actual HID backend; no device I/O."""
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'tools/dali_input_tests/base'
PATCHES = [ROOT / 'xnu/patches' / name for name in (
    '0209-dali-volume-up-readonly-gpio.patch',
    '0210-sweet-dali-legacy-touch-guard.patch',
    '0211-dali-volume-up-rejection-diagnostics.patch',
    '0212-dali-volume-up-mode1-input.patch',
)]
EXPECTED = {'SweetHID.cpp': '3025242102562a690dc3d63b7ec42f73f80d389941849739867f600a890a6334',
            'DaliVolumeKey.h': 'b4d268161ce2ee3956d825be12a582b93f0b74b205baa481352cb061075d5b8a'}


def main():
    compiler = shutil.which('clang++')
    if not compiler:
        raise SystemExit('clang++ required')
    with tempfile.TemporaryDirectory(prefix='dali-volume-mode1-') as name:
        work = Path(name)
        for rel in ('bsd/dev/sweet_touch.c', 'iokit/Kernel/arm/hid/SweetHID.cpp'):
            target = work / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(BASE / rel, target)
        for patch in PATCHES:
            p = subprocess.run(['patch', '--batch', '--fuzz=0', '-p1', '-i', str(patch)],
                               cwd=work, capture_output=True, text=True, check=True)
            if 'offset' in p.stdout.lower() or 'fuzz' in p.stdout.lower():
                raise AssertionError('non-exact patch replay: ' + p.stdout)
        hid = work / 'iokit/Kernel/arm/hid'
        for filename, expected in EXPECTED.items():
            assert hashlib.sha256((hid / filename).read_bytes()).hexdigest() == expected
        source = (hid / 'SweetHID.cpp').read_text()
        start = source.index('struct DaliVolumeDiagnosticState {')
        end = source.index('\nvoid\nSweetHIDButtons::stop(IOService', start)
        backend = source[start:end]
        barrier = '__asm__ volatile("dmb oshld" ::: "memory");'
        assert backend.count(barrier) == 1
        (work / 'actual_backend.inc').write_text(backend.replace(barrier, '(void)0; /* host mock */'))
        shutil.copyfile(hid / 'DaliVolumeKey.h', work / 'DaliVolumeKey.h')
        env = dict(os.environ, ASAN_OPTIONS=('detect_leaks=0:abort_on_error=1' if sys.platform == 'darwin' else 'detect_leaks=1:abort_on_error=1'),
                   UBSAN_OPTIONS='halt_on_error=1')
        for test in ('test_volume', 'test_backend'):
            src = ROOT / 'tools/dali_volume_mode1_tests' / (test + '.cpp')
            shutil.copyfile(src, work / src.name)
            subprocess.run([compiler, '-std=c++17', '-Wall', '-Wextra', '-Werror',
                            '-fsanitize=address,undefined', '-fno-omit-frame-pointer',
                            str(work / src.name), '-o', str(work / test)], cwd=work, check=True)
            subprocess.run([str(work / test)], cwd=work, env=env, check=True)
    print('PASS 0209-0212 exact replay and mode-1 actual backend host tests')
    print('MMIO and HID are mocked; physical key correlation remains unverified.')


if __name__ == '__main__':
    main()
