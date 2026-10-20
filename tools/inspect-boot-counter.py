#!/usr/bin/env python3
"""Inspect the linked arm64 Mach-O counter helper, including emitted reads."""
import argparse
import struct
from pathlib import Path


def inspect(path):
    data = path.read_bytes()
    magic, cpu, _, _, commands, _, _, _ = struct.unpack_from('<8I', data)
    if magic != 0xfeedfacf or cpu != 0x100000c:
        raise ValueError('expected an arm64 Mach-O kernel')
    segments, symbols = [], None
    offset = 32
    for _ in range(commands):
        command, size = struct.unpack_from('<2I', data, offset)
        if size < 8 or offset + size > len(data):
            raise ValueError('invalid load command')
        if command == 0x19:
            segments.append(struct.unpack_from('<4Q', data, offset + 24))
        elif command == 2:
            symbols = struct.unpack_from('<4I', data, offset + 8)
        offset += size
    if symbols is None:
        raise ValueError('kernel has no symbol table')
    symoff, count, stroff, strsize = symbols
    strings = data[stroff:stroff + strsize]
    entries = []
    for index in range(count):
        nameoff, kind, section, _, address = struct.unpack_from(
            '<IBBHQ', data, symoff + index * 16)
        if kind & 0xe0 or (kind & 0x0e) != 0x0e:
            continue
        name = strings[nameoff:].split(b'\0', 1)[0]
        entries.append((name, section, address))
    matches = [entry for entry in entries if entry[0] == b'_sweet_boot_counter_ms']
    if len(matches) != 1:
        raise ValueError('expected one defined counter helper')
    _, section, address = matches[0]
    end = min((value for _, sec, value in entries
               if sec == section and value > address), default=address + 128)
    for va, _, fileoff, filesize in segments:
        if va <= address < end <= va + filesize:
            start = fileoff + address - va
            code = data[start:start + end - address]
            break
    else:
        raise ValueError('helper is not wholly file backed')
    words = [word[0] for word in struct.iter_unpack('<I', code[:len(code) // 4 * 4])]
    # MRS opcodes include a destination register in bits[4:0].
    counter = [i for i, word in enumerate(words) if word & ~31 == 0xd53be020]
    frequency = [i for i, word in enumerate(words) if word & ~31 == 0xd53be000]
    barriers = [i for i, word in enumerate(words) if word == 0xd5033fdf]
    if not (counter and frequency and barriers and
            min(barriers) < min(counter) < min(frequency)):
        raise ValueError('counter helper lacks ordered ISB / CNTPCT_EL0 / CNTFRQ_EL0 reads')
    print(f'{path.name}: counter helper at {address:#x}, {len(code)} bytes; '
          'ISB and both counter/frequency reads verified')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('kernel', type=Path)
    inspect(parser.parse_args().kernel)
