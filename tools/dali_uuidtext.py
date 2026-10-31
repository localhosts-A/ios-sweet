#!/usr/bin/env python3
"""只读 HFSX 根，离线生成已验证 arm64 cache 布局的 DSC v2；绝不植入根或操作设备。"""
import argparse
import bisect
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import struct
import sys

VERSION = 1
MAX_ROOT = 16 * 1024**3
MAX_OUTPUT = 256 * 1024**2
MAX_READ = 16 * 1024**2
CACHE_NAME = 'dyld_shared_cache_arm64'
CACHE_DIRECTORY = 'iOSRoot/System/Library/Caches/com.apple.dyld'
CACHE_PATTERN = re.compile(r'dyld_shared_cache_arm64(?:\.\d{2}(?:\.dylddata|\.dyldlinkedit)?|\.symbols)?\Z')
SECTION_NAMES = frozenset(('__cstring', '__oslogstring', '__asan_cstring', '__ctf', '__string'))


class FormatError(ValueError):
    pass


def require(condition, message):
    # 输入和发布门禁不能使用会被 python -O 移除的 assert。
    if not condition:
        raise FormatError(message)


def bounded(offset, count, size, message):
    require(isinstance(offset, int) and isinstance(count, int) and
            0 <= offset <= size and 0 <= count <= size - offset, message)


def stamp(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def hash_fd(fd, size):
    digest = hashlib.sha256()
    for offset in range(0, size, 8 * 1024**2):
        count = min(8 * 1024**2, size - offset)
        data = os.pread(fd, count, offset)
        require(len(data) == count, '输入完整哈希遇到短读')
        digest.update(data)
    return digest.hexdigest()


class RootImage:
    def __init__(self, path, expected):
        require(re.fullmatch(r'[0-9a-fA-F]{64}', expected or '') is not None,
                '必须提供完整64位十六进制 SHA-256')
        self.fd = None
        path = Path(path).absolute()
        require('/dev' not in (str(path), str(path.parent)) and
                not str(path).startswith('/dev/'), '不接受设备路径')
        require(hasattr(os, 'O_NOFOLLOW'), '此平台没有所需的 O_NOFOLLOW')
        flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, 'O_NONBLOCK', 0)
        fd = os.open(path, flags)
        try:
            info = os.fstat(fd)
            require(stat.S_ISREG(info.st_mode), '输入必须是普通文件')
            require(4096 <= info.st_size <= MAX_ROOT and info.st_size % 4096 == 0,
                    '输入容量必须按4096对齐且不大于16 GiB')
            self.size = info.st_size
            self.identity = stamp(info)
            self.expected = expected.lower()
            require(hash_fd(fd, self.size) == self.expected, '输入完整 SHA-256 不匹配')
            require(stamp(os.fstat(fd)) == self.identity, '输入在完整哈希期间变化')
            self.fd = fd
        except BaseException:
            os.close(fd)
            raise

    def read(self, offset, count):
        bounded(offset, count, self.size, '根读取越界')
        require(count <= MAX_READ, '单次根读取超过上限')
        result = os.pread(self.fd, count, offset)
        require(len(result) == count, '根读取短读')
        return result

    def reverify(self):
        require(stamp(os.fstat(self.fd)) == self.identity, '输入状态在生成期间变化')
        require(hash_fd(self.fd, self.size) == self.expected, '输入在生成期间改变')
        require(stamp(os.fstat(self.fd)) == self.identity, '输入在复核期间变化')
        return self.expected

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None


class HFSX:
    def __init__(self, root):
        self.root = root
        header = root.read(1024, 512)
        require(header[:2] == b'HX' and struct.unpack_from('>H', header, 2)[0] == 5,
                '只支持 HFSX v5')
        attributes = struct.unpack_from('>I', header, 4)[0]
        require(not attributes & (1 << 13), '不支持需要 journal replay 的卷')
        self.block, self.blocks = struct.unpack_from('>II', header, 40)
        require(self.block == 4096 and self.block * self.blocks == root.size,
                'HFSX allocation geometry 与输入长度不符')
        self.catalog = self.fork(header[272:352])
        head = self.read(self.catalog, 0, 64)
        require(head[8] == 1, 'catalog header 节点类型错误')
        self.node_size = struct.unpack_from('>H', head, 32)[0]
        self.first = struct.unpack_from('>I', head, 24)[0]
        self.nodes = struct.unpack_from('>I', head, 36)[0]
        require(512 <= self.node_size <= 32768 and
                self.node_size & (self.node_size - 1) == 0, 'catalog node size 错误')
        require(0 < self.nodes <= 1000000 and 0 < self.first < self.nodes and
                self.nodes * self.node_size <= self.catalog[0], 'catalog 容量/首leaf错误')

    def fork(self, raw):
        require(len(raw) == 80, 'fork 长度错误')
        size = struct.unpack_from('>Q', raw)[0]
        allocated = struct.unpack_from('>I', raw, 12)[0]
        extents = [struct.unpack_from('>II', raw, 16 + 8 * i) for i in range(8)]
        used = [(start, count) for start, count in extents if count]
        total = sum(count for _, count in used)
        require(total == allocated and size <= total * self.block,
                '不支持 overflow extents 或不完整 fork')
        for start, count in used:
            require(start < self.blocks and count <= self.blocks - start, 'fork extent 越界')
        physical = sorted(used)
        require(all(a + n <= b for (a, n), (b, _) in zip(physical, physical[1:])),
                'fork extents 重叠')
        require(size <= self.root.size, 'fork 逻辑长度过大')
        return size, used

    def read(self, fork, offset, count):
        bounded(offset, count, fork[0], 'fork 读取越界')
        require(count <= MAX_READ, '单次 fork 读取超过上限')
        result = bytearray()
        logical = 0
        for start, blocks in fork[1]:
            length = blocks * self.block
            low, high = max(offset, logical), min(offset + count, logical + length)
            if low < high:
                result += self.root.read(start * self.block + low - logical, high - low)
            logical += length
        require(len(result) == count, 'fork extent 覆盖缺口')
        return bytes(result)

    def records(self):
        node, previous, seen = self.first, 0, set()
        while node:
            require(0 < node < self.nodes and node not in seen, 'catalog leaf 循环/越界')
            seen.add(node)
            data = self.read(self.catalog, node * self.node_size, self.node_size)
            forward, backward = struct.unpack_from('>II', data)
            require(data[8:10] == b'\xff\x01' and backward == previous,
                    'catalog leaf 类型/反向链接错误')
            count = struct.unpack_from('>H', data, 10)[0]
            require(0 < count < self.node_size // 4, 'catalog leaf 记录数错误')
            table = self.node_size - 2 * (count + 1)
            offsets = [struct.unpack_from('>H', data, self.node_size - 2 * (i + 1))[0]
                       for i in range(count + 1)]
            require(14 <= offsets[0] and offsets[-1] <= table and
                    all(a < b for a, b in zip(offsets, offsets[1:])),
                    'catalog 记录偏移错误')
            for start, end in zip(offsets, offsets[1:]):
                require(end - start >= 10, 'catalog 记录截断')
                key = struct.unpack_from('>H', data, start)[0]
                parent, chars = struct.unpack_from('>IH', data, start + 2)
                require(0 < parent and chars <= 255 and key == 6 + 2 * chars and
                        start + 2 + key + 2 <= end, 'catalog key 越界')
                name = data[start + 8:start + 8 + 2 * chars].decode('utf-16-be')
                body = data[start + 2 + key:end]
                kind = struct.unpack_from('>H', body)[0]
                require(kind in (1, 2, 3, 4), '未知 catalog 记录类型')
                if kind in (1, 2):
                    require(len(body) >= (88 if kind == 1 else 248), 'catalog body 截断')
                yield parent, name, body
            previous, node = node, forward

    def cache_files(self):
        folders, candidates, private = {}, [], None
        for parent, name, body in self.records():
            kind = struct.unpack_from('>H', body)[0]
            if kind == 1:
                node = struct.unpack_from('>I', body, 8)[0]
                require(node not in folders, '重复 catalog folder ID')
                folders[node] = (parent, name)
                if name == '\0\0\0\0HFS+ Private Data':
                    require(private is None, '重复 HFS private folder')
                    private = node
            elif kind == 2 and CACHE_PATTERN.fullmatch(name):
                require(len(candidates) < 512, 'cache 候选数量超过上限')
                candidates.append((parent, name, body))
        require(private is not None, '缺少已支持的 HFS private folder')

        def path(parent, name):
            parts, seen = [name], set()
            while parent != 1:
                require(parent in folders and parent not in seen and len(seen) < 256,
                        'catalog 父路径缺失/循环/过深')
                seen.add(parent)
                parent, name = folders[parent]
                parts.append(name)
            return '/'.join(reversed(parts))

        selected = {}
        for parent, name, body in candidates:
            if path(parent, name) == CACHE_DIRECTORY + '/' + name:
                require(name not in selected, '重复 cache 目标路径')
                selected[name] = body
        require(CACHE_NAME in selected and len(selected) <= 128, '缺少主cache或cache过多')
        wanted = {'iNode' + str(struct.unpack_from('>I', body, 44)[0])
                  for body in selected.values() if body[48:56] == b'hlnkhfs+'}
        inodes = {}
        if wanted:
            for parent, name, body in self.records():
                if parent == private and name in wanted and struct.unpack_from('>H', body)[0] == 2:
                    require(name not in inodes, '重复 private inode')
                    inodes[name] = body
            require(set(inodes) == wanted, 'legacy hardlink inode 缺失')
        result = {}
        for name, body in selected.items():
            if body[48:56] == b'hlnkhfs+':
                body = inodes['iNode' + str(struct.unpack_from('>I', body, 44)[0])]
                require(body[48:56] != b'hlnkhfs+', '不支持嵌套 legacy hardlink')
            result[name] = self.fork(body[88:168])
        return result


def cstring(data, offset, minimum=0):
    require(minimum <= offset < len(data), '字符串偏移越界')
    end = data.find(b'\0', offset, min(len(data), offset + 4096))
    require(end >= 0, '字符串缺少有界NUL终止')
    return data[offset:end].decode('utf-8')


def fixed_name(raw):
    return raw.split(b'\0', 1)[0].decode('ascii')


class Cache:
    def __init__(self, fs):
        self.fs = fs
        self.files = fs.cache_files()
        self.headers = {}
        self.maps = []
        for name, fork in self.files.items():
            require(fork[0] >= 512, 'cache header 截断')
            head = fs.read(fork, 0, 512)
            require(head[:16] == b'dyld_v1   arm64\0', '不支持该 dyld cache 架构/头格式')
            self.headers[name] = head
            offset, count = struct.unpack_from('<II', head, 16)
            require(count <= 16 and offset < 65536, 'cache mapping table 过大')
            raw = fs.read(fork, offset, count * 32)
            for index in range(count):
                address, size, fileoff, maximum, prot = struct.unpack_from('<QQQII', raw, index * 32)
                require(address > 0 and size > 0 and address + size <= 2**64 and
                        prot & ~maximum == 0, 'cache VM mapping 错误')
                bounded(fileoff, size, fork[0], 'cache mapping 文件范围越界')
                self.maps.append(dict(address=address, end=address + size,
                                      fileoff=fileoff, name=name))
        self.maps.sort(key=lambda row: row['address'])
        require(self.maps and all(a['end'] <= b['address'] for a, b in zip(self.maps, self.maps[1:])),
                'cache VM mapping 重叠/为空')
        self.starts = [row['address'] for row in self.maps]
        self.base = self.starts[0]
        head = self.headers[CACHE_NAME]
        self.uuid = head[0x58:0x68]
        require(any(self.uuid), 'cache UUID 为空')
        suboff, count = struct.unpack_from('<II', head, 0x188)
        require(count <= 126, 'subcache 数量超过上限')
        entries = fs.read(self.files[CACHE_NAME], suboff, count * 56)
        expected, identities = {CACHE_NAME}, {self.uuid}
        for index in range(count):
            entry = entries[index * 56:(index + 1) * 56]
            uuid, vmoff = entry[:16], struct.unpack_from('<Q', entry, 16)[0]
            suffix = cstring(entry, 24)
            require(re.fullmatch(r'\.\d{2}(?:\.dylddata|\.dyldlinkedit)?', suffix) is not None,
                    '不支持 subcache suffix 布局')
            name = CACHE_NAME + suffix
            require(name not in expected and name in self.headers and any(uuid) and uuid not in identities,
                    '缺少/重复 subcache 身份')
            require(self.headers[name][0x58:0x68] == uuid, 'subcache UUID 不匹配')
            addresses = [row['address'] for row in self.maps if row['name'] == name]
            require(addresses and min(addresses) == self.base + vmoff, 'subcache VM offset 不匹配')
            expected.add(name)
            identities.add(uuid)
        symbols_uuid = head[0x190:0x1a0]
        if any(symbols_uuid):
            name = CACHE_NAME + '.symbols'
            require(name in self.headers and self.headers[name][0x58:0x68] == symbols_uuid,
                    'symbols cache UUID 不匹配')
            expected.add(name)
        require(set(self.files) == expected, 'cache family 与主cache身份表不一致')
        offset, count = struct.unpack_from('<II', head, 0x1c0)
        require(0 < count <= 10000, 'image table 数量错误')
        table = fs.read(self.files[CACHE_NAME], offset, count * 32)
        self.images, addresses = [], set()
        for index in range(count):
            address, _, _, pathoff, _ = struct.unpack_from('<QQQII', table, index * 32)
            require(address not in addresses, '重复 image VM 地址')
            addresses.add(address)
            fork = self.files[CACHE_NAME]
            require(pathoff < fork[0], 'image path offset 越界')
            raw = fs.read(fork, pathoff, min(4096, fork[0] - pathoff))
            path = cstring(raw, 0)
            require(path.startswith('/'), 'cache image path 不是绝对路径')
            self.images.append((address, path))

    def covered(self, address, size):
        require(0 <= address < 2**64 and 0 <= size <= 2**64 - address, 'VM range 溢出')
        while size:
            index = bisect.bisect_right(self.starts, address) - 1
            require(index >= 0, 'VM range 不在mapping中')
            row = self.maps[index]
            count = min(size, row['end'] - address)
            require(count > 0, 'VM mapping 覆盖缺口')
            yield row, address, count
            address += count
            size -= count

    def read(self, address, size):
        require(size <= MAX_READ, '单次 VM 读取超过上限')
        result = bytearray()
        for row, current, count in self.covered(address, size):
            result += self.fs.read(self.files[row['name']], row['fileoff'] + current - row['address'], count)
        return bytes(result)

    def macho(self, address):
        header = self.read(address, 32)
        magic, cpu, subtype, kind, count, size, flags, reserved = struct.unpack('<8I', header)
        require(magic == 0xfeedfacf and cpu == 0x100000c and kind in (6, 7),
                '不支持该 Mach-O 架构/文件类型')
        require(0 < count <= 512 and 0 < size <= 1024**2, 'Mach-O command 数量/大小错误')
        commands = self.read(address + 32, size)
        offset, segments, sections, uuids, install = 0, [], [], [], []
        for _ in range(count):
            bounded(offset, 8, size, 'Mach-O load command 截断')
            command, length = struct.unpack_from('<II', commands, offset)
            require(length >= 8 and length % 8 == 0, 'Mach-O command length 错误')
            bounded(offset, length, size, 'Mach-O command 越界')
            raw = commands[offset:offset + length]
            if command == 0x19:
                require(length >= 72, 'segment command 截断')
                name = fixed_name(raw[8:24])
                start, vm_size = struct.unpack_from('<QQ', raw, 24)
                nsections = struct.unpack_from('<I', raw, 64)[0]
                require(nsections <= 512 and 72 + nsections * 80 == length and
                        start + vm_size <= 2**64, 'segment section table/范围错误')
                segments.append((name, start, vm_size))
                for i in range(nsections):
                    at = 72 + i * 80
                    sname, segment = fixed_name(raw[at:at + 16]), fixed_name(raw[at + 16:at + 32])
                    saddr, ssize = struct.unpack_from('<QQ', raw, at + 32)
                    require(segment == name and start <= saddr <= start + vm_size and
                            ssize <= start + vm_size - saddr, 'section 不属于声明的segment')
                    sections.append((segment, sname, saddr, ssize))
            elif command == 0x1b:
                require(length == 24, 'LC_UUID 长度错误')
                uuids.append(raw[8:24])
            elif command in (0xd, 0xf):
                require(command == (0xd if kind == 6 else 0xf), '安装名命令与文件类型不符')
                minimum = 24 if command == 0xd else 12
                require(length >= minimum, '安装名 command 截断')
                install.append(cstring(raw, struct.unpack_from('<I', raw, 8)[0], minimum))
            offset += length
        require(offset == size and len(uuids) == 1 and any(uuids[0]) and len(install) == 1,
                '缺少/重复 Mach-O UUID或安装名命令')
        texts = [row for row in segments if row[0] == '__TEXT']
        require(len(texts) == 1 and texts[0][1] == address and texts[0][2] < 2**32,
                'Mach-O TEXT 与image地址不符')
        require(install[0].startswith('/'), '安装名不是已支持的绝对路径')
        return texts[0], uuids[0], install[0].encode('utf-8') + b'\0', sections


def make_layout(cache):
    ranges, images, data_bytes, path_bytes = [], [], 0, 0
    for index, (address, _) in enumerate(cache.images):
        text, uuid, path, sections = cache.macho(address)
        images.append(dict(offset=address - cache.base, size=text[2], uuid=uuid,
                           path=path, path_relative=path_bytes, original=index))
        path_bytes += len(path)
        for segment, name, start, size in sections:
            if segment not in ('__TEXT', '__CTF') or name not in SECTION_NAMES:
                continue
            require(start >= cache.base and size < 2**32, 'DSC range 字段溢出')
            list(cache.covered(start, size))
            ranges.append(dict(offset=start - cache.base, relative=data_bytes, size=size, original=index))
            data_bytes += size
            require(len(ranges) <= 50000 and data_bytes <= MAX_OUTPUT, 'DSC range 数量/数据超过上限')
    require(ranges, 'cache没有目标logging sections')
    metadata_size = 16 + 24 * len(ranges) + 32 * len(images)
    total = metadata_size + data_bytes + path_bytes
    require(total <= MAX_OUTPUT and total < 2**32, 'DSC总输出超过上限')
    ordered = sorted(images, key=lambda row: row['offset'])
    indices = {row['original']: index for index, row in enumerate(ordered)}
    sorted_ranges = sorted(ranges, key=lambda row: row['offset'])
    require(all(a['offset'] + a['size'] <= b['offset'] for a, b in zip(sorted_ranges, sorted_ranges[1:])),
            'DSC VM ranges重叠')
    metadata = bytearray(struct.pack('<4I', 0x64736368, 2, len(ranges), len(images)))
    for row in sorted_ranges:
        metadata += struct.pack('<QIIQ', row['offset'], metadata_size + row['relative'],
                                row['size'], indices[row['original']])
    for row in ordered:
        metadata += struct.pack('<QI16sI', row['offset'], row['size'], row['uuid'],
                                metadata_size + data_bytes + row['path_relative'])
    return dict(metadata=bytes(metadata), ranges=ranges, images=images,
                metadata_bytes=metadata_size, data_bytes=data_bytes,
                path_bytes=path_bytes, total=total)


def publish(root, cache, layout, output):
    output = Path(output).absolute()
    flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, 'O_DIRECTORY', 0)
    parent = os.open(output.parent, flags)
    directory = None
    manifest_linked = False
    try:
        # mkdir本身是排他的；已有目录、文件或悬空symlink均不可覆盖。
        os.mkdir(output.name, 0o700, dir_fd=parent)
        directory = os.open(output.name, flags, dir_fd=parent)
        os.fchmod(directory, 0o700)
        name = cache.uuid.hex().upper()
        partial = name + '.part'
        fd = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=directory)
        digest = hashlib.sha256()
        with os.fdopen(fd, 'wb') as stream:
            def emit(data):
                require(stream.write(data) == len(data), 'DSC输出短写')
                digest.update(data)
            emit(layout['metadata'])
            for row in layout['ranges']:
                require(stream.tell() == layout['metadata_bytes'] + row['relative'], 'DSC payload偏移错误')
                for offset in range(0, row['size'], 1024**2):
                    emit(cache.read(cache.base + row['offset'] + offset,
                                    min(1024**2, row['size'] - offset)))
            for row in layout['images']:
                require(stream.tell() == layout['metadata_bytes'] + layout['data_bytes'] +
                        row['path_relative'], 'DSC path偏移错误')
                emit(row['path'])
            require(stream.tell() == layout['total'], 'DSC长度错误')
            stream.flush()
            os.fsync(stream.fileno())
            file_stamp = stamp(os.fstat(stream.fileno()))
        fd = os.open(partial, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory)
        try:
            require(stamp(os.fstat(fd)) == file_stamp, 'DSC部分文件被替换')
            require(hash_fd(fd, layout['total']) == digest.hexdigest(), 'DSC回读hash不符')
        finally:
            os.close(fd)
        final_source_hash = root.reverify()
        record = dict(status='verified', format='DSC-v2', tool_version=VERSION,
                      tool_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                      root_sha256=root.expected, root_after_sha256=final_source_hash,
                      root_bytes=root.size, cache_uuid=cache.uuid.hex(),
                      images=len(layout['images']), ranges=len(layout['ranges']),
                      metadata_bytes=layout['metadata_bytes'], range_bytes=layout['data_bytes'],
                      path_bytes=layout['path_bytes'], bytes=layout['total'], sha256=digest.hexdigest(),
                      file=name, target='/private/var/db/uuidtext/dsc/' + name,
                      target_uid=272, target_gid=272, target_mode='0644',
                      root_image_modified=False, device_accessed=False,
                      apple_binaries_executed=False, runtime_acceptance_verified=False,
                      desktop_verified=False)
        fd = os.open('manifest.json.part', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=directory)
        with os.fdopen(fd, 'w') as stream:
            json.dump(record, stream, indent=2, ensure_ascii=False)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.link(partial, name, src_dir_fd=directory, dst_dir_fd=directory, follow_symlinks=False)
        os.unlink(partial, dir_fd=directory)
        os.fsync(directory)
        os.link('manifest.json.part', 'manifest.json', src_dir_fd=directory,
                dst_dir_fd=directory, follow_symlinks=False)
        manifest_linked = True
        os.fsync(directory)
        # 完成标记已持久发布；.part清理失败不抹掉已验证产物。
        try:
            os.unlink('manifest.json.part', dir_fd=directory)
        except OSError:
            pass
        return record
    except BaseException:
        if manifest_linked:
            try:
                os.unlink('manifest.json', dir_fd=directory)
                os.fsync(directory)
            except OSError:
                pass
        raise
    finally:
        if directory is not None:
            os.close(directory)
        os.close(parent)


def generate(image, expected, output):
    root = RootImage(image, expected)
    try:
        cache = Cache(HFSX(root))
        return publish(root, cache, make_layout(cache), output)
    finally:
        root.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root-image', type=Path, required=True)
    parser.add_argument('--expected-sha256', required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args(argv)
    if not sys.flags.isolated:
        parser.error('读取镜像请使用 python3 -I tools/dali_uuidtext.py ...')
    try:
        record = generate(args.root_image, args.expected_sha256, args.output_dir)
    except (FormatError, OSError, UnicodeError, struct.error) as error:
        print('DSC生成失败（不覆盖已有结果；未完成目录不得当作成功）：' + str(error), file=sys.stderr)
        return 1
    print(json.dumps(record, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
