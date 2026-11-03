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

    def test_dali_pwm_optin_preserves_return_and_input_bounds(self):
        # Exact current601-byte argument fixture, not a device identity or boot proof.
        old = ('debug=0xc cpus=1 keepsyms=1 sweettouch=0 sweetkeys=0 sweetbattery=0 '
               'rd=ufsroot0 ufsroot=unixnu_data sweetlog=1 ufslogsecs=30 '
               'unentitled_ios_sim_launch=1 ignition_level=1 launchd_unsecure_cache=1 '
               'wdt=-1 ufsrw=1 rootrw=1 sweetsample=- sweetsampledelay=30 '
               'sweetsampleinterval=30 sweethfsdiag=1 sweethfsblock=3059250 '
               'sweetheartbeat=1 sweettasktrace=1 daligui=1 sweetfb_noconsole=1 '
               'sweetfb_hz=10 sweethidswipe=60 sweetfftrace=3 sweetffscreen=0 '
               'sweetfffault=0 serial=0 daliwdtlease=1140 sweetguiexec=1 '
               'sweethidwait=1200 sweetffpc=1 sweetbbvm=1 sweetbbipc=1 '
               'sweetload=1 ufswait=1 dalifastboot=1 sweetloadrw=1')
        self.assertEqual(len(old.encode()), 601)
        before = old.split()
        self.assertEqual(before.count('sweetffscreen=0'), 1)
        after = [word for word in before if word != 'sweetffscreen=0'] + ['ufspwm=1']
        self.assertEqual(set(before) - set(after), {'sweetffscreen=0'})
        self.assertEqual(set(after) - set(before), {'ufspwm=1'})
        for required in ('sweettouch=0', 'sweetkeys=0', 'serial=0', 'dalifastboot=1',
                         'daliwdtlease=1140', 'sweetsample=-', 'sweetsampledelay=30',
                         'sweetsampleinterval=30', 'sweetheartbeat=1', 'sweethidwait=1200'):
            self.assertEqual(after.count(required), 1)
        proposed = ' '.join(after)
        self.assertEqual(len(proposed.encode()), 594)
        self.assertEqual(self.packed_command(proposed), proposed)

    def test_dali_bbctx_optin_at_exact_limit(self):
        # The existing 594-byte PWM fixture plus one 13-byte diagnostic opt-in.
        before = ('debug=0xc cpus=1 keepsyms=1 sweettouch=0 sweetkeys=0 sweetbattery=0 '
                  'rd=ufsroot0 ufsroot=unixnu_data sweetlog=1 ufslogsecs=30 '
                  'unentitled_ios_sim_launch=1 ignition_level=1 launchd_unsecure_cache=1 '
                  'wdt=-1 ufsrw=1 rootrw=1 sweetsample=- sweetsampledelay=30 '
                  'sweetsampleinterval=30 sweethfsdiag=1 sweethfsblock=3059250 '
                  'sweetheartbeat=1 sweettasktrace=1 daligui=1 sweetfb_noconsole=1 '
                  'sweetfb_hz=10 sweethidswipe=60 sweetfftrace=3 '
                  'sweetfffault=0 serial=0 daliwdtlease=1140 sweetguiexec=1 '
                  'sweethidwait=1200 sweetffpc=1 sweetbbvm=1 sweetbbipc=1 '
                  'sweetload=1 ufswait=1 dalifastboot=1 sweetloadrw=1 ufspwm=1')
        self.assertEqual(len(before.encode('utf-8')), 594)
        candidate = before + ' sweetbbctx=1'
        self.assertEqual(len(candidate.encode('utf-8')), 607)
        self.assertEqual(self.packed_command(candidate), candidate)
        for required in ('sweettouch=0', 'sweetkeys=0', 'serial=0', 'dalifastboot=1',
                         'daliwdtlease=1140', 'sweetsample=-', 'sweetsampledelay=30',
                         'sweetsampleinterval=30', 'sweetheartbeat=1', 'sweethidwait=1200',
                         'ufspwm=1', 'sweetbbctx=1'):
            self.assertEqual(candidate.split().count(required), 1, required)
        with self.assertRaisesRegex(ValueError, 'UTF-8 bytes; maximum is 607'):
            mkpayload.build(self.root / 'missing', self.tree, candidate + 'x')
        # Current v5 uses PWM disabled; only the lease and interval change for
        # the extended observation, without displacing any other boot argument.
        v5 = candidate.replace('ufspwm=1', 'ufspwm=0')
        extended = v5.replace('daliwdtlease=1140', 'daliwdtlease=2400').replace(
            'sweetsampleinterval=30', 'sweetsampleinterval=60')
        self.assertEqual(len(v5.encode('utf-8')), 607)
        self.assertEqual(len(extended.encode('utf-8')), 607)
        self.assertEqual(extended.replace('daliwdtlease=2400', 'daliwdtlease=1140').replace(
            'sweetsampleinterval=60', 'sweetsampleinterval=30'), v5)
        self.assertEqual(self.packed_command(extended), extended)

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
