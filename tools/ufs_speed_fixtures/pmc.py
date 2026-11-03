#!/usr/bin/env python3
"""Execute actual PMC/idle/latch/HS functions with final and old policy headers.
The second event/final readiness are explicit mocks, not hardware observations.
"""
from pathlib import Path
import subprocess
import sys
import tempfile

repo = Path(sys.argv[1]).resolve()
view = Path(sys.argv[2]).resolve()
sys.path.insert(0, str(repo / 'tools'))
import test_dali_gui_load_progress as sources

paths = ('bsd/dev/sweet_ufs.c', 'bsd/dev/SweetUFSPWMPolicy.h', 'bsd/dev/SweetUFSHSG1.h')
for path in paths:
    assert (view / path).read_text(encoding='latin-1') == sources.SOURCE[path], path
source = sources.SOURCE[paths[0]]

def section(start, end):
    assert source.count(start) == source.count(end) == 1
    return source[source.index(start):source.index(end, source.index(start))]

actual = (section('static int\nufs_pwm_latch(', '/* One nonblocking claim.') +
          section('/* Failure-only instrumentation;', '/* Strict wrapper for healthy, owned, completed GETs only.') +
          section('static int\nufs_pwm_pmc_observe(', '/* Called before read-capacity/GPT.'))
template = (Path(__file__).with_name('pmc.c')).read_text()
marker = '/* INSERT_PRODUCTION_FUNCTIONS */'
assert template.count(marker) == 1
code = template.replace(marker, actual)
with tempfile.TemporaryDirectory(prefix='dali-pmc-phase-') as name:
    folder = Path(name)
    (folder / 'original').mkdir()
    (folder / 'SweetUFSPWMPolicy.h').write_text(sources.SOURCE[paths[1]])
    (folder / 'original/SweetUFSPWMPolicy.h').write_text(sources.PMC_PRE_POLICY[paths[1]])
    (folder / 'hsg1-snapshot.h').write_text(sources.SOURCE[paths[2]])
    (folder / 'fixture.c').write_text(code)
    for negative in (False, True):
        exe = folder / ('original-test' if negative else 'final-test')
        command = ['clang', '-std=c11', '-O1', '-g', '-Wall', '-Wextra', '-Werror',
                   '-fsanitize=address,undefined', '-fno-omit-frame-pointer',
                   *(['-DNEGATIVE_CONTROL=1'] if negative else []),
                   str(folder / 'fixture.c'), '-o', str(exe)]
        built = subprocess.run(command, capture_output=True, text=True, timeout=120)
        assert built.returncode == 0, built.stdout + built.stderr
        run = subprocess.run([str(exe)], capture_output=True, text=True, timeout=30)
        assert run.returncode == 0, run.stdout + run.stderr
        wanted = ('PASS live-first-sample-then-UPMS ret=5 commands=1 ack=0' if negative else
                  'PASS live-first-sample-then-UPMS ret=0 commands=1 ack=1')
        assert wanted in run.stdout
        assert 'PASS all actual PMC/idle/atomic latch and HS readback cases' in run.stdout
        print('PASS actual PMC phase fixture: ' + ('old-policy negative control' if negative else 'final pending-DP policy'))
print('PASS actual PMC events, strict pre/post ownership/readiness, ACK/readback, fatal/timeout/no-retry cases; hardware mocked')
