#!/usr/bin/env python3
"""Add the Dali UFS PWM two-lane opt-in property to a raw canonical ADT.

Run with python3 -I. Input is never overwritten; no bootargs or other node is
changed. This prepares a controlled performance trial, not a proven speedup.
"""
import argparse
import importlib.util
from pathlib import Path
import struct

spec = importlib.util.spec_from_file_location('isolated_adt', Path(__file__).with_name('adt.py'))
adt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adt)
PROPERTY = 'dali-pwm-two-lanes'


def one(node, name):
    props = [(flags, value) for key, flags, value in node.props if key == name]
    if len(props) != 1 or props[0][0]:
        raise ValueError('missing, duplicate or flagged property: ' + name)
    return props[0][1]


def transform(raw):
    root, end = adt.parse(raw)
    if end != len(raw) or adt.serialize(root) != raw:
        raise ValueError('input is not a canonical complete ADT')
    compat = one(root, 'compatible')
    if not compat.endswith(b'\0') or b'xiaomi,dali' not in compat.split(b'\0'):
        raise ValueError('not a Dali ADT')
    matches = [child for child in root.children if child.name == 'sweet-ufs']
    if len(matches) != 1:
        raise ValueError('expected exactly one root sweet-ufs node')
    ufs = matches[0]
    if one(ufs, 'name') != b'sweet-ufs\0':
        raise ValueError('invalid sweet-ufs name property')
    compat = one(ufs, 'compatible')
    if not compat.startswith(b'mediatek,mt6991-ufshc\0'):
        raise ValueError('not a Dali UFS controller')
    if any(key == PROPERTY for key, _, _ in ufs.props):
        raise ValueError('UFS two-lane property already exists')
    ufs.props.append((PROPERTY, 0, struct.pack('<I', 1)))
    result = adt.serialize(root)
    ufs.props.pop()
    if adt.serialize(root) != raw:
        raise AssertionError('unrelated ADT content changed')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    result = transform(args.input.read_bytes())
    with args.output.open('xb') as out:
        out.write(result)
    print(f'wrote {len(result)} bytes to {args.output}; Dali PWM two-lane opt-in enabled')


if __name__ == '__main__':
    main()
