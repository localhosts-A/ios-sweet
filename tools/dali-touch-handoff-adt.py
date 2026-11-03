#!/usr/bin/env python3
"""Prepare a Dali ADT with a one-shot touch handoff observation enabled.

Run with python3 -I. The input is never overwritten. The output differs only
by one root property; this does not establish touch or SPI ownership.
"""
import argparse
import importlib.util
from pathlib import Path
import struct

spec = importlib.util.spec_from_file_location('isolated_adt', Path(__file__).with_name('adt.py'))
adt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adt)
PROPERTY = 'dali-touch-handoff'


def transform(raw):
    root, end = adt.parse(raw)
    if end != len(raw) or adt.serialize(root) != raw:
        raise ValueError('input ADT is not a canonical complete tree')
    compat = [(f, v) for k, f, v in root.props if k == 'compatible']
    if len(compat) != 1 or compat[0][0] or not compat[0][1].endswith(b'\0') or b'xiaomi,dali' not in compat[0][1].split(b'\0'):
        raise ValueError('not a Dali root')
    if any(k == PROPERTY for k, _, _ in root.props):
        raise ValueError('touch handoff property already exists')
    root.props.append((PROPERTY, 0, struct.pack('<I', 1)))
    result = adt.serialize(root)
    root.props.pop()
    if adt.serialize(root) != raw:
        raise AssertionError('unrelated bytes changed')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    result = transform(args.input.read_bytes())
    with args.output.open('xb') as out:
        out.write(result)
    print(f'wrote {len(result)} bytes to {args.output}; touch handoff observation enabled')


if __name__ == '__main__':
    main()
