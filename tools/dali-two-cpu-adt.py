#!/usr/bin/env python3
"""Clone the boot CPU node in an existing raw Dali ADT, adding CPU1 only.

Invoke with python3 -I. Never changes the source, boot args, or non-CPU nodes.
This prepares a trial ADT; it does not establish that CPU1 can be started.
"""
import argparse
import copy
import importlib.util
from pathlib import Path
import struct

spec = importlib.util.spec_from_file_location('isolated_adt', Path(__file__).with_name('adt.py'))
adt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adt)


def u32(n):
    return struct.pack('<I', n)


def one_prop(node, key):
    matches = [(f, v) for k, f, v in node.props if k == key]
    if len(matches) != 1 or matches[0][0] != 0:
        raise ValueError('missing, duplicate or flagged property: ' + key)
    return matches[0][1]


def put(node, key, value):
    one_prop(node, key)
    node.props = [(k, f, value if k == key else v) for k, f, v in node.props]


def walk(node):
    yield node
    for child in node.children:
        yield from walk(child)


def transform(raw):
    root, end = adt.parse(raw)
    if end != len(raw) or adt.serialize(root) != raw:
        raise ValueError('input ADT has trailing data or non-roundtrippable layout')
    if b'xiaomi,dali\0' not in one_prop(root, 'compatible'):
        raise ValueError('not a Dali ADT')
    cpus = [n for n in root.children if n.name == 'cpus']
    if len(cpus) != 1 or len(cpus[0].children) != 1:
        raise ValueError('expected exactly one cpus node with one boot CPU')
    parent = cpus[0]
    boot = parent.children[0]
    expected = {'name': b'cpu0\0', 'device_type': b'cpu\0',
                'compatible': b'arm,armv8\0', 'reg': u32(0),
                'cpu-id': u32(0), 'cluster-id': u32(0),
                'cluster-type': u32(0), 'die-cluster-id': u32(0),
                'cluster-core-id': u32(0), 'l2-cache-id': u32(0),
                'state': b'running\0', 'dali-boot-cpu': u32(1)}
    if boot.children or any(one_prop(boot, k) != v for k, v in expected.items()):
        raise ValueError('unexpected boot CPU layout')
    handles = []
    for node in walk(root):
        props = [(f, v) for k, f, v in node.props if k == 'AAPL,phandle']
        if props:
            if len(props) != 1 or props[0][0] or len(props[0][1]) != 4:
                raise ValueError('invalid phandle')
            handles.append(struct.unpack('<I', props[0][1])[0])
    if len(handles) != len(set(handles)) or max(handles, default=0) >= 0xfffffffe:
        raise ValueError('duplicate or exhausted phandles')
    secondary = copy.deepcopy(boot)
    for k, v in {'name': b'cpu1\0', 'AAPL,phandle': u32(max(handles) + 1),
                 'reg': u32(0x100), 'cpu-id': u32(1),
                 'cluster-core-id': u32(1), 'l2-cache-id': u32(1),
                 'state': b'stopped\0'}.items():
        put(secondary, k, v)
    parent.children.append(secondary)
    result = adt.serialize(root)
    # Removing only the new CPU must reproduce the complete original byte stream.
    parent.children.pop()
    if adt.serialize(root) != raw:
        raise AssertionError('non-CPU content changed')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    result = transform(args.input.read_bytes())
    with args.output.open('xb') as out:
        out.write(result)
    print(f'wrote {len(result)} bytes to {args.output}; CPU1 reg=0x100')


if __name__ == '__main__':
    main()
