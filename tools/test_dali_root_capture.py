#!/usr/bin/env python3
"""Test root evidence gates without ADB, a phone or large fixture images."""

import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location('root_capture', Path(__file__).with_name('dali-root-capture.py'))
capture = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(capture)


class RootCaptureTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'root.raw'
        self.data = bytes(range(256)) * 4
        self.digest = hashlib.sha256(self.data).hexdigest()

    def run_capture(self, data=None, hashes=None, identities=None):
        data = self.data if data is None else data
        identity = {'properties': {'ro.product.device': 'dali'}}
        with patch.object(capture, 'PREFIX_BYTES', len(self.data)), \
             patch.object(capture, 'probe', side_effect=identities or [identity, identity]), \
             patch.object(capture, 'remote_hash', side_effect=hashes or [self.digest, self.digest]), \
             patch.object(capture, 'stream_prefix', side_effect=lambda serial, stream: stream.write(data)), \
             patch.object(capture.shutil, 'disk_usage', return_value=type('Space', (), {'free': 1 << 30})()):
            return capture.capture('test-target', self.path, 'b')

    def test_dry_run_never_uses_adb_or_creates_files(self):
        with patch.object(capture, 'adb', side_effect=AssertionError('unexpected ADB')), \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(capture.main(['--serial', 'test-target', '--output', str(self.path)]), 0)
        self.assertEqual(list(self.path.parent.iterdir()), [])

    def test_complete_capture_hashes_and_metadata(self):
        result = self.run_capture()
        self.assertEqual(self.path.read_bytes(), self.data)
        self.assertEqual(result['sha256'], self.digest)
        self.assertFalse(result['phone_write_occurred'])
        self.assertEqual(json.loads(self.path.with_suffix('.raw.json').read_text()), result)
        self.assertFalse(self.path.with_suffix('.raw.part').exists())
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.path.with_suffix('.raw.json').stat().st_mode & 0o777, 0o600)

    def test_short_capture_retains_only_unverified_partial(self):
        with self.assertRaisesRegex(RuntimeError, 'short/oversized'):
            self.run_capture(self.data[:-1])
        self.assertFalse(self.path.exists())
        self.assertTrue(self.path.with_suffix('.raw.part').exists())
        self.assertFalse(self.path.with_suffix('.raw.json').exists())

    def test_hash_or_identity_change_not_published(self):
        for kwargs in ({'hashes': [self.digest, '0' * 64]},
                       {'identities': [{'device': 'one'}, {'device': 'two'}]}):
            with self.subTest(kwargs=kwargs):
                with self.assertRaisesRegex(RuntimeError, 'changed during capture'):
                    self.run_capture(**kwargs)
                self.assertFalse(self.path.exists())
                self.path.with_suffix('.raw.part').unlink()

    def test_existing_evidence_is_not_overwritten(self):
        self.path.write_bytes(b'keep')
        with self.assertRaisesRegex(RuntimeError, 'already exist'):
            self.run_capture()
        self.assertEqual(self.path.read_bytes(), b'keep')

    def test_probe_rejects_mounted_root_and_wrong_slot(self):
        values = {'ro.product.device': 'dali', 'ro.product.model': '25060RK16C',
                  'ro.boot.slot_suffix': '_b', 'sys.boot_completed': '1'}
        def adb(serial, *args):
            if args[:2] == ('shell', 'getprop'):
                return values[args[2]]
            if args == ('shell', 'id', '-u'):
                return '0'
            if args[:2] == ('shell', 'blockdev'):
                return str(capture.PARTITION_BYTES)
            if args[:2] == ('shell', 'readlink'):
                return '/dev/block/sdc97'
            if args[-1] == '/sys/class/block/sdc97/dev':
                return '8:97'
            if args[-1] == '/proc/self/mountinfo':
                return '1 0 8:97 / /mnt/ios rw - hfsplus /dev/block/sdc97 rw'
            raise AssertionError(args)
        with patch.object(capture, 'adb', side_effect=adb):
            with self.assertRaisesRegex(RuntimeError, 'mounted'):
                capture.probe('test-target', 'b')
            with self.assertRaisesRegex(RuntimeError, 'identity/slot'):
                capture.probe('test-target', 'a')


if __name__ == '__main__':
    unittest.main()
