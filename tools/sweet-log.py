#!/usr/bin/env python3
"""Inspect/decode a raw SWEET slot; header integrity never certifies payload."""
import argparse
import json
from pathlib import Path
import re
import struct
import sys
import zlib

BLOCK = 4096
RING = 4 * 1024 * 1024
AREA = BLOCK + RING


def inspect(data, expected=None):
    result = {'classification': 'invalid-length', 'bytes': len(data),
              'boot_identity': 'unverified', 'payload_integrity': 'unverified',
              'run_id': None, 'match': None, 'match_scope': 'image-id-only'}
    if len(data) != AREA:
        return result
    if not any(data):
        result['classification'] = 'empty'
        return result
    if data[:8] != b'SWEETLOG':
        result['classification'] = 'partial-or-unknown'
        return result
    magic, version, size, total, seq = struct.unpack_from('<8sIIQQ', data)
    result.update(version=version, size=size, total=total, seq=seq)
    if version != 1 or size != RING or seq == 0 or total == 0:
        result['classification'] = 'invalid-header'
        return result
    marker = data[32:40]
    if marker == b'\0'*8:
        # Old headers have no checksum or identity and cannot establish boot provenance.
        if any(data[40:92]):
            result['classification'] = 'partial-or-unknown-extension'
        else:
            result['classification'] = 'legacy-unattributed'
        return result
    if marker not in (b'SWIDv001', b'SWIDv002'):
        result['classification'] = 'unknown-or-torn-extension'
        return result
    raw_id = data[40:72]
    if not re.fullmatch(b'[0-9a-f]{32}', raw_id):
        result['classification'] = 'invalid-identity'
        return result
    checksum_offset = 88 if marker == b'SWIDv002' else 72
    checksum, = struct.unpack_from('<I', data, checksum_offset)
    if zlib.crc32(data[:checksum_offset]) != checksum:
        result['classification'] = 'torn-or-invalid-header'
        return result
    run_id = raw_id.decode('ascii')
    result.update(classification='identified-header', run_id=run_id,
                  match=(run_id == expected) if expected else None,
                  header_crc='valid-not-payload')
    if marker == b'SWIDv002':
        chunks, count = struct.unpack_from('<QQ', data, 72)
        result.update(dropped_chunks=chunks, dropped_bytes=count,
                      drop_count_scope='ring-producer-only-independent-samples-modulo-2^64')
    return result


def decode(data, info):
    size = info['size']
    total = info['total']
    ring = data[BLOCK:]
    if total <= size:
        text = ring[:total]
    else:
        start = total % size
        text = ring[start:] + ring[:start]
    return text.decode('utf-8', 'replace').replace('\r', '')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--inspect', action='store_true')
    parser.add_argument('--expect-run-id')
    parser.add_argument('area', type=Path)
    parser.add_argument('output', nargs='?', type=Path)
    args = parser.parse_args()
    if args.expect_run_id is not None and not re.fullmatch('[0-9a-f]{32}', args.expect_run_id):
        parser.error('expected run-id must be exactly 32 lowercase hex characters')
    data = args.area.read_bytes()
    info = inspect(data, args.expect_run_id)
    if args.inspect:
        print(json.dumps(info, sort_keys=True))
        return
    if info['classification'] not in ('identified-header', 'legacy-unattributed'):
        sys.exit('refusing decode: ' + info['classification'])
    if args.expect_run_id is not None and info['match'] is not True:
        sys.exit('refusing decode: run-id missing or different')
    text = decode(data, info)
    print('# ' + json.dumps(info, sort_keys=True) + ' (payload remains unverified)', file=sys.stderr)
    if args.output:
        # Never replace an earlier capture by accident; remove partial text on error.
        try:
            with args.output.open('x') as out:
                try:
                    out.write(text)
                except BaseException:
                    args.output.unlink(missing_ok=True)
                    raise
        except FileExistsError:
            sys.exit('refusing to replace existing decoded output')
    else:
        sys.stdout.write(text)


if __name__ == '__main__':
    main()
