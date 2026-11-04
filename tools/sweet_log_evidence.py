#!/usr/bin/env python3
"""Offline three-slot inventory and pre/post comparison; never accesses a device.

Exit 5 is a new header matching an image ID and an externally pinned preboot
inventory; it is candidate-only, NOT proof of boot identity or payload integrity.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import sys

BLOCK = 4096
SLOT_BLOCKS = 1025
SLOT_BYTES = BLOCK * SLOT_BLOCKS
SLOTS = 3
SCHEMA = 'SWEETLOG-PREBOOT-INVENTORY-1'
HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('sweet_log_decoder', HERE / 'sweet-log.py')
decoder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(decoder)


class EvidenceError(Exception):
    pass


def valid_id(value):
    return bool(re.fullmatch(r'[0-9a-f]{32}', value or '')) and value != '0' * 32


def read_regular(path, maximum, exact=None):
    path = Path(path)
    # Check file type after a nonblocking open, so a FIFO cannot hang inspection.
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_size > maximum or (exact is not None and before.st_size != exact):
            raise EvidenceError('invalid local file type or length: ' + path.name)
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            data = stream.read(maximum + 1)
        after = os.fstat(fd)
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise EvidenceError('local file changed while reading: ' + path.name)
        if len(data) != before.st_size:
            raise EvidenceError('short local read: ' + path.name)
        return data
    finally:
        os.close(fd)


def write_private_new(path, data):
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW
    fd = os.open(Path(path), flags, 0o600)
    try:
        with os.fdopen(fd, 'wb', closefd=False) as stream:
            stream.write(data)
            stream.flush()
            os.fsync(fd)
    finally:
        os.close(fd)


def slot_offset(i):
    return (4096 + i * SLOT_BLOCKS) * BLOCK


def describe(data, i, run_id):
    if len(data) != SLOT_BYTES:
        raise EvidenceError('slot %d length is not %d' % (i, SLOT_BYTES))
    info = decoder.inspect(data, run_id)
    return {'slot': i, 'offset': slot_offset(i), 'bytes': len(data),
            'sha256': hashlib.sha256(data).hexdigest(),
            'classification': info['classification'], 'run_id': info['run_id'],
            'match': info['match'], 'all_zero': info['classification'] == 'empty'}


def inspect_directory(directory, run_id):
    return [describe(read_regular(Path(directory) / ('slot%d.bin' % i), SLOT_BYTES, SLOT_BYTES), i, run_id)
            for i in range(SLOTS)]


def check_preconditions(slots, run_id):
    if not valid_id(run_id):
        raise EvidenceError('run ID must be 32 lowercase hex and not all zero')
    recognized = {'identified-header', 'legacy-unattributed'}
    if slots[0]['classification'] not in recognized:
        raise EvidenceError('slot 0 does not have a recognized retained header')
    for slot in slots:
        if slot['classification'] not in recognized | {'empty'}:
            raise EvidenceError('unknown or partial preboot slot %d' % slot['slot'])
        if slot['match'] is True:
            raise EvidenceError('image run ID already exists in preboot slot %d' % slot['slot'])
    free = [s['slot'] for s in slots if s['all_zero']]
    if not free:
        raise EvidenceError('no preboot empty slot')
    return free[0]


def make_inventory(directory, run_id):
    if not valid_id(run_id):
        raise EvidenceError('run ID must be 32 lowercase hex and not all zero')
    slots = inspect_directory(directory, run_id)
    candidate = check_preconditions(slots, run_id)
    return {'schema': SCHEMA, 'created_utc': datetime.now(timezone.utc).isoformat(),
            'image_run_id': run_id, 'slots': slots, 'first_empty_slot': candidate,
            'device_access': 'none-by-inventory',
            'attestation': 'hash binding only; freshness and boot identity require external one-shot evidence'}


def save_inventory(directory, run_id):
    receipt = make_inventory(directory, run_id)
    data = (json.dumps(receipt, indent=2, sort_keys=True) + '\n').encode('utf-8')
    write_private_new(Path(directory) / 'receipt.json', data)
    return hashlib.sha256(data).hexdigest(), receipt


def compare(pre_dir, receipt_sha256, post_dir, run_id):
    if not valid_id(run_id):
        raise EvidenceError('run ID must be 32 lowercase hex and not all zero')
    if not re.fullmatch(r'[0-9a-f]{64}', receipt_sha256 or ''):
        raise EvidenceError('preboot receipt SHA-256 must be 64 lowercase hex')
    receipt_bytes = read_regular(Path(pre_dir) / 'receipt.json', 65536)
    if hashlib.sha256(receipt_bytes).hexdigest() != receipt_sha256:
        raise EvidenceError('preboot receipt hash does not match the externally pinned hash')
    receipt = json.loads(receipt_bytes)
    if receipt.get('schema') != SCHEMA or receipt.get('image_run_id') != run_id:
        raise EvidenceError('preboot receipt schema or image run ID mismatch')
    pre = inspect_directory(pre_dir, run_id)
    first_empty = check_preconditions(pre, run_id)
    if pre != receipt.get('slots') or first_empty != receipt.get('first_empty_slot'):
        raise EvidenceError('preboot raw files differ from the pinned receipt')
    post = inspect_directory(post_dir, run_id)
    matches = [s['slot'] for s in post if s['match'] is True]
    result = {'schema': 'SWEETLOG-COMPARISON-1', 'image_run_id': run_id,
              'preboot_receipt_sha256': receipt_sha256, 'pre': pre, 'post': post,
              'image_id_matches': matches, 'boot_identity': 'unverified',
              'payload_integrity': 'unverified',
              'freshness': 'requires external one-shot preboot ordering',
              'result': 'not-evaluated'}
    if len(matches) > 1:
        result['result'] = 'ambiguous-image-id'
        return 3, result
    changed = [i for i in range(SLOTS) if pre[i]['sha256'] != post[i]['sha256']]
    if any(i != first_empty or not pre[i]['all_zero'] for i in changed):
        result.update(result='prepost-contradiction', changed_slots=changed)
        return 6, result
    if not matches:
        result.update(result='no-header-match', changed_slots=changed)
        return 4, result
    selected = matches[0]
    if selected != first_empty or changed != [selected] or not pre[selected]['all_zero']:
        result.update(result='prepost-contradiction', changed_slots=changed)
        return 6, result
    result.update(result='candidate-only', candidate_slot=selected, changed_slots=changed)
    return 5, result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='cmd', required=True)
    inv = sub.add_parser('inventory')
    inv.add_argument('--directory', type=Path, required=True)
    inv.add_argument('--run-id', required=True)
    cmp = sub.add_parser('compare')
    cmp.add_argument('--preboot-dir', type=Path, required=True)
    cmp.add_argument('--receipt-sha256', required=True)
    cmp.add_argument('--post-dir', type=Path, required=True)
    cmp.add_argument('--run-id', required=True)
    args = p.parse_args()
    try:
        if args.cmd == 'inventory':
            digest, receipt = save_inventory(args.directory, args.run_id)
            print(json.dumps({'result': 'inventory-only', 'receipt_sha256': digest,
                              'first_empty_slot': receipt['first_empty_slot'],
                              'boot_identity': 'unverified'}, sort_keys=True))
            return 0
        code, result = compare(args.preboot_dir, args.receipt_sha256, args.post_dir, args.run_id)
        print(json.dumps(result, sort_keys=True))
        return code
    except (EvidenceError, OSError, ValueError, json.JSONDecodeError) as error:
        print(json.dumps({'result': 'invalid-or-incomplete-evidence', 'error': str(error),
                          'boot_identity': 'unverified', 'payload_integrity': 'unverified'}, sort_keys=True))
        return 6


if __name__ == '__main__':
    sys.exit(main())
