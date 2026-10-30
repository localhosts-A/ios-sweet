#!/usr/bin/env python3
"""Guard the payload boot-argument byte limit and round-trip packing."""

from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest

import adt
import mkpayload


class PayloadCommandLineTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.kernel = self.root / 'kernel'
        self.tree = self.root / 'tree.adt'
        vmaddr = 0xfffffff007004000
        registers = [0] * 34
        registers[32] = vmaddr + 0x100
        thread = struct.pack('<4I34Q', mkpayload.LC_UNIXTHREAD, 288, 6, 68, *registers)
        header = struct.pack('<8I', 0xfeedfacf, 0x100000c, 0, 2, 2, 72 + len(thread), 0, 0)
        segment = struct.pack('<II16s4Q4I', mkpayload.LC_SEGMENT_64, 72, b'__TEXT',
                              vmaddr, mkpayload.PAGE, 0, len(header) + 72 + len(thread), 5, 5, 0, 0)
        self.kernel.write_bytes(header + segment + thread)
        root, chosen = adt.Node(), adt.Node()
        root.props = [('name', 0, b'device-tree\0')]
        chosen.props = [('name', 0, b'chosen\0'),
                        ('dram-base', 0, struct.pack('<Q', 0x200000000)),
                        ('dram-size', 0, struct.pack('<Q', 0x13fe00000))]
        root.children = [chosen]
        self.tree.write_bytes(adt.serialize(root))

    def packed_command(self, text):
        payload, _ = mkpayload.build(self.kernel, self.tree, text)
        field = payload[mkpayload.CMDLINE:mkpayload.SEG_TABLE]
        self.assertEqual(len(field), mkpayload.BOOT_LINE_LENGTH)
        self.assertEqual(field[len(text.encode('utf-8')):], b'\0' * (len(field) - len(text.encode('utf-8'))))
        self.assertEqual(struct.unpack_from('<Q', payload, mkpayload.SEG_TABLE)[0], 0xfffffff007004000)
        return field.split(b'\0', 1)[0].decode('utf-8')

    def test_empty(self):
        self.assertEqual(self.packed_command(''), '')

    def test_ascii_boundary(self):
        text = 'x' * 607
        self.assertEqual(self.packed_command(text), text)

    def test_utf8_boundary(self):
        text = '界' * 202 + 'x'
        self.assertEqual(self.packed_command(text), text)

    def test_overlong_rejected_before_reading_inputs(self):
        for text in ('x' * 608, '界' * 203):
            with self.subTest(bytes=len(text.encode('utf-8'))):
                with self.assertRaisesRegex(ValueError, 'UTF-8 bytes; maximum is 607'):
                    mkpayload.build(self.root / 'missing', self.tree, text)

    def test_embedded_nul_rejected(self):
        with self.assertRaisesRegex(ValueError, 'NUL'):
            mkpayload.build(self.kernel, self.tree, 'rootrw=0\0rootrw=1')

    def test_cli_leaves_existing_output_untouched(self):
        output = self.root / 'payload'
        output.write_bytes(b'preserve existing artifact')
        result = subprocess.run([sys.executable, str(Path(mkpayload.__file__).resolve()),
                                 str(self.kernel), str(self.tree), str(output),
                                 '--cmdline', 'x' * 608], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn('maximum is 607', result.stderr)
        self.assertEqual(output.read_bytes(), b'preserve existing artifact')


if __name__ == '__main__':
    unittest.main()
