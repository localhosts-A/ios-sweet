#!/usr/bin/env python3
"""Exercise the real 1/2-to-8 Dali CPU ADT transformer with synthetic trees."""
import importlib.util
from pathlib import Path
import struct
import sys
import unittest

TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))
import mkadt

_spec = importlib.util.spec_from_file_location('cpu_count', TOOLS / 'dali-cpu-count-adt.py')
cpu = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cpu)
mkadt.d22ap_product = lambda: None


class CPUCountTests(unittest.TestCase):
    def setUp(self):
        self.raw = cpu.adt.serialize(mkadt.build(seed=bytes(64), dali=True))

    def test_all_prefixes_and_byte_preservation(self):
        for count in range(2, 9):
            with self.subTest(count=count):
                result = cpu.transform(self.raw, count)
                tree, end = cpu.adt.parse(result)
                self.assertEqual(end, len(result))
                nodes = next(node for node in tree.children if node.name == 'cpus').children
                self.assertEqual(len(nodes), count)
                for index, node in enumerate(nodes):
                    self.assertEqual(node.prop('reg'), struct.pack('<I', index << 8))
                    self.assertEqual(node.prop('cpu-id'), struct.pack('<I', index))
                    self.assertEqual(node.prop('cluster-type'), bytes(4))
                    self.assertEqual(node.prop('cluster-core-id'), struct.pack('<I', index))
                    self.assertEqual(node.prop('state'), b'running\0' if index == 0 else b'stopped\0')
                    self.assertEqual(node.prop('dali-boot-cpu'), b'\1\0\0\0')
                handles = [node.prop('AAPL,phandle') for node in cpu.walk(tree) if node.prop('AAPL,phandle') is not None]
                self.assertEqual(len(handles), len(set(handles)))
                del nodes[1:]
                self.assertEqual(cpu.adt.serialize(tree), self.raw)

    def test_extend_existing_two_cpus(self):
        two = cpu.transform(self.raw, 2)
        eight = cpu.transform(two, 8)
        tree, _ = cpu.adt.parse(eight)
        nodes = next(node for node in tree.children if node.name == 'cpus').children
        del nodes[2:]
        self.assertEqual(cpu.adt.serialize(tree), two)

    def test_invalid_counts_and_duplicate_conversion(self):
        for count in (0, 1, 9, -1, True, 2.0):
            with self.subTest(count=count), self.assertRaises(ValueError):
                cpu.transform(self.raw, count)
        eight = cpu.transform(self.raw, 8)
        with self.assertRaises(ValueError):
            cpu.transform(eight, 8)
        with self.assertRaises(ValueError):
            cpu.transform(eight, 2)

    def test_malformed_or_non_dali(self):
        with self.assertRaises(ValueError):
            cpu.transform(self.raw + b'x', 8)
        tree, _ = cpu.adt.parse(self.raw)
        cpu.replace(tree, 'compatible', b'xiaomi,dali-not\0')
        with self.assertRaises(ValueError):
            cpu.transform(cpu.adt.serialize(tree), 8)

    def test_bad_cpu_prefix_and_duplicate_phandle(self):
        tree, _ = cpu.adt.parse(cpu.transform(self.raw, 2))
        nodes = next(node for node in tree.children if node.name == 'cpus').children
        cpu.replace(nodes[1], 'reg', struct.pack('<I', 0x200))
        with self.assertRaises(ValueError):
            cpu.transform(cpu.adt.serialize(tree), 8)
        cpu.replace(nodes[1], 'reg', struct.pack('<I', 0x100))
        cpu.replace(nodes[1], 'AAPL,phandle', nodes[0].prop('AAPL,phandle'))
        with self.assertRaises(ValueError):
            cpu.transform(cpu.adt.serialize(tree), 8)


if __name__ == '__main__':
    unittest.main()
