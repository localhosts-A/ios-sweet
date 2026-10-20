#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"

OUT=out-dali/chain
mkdir -p "$OUT"
python3 - "${1:-../backup/dali-25060RK16C-pairtest-20261020/boot_b.img}" \
	"${2:-../backup/dali-25060RK16C-pairtest-20261020/init_boot_b.img}" "$OUT" <<'PY'
import json
import struct
import subprocess
import sys
from pathlib import Path
boot, init_boot, out = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
b, r = boot.read_bytes(), init_boot.read_bytes()
for name, image in [('boot', b), ('init_boot', r)]:
    if image[:8] != b'ANDROID!' or struct.unpack_from('<I', image, 40)[0] != 4:
        raise SystemExit(f'{name}: expected Android boot v4 backup')
ks, rs = struct.unpack_from('<II', b, 8)
rk, rr = struct.unpack_from('<II', r, 8)
if rs or rk or not ks or not rr or 4096 + ks > len(b) or 4096 + rr > len(r):
    raise SystemExit('Unexpected stock GKI boot/init_boot arrangement')
(out / 'stock-kernel.lz4').write_bytes(b[4096:4096+ks])
subprocess.run(['lz4', '-d', '-f', str(out / 'stock-kernel.lz4'), str(out / 'stock-kernel.Image')], check=True)
raw = (out / 'stock-kernel.Image').read_bytes()
extent = struct.unpack_from('<Q', raw, 16)[0]
if len(raw) < 64 or raw[56:60] != b'ARM\x64' or extent < len(raw) or extent >= 0x7000000:
    raise SystemExit('Unexpected stock Linux Image header/extent')
(out / 'stock-ramdisk.bin').write_bytes(r[4096:4096+rr])
asm = ['.section .rodata.android_stock, "a"']
for label, file in [('stock_kernel', 'stock-kernel.Image'), ('stock_ramdisk', 'stock-ramdisk.bin')]:
    path = str((out / file).resolve()).replace('\\', '\\\\').replace('"', '\\"')
    asm += ['.balign 16', f'.global {label}_start, {label}_end', f'{label}_start:', f'.incbin "{path}"', f'{label}_end:']
(out / 'stock.S').write_text('\n'.join(asm) + '\n')
(out / 'stock-meta.json').write_text(json.dumps({'cmdline': b[44:1580].split(b'\0')[0].decode(),
    'kernel_bytes': len(raw), 'kernel_extent': extent, 'ramdisk_bytes': rr}))
print('Embedded stock kernel', len(raw), 'extent', extent, 'generic ramdisk', rr)
PY

TARGET=(--target=aarch64-none-elf)
CFLAGS=(-O2 -ffreestanding -fno-builtin -fno-jump-tables -nostdlib -fno-pic
	-mgeneral-regs-only -mstrict-align -fno-stack-protector -Wall -Wextra
	-DBOARD_DALI -DDALI_PAYLOAD_PROBE -DDALI_PAYLOAD_PROBE_PARSE_ONLY
	-DDALI_RAMLOG -DDALI_RAMLOG_PROBE -DDALI_ANDROID_CHAIN)
for f in start.S shim.c console.c ramlog.c mmu.c fdt.c loader.c ufs.c android_chain.c; do
	clang "${TARGET[@]}" "${CFLAGS[@]}" -c "$f" -o "$OUT/${f%.*}.o"
done
clang "${TARGET[@]}" "${CFLAGS[@]}" -c "$OUT/stock.S" -o "$OUT/stock.o"
clang "${TARGET[@]}" "${CFLAGS[@]}" -static -fuse-ld=lld -T shim.ld \
	-Wl,--build-id=none -Wl,-N \
	"$OUT/start.o" "$OUT/shim.o" "$OUT/console.o" "$OUT/ramlog.o" \
	"$OUT/mmu.o" "$OUT/fdt.o" "$OUT/loader.o" "$OUT/ufs.o" \
	"$OUT/android_chain.o" "$OUT/stock.o" -o "$OUT/chain-probe.elf"
llvm-objcopy -O binary "$OUT/chain-probe.elf" "$OUT/chain-probe.Image"
end_hex=$(llvm-nm "$OUT/chain-probe.elf" | awk '$3 == "_end" { print $1 }')
test -n "$end_hex"
truncate -s "$((16#$end_hex))" "$OUT/chain-probe.Image"
python3 - "$OUT" <<'PY'
import json
import struct
import sys
from pathlib import Path
p = Path(sys.argv[1]); meta = json.loads((p / 'stock-meta.json').read_text())
size = (p / 'chain-probe.Image').stat().st_size
if size < meta['kernel_extent'] or 0x80000000 + size >= 0x87c80000:
    raise SystemExit('Bundle does not reserve the original kernel extent or overlaps DTB')
print('Bundle Image bytes', size, 'high relocation end', hex(0x200000000 + size))
PY
lz4 -l -12 -f "$OUT/chain-probe.Image" "$OUT/chain-probe.Image.lz4"
python3 - "$OUT" <<'PY'
import json
import subprocess
import sys
from pathlib import Path
p = Path(sys.argv[1]); meta = json.loads((p / 'stock-meta.json').read_text())
subprocess.run(['mkbootimg', '--header_version', '4', '--kernel', str(p / 'chain-probe.Image.lz4'),
    '--pagesize', '4096', '--cmdline', meta['cmdline'], '-o', str(p / 'dali-chain-probe-boot.img')], check=True)
if (p / 'dali-chain-probe-boot.img').stat().st_size > 64 << 20:
    raise SystemExit('Boot image exceeds stock partition')
PY
ls -lh "$OUT/chain-probe.Image" "$OUT/dali-chain-probe-boot.img"
