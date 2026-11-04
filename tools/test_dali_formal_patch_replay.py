#!/usr/bin/env python3
"""Replay ordered patches 0207-0212 from formal post-0206 source bytes."""
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

POLICY_PATCH = ROOT / 'xnu/patches/0170-dali-finite-late-gui-startup-budgets.patch'
POLICY_HEADER = 'iokit/Kernel/arm/hid/SweetHIDWaitPolicy.h'
PATCHES = [
    ROOT / 'xnu/patches/0207-dali-ufs-g4-root-slot-logger-v6.patch',
    ROOT / 'xnu/patches/0208-sweet-hfs-nonjournal-full-fsync-error.patch',
    ROOT / 'xnu/patches/0209-dali-volume-up-readonly-gpio.patch',
    ROOT / 'xnu/patches/0210-sweet-dali-legacy-touch-guard.patch',
    ROOT / 'xnu/patches/0211-dali-volume-up-rejection-diagnostics.patch',
    ROOT / 'xnu/patches/0212-dali-volume-up-mode1-input.patch',
]
PATCH_SHA256 = [
    '559df291118b3bacf6bd1c30132a210ce41af75c0d0cf30b3e640ee22314917d',
    '898da76194126c9ecbec9e439928ff0af2d4ea367be303d391fb0f9adb88749a',
    '2c80c29cbf1433922efa864b964cd57a71bed67b5764facf6c586f78ab0335d9',
    '82810df5f999bf56e454e3d4ccb89d9a568d3b81de7c60714a9e371b37c15c1a',
    '905c3cd8e106040ff190e1d8e2a915a04fe214e411fb5b8b8aa7d75b0462c143',
    'a3b2386a63e7a28515729ca0b2a23e61b2755a7af580e5126fe56752e6d19d84',
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
    # Materialize the prerequisite header directly from its formal 0170 patch;
    # do not depend on a workstation-specific source checkout or fixture path.
    subprocess.run(['git', 'apply', '--include=' + POLICY_HEADER, str(POLICY_PATCH)],
                   cwd=xnu, check=True)
    assert (xnu / POLICY_HEADER).is_file()

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

print('PASS ordered 0207-0212 zero-fuzz replay and materialized-source regressions.')
print('Source replay does not constitute native kernel build or hardware validation.')
