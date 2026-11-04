#!/usr/bin/env python3
"""校验同板原始FDT，只向独立raw ADT增加默认off的Fastboot返回契约；不操作设备。"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import struct
import sys

_SPEC = importlib.util.spec_from_file_location('dali_raw_adt', Path(__file__).with_name('adt.py'))
adt = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(adt)
MAX_INPUT = 2 * 1024**2
CONTRACT = (0x24, 0x0f, 3)
PROPERTIES = ('fastboot-return-contract', 'fastboot-return-policy')


def require(value, message):
    if not value:
        raise ValueError(message)


def read_raw(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, 'O_NONBLOCK', 0))
    try:
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and 40 <= info.st_size <= MAX_INPUT,
                '输入必须为有界普通raw文件')
        data = os.pread(fd, info.st_size, 0)
        after = os.fstat(fd)
        require(len(data) == info.st_size and
                (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns) ==
                (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns),
                '输入短读或读取期间变化')
        return data
    finally:
        os.close(fd)


def parse_fdt(data):
    require(len(data) >= 40, 'FDT头截断')
    magic, total, structures, strings, reserve, version, compatible, cpu, string_size, structure_size = \
        struct.unpack_from('>10I', data)
    require(magic == 0xd00dfeed and total == len(data) and version == 17 and compatible <= 17,
            '只支持完整FDT v17')
    require(40 <= structures <= len(data) - structure_size and
            40 <= strings <= len(data) - string_size and reserve % 8 == 0 and
            40 <= reserve < len(data) and structures % 4 == 0 and
            (structures + structure_size <= strings or strings + string_size <= structures),
            'FDT区域范围/重叠错误')
    block, names = data[structures:structures + structure_size], data[strings:strings + string_size]
    offset, stack, nodes, started = 0, [], {}, False
    while offset < len(block):
        require(offset + 4 <= len(block), 'FDT token截断')
        token = struct.unpack_from('>I', block, offset)[0]
        offset += 4
        if token == 1:
            end = block.find(b'\0', offset, min(len(block), offset + 256))
            require(end >= 0 and len(stack) < 64 and len(nodes) < 32768, 'FDT节点名/深度过大')
            name = block[offset:end].decode('ascii')
            require('/' not in name and (started or name == '') and (not started or stack),
                    'FDT根/节点名错误')
            stack.append(name)
            path = '/' + '/'.join(stack[1:])
            require(path not in nodes, '重复FDT节点')
            nodes[path] = {}
            started = True
            offset = (end + 4) & ~3
        elif token == 2:
            require(stack, 'FDT节点栈下溢')
            stack.pop()
        elif token == 3:
            require(stack and offset + 8 <= len(block), 'FDT属性位置/头错误')
            size, nameoff = struct.unpack_from('>II', block, offset)
            offset += 8
            require(size <= len(block) - offset and nameoff < len(names), 'FDT属性越界')
            end = names.find(b'\0', nameoff, min(len(names), nameoff + 256))
            require(end >= 0, 'FDT属性名无终止')
            name = names[nameoff:end].decode('ascii')
            props = nodes['/' + '/'.join(stack[1:])]
            # 原厂/__symbols__有1338项；辅助调试表单独有界，不放宽敏感节点。
            limit = 4096 if stack == ['', '__symbols__'] else 1024
            require(name and name not in props and len(props) < limit,
                    '重复或超上限FDT属性: ' + '/'.join(stack) + ':' + name)
            props[name] = block[offset:offset + size]
            offset = (offset + size + 3) & ~3
        elif token == 4:
            pass
        elif token == 9:
            require(started and not stack and not any(block[offset:]), 'FDT结束状态错误')
            return nodes
        else:
            raise ValueError('未知FDT token')
    raise ValueError('FDT缺少结束token')


def strings(raw):
    require(raw and raw.endswith(b'\0'), 'FDT字符串表值缺少NUL')
    values = raw[:-1].split(b'\0')
    require(all(values), 'FDT字符串列表为空')
    return values


def cell(props, name):
    raw = props.get(name, b'')
    require(len(raw) == 4, 'FDT单cell属性错误: ' + name)
    return struct.unpack('>I', raw)[0]


def validate_fdt(data):
    nodes = parse_fdt(data)
    require(b'mediatek,MT6991' in strings(nodes['/'].get('compatible', b'')), '不是MT6991 FDT')
    watchdog = nodes.get('/watchdog@1c010000', {})
    compatibles = strings(watchdog.get('compatible', b''))
    require(b'mediatek,mt6991-wdt' in compatibles and b'mediatek,mt6589-wdt' in compatibles,
            'TOPRGU compatible不匹配')
    address_cells, size_cells = cell(nodes['/'], '#address-cells'), cell(nodes['/'], '#size-cells')
    require(address_cells in (1, 2) and size_cells in (1, 2), '不支持FDT地址cell布局')
    reg = watchdog.get('reg', b'')
    require(len(reg) == (address_cells + size_cells) * 4, 'TOPRGU reg长度错误')
    base = int.from_bytes(reg[:address_cells * 4], 'big')
    size = int.from_bytes(reg[address_cells * 4:], 'big')
    reboot = nodes.get('/watchdog@1c010000/reboot-mode', {})
    require((base, size) == (0x1c010000, 0x1000) and
            b'syscon-reboot-mode' in strings(reboot.get('compatible', b'')) and
            tuple(cell(reboot, key) for key in ('offset', 'mask', 'mode-bootloader')) == CONTRACT,
            'FDT bootloader重启契约不匹配')
    psci = nodes.get('/psci', {})
    require(b'arm,psci-1.0' in strings(psci.get('compatible', b'')) and
            psci.get('method') == b'smc\0', 'PSCI SMC conduit不匹配')
    require(cell(nodes.get('/timer', {}), 'clock-frequency') == 13000000, 'timer频率不匹配')
    return (base, size)


def parse_raw_adt(data):
    count = [0]
    def node(offset, depth):
        require(depth <= 64 and count[0] < 4096 and offset + 8 <= len(data), 'ADT节点截断/过深')
        count[0] += 1
        properties, children = struct.unpack_from('<II', data, offset)
        require(properties <= 256 and children <= 1024, 'ADT属性/子节点数过大')
        offset += 8
        result, seen = adt.Node(), set()
        for _ in range(properties):
            require(offset + 36 <= len(data), 'ADT属性头截断')
            field = data[offset:offset + 32]
            end = field.find(b'\0')
            require(0 < end <= 31 and not any(field[end:]), 'ADT属性名错误')
            name = field[:end].decode('ascii')
            size = struct.unpack_from('<I', data, offset + 32)[0]
            flags, length = size >> 31, size & 0x7fffffff
            offset += 36
            padded = (length + 3) & ~3
            require(name not in seen and padded <= len(data) - offset, 'ADT属性重复/越界')
            require(not any(data[offset + length:offset + padded]), 'ADT padding非零')
            seen.add(name)
            result.props.append((name, flags, data[offset:offset + length]))
            offset += padded
        for _ in range(children):
            child, offset = node(offset, depth + 1)
            result.children.append(child)
        return result, offset
    root, end = node(0, 0)
    require(end == len(data) and adt.serialize(root) == data, 'ADT有尾部数据或不能无损往返')
    return root


def prepare(fdt_data, adt_data, token, after, lease, window=1200):
    require(re.fullmatch(r'[0-9a-f]{32}', token or '') is not None and int(token, 16) != 0,
            'run-token必须为非零32位小写hex')
    require(120 <= after <= 1080 and after + 60 <= lease <= 1140 and window == 1200,
            '自动返回/lease/window预算不匹配')
    base, size = validate_fdt(fdt_data)
    root = parse_raw_adt(adt_data)
    matches = []
    def visit(node):
        if node.name == 'dali-watchdog':
            matches.append(node)
        for child in node.children:
            visit(child)
    visit(root)
    require(len(matches) == 1, 'ADT必须只有一个dali-watchdog节点')
    target = matches[0]
    require(target.prop('compatible') == b'mediatek,mt6991-wdt\0mediatek,mt6589-wdt\0' and
            target.prop('reg') == struct.pack('<QQ', base, size), 'ADT watchdog资源不匹配')
    require(all(target.prop(key) is None for key in PROPERTIES), 'ADT返回契约已存在，拒绝覆盖')
    target.props += [(PROPERTIES[0], 0, struct.pack('<III', *CONTRACT)),
                     (PROPERTIES[1], 0, struct.pack('<I16s', after, bytes.fromhex(token)))]
    output = adt.serialize(root)
    # 删除指定的两个新增字段后，必须完整恢复原始输入字节。
    target.props = target.props[:-2]
    require(adt.serialize(root) == adt_data, '除允许属性外发生ADT变化')
    record = dict(status='prepared-host-only', fdt_sha256=hashlib.sha256(fdt_data).hexdigest(),
                  input_adt_sha256=hashlib.sha256(adt_data).hexdigest(),
                  output_adt_sha256=hashlib.sha256(output).hexdigest(), run_token=token,
                  added_properties=list(PROPERTIES), after_seconds=after, lease_seconds=lease,
                  observation_window_seconds=window, marker_offset=CONTRACT[0], mask=CONTRACT[1],
                  bootloader_value=CONTRACT[2], rgu_base=base, rgu_bytes=size,
                  required_boot_args={'dalifastboot': 1, 'daliwdtlease': lease, 'sweetheartbeat': 1,
                                      'sweetsample': '-', 'sweetsampledelay': 30, 'sweetsampleinterval': 30},
                  device_accessed=False, mmio_written=False, runtime_verified=False,
                  fastboot_verified=False, desktop_verified=False)
    return output, record


def publish(output, data, record):
    output = Path(output)
    part = output.with_name(output.name + '.part')
    metadata = output.with_name(output.name + '.json')
    require(not any(path.exists() or path.is_symlink() for path in (output, part, metadata)),
            '输出已存在，拒绝覆盖')
    with part.open('xb') as stream:
        os.fchmod(stream.fileno(), 0o600)
        require(stream.write(data) == len(data), 'ADT输出短写')
        stream.flush()
        os.fsync(stream.fileno())
    require(part.read_bytes() == data, 'ADT回读不符')
    os.link(part, output, follow_symlinks=False)
    with metadata.open('x') as stream:
        os.fchmod(stream.fileno(), 0o600)
        json.dump(record, stream, indent=2)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    directory = os.open(output.parent, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
    try:
        os.fsync(directory)
        part.unlink()
        os.fsync(directory)
    finally:
        os.close(directory)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fdt', type=Path, required=True)
    parser.add_argument('--adt', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--run-token', required=True)
    parser.add_argument('--after-seconds', type=int, default=1080)
    parser.add_argument('--lease-seconds', type=int, default=1140)
    args = parser.parse_args(argv)
    if not sys.flags.isolated:
        parser.error('请使用python3 -I读取FDT/ADT数据')
    try:
        output, record = prepare(read_raw(args.fdt), read_raw(args.adt), args.run_token,
                                 args.after_seconds, args.lease_seconds)
        publish(args.output, output, record)
    except (ValueError, KeyError, UnicodeError, OSError, struct.error) as error:
        print('ADT返回契约准备失败：' + str(error), file=sys.stderr)
        return 1
    print(json.dumps(record, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
