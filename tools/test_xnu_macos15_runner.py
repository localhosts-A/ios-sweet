#!/usr/bin/env python3
"""Execute the runner guards; native macOS/iig compatibility still requires CI."""
from pathlib import Path
import os
import subprocess
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / '.github/workflows/xnu.yml'


class MacOS15RunnerTest(unittest.TestCase):
    def run_guard(self, runner, machine, version):
        workflow = WORKFLOW.read_text()
        start = workflow.index('          test "$RUNNER_ARCH" = ARM64')
        stop = workflow.index('          brew install ', start)
        guard = textwrap.dedent(workflow[start:stop])
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name, argument, value in [('uname', '-m', machine),
                                          ('sw_vers', '-productVersion', version)]:
                path = root / name
                path.write_text('#!/bin/sh\n[ "$#" = 1 ] && [ "$1" = "' + argument +
                                '" ] || exit 99\nprintf "%s\\n" "' + value + '"\n')
                path.chmod(0o700)
            environment = dict(os.environ, RUNNER_ARCH=runner,
                               PATH=str(root) + os.pathsep + os.environ['PATH'])
            return subprocess.run(['bash', '-c', 'set -eu\n' + guard],
                                  env=environment, capture_output=True, text=True,
                                  timeout=10)

    def test_actual_guard_accepts_macos15_arm64(self):
        for version in ['15.0', '15.8.2']:
            with self.subTest(version=version):
                result = self.run_guard('ARM64', 'arm64', version)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_actual_guard_rejects_non_arm64_runner(self):
        self.assertNotEqual(self.run_guard('X64', 'arm64', '15.8.2').returncode, 0)

    def test_actual_guard_rejects_intel_host(self):
        self.assertNotEqual(self.run_guard('ARM64', 'x86_64', '15.8.2').returncode, 0)

    def test_actual_guard_rejects_other_os_versions(self):
        for version in ['14.8', '26.0', '']:
            with self.subTest(version=version):
                result = self.run_guard('ARM64', 'arm64', version)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('Expected macOS 15 Arm64 runner', result.stderr)

    def test_pinned_toolchain_and_existing_target_gates(self):
        workflow = WORKFLOW.read_text()
        self.assertIn('    runs-on: macos-15\n', workflow)
        self.assertNotIn('    runs-on: macos-latest', workflow)
        self.assertIn('          XC=/Applications/Xcode_16.4.app', workflow)
        self.assertIn('          test -d "$XC/Contents/Developer"', workflow)
        self.assertIn('          command -v xcodes', workflow)
        self.assertNotIn('/usr/local/bin/xcodes', workflow)
        self.assertIn("MACOS_VERSION: ${{ inputs.macos_version || '13.5' }}", workflow)
        self.assertIn("KERNEL_CONFIG: ${{ inputs.kernel_config || 'DEVELOPMENT' }}", workflow)
        self.assertIn("MACHINE_CONFIG: ${{ inputs.machine_config || 'SWEET' }}", workflow)
        self.assertIn('${{ runner.arch }}', workflow)
        self.assertIn('svc3=backboardd fault3=off', workflow)
        self.assertIn('python3 tools/test_dali_backboard_user_entry.py', workflow)
        self.assertIn('python3 tools/test_xnu_macos15_runner.py', workflow)
        self.assertNotIn('sed -i \'\' \'s/kernelContent', workflow)


if __name__ == '__main__':
    unittest.main()
