#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"

OUT=out-dali
TARGET=(--target=aarch64-none-elf)
CFLAGS=(-O2 -ffreestanding -fno-builtin -fno-jump-tables -nostdlib -fno-pic
	-mgeneral-regs-only -mstrict-align -fno-stack-protector -Wall -Wextra
	-DBOARD_DALI -DDALI_PAYLOAD_PROBE -DDALI_PAYLOAD_PROBE_PARSE_ONLY)
case "${PROBE_MODE:-validation}" in
	stages) ;;
	validation) CFLAGS+=(-DDALI_PAYLOAD_PROBE_VALIDATE) ;;
	files) CFLAGS+=(-DDALI_PAYLOAD_PROBE_VALIDATE -DDALI_PAYLOAD_PROBE_FILES) ;;
	ramlog) CFLAGS+=(-DDALI_RAMLOG -DDALI_RAMLOG_PROBE) ;;
	metadata)
		test -n "${PAYLOAD_REFERENCE:-}" || { echo 'PAYLOAD_REFERENCE is required' >&2; exit 1; }
		mkdir -p "$OUT"
		python3 - "$PAYLOAD_REFERENCE" "$OUT/probe-expected-meta.h" <<'PY'
import struct
import sys
from pathlib import Path
b = Path(sys.argv[1]).read_bytes()
if len(b) < 680 or b[:8] != b'SWEETXNU':
    raise SystemExit('Invalid reference payload')
version, nsegs = struct.unpack_from('<II', b, 8)
if version != 1 or not 1 <= nsegs <= 32 or len(b) < 680 + 32 * nsegs:
    raise SystemExit('Invalid reference metadata')
meta = b[:680 + 32 * nsegs]
rows = [', '.join(f'0x{x:02x}' for x in meta[i:i + 16]) for i in range(0, len(meta), 16)]
Path(sys.argv[2]).write_text(
    f'#define EXPECTED_PAYLOAD_SIZE {len(b)}ULL\n'
    'static const uint8_t expected_payload_meta[] = {\n' +
    ',\n'.join(rows) + '\n};\n')
PY
		CFLAGS+=(-DDALI_PAYLOAD_PROBE_METADATA -I"$OUT") ;;
	*) echo 'PROBE_MODE must be stages, validation, files, metadata or ramlog' >&2; exit 1 ;;
esac
if [[ "${PROBE_MODE:-validation}" != ramlog ]]; then
	CFLAGS+=(-DDALI_PAYLOAD_PROBE_STAGED)
fi
mkdir -p "$OUT"

for f in start.S shim.c console.c ramlog.c mmu.c fdt.c loader.c ufs.c; do
	clang "${TARGET[@]}" "${CFLAGS[@]}" -c "$f" -o "$OUT/${f%.*}.o"
done

clang "${TARGET[@]}" "${CFLAGS[@]}" -static -fuse-ld=lld -T shim.ld \
	-Wl,--build-id=none -Wl,-N \
	"$OUT/start.o" "$OUT/shim.o" "$OUT/console.o" "$OUT/mmu.o" \
	"$OUT/fdt.o" "$OUT/loader.o" "$OUT/ufs.o" "$OUT/ramlog.o" -o "$OUT/payload-probe.elf"
llvm-objcopy -O binary "$OUT/payload-probe.elf" "$OUT/payload-probe.Image"
end_hex=$(llvm-nm "$OUT/payload-probe.elf" | awk '$3 == "_end" { print $1 }')
test -n "$end_hex"
truncate -s "$((16#$end_hex))" "$OUT/payload-probe.Image"
lz4 -l -12 -f "$OUT/payload-probe.Image" "$OUT/payload-probe.Image.lz4"
mkbootimg --header_version 4 --kernel "$OUT/payload-probe.Image.lz4" \
	--pagesize 4096 --cmdline "console=null" \
	-o "$OUT/dali-payload-probe-boot.img"
ls -lh "$OUT/payload-probe.Image" "$OUT/dali-payload-probe-boot.img"
