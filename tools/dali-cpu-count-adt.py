#!/usr/bin/env python3
"""Extend a Dali trial ADT to a requested prefix of its eight real affinities.

This retains generic SMP scheduler topology; it does not claim energy-aware
4+3+1 scheduling. CPU_ON, IPIs, timers and scheduling still require hardware
validation. No boot argument, source image, or non-CPU node is changed.
"""
import argparse
import copy
import importlib.util
from pathlib import Path
import struct

_spec = importlib.util.spec_from_file_location('trusted_adt', Path(__file__).with_name('adt.py'))
adt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(adt)


def integer(value):
    return struct.pack('<I', value)


def property_value(node, key):
    values = [(flags, value) for name, flags, value in node.props if name == key]
    if len(values) != 1 or values[0][0] != 0:
        raise ValueError('missing, duplicate or flagged CPU property: ' + key)
    return values[0][1]


def replace(node, key, value):
    property_value(node, key)
    node.props = [(name, flags, value if name == key else old) for name, flags, old in node.props]


def walk(node):
    yield node
    for child in node.children:
        yield from walk(child)


def transform(raw, count):
    if type(count) is not int or not 1 <= count <= 8:
        raise ValueError('CPU count must be in 1..8')
    root, end = adt.parse(raw)
    if end != len(raw) or adt.serialize(root) != raw:
        raise ValueError('expected canonical complete ADT')
    compatible = property_value(root, 'compatible')
    if not compatible.endswith(b'\0') or b'xiaomi,dali' not in compatible.split(b'\0'):
        raise ValueError('not a Dali ADT')
    parents = [node for node in root.children if node.name == 'cpus']
    if len(parents) != 1:
        raise ValueError('expected one cpus node')
    parent = parents[0]
    existing = len(parent.children)
    if not 1 <= existing < count:
        raise ValueError('requested count must extend an existing nonempty CPU prefix')
    for index, node in enumerate(parent.children):
        expected = {'name': f'cpu{index}'.encode() + b'\0', 'device_type': b'cpu\0',
                    'compatible': b'arm,armv8\0', 'reg': integer(index << 8),
                    'cpu-id': integer(index), 'cluster-id': integer(0),
                    'cluster-type': integer(0), 'die-cluster-id': integer(0),
                    'cluster-core-id': integer(index), 'l2-cache-id': integer(index),
                    'l3-cache-id': integer(0), 'dali-boot-cpu': integer(1),
                    'state': b'running\0' if index == 0 else b'stopped\0'}
        if node.children or any(property_value(node, key) != value for key, value in expected.items()):
            raise ValueError('CPU prefix does not match known Dali SMP encoding')
    handles = []
    for node in walk(root):
        values = [(flags, value) for key, flags, value in node.props if key == 'AAPL,phandle']
        if values:
            if len(values) != 1 or values[0][0] or len(values[0][1]) != 4:
                raise ValueError('invalid phandle')
            handles.append(struct.unpack('<I', values[0][1])[0])
    next_handle = max(handles, default=0) + 1
    if len(handles) != len(set(handles)) or next_handle + count - existing > 0xffffffff:
        raise ValueError('duplicate or exhausted phandles')
    for index in range(existing, count):
        node = copy.deepcopy(parent.children[0])
        for key, value in {'name': f'cpu{index}'.encode() + b'\0',
                           'AAPL,phandle': integer(next_handle),
                           'reg': integer(index << 8), 'cpu-id': integer(index),
                           'cluster-core-id': integer(index), 'l2-cache-id': integer(index),
                           'state': b'stopped\0'}.items():
            replace(node, key, value)
        parent.children.append(node)
        next_handle += 1
    result = adt.serialize(root)
    del parent.children[existing:]
    if adt.serialize(root) != raw:
        raise AssertionError('existing CPU or non-CPU bytes changed')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--count', type=int, required=True)
    args = parser.parse_args()
    result = transform(args.input.read_bytes(), args.count)
    with args.output.open('xb') as output:
        output.write(result)
    print(f'wrote {len(result)} bytes, CPU prefix count={args.count}; physical operation unverified')


if __name__ == '__main__':
    main()
