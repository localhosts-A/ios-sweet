#!/usr/bin/env python3
"""Offline inventory/compare tests; creates only temporary host files."""
from pathlib import Path
import os
import json
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib

D = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(D / 'tools'))
import sweet_log_evidence as e

RID = '0123456789abcdef0123456789abcdef'
OTHER = 'fedcba9876543210fedcba9876543210'
EMPTY = bytes(e.SLOT_BYTES)


def header(run_id=None):
    data = bytearray(e.SLOT_BYTES)
    struct.pack_into('<8sIIQQ', data, 0, b'SWEETLOG', 1, 4 * 1024 * 1024, 4, 1)
    data[4096:4100] = b'test'
    if run_id:
        data[32:40] = b'SWIDv002'
        data[40:72] = run_id.encode()
        struct.pack_into('<QQ', data, 72, 1, 2)
        struct.pack_into('<I', data, 88, zlib.crc32(data[:88]))
    return bytes(data)


def write_slots(directory, values):
    directory.mkdir()
    for i, data in enumerate(values):
        (directory / ('slot%d.bin' % i)).write_bytes(data)


class TestEvidence(unittest.TestCase):
    def test_candidate_is_not_proof(self):
        with tempfile.TemporaryDirectory(prefix='sweetlog-evidence-') as t:
            root = Path(t); pre = root / 'pre'; post = root / 'post'
            write_slots(pre, [header(), EMPTY, EMPTY])
            digest, receipt = e.save_inventory(pre, RID)
            self.assertEqual(receipt['first_empty_slot'], 1)
            write_slots(post, [header(), header(RID), EMPTY])
            code, result = e.compare(pre, digest, post, RID)
            self.assertEqual(code, 5); self.assertEqual(result['result'], 'candidate-only')
            self.assertEqual(result['candidate_slot'], 1)
            self.assertEqual(result['boot_identity'], 'unverified')
            self.assertEqual(result['payload_integrity'], 'unverified')
            # Payload can be changed without violating header CRC; never claim its integrity.
            data = bytearray(header(RID)); data[4096] ^= 1; (post / 'slot1.bin').write_bytes(data)
            code, result = e.compare(pre, digest, post, RID)
            self.assertEqual(code, 5); self.assertEqual(result['payload_integrity'], 'unverified')
            cli = subprocess.run([sys.executable, '-I', str(D/'tools/sweet_log_evidence.py'), 'compare',
                                  '--preboot-dir', str(pre), '--receipt-sha256', digest,
                                  '--post-dir', str(post), '--run-id', RID], capture_output=True, text=True)
            self.assertEqual(cli.returncode, 5, cli.stderr)
            self.assertEqual(json.loads(cli.stdout)['result'], 'candidate-only')

    def test_missing_duplicate_wrong_slot_and_unexpected_changes(self):
        with tempfile.TemporaryDirectory(prefix='sweetlog-evidence-') as t:
            root=Path(t); pre=root/'pre'; post=root/'post'
            write_slots(pre,[header(),EMPTY,EMPTY]); digest,_=e.save_inventory(pre,RID)
            write_slots(post,[header(),EMPTY,EMPTY]);self.assertEqual(e.compare(pre,digest,post,RID)[0],4)
            (post/'slot1.bin').write_bytes(header(RID));(post/'slot2.bin').write_bytes(header(RID))
            self.assertEqual(e.compare(pre,digest,post,RID)[0],3)
            (post/'slot1.bin').write_bytes(EMPTY)
            code,result=e.compare(pre,digest,post,RID);self.assertEqual(code,6);self.assertEqual(result['result'],'prepost-contradiction')
            (post/'slot1.bin').write_bytes(header(RID));(post/'slot2.bin').write_bytes(EMPTY)
            (post/'slot0.bin').write_bytes(header(OTHER))
            self.assertEqual(e.compare(pre,digest,post,RID)[0],6)
            (post/'slot0.bin').write_bytes(b'short')
            with self.assertRaises(e.EvidenceError):e.compare(pre,digest,post,RID)

    def test_preboot_safety_and_receipt_hash(self):
        with tempfile.TemporaryDirectory(prefix='sweetlog-evidence-') as t:
            root=Path(t);pre=root/'pre';post=root/'post'
            write_slots(pre,[header(RID),EMPTY,EMPTY])
            with self.assertRaises(e.EvidenceError):e.save_inventory(pre,RID)
            self.assertFalse((pre/'receipt.json').exists())
            (pre/'slot0.bin').write_bytes(header());digest,_=e.save_inventory(pre,RID)
            write_slots(post,[header(),header(RID),EMPTY])
            with self.assertRaises(e.EvidenceError):e.compare(pre,'0'*64,post,RID)
            changed=bytearray(EMPTY);changed[-1]=1;(pre/'slot2.bin').write_bytes(changed)
            with self.assertRaises(e.EvidenceError):e.compare(pre,digest,post,RID)
            (pre/'slot2.bin').write_bytes(EMPTY)
            with self.assertRaises(FileExistsError):e.save_inventory(pre,RID)
            with self.assertRaises(e.EvidenceError):e.make_inventory(pre,'0'*32)

    def test_invalid_preboot_or_symlink(self):
        with tempfile.TemporaryDirectory(prefix='sweetlog-evidence-') as t:
            root=Path(t);pre=root/'pre';write_slots(pre,[header(),EMPTY,EMPTY])
            data=bytearray(EMPTY);data[9000]=1;(pre/'slot1.bin').write_bytes(data)
            with self.assertRaises(e.EvidenceError):e.make_inventory(pre,RID)
            (pre/'slot1.bin').unlink();(pre/'slot1.bin').symlink_to(pre/'slot2.bin')
            with self.assertRaises(OSError):e.make_inventory(pre,RID)

    def test_fifo_is_rejected_without_blocking(self):
        with tempfile.TemporaryDirectory(prefix='sweetlog-fifo-') as t:
            root = Path(t); os.mkfifo(root/'slot0.bin')
            (root/'slot1.bin').write_bytes(EMPTY); (root/'slot2.bin').write_bytes(EMPTY)
            proc = subprocess.run([sys.executable, '-I', str(D/'tools/sweet_log_evidence.py'),
                                   'inventory', '--directory', str(root), '--run-id', RID],
                                  capture_output=True, text=True, timeout=3)
            self.assertEqual(proc.returncode, 6)
            self.assertIn('invalid local file type', proc.stdout)
            self.assertFalse((root/'receipt.json').exists())

if __name__ == '__main__':unittest.main(verbosity=2)
