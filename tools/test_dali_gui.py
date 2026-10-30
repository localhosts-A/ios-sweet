#!/usr/bin/env python3
"""Exercise the real GUI blitter header and guard refresh integration structure."""

import ctypes
from pathlib import Path
import random
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / 'xnu/patches/0162-sweet-opt-in-dali-gui-refresh.patch'


class Plane(ctypes.Structure):
    _fields_ = [('pixels', ctypes.POINTER(ctypes.c_uint8)), ('bytes', ctypes.c_uint64),
                ('width', ctypes.c_uint32), ('height', ctypes.c_uint32), ('row_bytes', ctypes.c_uint32)]


class Rect(ctypes.Structure):
    _fields_ = [(name, ctypes.c_int32) for name in ('x', 'y', 'width', 'height')]


def header_text():
    active, lines = False, []
    for line in PATCH.read_text(encoding='latin-1').splitlines(keepends=True):
        if line.startswith('+++ '):
            active = line.strip() == '+++ b/iokit/Kernel/arm/SweetFBBlit.h'
        elif line.startswith('--- '):
            active = False
        elif active and line.startswith('+'):
            lines.append(line[1:])
    if not lines:
        raise AssertionError('actual blitter header missing from patch')
    return ''.join(lines)


def reference_pack(value, fmt):
    r, g, b = (value >> 16) & 255, (value >> 8) & 255, value & 255
    if fmt & (1 << 17):
        r, b = b, r
    value = 0xff000000 | (r << 16) | (g << 8) | b
    if fmt & 15 == 3:
        value = ((value << 8) | 255) & 0xffffffff
    if fmt & (1 << 16):
        value = int.from_bytes(value.to_bytes(4, 'little'), 'big')
    return value


class DaliGuiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        root = Path(cls.temp.name)
        (root / 'SweetFBBlit.h').write_text(header_text())
        (root / 'wrap.c').write_text('''
#include "SweetFBBlit.h"
bool run(struct sweet_fb_plane d,struct sweet_fb_plane s,uint32_t w,uint32_t h,
    struct sweet_fb_rect a,struct sweet_fb_rect b,uint32_t f) {
    return sweet_fb_blit_scaled(d,s,w,h,a,b,f);
}
uint32_t pack(uint32_t p,uint32_t f) { return sweet_fb_pack_pixel(p,f); }
uint32_t unpack(uint32_t p,uint32_t f) { return sweet_fb_unpack_pixel(p,f); }
''')
        lib = root / ('blit.dylib' if sys.platform == 'darwin' else 'blit.so')
        flags = ['-dynamiclib'] if sys.platform == 'darwin' else ['-shared', '-fPIC']
        compiler = shutil.which('clang') or shutil.which('cc')
        if not compiler:
            raise RuntimeError('host C compiler required')
        subprocess.run([compiler, *flags, '-std=c11', '-Wall', '-Wextra', '-Werror',
                        str(root / 'wrap.c'), '-o', str(lib)], check=True)
        cls.lib = ctypes.CDLL(str(lib))
        cls.lib.run.argtypes = [Plane, Plane, ctypes.c_uint32, ctypes.c_uint32, Rect, Rect, ctypes.c_uint32]
        cls.lib.run.restype = ctypes.c_bool
        for name in ('pack', 'unpack'):
            getattr(cls.lib, name).argtypes = [ctypes.c_uint32, ctypes.c_uint32]
            getattr(cls.lib, name).restype = ctypes.c_uint32

    def test_seeded_actual_c_vs_independent_reference(self):
        rng = random.Random(6991)
        for case in range(1200):
            sw, sh, dw, dh, cw, ch = [rng.randint(1, 8) for _ in range(6)]
            sr, dr = sw * 4 + rng.randrange(4) * 4, dw * 4 + rng.randrange(4) * 4
            source = bytearray(rng.randbytes(sr * sh))
            target = bytearray([0xa5] * (dr * dh + 32))
            expected = bytearray(target)
            a = Rect(rng.randint(-3, 3), rng.randint(-3, 3), rng.randint(1, 9), rng.randint(1, 9))
            b = Rect(rng.randint(-4, 8), rng.randint(-4, 8), rng.randint(1, 9), rng.randint(1, 9))
            fmt = rng.choice([2, 3]) | rng.choice([0, 1 << 16, 1 << 17, (1 << 16) | (1 << 17)])
            for y in range(dh):
                ly = y * ch // dh
                for x in range(dw):
                    lx = x * cw // dw
                    if b.x <= lx < b.x + b.width and b.y <= ly < b.y + b.height:
                        sx = a.x + (lx - b.x) * a.width // b.width
                        sy = a.y + (ly - b.y) * a.height // b.height
                        if 0 <= sx < sw and 0 <= sy < sh:
                            value = struct.unpack_from('<I', source, sy * sr + sx * 4)[0]
                            struct.pack_into('<I', expected, y * dr + x * 4, reference_pack(value, fmt))
            sa = (ctypes.c_uint8 * len(source)).from_buffer(source)
            da = (ctypes.c_uint8 * len(target)).from_buffer(target)
            self.lib.run(Plane(da, dr * dh, dw, dh, dr), Plane(sa, len(source), sw, sh, sr), cw, ch, a, b, fmt)
            self.assertEqual(target, expected, f'case {case}; padding/guard included')

    def test_reject_invalid_geometry_length_and_format(self):
        source = (ctypes.c_uint8 * 64)(*range(64))
        for width, height, stride, size, cw, fmt in [
            (0, 4, 16, 64, 4, 2), (16385, 4, 16, 64, 4, 2),
            (4, 4, 15, 64, 4, 2), (4, 4, 16, 63, 4, 2),
            (4, 4, 16, 64, 0, 2), (4, 4, 16, 64, 16385, 2),
            (4, 4, 16, 64, 4, 0), (4, 4, 16, 64, 4, 2 | (1 << 4)),
        ]:
            target = (ctypes.c_uint8 * 96)(*([0xa5] * 96))
            result = self.lib.run(Plane(target, 64, 4, 4, 16), Plane(source, size, width, height, stride),
                                  cw, 4, Rect(0, 0, 4, 4), Rect(0, 0, 4, 4), fmt)
            self.assertFalse(result)
            self.assertEqual(bytes(target), b'\xa5' * 96)

    def test_pixel_roundtrip_all_supported_orders(self):
        for fmt in [code | flags for code in [2, 3] for flags in [0, 1 << 16, 1 << 17, 3 << 16]]:
            for pixel in [0, 0xffffffff, 0x00123456, 0x7fab8967, 0xff0000ff, 0xffff0000]:
                encoded = self.lib.pack(pixel, fmt)
                self.assertEqual(encoded, reference_pack(pixel, fmt))
                self.assertEqual(self.lib.unpack(encoded, fmt), pixel | 0xff000000)

    def test_refresh_and_default_surface_contracts(self):
        text = PATCH.read_text(encoding='latin-1')
        for marker in ['"daligui"', 'sweet_surface_create_buffer(fLogicalW, fLogicalH',
                       'OSCompareAndSwap(0, 1, &early_flushing)', 'sweet_early_gui_refresh()',
                       'fRefreshPending = true', 'fb->flushPanel()', 's == fPanelSurface',
                       'fRefreshFailures++ < 4', 'sweet_fb_pack_pixel(pixel, fb->fPanelFormat)',
                       'sweet_dali_gui_presented', 'if (fGuiFrames)',
                       'OSCompareAndSwap(1, 0, &sweet_dali_gui_frame_ready)']:
            self.assertIn(marker, text)
        self.assertIn('return 2;', text)  # busy means retry, never spin
        self.assertIn('kIOMemoryKernelUserShared', text)


if __name__ == '__main__':
    unittest.main()
