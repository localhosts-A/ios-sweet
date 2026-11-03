#!/usr/bin/env python3
"""Test ADT-only opt-in and actual XNU handoff function against fake IO."""
import importlib.util
import pathlib
import subprocess
import sys
import tempfile
import unittest

TOOLS = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('touch_adt', TOOLS / 'dali-touch-handoff-adt.py')
touch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(touch)

class ADTTests(unittest.TestCase):
    def setUp(self):
        self.root = touch.adt.Node()
        self.root.props = [('name', 0, b'root\0'), ('compatible', 0, b'D22AP\0xiaomi,dali\0'), ('other', 0, b'xyz')]
        child = touch.adt.Node()
        child.props = [('name', 0, b'child\0'), ('reg', 0, b'abcd')]
        self.root.children.append(child)
        self.raw = touch.adt.serialize(self.root)

    def test_only_property_added(self):
        result = touch.transform(self.raw)
        node, end = touch.adt.parse(result)
        self.assertEqual(end, len(result))
        self.assertEqual(node.props.pop(), (touch.PROPERTY, 0, b'\1\0\0\0'))
        self.assertEqual(touch.adt.serialize(node), self.raw)

    def test_reject_other_invalid_duplicate_and_trailing(self):
        self.root.props[1] = ('compatible', 0, b'xiaomi,sweet\0')
        with self.assertRaises(ValueError): touch.transform(touch.adt.serialize(self.root))
        with self.assertRaises(ValueError): touch.transform(self.raw + b'x')
        with self.assertRaises(ValueError): touch.transform(touch.transform(self.raw))
        self.root.props[1] = ('compatible', 0, b'xiaomi,dali-not\0')
        with self.assertRaises(ValueError): touch.transform(touch.adt.serialize(self.root))

if __name__ == '__main__':
    if len(sys.argv) != 2:
        raise SystemExit('usage: test_dali_touch_handoff.py MATERIALIZED_XNU')
    xnu = pathlib.Path(sys.argv.pop())
    with tempfile.TemporaryDirectory(prefix='dali-touch-') as tmp:
        exe = pathlib.Path(tmp) / 'handoff'
        subprocess.run(['clang++', '-std=c++17', '-Wall', '-Wextra', '-Werror',
                        '-fsanitize=address,undefined', '-fno-omit-frame-pointer',
                        '-I', str(xnu / 'iokit/Kernel/arm'),
                        str(TOOLS / 'test_dali_touch_handoff.cpp'), '-o', str(exe)], check=True)
        subprocess.run([str(exe)], check=True)
    unittest.main()
