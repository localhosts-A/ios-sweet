#!/usr/bin/env python3
"""Append the symbols ld64 reported as undefined to the PanVK link response file.

The X9 Mesa patch marks Vulkan entrypoints weak_import on Apple and links with
-Wl,@<rsp>; upstream ships no rsp. With one PAN_ARCH built, the dispatch tables
still reference the other archs' entrypoints, so the first link fails with
"Undefined symbols". This turns that list into "-U _sym" lines (allowed
undefined, NULL at runtime when weak) and keeps every symbol it ever added.

    python3 -I ld-undefined-to-rsp.py --log panvk-pass1.log --rsp optional-vulkan-entrypoints.rsp

Exit 1 when the log has no undefined-symbol report: that failure is something else.
"""
import argparse
import re
import sys
from pathlib import Path

UNDEFINED = re.compile(r'^\s+"(_[^"]+)", referenced from:', re.M)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--log', type=Path, required=True)
    ap.add_argument('--rsp', type=Path, required=True)
    a = ap.parse_args()
    found = sorted(set(UNDEFINED.findall(a.log.read_text(errors='replace'))))
    if not found:
        sys.exit(f'{a.log}: no "Undefined symbols" report; build failed for another reason')
    lines = a.rsp.read_text().split('\n') if a.rsp.exists() else []
    have = {l[3:] for l in lines if l.startswith('-U ')}
    new = [s for s in found if s not in have]
    if not new:
        sys.exit(f'{a.log}: all {len(found)} undefined symbols are already in {a.rsp}; -U did not help')
    keep = [l for l in lines if l and not l.startswith('-U ')]
    a.rsp.write_text('\n'.join(keep + [f'-U {s}' for s in sorted(have | set(new))]) + '\n')
    print(f'{a.rsp}: +{len(new)} allowed-undefined symbols ({len(have) + len(new)} total)')
    for s in new:
        print('  ' + s)


if __name__ == '__main__':
    main()
