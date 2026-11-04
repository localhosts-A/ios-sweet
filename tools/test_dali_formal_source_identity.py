#!/usr/bin/env python3
"""Verify the actual post-patch XNU tree matches the tested candidate bytes."""
from pathlib import Path
import hashlib
import sys

ROOT = Path(__file__).resolve().parents[1]
if len(sys.argv) != 2:
    raise SystemExit('usage: test_dali_formal_source_identity.py XNU_SOURCE_DIR')
source_dir = Path(sys.argv[1]).resolve(strict=True)
expected = {
    'bsd/dev/sweet_ufs.c': 'c87a315f99d56348017db3d93fbc2e5466b8b3c7f83438ac8f13c6767fe629bb',
    'bsd/dev/SweetUFSHSG1.h': '10a0d8e00890569dd6997765f0b602b9e33b624007408029cc352934d34ab6a9',
    'bsd/sweet_hfs/core/hfs_vnops.c': '1863c19fc3231151be9f800147d0a5ba72967ad98a8432284e1b13d4c460e4d9',
    'bsd/dev/sweet_touch.c': '90ec1634c1836d16e9d1810055796f14b31a8b320878b069d82027c6f6ce5a71',
    'bsd/dev/SweetLegacyTouchPolicy.h': 'cc79805619d4726775512998bc2e3eafea98fa7da42a8a5012449f63592108fc',
    'iokit/Kernel/arm/hid/SweetHID.cpp': '61a5671cd01f834e42f6374432f394049d59c766ab52bfd76b1666dddc3f5c36',
    'iokit/Kernel/arm/hid/DaliVolumeKey.h': '42da45b453b3f9bebd34f29bf434d6a88786b673ae0c6bf8c98520a4f67a1837',
}


def digest(data):
    return hashlib.sha256(data).hexdigest()


for relative, wanted in expected.items():
    actual = digest((source_dir / relative).read_bytes())
    assert actual == wanted, f'{relative}: expected {wanted}, got {actual}'

# Bind the HID wait header to the formal 0170 patch, not to a 600-second literal
# left in the earlier geometry patch. The current source uses the dynamic limit.
patch = (ROOT / 'xnu/patches/0170-dali-finite-late-gui-startup-budgets.patch').read_text(encoding='latin-1')
header = []
active = False
for line in patch.splitlines(keepends=True):
    if line.startswith('+++ '):
        active = line.strip() == '+++ b/iokit/Kernel/arm/hid/SweetHIDWaitPolicy.h'
    elif line.startswith('--- '):
        active = False
    elif active and line.startswith('+'):
        header.append(line[1:])
assert header, 'SweetHIDWaitPolicy.h addition missing from 0170'
expected_wait = ''.join(header).encode('latin-1')
actual_wait = (source_dir / 'iokit/Kernel/arm/hid/SweetHIDWaitPolicy.h').read_bytes()
assert actual_wait == expected_wait, 'SweetHIDWaitPolicy.h does not match the formal 0170 patch'
hid = (source_dir / 'iokit/Kernel/arm/hid/SweetHID.cpp').read_text(encoding='latin-1')
assert 'PE_parse_boot_argn("sweethidwait"' in hid
assert 'self->fSwipeTicks >= self->fSwipeLimit' in hid
assert b'#define SWEET_HID_WAIT_DEFAULT_SECONDS 600u' in expected_wait
assert b'#define SWEET_HID_WAIT_MAX_SECONDS 1200u' in expected_wait
for relative, wanted in expected.items():
    print(f'PASS {relative} sha256={wanted}')
print(f'PASS iokit/Kernel/arm/hid/SweetHIDWaitPolicy.h sha256={digest(expected_wait)}')
print('Exact source identity only; this is not a kernel build or hardware validation.')
