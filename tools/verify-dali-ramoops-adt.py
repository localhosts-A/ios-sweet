#!/usr/bin/env python3
"""Verify the Dali ramoops ranges emitted into /chosen by mkadt.py."""

import struct

import adt
import mkadt


EXPECTED = {
    "dali-ramoops-base": 0xAEC00000,
    "dali-ramoops-size": 0x00410000,
    "dali-ramoops-console-offset": 0x0000F000,
    "dali-ramoops-console-size": 0x00200000,
}


def properties(dali):
    blob = adt.serialize(mkadt.build(seed=bytes(64), dali=dali))
    root, end = adt.parse(blob)
    assert end == len(blob), "ADT parser did not consume the generated tree"
    chosen = next(child for child in root.children if child.name == "chosen")
    return {name: value for name, _, value in chosen.props}


dali_props = properties(True)
for name, expected in EXPECTED.items():
    value = dali_props.get(name)
    assert value is not None and len(value) == 8, (name, value)
    actual, = struct.unpack("<Q", value)
    assert actual == expected, (name, hex(actual), hex(expected))

sweet_props = properties(False)
assert all(name not in sweet_props for name in EXPECTED), "Sweet ADT contains Dali ramoops data"

assert EXPECTED["dali-ramoops-base"] + EXPECTED["dali-ramoops-console-offset"] == 0xAEC0F000
print("Dali ramoops ADT: PASS (base=0xaec00000 console=0xaec0f000 size=0x200000; Sweet omitted)")
