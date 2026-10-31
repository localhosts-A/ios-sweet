#!/usr/bin/env python3
"""人工 HFSX/cache fixture 执行生产CLI；不需要Apple数据或设备。"""
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

TOOL = Path(__file__).with_name('dali_uuidtext.py').resolve()
spec = importlib.util.spec_from_file_location('dali_uuidtext', TOOL)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
BASE = 0x180000000
NAME = 'dyld_shared_cache_arm64'
PATH_A, PATH_B = b'/usr/lib/A.dylib\0', b'/usr/lib/dyld\0'


def fork(size, extents):
    raw = bytearray(80)
    struct.pack_into('>QII', raw, 0, size, 0, sum(n for _, n in extents))
    for i, (start, count) in enumerate(extents):
        struct.pack_into('>II', raw, 16 + 8 * i, start, count)
    return raw


def macho(kind, address, uuid, path, segments):
    commands = bytearray()
    for segment, start, length, sections in segments:
        commands += struct.pack('<II16sQQQQiiII', 0x19, 72 + 80 * len(sections),
                                segment.encode(), start, length, 0, length, 7, 5, len(sections), 0)
        for name, at, size in sections:
            commands += struct.pack('<16s16sQQ8I', name.encode(), segment.encode(), at, size,
                                    0, 0, 0, 0, 0, 0, 0, 0)
    minimum = 24 if kind == 6 else 12
    name_command = bytearray((minimum + len(path) + 7) & ~7)
    struct.pack_into('<III', name_command, 0, 0xd if kind == 6 else 0xf, len(name_command), minimum)
    name_command[minimum:minimum + len(path)] = path
    commands += name_command
    commands += struct.pack('<II16s', 0x1b, 24, uuid)
    header = struct.pack('<8I', 0xfeedfacf, 0x100000c, 0, kind,
                         len(segments) + 2, len(commands), 0, 0)
    return header + commands


class Fixture:
    def __init__(self):
        self.main = bytearray(0x3000)
        self.sub = bytearray(0x5000)
        self.inode = 'iNode500'
        self.duplicate = False
        self.corrupt_root = None
        self.offsets = {}
        for data, uuid in ((self.main, b'C' * 16), (self.sub, b'D' * 16)):
            data[:16] = b'dyld_v1   arm64\0'
            data[0x58:0x68] = uuid
            struct.pack_into('<II', data, 16, 0x200, 1)
        struct.pack_into('<QQQII', self.main, 0x200, BASE, 0x1800, 0x1000, 7, 5)
        struct.pack_into('<QQQII', self.sub, 0x200, BASE + 0x1800, 0x3800, 0x1000, 7, 5)
        struct.pack_into('<II', self.main, 0x188, 0x600, 1)
        self.main[0x600:0x610] = b'D' * 16
        struct.pack_into('<Q', self.main, 0x610, 0x1800)
        self.main[0x618:0x61c] = b'.01\0'
        struct.pack_into('<II', self.main, 0x1c0, 0x500, 2)
        struct.pack_into('<QQQII', self.main, 0x500, BASE + 0x3000, 0, 0, 0x580, 0)
        struct.pack_into('<QQQII', self.main, 0x520, BASE + 0x1000, 0, 0, 0x5b0, 0)
        alias = b'/table/alias-A\0'
        self.main[0x580:0x580 + len(alias)] = alias
        self.main[0x5b0:0x5b0 + len(PATH_B)] = PATH_B
        a = macho(6, BASE + 0x3000, b'A' * 16, PATH_A, [
            ('__TEXT', BASE + 0x3000, 0x1000, [
                ('__cstring', BASE + 0x3800, 2), ('__oslogstring', BASE + 0x3810, 5),
                ('__asan_cstring', BASE + 0x3820, 3), ('__string', BASE + 0x3830, 4)]),
            ('__CTF', BASE + 0x4100, 0x100, [('__ctf', BASE + 0x4120, 3)]),
            ('__DATA', BASE + 0x4800, 0x100, [('__cstring', BASE + 0x4820, 7)])])
        b = macho(7, BASE + 0x1000, b'B' * 16, PATH_B, [
            ('__TEXT', BASE + 0x1000, 0x1000, [('__cstring', BASE + 0x17fe, 8)])])
        self.put_vm(BASE + 0x3000, a)
        self.put_vm(BASE + 0x1000, b)
        for address, data in ((0x3800, b'A\0'), (0x3810, b'OSLOG'), (0x3820, b'ASN'),
                              (0x3830, b'STR\0'), (0x4120, b'CTF'), (0x4820, b'IGNORE!'),
                              (0x17fe, b'ABCDEFGH')):
            self.put_vm(BASE + address, data)

    def put_vm(self, address, data):
        relative = address - BASE
        while data:
            if relative < 0x1800:
                count = min(len(data), 0x1800 - relative)
                self.main[0x1000 + relative:0x1000 + relative + count] = data[:count]
            else:
                count = len(data)
                at = 0x1000 + relative - 0x1800
                self.sub[at:at + count] = data[:count]
            relative += count
            data = data[count:]

    def build(self):
        root = bytearray(128 * 4096)
        root[1024:1028] = b'HX\x00\x05'
        struct.pack_into('>II', root, 1064, 4096, 128)
        root[1296:1376] = fork(8192, [(4, 2)])
        root[20 * 4096:21 * 4096] = self.main[:4096]
        root[30 * 4096:32 * 4096] = self.main[4096:]
        root[40 * 4096:45 * 4096] = self.sub
        records = []
        for parent, name, node in ((1, 'iOSRoot', 2), (2, 'System', 10), (10, 'Library', 11),
                                   (11, 'Caches', 12), (12, 'com.apple.dyld', 13),
                                   (2, '\0\0\0\0HFS+ Private Data', 14)):
            body = bytearray(88)
            struct.pack_into('>H', body, 0, 1)
            struct.pack_into('>I', body, 8, node)
            records.append((parent, name, body))
        for parent, name, node, data_fork, hardlink in (
                (13, NAME, 20, fork(0, []), True),
                (14, self.inode, 21, fork(len(self.main), [(20, 1), (30, 2)]), False),
                (13, NAME + '.01', 22, fork(len(self.sub), [(40, 5)]), False)):
            body = bytearray(248)
            struct.pack_into('>H', body, 0, 2)
            struct.pack_into('>I', body, 8, node)
            if hardlink:
                struct.pack_into('>I', body, 44, 500)
                body[48:56] = b'hlnkhfs+'
            body[88:168] = data_fork
            records.append((parent, name, body))
            if self.duplicate and name == NAME:
                records.append((parent, name, body[:]))
        header, leaf = 4 * 4096, 5 * 4096
        root[header + 8] = 1
        struct.pack_into('>I', root, header + 24, 1)
        struct.pack_into('>H', root, header + 32, 4096)
        struct.pack_into('>I', root, header + 36, 2)
        root[leaf + 8:leaf + 10] = b'\xff\x01'
        struct.pack_into('>H', root, leaf + 10, len(records))
        at = 14
        for index, (parent, name, body) in enumerate(sorted(records, key=lambda row: (row[0], row[1]))):
            encoded = name.encode('utf-16-be')
            key = struct.pack('>HIH', 6 + len(encoded), parent, len(encoded) // 2) + encoded
            struct.pack_into('>H', root, leaf + 4096 - 2 * (index + 1), at)
            self.offsets[name] = (leaf + at, leaf + at + len(key))
            root[leaf + at:leaf + at + len(key) + len(body)] = key + body
            at += len(key) + len(body)
        struct.pack_into('>H', root, leaf + 4096 - 2 * (len(records) + 1), at)
        if self.corrupt_root:
            self.corrupt_root(root, self.offsets)
        return bytes(root)


def golden():
    # 人工推导，与生产make_layout无共享实现。
    data = b'A\0OSLOGASNSTR\0CTFABCDEFGH'
    metadata = struct.pack('<4I', 0x64736368, 2, 6, 2)
    metadata += struct.pack('<QIIQ', 0x17fe, 241, 8, 0)
    metadata += struct.pack('<QIIQ', 0x3800, 224, 2, 1)
    metadata += struct.pack('<QIIQ', 0x3810, 226, 5, 1)
    metadata += struct.pack('<QIIQ', 0x3820, 231, 3, 1)
    metadata += struct.pack('<QIIQ', 0x3830, 234, 4, 1)
    metadata += struct.pack('<QIIQ', 0x4120, 238, 3, 1)
    metadata += struct.pack('<QI16sI', 0x1000, 0x1000, b'B' * 16, 249 + len(PATH_A))
    metadata += struct.pack('<QI16sI', 0x3000, 0x1000, b'A' * 16, 249)
    return metadata + data + PATH_A + PATH_B


def independently_decode(data):
    if len(data) < 16 or struct.unpack_from('<4I', data) != (0x64736368, 2, 6, 2):
        raise ValueError('header')
    occupied = [(0, 224)]
    expected_ranges = [(0x17fe, b'ABCDEFGH', 0), (0x3800, b'A\0', 1),
                       (0x3810, b'OSLOG', 1), (0x3820, b'ASN', 1),
                       (0x3830, b'STR\0', 1), (0x4120, b'CTF', 1)]
    for index, (address, content, owner) in enumerate(expected_ranges):
        vm, offset, size, observed_owner = struct.unpack_from('<QIIQ', data, 16 + index * 24)
        if (vm, size, observed_owner) != (address, len(content), owner) or data[offset:offset + size] != content:
            raise ValueError('range')
        occupied.append((offset, offset + size))
    for index, (address, uuid, path) in enumerate(((0x1000, b'B' * 16, PATH_B),
                                                (0x3000, b'A' * 16, PATH_A))):
        vm, size, observed_uuid, offset = struct.unpack_from('<QI16sI', data, 160 + index * 32)
        if (vm, size, observed_uuid) != (address, 0x1000, uuid) or data[offset:offset + len(path)] != path:
            raise ValueError('image')
        occupied.append((offset, offset + len(path)))
    occupied.sort()
    if occupied[-1][1] != len(data) or any(a[1] != b[0] for a, b in zip(occupied, occupied[1:])):
        raise ValueError('coverage')


class UUIDTextTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.image = self.directory / 'root.img'
        self.output = self.directory / 'generated'
        self.fixture = Fixture()

    def write(self):
        data = self.fixture.build()
        self.image.write_bytes(data)
        return hashlib.sha256(data).hexdigest()

    def cli(self, expected=None, optimize=False, image=None, output=None):
        digest = self.write() if expected is None else expected
        command = [sys.executable, '-I'] + (['-O'] if optimize else [])
        command += [str(TOOL), '--root-image', str(image or self.image),
                    '--expected-sha256', digest, '--output-dir', str(output or self.output)]
        return subprocess.run(command, cwd=self.directory, text=True, capture_output=True, timeout=30)

    def refused(self, text=None):
        result = self.cli()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertNotIn('SHA-256 不匹配', result.stderr)
        if text:
            self.assertIn(text, result.stderr)
        self.assertFalse((self.output / 'manifest.json').exists())
        self.assertEqual(self.image.read_bytes(), self.fixture.build())

    def test_full_cli_golden_and_private_output(self):
        digest = self.write()
        before = self.image.read_bytes()
        result = self.cli(digest)
        self.assertEqual(result.returncode, 0, result.stderr)
        record = json.loads(result.stdout)
        data = (self.output / record['file']).read_bytes()
        self.assertEqual(data, golden())
        independently_decode(data)
        self.assertEqual(record['sha256'], hashlib.sha256(data).hexdigest())
        self.assertEqual(record['root_sha256'], digest)
        self.assertEqual(record['root_after_sha256'], digest)
        self.assertEqual(self.image.read_bytes(), before)
        self.assertFalse(record['device_accessed'])
        self.assertFalse(record['root_image_modified'])
        self.assertFalse(record['runtime_acceptance_verified'])
        self.assertEqual(stat.S_IMODE(self.output.stat().st_mode), 0o700)
        for path in self.output.iterdir():
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(json.loads((self.output / 'manifest.json').read_text()), record)

    def test_independent_verifier_rejects_corruption_and_truncation(self):
        data = bytearray(golden())
        independently_decode(data)
        for at in (0, 24, 48, 180, 226, len(data) - 2):
            damaged = data[:]
            damaged[at] ^= 1
            with self.subTest(at=at), self.assertRaises((ValueError, struct.error)):
                independently_decode(damaged)
        with self.assertRaises((ValueError, struct.error)):
            independently_decode(data[:-1])

    def test_bad_geometry(self):
        self.fixture.corrupt_root = lambda data, _: struct.pack_into('>I', data, 1068, 127)
        self.refused('geometry')

    def test_journaled_volume(self):
        self.fixture.corrupt_root = lambda data, _: struct.pack_into('>I', data, 1028, 1 << 13)
        self.refused('journal')

    def test_catalog_leaf_cycle(self):
        self.fixture.corrupt_root = lambda data, _: struct.pack_into('>I', data, 5 * 4096, 1)
        self.refused('循环')

    def test_catalog_key_boundary(self):
        self.fixture.corrupt_root = lambda data, offsets: struct.pack_into('>H', data, offsets[NAME][0], 0xfffe)
        self.refused('key')

    def test_parent_cycle(self):
        self.fixture.corrupt_root = lambda data, offsets: struct.pack_into('>I', data, offsets['Library'][0] + 2, 11)
        self.refused('父路径')

    def test_overflow_extent_rejected(self):
        self.fixture.corrupt_root = lambda data, offsets: struct.pack_into('>I', data, offsets['iNode500'][1] + 100, 4)
        self.refused('overflow')

    def test_missing_hardlink_inode(self):
        self.fixture.inode = 'iNode501'
        self.refused('inode')

    def test_duplicate_cache_path(self):
        self.fixture.duplicate = True
        self.refused('重复 cache')

    def test_wrong_subcache_identity(self):
        self.fixture.sub[0x58] ^= 1
        self.refused('UUID')

    def test_mapping_overlap(self):
        struct.pack_into('<Q', self.fixture.sub, 0x200, BASE + 0x1700)
        self.refused('重叠')

    def test_mapping_gap(self):
        # 保持两个Mach-O头所在映射不变，仅在跨界cstring中留一个字节的洞。
        struct.pack_into('<Q', self.fixture.main, 0x208, 0x17ff)
        self.refused('覆盖缺口')

    def test_image_table_count_bound(self):
        struct.pack_into('<I', self.fixture.main, 0x1c4, 0xffffffff)
        self.refused('image table')

    def test_unterminated_image_path(self):
        struct.pack_into('<I', self.fixture.main, 0x518, len(self.fixture.main) - 1)
        self.fixture.main[-1] = 65
        self.refused('NUL')

    def test_macho_command_truncation(self):
        # A位于subcache文件偏移0x2800，破坏实际segment的cmdsize。
        struct.pack_into('<I', self.fixture.sub, 0x2800 + 36, 0xfffffff8)
        self.refused('command')

    def test_section_outside_segment(self):
        struct.pack_into('<Q', self.fixture.sub, 0x2800 + 32 + 72 + 32, BASE + 0x6000)
        self.refused('section')

    def test_duplicate_uuid_command(self):
        at = 0x2800
        count, size = struct.unpack_from('<II', self.fixture.sub, at + 16)
        struct.pack_into('<II', self.fixture.sub, at + 16, count + 1, size + 24)
        self.fixture.sub[at + 32 + size:at + 32 + size + 24] = struct.pack('<II16s', 0x1b, 24, b'A' * 16)
        self.refused('重复')

    def test_wrong_hash(self):
        self.write()
        result = self.cli('0' * 64)
        self.assertEqual(result.returncode, 1)
        self.assertIn('SHA-256 不匹配', result.stderr)
        self.assertFalse(self.output.exists())

    def test_optimized_python_still_refuses_bad_format(self):
        self.fixture.corrupt_root = lambda data, _: data.__setitem__(slice(1024, 1026), b'H+')
        result = self.cli(optimize=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn('HFSX', result.stderr)

    def test_existing_output_directory_is_untouched(self):
        self.output.mkdir()
        sentinel = self.output / 'keep'
        sentinel.write_bytes(b'original')
        result = self.cli()
        self.assertEqual(result.returncode, 1)
        self.assertEqual(list(self.output.iterdir()), [sentinel])
        self.assertEqual(sentinel.read_bytes(), b'original')

    def test_dangling_output_symlink_is_untouched(self):
        self.output.symlink_to(self.directory / 'absent')
        result = self.cli()
        self.assertEqual(result.returncode, 1)
        self.assertTrue(self.output.is_symlink())
        self.assertFalse((self.directory / 'absent').exists())

    def test_input_symlink_and_fifo_refused_without_blocking(self):
        digest = self.write()
        alias = self.directory / 'alias'
        alias.symlink_to(self.image)
        self.assertEqual(self.cli(digest, image=alias).returncode, 1)
        alias.unlink()
        os.mkfifo(alias)
        self.assertEqual(self.cli(digest, image=alias).returncode, 1)
        self.assertFalse(self.output.exists())

    def test_publish_failure_has_no_success_marker(self):
        digest = self.write()
        before = self.image.read_bytes()
        with mock.patch.object(module.os, 'link', side_effect=OSError('injected publication failure')):
            with self.assertRaises(OSError):
                module.generate(self.image, digest, self.output)
        self.assertFalse((self.output / 'manifest.json').exists())
        self.assertEqual(self.image.read_bytes(), before)
        self.assertTrue(any(self.output.iterdir()))

    def test_source_mutation_before_publication_is_refused(self):
        digest = self.write()
        original = module.RootImage.reverify
        def changed(root):
            with self.image.open('r+b') as stream:
                stream.seek(4096)
                stream.write(b'x')
            return original(root)
        with mock.patch.object(module.RootImage, 'reverify', changed):
            with self.assertRaisesRegex(module.FormatError, '输入状态'):
                module.generate(self.image, digest, self.output)
        self.assertFalse((self.output / 'manifest.json').exists())

    def test_import_and_help_do_not_touch_input_or_launch_programs(self):
        with mock.patch.object(module.os, 'open', side_effect=AssertionError('unexpected file access')):
            with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as result:
                module.main(['--help'])
            self.assertEqual(result.exception.code, 0)
        (self.directory / 'json.py').write_text('raise RuntimeError("untrusted cwd import")\n')
        result = subprocess.run([sys.executable, '-I', str(TOOL), '--help'], cwd=self.directory,
                                text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.image.exists())


if __name__ == '__main__':
    unittest.main()
