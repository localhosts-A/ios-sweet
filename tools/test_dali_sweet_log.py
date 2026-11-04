#!/usr/bin/env python3
"""Replay and host-test the exact G4-root/logger-v6 source without device I/O."""
from pathlib import Path
import hashlib
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
LEGACY_TOOLS = ROOT / 'tools/sweet_log_tests/legacy/tools'
sys.path.insert(0, str(LEGACY_TOOLS))
sys.path.insert(0, str(ROOT / 'tools'))
import test_dali_gui_load_progress as source

EXPECTED_UFS = 'c87a315f99d56348017db3d93fbc2e5466b8b3c7f83438ac8f13c6767fe629bb'
EXPECTED_HSG1 = '10a0d8e00890569dd6997765f0b602b9e33b624007408029cc352934d34ab6a9'
actual_ufs = source.TRIAL_SOURCE['bsd/dev/sweet_ufs.c'].encode('latin-1')
actual_hsg1 = source.TRIAL_SOURCE['bsd/dev/SweetUFSHSG1.h'].encode('latin-1')
assert hashlib.sha256(actual_ufs).hexdigest() == EXPECTED_UFS
assert hashlib.sha256(actual_hsg1).hexdigest() == EXPECTED_HSG1


def copy_referenced_patches(destination):
    """Materialize only legacy test inputs from the repository's exact patches."""
    destination.mkdir(parents=True, exist_ok=True)
    names = set()
    for script in LEGACY_TOOLS.glob('*.py'):
        names.update(re.findall(r'\b\d{4}-[A-Za-z0-9_.-]+\.patch\b', script.read_text()))
    for name in sorted(names):
        patch = ROOT / 'xnu/patches' / name
        if not patch.is_file():
            raise AssertionError('missing referenced formal patch: ' + name)
        shutil.copyfile(patch, destination / name)


with tempfile.TemporaryDirectory(prefix='dali-sweetlog-formal-') as tmp:
    parent = Path(tmp)
    workspace = parent / 'workspace'
    workspace.mkdir()
    work = workspace / 'sweetlog'
    fixture_root = workspace / 'ufs-hsg4-root-20261104-v1'
    v4_baseline = workspace / 'sweetlog-slots-20261104-v4/xnu/bsd/dev'
    v4_baseline.mkdir(parents=True)
    shutil.copyfile(ROOT / 'tools/sweet_log_tests/sweetlog-v4-baseline.c',
                    v4_baseline / 'sweet_ufs.c')
    (work / 'tests').mkdir(parents=True)
    (work / 'tools').mkdir()
    (work / 'xnu/bsd/dev').mkdir(parents=True)
    (fixture_root / 'tests').mkdir(parents=True)
    (fixture_root / 'artifacts').mkdir()
    (fixture_root / 'xnu/bsd/dev').mkdir(parents=True)
    (fixture_root / 'xnu/pexpert/gen').mkdir(parents=True)
    (fixture_root / 'fixtures/legacy').mkdir(parents=True)

    # Materialize actual source bytes from the exact patch chain, not a handwritten model.
    (work / 'xnu/bsd/dev/sweet_ufs.c').write_bytes(actual_ufs)
    for name, value in source.TRIAL_SOURCE.items():
        path = fixture_root / 'xnu' / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding='latin-1')
    # Keep the old logger as the baseline side of producer-interleaving tests.
    # This is the exact pre-0201 UFS source used in the pretrial fixture.
    baseline_ufs = source.apply_exact_hunks(
        source.UFS_PRE_SPEED, 'bsd/dev/sweet_ufs.c', source.UFS_SPEED_PATCH)
    (fixture_root / 'xnu/bsd/dev/sweet_ufs.c').write_text(baseline_ufs, encoding='latin-1')
    shutil.copyfile(ROOT / 'tools/sweet_log_tests/sweet_usb.c',
                    fixture_root / 'xnu/bsd/dev/sweet_usb.c')
    # Legacy host tests read their historical patch inputs by the repository paths.
    legacy_tools = ROOT / 'tools/sweet_log_tests/legacy/tools'
    shutil.copytree(legacy_tools, fixture_root / 'fixtures/legacy/tools')
    copy_referenced_patches(fixture_root / 'fixtures/legacy/xnu/patches')
    for file in (ROOT / 'tools/sweet_log_v6_tests').glob('test_*.py'):
        if file.name == 'test_g4_integration.py':
            # This old wrapper reinjects a pre-root HSG4 source delta into the
            # trial-final source and creates duplicate fields. The exact replay
            # plus current slot/GPT/flow tests below replace that stale harness.
            continue
        shutil.copyfile(file, work / 'tests' / file.name)
    for name in ('sweet-log.py', 'sweet-log-pull.sh', 'sweet_log_evidence.py',
                 'sweet_preboot_capture.py', 'wait_return_recover.py'):
        shutil.copyfile(ROOT / 'tools' / name, work / 'tools' / name)
        if name.endswith('.sh'):
            (work / 'tools' / name).chmod(0o755)

    # Give the logger harness its sibling G4 tree and final UFS helper header.
    # Keep the sibling UFS C file at the pre-v6 baseline for old/new controls.
    (fixture_root / 'xnu/bsd/dev/SweetUFSHSG1.h').write_bytes(actual_hsg1)
    shutil.copytree(ROOT / 'tools/ufs_speed_fixtures',
                    fixture_root / 'fixtures/legacy/tools/ufs_speed_fixtures', dirs_exist_ok=True)

    # The HSG4 transition test's only historical dependency is a frozen pre-PMC
    # header used as an explicit negative control; no device or trial files enter.
    legacy_root = parent / '.tmp/dali-ufs-speed-20261103'
    legacy_tools = legacy_root / 'tools'
    legacy_tools.parent.mkdir(parents=True)
    shutil.copytree(LEGACY_TOOLS, legacy_tools)
    copy_referenced_patches(legacy_root / 'xnu/patches')
    diagnostic_header = parent / '.tmp/ufs-hsg4-diag-20261104/xnu/bsd/dev'
    diagnostic_header.mkdir(parents=True)
    shutil.copyfile(ROOT / 'tools/sweet_log_tests/SweetUFSHSG1.pre-pmc-diagnostic.h',
                    diagnostic_header / 'SweetUFSHSG1.h')
    shutil.copyfile(ROOT / 'tools/sweet_log_tests/test_hsg4.py',
                    fixture_root / 'tests/test_hsg4.py')

    tests = sorted((work / 'tests').glob('test_*.py'))
    if len(sys.argv) > 1:
        requested = set(sys.argv[1:])
        tests = [test for test in tests if test.name in requested]
        if {test.name for test in tests} != requested:
            raise SystemExit('unknown logger test filter')
    for test in tests:
        print('RUN', test.name, flush=True)
        subprocess.run([sys.executable, '-I', str(test)], cwd=work, check=True, timeout=180)

    (fixture_root / 'xnu/bsd/dev/sweet_ufs.c').write_bytes(actual_ufs)
    test = fixture_root / 'tests/test_hsg4.py'
    print('RUN HSG4 transition', flush=True)
    subprocess.run([sys.executable, '-I', str(test)], cwd=fixture_root,
                   check=True, timeout=240)

print('PASS exact post-0206 UFS source replay, HSG4 transition, and slot/logger host suite')
print('Limit: mocked UFS I/O and candidate-only evidence do not prove hardware writes, boot identity, or payload integrity.')
