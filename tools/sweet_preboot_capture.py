#!/usr/bin/env python3
"""Capture a new, read-only three-slot preboot inventory; no reboot or flash."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sweet_log_evidence as evidence

PARTITION = '/dev/block/by-name/unixnu_logs'
PARTITION_BYTES = 33554432


def utc():
    return datetime.now(timezone.utc).isoformat()


class Transport:
    def __init__(self, serial=None):
        self.serial = serial

    def adb(self, args, timeout=60):
        prefix = ['adb'] + (['-s', self.serial] if self.serial else [])
        return subprocess.run(prefix + list(args), capture_output=True, timeout=timeout)


def capture_slots(directory, transport):
    """Attempt every bounded slot exactly once; save partial bytes if returned."""
    results = []
    for i in range(evidence.SLOTS):
        item = {'slot': i, 'offset': evidence.slot_offset(i), 'bytes': 0,
                'complete': False, 'file': None}
        try:
            result = transport.adb(['exec-out', 'dd', 'if=' + PARTITION, 'bs=4096',
                                    'skip=' + str(4096 + i * evidence.SLOT_BLOCKS),
                                    'count=1025', 'status=none'], timeout=60)
            data = result.stdout
            item.update(exit=result.returncode, bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
            evidence.write_private_new(Path(directory) / ('slot%d.bin' % i), data)
            item['file'] = 'slot%d.bin' % i
            item['complete'] = result.returncode == 0 and len(data) == evidence.SLOT_BYTES
        except subprocess.TimeoutExpired as error:
            item['error'] = type(error).__name__ + ': ' + str(error)
            data = error.output or b''
            if data:
                try:
                    evidence.write_private_new(Path(directory) / ('slot%d.bin' % i), data)
                    item.update(file='slot%d.bin' % i, bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
                except Exception as save_error:
                    item['save_error'] = type(save_error).__name__ + ': ' + str(save_error)
        except Exception as error:
            item['error'] = type(error).__name__ + ': ' + str(error)
        results.append(item)
    return results


def capture_preboot(directory, run_id, transport):
    if not evidence.valid_id(run_id):
        raise evidence.EvidenceError('run ID must be 32 lowercase hex and not all zero')
    directory = Path(directory)
    directory.mkdir(mode=0o700)  # exclusive: no previous snapshot is replaced
    status = {'schema': 'SWEETLOG-PREBOOT-CAPTURE-1', 'started_utc': utc(),
              'image_run_id': run_id, 'device_write_occurred': False,
              'boot_identity': 'unverified', 'slots': []}
    exit_code = 6
    try:
        geometry = transport.adb(['shell', 'blockdev', '--getsize64', PARTITION], timeout=15)
        if geometry.returncode or geometry.stdout.strip() != str(PARTITION_BYTES).encode():
            raise evidence.EvidenceError('unixnu_logs geometry is unavailable or not 32 MiB')
        status['partition_bytes'] = PARTITION_BYTES
        status['slots'] = capture_slots(directory, transport)
        if not all(item['complete'] for item in status['slots']):
            raise evidence.EvidenceError('one or more preboot slots were not captured completely')
        digest, inventory = evidence.save_inventory(directory, run_id)
        status.update(result='inventory-only', receipt_sha256=digest,
                      first_empty_slot=inventory['first_empty_slot'],
                      freshness='requires external one-shot preboot ordering')
        exit_code = 0
    except Exception as error:
        status.update(result='incomplete-or-refused', error=type(error).__name__ + ': ' + str(error))
    status['finished_utc'] = utc()
    evidence.write_private_new(directory / 'capture-status.json',
                               (json.dumps(status, indent=2, sort_keys=True) + '\n').encode())
    return exit_code, status


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output-dir', type=Path, required=True)
    p.add_argument('--run-id', required=True)
    p.add_argument('--serial')
    args = p.parse_args()
    try:
        code, status = capture_preboot(args.output_dir, args.run_id, Transport(args.serial))
        print(json.dumps(status, sort_keys=True))
        return code
    except Exception as error:
        print(json.dumps({'result': 'capture-not-started', 'error': str(error),
                          'device_write_occurred': False}, sort_keys=True))
        return 6


if __name__ == '__main__':
    sys.exit(main())
