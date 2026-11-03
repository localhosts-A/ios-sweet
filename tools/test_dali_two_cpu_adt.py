#!/usr/bin/env python3
import importlib.util
from pathlib import Path
import struct
import sys

here = Path(__file__).resolve().parent
sys.path.insert(0, str(here))
import mkadt

spec = importlib.util.spec_from_file_location('transformer', here / 'dali-two-cpu-adt.py')
t = importlib.util.module_from_spec(spec)
spec.loader.exec_module(t)
# Avoid importing optional proprietary product data even if present locally.
mkadt.d22ap_product = lambda: None
raw = t.adt.serialize(mkadt.build(seed=bytes(64), dali=True))
out = t.transform(raw)
root, end = t.adt.parse(out)
assert end == len(out)
cpus = next(c for c in root.children if c.name == 'cpus')
assert len(cpus.children) == 2
boot, second = cpus.children
assert boot.name == 'cpu0' and second.name == 'cpu1'
assert t.one_prop(second, 'reg') == struct.pack('<I', 0x100)
assert t.one_prop(second, 'state') == b'stopped\0'
assert t.one_prop(second, 'dali-boot-cpu') == struct.pack('<I', 1)
assert t.one_prop(second, 'cpu-id') == struct.pack('<I', 1)
assert t.one_prop(second, 'cluster-core-id') == struct.pack('<I', 1)
assert t.one_prop(second, 'l2-cache-id') == struct.pack('<I', 1)
assert t.one_prop(second, 'AAPL,phandle') != t.one_prop(boot, 'AAPL,phandle')
cpus.children.pop()
assert t.adt.serialize(root) == raw
for bad in (out, raw + b'\0'):
    try:
        t.transform(bad)
    except (ValueError, AssertionError, struct.error):
        pass
    else:
        raise AssertionError('invalid input accepted')
print('actual transformer: two CPU properties, full original byte preservation, duplicate transformation/trailing-data rejection PASS')
