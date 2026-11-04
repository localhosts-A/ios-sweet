#!/usr/bin/env python3
"""Compile and run PMIC key protocol against its actual patch header."""
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / 'xnu/patches/0213-dali-pmic-power-volume-down.patch'
HEADER = 'iokit/Kernel/arm/hid/DaliPmicKeyBackend.h'
with tempfile.TemporaryDirectory(prefix='dali-pmic-key-test-') as temp:
    work = Path(temp)
    subprocess.run(['git', 'apply', '--include=' + HEADER, str(PATCH)], cwd=work, check=True)
    compiler = shutil.which('clang++')
    if not compiler:
        raise SystemExit('clang++ is required')
    binary = work / 'test'
    subprocess.run([compiler, '-std=c++17', '-Wall', '-Wextra', '-Werror',
                    '-fsanitize=address,undefined', '-fno-omit-frame-pointer',
                    '-I', str((work / HEADER).parent),
                    str(ROOT / 'tools/dali_pmic_key_tests/test_pmic_key_backend.cpp'),
                    '-o', str(binary)], check=True)
    subprocess.run([str(binary)], check=True)
print('PASS PMIC key protocol host test; physical operation is not simulated')
