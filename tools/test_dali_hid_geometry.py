#!/usr/bin/env python3
"""Test the real HID geometry policy without synthesizing device input."""

import ctypes
from pathlib import Path
import random
import shutil
import subprocess
import sys
import tempfile
import unittest

PATCH = Path(__file__).resolve().parents[1] / 'xnu/patches/0163-sweet-opt-in-dali-hid-swipe.patch'


class DaliHidGeometryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        root = Path(cls.temp.name)
        active, lines = False, []
        for line in PATCH.read_text(encoding='latin-1').splitlines(keepends=True):
            if line.startswith('+++ '):
                active = line.strip() == '+++ b/iokit/Kernel/arm/hid/SweetHIDGeometry.h'
            elif line.startswith('--- '):
                active = False
            elif active and line.startswith('+'):
                lines.append(line[1:])
        if not lines:
            raise AssertionError('geometry policy header missing')
        (root / 'SweetHIDGeometry.h').write_text(''.join(lines))
        (root / 'wrap.c').write_text('''#include "SweetHIDGeometry.h"
bool run(int32_t x,int32_t y,int32_t pw,int32_t ph,int32_t lw,int32_t lh,
 bool scaled,int32_t *nx,int32_t *ny) { return sweet_hid_normalize(x,y,pw,ph,lw,lh,scaled,nx,ny); }
''')
        lib = root / ('hid.dylib' if sys.platform == 'darwin' else 'hid.so')
        flags = ['-dynamiclib'] if sys.platform == 'darwin' else ['-shared', '-fPIC']
        compiler = shutil.which('clang') or shutil.which('cc')
        if not compiler:
            raise RuntimeError('host C compiler required')
        subprocess.run([compiler, *flags, '-std=c11', '-Wall', '-Wextra', '-Werror',
                        str(root / 'wrap.c'), '-o', str(lib)], check=True)
        cls.library = ctypes.CDLL(str(lib))
        cls.fn = cls.library.run
        cls.fn.argtypes = [ctypes.c_int32] * 6 + [ctypes.c_bool, ctypes.POINTER(ctypes.c_int32), ctypes.POINTER(ctypes.c_int32)]
        cls.fn.restype = ctypes.c_bool

    def normalize(self, x, y, pw, ph, lw, lh, scaled):
        nx, ny = ctypes.c_int32(), ctypes.c_int32()
        self.assertTrue(self.fn(x, y, pw, ph, lw, lh, scaled, ctypes.byref(nx), ctypes.byref(ny)))
        return nx.value, ny.value

    def test_seeded_mapping_keeps_sweet_crop_and_dali_scale(self):
        rng = random.Random(6991)
        for _ in range(1600):
            pw, ph = rng.randint(1, 4096), rng.randint(1, 4096)
            scaled = bool(rng.getrandbits(1))
            lw, lh = (rng.randint(1, 4096), rng.randint(1, 4096)) if scaled else (pw + rng.randint(0, 255), ph + rng.randint(0, 255))
            x, y = rng.randint(-1024, pw + 1024), rng.randint(-1024, ph + 1024)
            cx, cy = min(max(x, 0), pw - 1), min(max(y, 0), ph - 1)
            expected = ((cx * 65536 // pw, cy * 65536 // ph) if scaled else
                        ((cx + (lw - pw) // 2) * 65536 // lw, (cy + (lh - ph) // 2) * 65536 // lh))
            self.assertEqual(self.normalize(x, y, pw, ph, lw, lh, scaled), expected)

    def test_known_panel_and_logical_centers(self):
        self.assertEqual(self.normalize(640, 1386, 1280, 2772, 1125, 2436, True), (32768, 32768))
        self.assertEqual(self.normalize(540, 1200, 1080, 2400, 1080, 2400, False), (32768, 32768))
        self.assertEqual(self.normalize(-1, -1, 1280, 2772, 1125, 2436, True), (0, 0))

    def test_bad_dimensions_and_null_outputs_rejected(self):
        nx, ny = ctypes.c_int32(), ctypes.c_int32()
        for dims in [(0, 1, 1, 1), (1, 0, 1, 1), (16385, 1, 1, 1), (2, 2, 1, 1)]:
            self.assertFalse(self.fn(0, 0, *dims, False, ctypes.byref(nx), ctypes.byref(ny)))
        self.assertFalse(self.fn(0, 0, 1, 1, 1, 1, True, None, ctypes.byref(ny)))

    def test_swipe_is_gated_bounded_client_ready_and_one_shot(self):
        text = PATCH.read_text(encoding='latin-1')
        for marker in ['"daligui"', '"sweethidswipe"', 'fSwipeDelay <= 300',
                       'self->fSwipeTicks >= 600', 'self->fSwipeStep >= 24',
                       'self->fSwipeStep = 24', 'IOLockLock(self->_clientLock)',
                       'self->fDown[0]', 'self->fSwipeTicks < self->fSwipeReady',
                       'ready = ready && sweet_dali_gui_presented()',
                       'physical input remains unverified']:
            self.assertIn(marker, text)
        self.assertNotIn('kHIDUsage_Csmr_Power, true', text)


if __name__ == '__main__':
    unittest.main()
