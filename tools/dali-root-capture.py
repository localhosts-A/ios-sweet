#!/usr/bin/env python3
"""Capture the unmounted Dali root prefix before rollback; never writes the phone.

  dali-root-capture.py --serial SERIAL --output PRIVATE_TRIAL/root.raw [--capture]

Without --capture, no ADB command runs and no file is created. A real capture
requires root Android ADB, the expected device/slot/partition and an unmounted
root. All 16 GiB are saved, with before/local/after hashes and fresh identity
checks. This complements device_pair preflight; it does not replace stock boot
verification, authorize a trial, repair HFS or restore any partition.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess


ROOT_DEVICE = '/dev/block/by-name/unixnu_data'
PREFIX_BYTES = 16 * 1024**3
PARTITION_BYTES = 34_326_183_936
TRANSFER_BLOCK = 1024**2


def adb(serial, *args):
    result = subprocess.run(['adb', '-s', serial, *args], capture_output=True, timeout=600)
    if result.returncode:
        raise RuntimeError(result.stderr.decode(errors='replace').strip() or 'ADB command failed')
    return result.stdout.decode().strip()


def probe(serial, slot):
    expected = {
        'ro.product.device': 'dali',
        'ro.product.model': '25060RK16C',
        'ro.boot.slot_suffix': '_' + slot,
        'sys.boot_completed': '1',
    }
    observed = {key: adb(serial, 'shell', 'getprop', key) for key in expected}
    if observed != expected:
        raise RuntimeError('Android identity/slot/completion does not match the selected Dali target')
    if adb(serial, 'shell', 'id', '-u') != '0':
        raise RuntimeError('root ADB is required; this tool does not elevate privileges')
    size = adb(serial, 'shell', 'blockdev', '--getsize64', ROOT_DEVICE)
    if size != str(PARTITION_BYTES):
        raise RuntimeError('unexpected unixnu_data partition size')
    path = adb(serial, 'shell', 'readlink', '-f', ROOT_DEVICE)
    if not re.fullmatch(r'/dev/block/[A-Za-z0-9_-]+', path):
        raise RuntimeError('unexpected root block-device path')
    device = adb(serial, 'shell', 'cat', '/sys/class/block/' + path.rsplit('/', 1)[1] + '/dev')
    if not re.fullmatch(r'[0-9]+:[0-9]+', device):
        raise RuntimeError('invalid block-device identity')
    mounts = adb(serial, 'shell', 'cat', '/proc/self/mountinfo')
    for line in mounts.splitlines():
        fields = line.split()
        if len(fields) < 7 or '-' not in fields:
            raise RuntimeError('malformed mount table; cannot establish unmounted root')
        if fields[2] == device:
            raise RuntimeError('unixnu_data is mounted; refusing a non-snapshot capture')
    if not mounts:
        raise RuntimeError('empty mount table; cannot establish unmounted root')
    return dict(properties=observed, root_device=path, major_minor=device, partition_bytes=int(size))


def remote_hash(serial):
    # One shell argument preserves the pipeline and its pipefail semantics.
    command = (f'set -o pipefail; dd if={ROOT_DEVICE} bs={TRANSFER_BLOCK} '
               f'count={PREFIX_BYTES // TRANSFER_BLOCK} status=none | sha256sum')
    value = adb(serial, 'shell', command)
    if not re.fullmatch(r'[0-9a-f]{64}\s+-', value):
        raise RuntimeError('invalid remote prefix hash')
    return value.split()[0]


def stream_prefix(serial, stream):
    result = subprocess.run(['adb', '-s', serial, 'exec-out', 'dd',
                             'if=' + ROOT_DEVICE, f'bs={TRANSFER_BLOCK}',
                             f'count={PREFIX_BYTES // TRANSFER_BLOCK}', 'status=none'],
                            stdout=stream, stderr=subprocess.PIPE, timeout=1800)
    if result.returncode:
        raise RuntimeError(result.stderr.decode(errors='replace').strip() or 'root read failed')


def capture(serial, output, slot):
    output = Path(output)
    partial = output.with_name(output.name + '.part')
    metadata = output.with_name(output.name + '.json')
    if any(path.exists() or path.is_symlink() for path in (output, partial, metadata)):
        raise RuntimeError('capture paths already exist; refusing to overwrite evidence')
    if not output.parent.is_dir():
        raise RuntimeError('create a new private trial directory before capture')
    if shutil.disk_usage(output.parent).free < PREFIX_BYTES + 64 * 1024**2:
        raise RuntimeError('insufficient space for a complete root-prefix capture')
    before = probe(serial, slot)
    expected = remote_hash(serial)
    with partial.open('xb') as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream_prefix(serial, stream)
        stream.flush()
        os.fsync(stream.fileno())
    if partial.stat().st_size != PREFIX_BYTES:
        raise RuntimeError('short/oversized root capture; unverified .part is retained')
    digest = hashlib.sha256()
    with partial.open('rb') as stream:
        for block in iter(lambda: stream.read(8 * 1024**2), b''):
            digest.update(block)
    digest = digest.hexdigest()
    after = probe(serial, slot)
    observed = remote_hash(serial)
    if before != after or digest != expected or digest != observed:
        raise RuntimeError('root or identity changed during capture; unverified .part is retained')
    record = dict(status='verified', serial=serial, prefix_bytes=PREFIX_BYTES,
                  sha256=digest, remote_before_sha256=expected, remote_after_sha256=observed,
                  device=after, phone_write_occurred=False)
    # Hard-link publication is exclusive: a concurrent output cannot be replaced.
    os.link(partial, output)
    with metadata.open('x') as stream:
        os.fchmod(stream.fileno(), 0o600)
        json.dump(record, stream, indent=2)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    directory = os.open(output.parent, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
    try:
        os.fsync(directory)
        partial.unlink()
        os.fsync(directory)
    finally:
        os.close(directory)
    return record


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--serial', required=True)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--slot', choices=('a', 'b'), default='b')
    parser.add_argument('--capture', action='store_true')
    args = parser.parse_args(argv)
    if not args.capture:
        print(f'DRY RUN: no device access or files created; root prefix is {PREFIX_BYTES} bytes.')
        return 0
    try:
        record = capture(args.serial, args.output, args.slot)
    except (OSError, RuntimeError, UnicodeError, subprocess.SubprocessError) as error:
        parser.exit(1, f'capture failed: {error}\n')
    print(f'Verified {record["prefix_bytes"]} bytes: {record["sha256"]}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
