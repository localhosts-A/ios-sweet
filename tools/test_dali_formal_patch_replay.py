#!/usr/bin/env python3
"""Replay ordered patches 0207-0210 from formal post-0206 source bytes."""
from pathlib import Path
import gzip
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import test_dali_gui_load_progress as source

PATCHES = [
    ROOT / 'xnu/patches/0207-dali-ufs-g4-root-slot-logger-v6.patch',
    ROOT / 'xnu/patches/0208-sweet-hfs-nonjournal-full-fsync-error.patch',
    ROOT / 'xnu/patches/0209-dali-volume-up-readonly-gpio.patch',
    ROOT / 'xnu/patches/0210-sweet-dali-legacy-touch-guard.patch',
]
PATCH_SHA256 = [
    '559df291118b3bacf6bd1c30132a210ce41af75c0d0cf30b3e640ee22314917d',
    '898da76194126c9ecbec9e439928ff0af2d4ea367be303d391fb0f9adb88749a',
    '2c80c29cbf1433922efa864b964cd57a71bed67b5764facf6c586f78ab0335d9',
    '82810df5f999bf56e454e3d4ccb89d9a568d3b81de7c60714a9e371b37c15c1a',
]


def put(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content.encode('latin-1') if isinstance(content, str) else content)


for patch, wanted in zip(PATCHES, PATCH_SHA256):
    assert hashlib.sha256(patch.read_bytes()).hexdigest() == wanted, patch.name

with tempfile.TemporaryDirectory(prefix='dali-formal-series-') as tmp:
    xnu = Path(tmp) / 'xnu'
    for rel, content in source.SOURCE.items():
        put(xnu / rel, content)
    put(xnu / 'bsd/dev/sweet_ufs.c', source.UFS_POST_EXT['bsd/dev/sweet_ufs.c'])
    put(xnu / 'bsd/dev/SweetUFSHSG1.h', source.SOURCE['bsd/dev/SweetUFSHSG1.h'])
    put(xnu / 'bsd/dev/SweetUFSSpeedRead.h', source.UFS_POST_EXT['bsd/dev/SweetUFSSpeedRead.h'])
    put(xnu / 'bsd/sweet_hfs/core/hfs_vnops.c', gzip.decompress(
        (ROOT / 'tools/hfs_fsync_tests/hfs_vnops.before.c.gz').read_bytes()))
    for rel in ('bsd/dev/sweet_touch.c', 'iokit/Kernel/arm/hid/SweetHID.cpp'):
        target = xnu / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / 'tools/dali_input_tests/base' / rel, target)
    wait = Path('/run/media/baka/07ede09a-a5d9-4bb0-8dc6-9a4c9910f38f/IOS/.tmp/xnu-linux-local-20261104/workspace/xnu-0206-fixture-v1/iokit/Kernel/arm/hid/SweetHIDWaitPolicy.h')
    assert wait.is_file()
    shutil.copyfile(wait, xnu / 'iokit/Kernel/arm/hid/SweetHIDWaitPolicy.h')

    for patch in PATCHES:
        print(f'APPLY {patch.name}', flush=True)
        subprocess.run(['patch', '--batch', '--fuzz=0', '-p1', '-i', str(patch)],
                       cwd=xnu, check=True)
    subprocess.run([sys.executable, '-I', str(ROOT / 'tools/test_dali_formal_source_identity.py'), str(xnu)],
                   check=True)
    env = dict(os.environ, SWEET_LOAD_SOURCE_DIR=str(xnu))
    for script, args in (
        ('test_dali_gui_load_progress.py', []),
        ('test_dali_ufs_speed.py', [str(xnu)]),
        ('test_dali_ufs_extended.py', [str(xnu)]),
    ):
        print(f'RUN materialized-source regression {script}', flush=True)
        subprocess.run([sys.executable, str(ROOT / 'tools' / script), *args],
                       check=True, cwd=ROOT, env=env, timeout=600)

print('PASS ordered 0207-0210 zero-fuzz replay and materialized-source regressions.')
print('Source replay does not constitute native kernel build or hardware validation.')
