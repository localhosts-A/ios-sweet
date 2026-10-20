#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"

OUT=out-dali
TARGET=(--target=aarch64-none-elf)
CFLAGS=(-O2 -ffreestanding -fno-builtin -fno-jump-tables -nostdlib -fno-pic
	-mgeneral-regs-only -mstrict-align -fno-stack-protector -Wall -Wextra
	-DBOARD_DALI -DDALI_PAYLOAD_PROBE -DDALI_PAYLOAD_PROBE_PARSE_ONLY \
	-DDALI_PAYLOAD_PROBE_STAGED)
case "${PROBE_MODE:-validation}" in
	stages) ;;
	validation) CFLAGS+=(-DDALI_PAYLOAD_PROBE_VALIDATE) ;;
	files) CFLAGS+=(-DDALI_PAYLOAD_PROBE_VALIDATE -DDALI_PAYLOAD_PROBE_FILES) ;;
	*) echo 'PROBE_MODE must be stages, validation or files' >&2; exit 1 ;;
esac
mkdir -p "$OUT"

for f in start.S shim.c console.c mmu.c fdt.c loader.c ufs.c; do
	clang "${TARGET[@]}" "${CFLAGS[@]}" -c "$f" -o "$OUT/${f%.*}.o"
done

clang "${TARGET[@]}" "${CFLAGS[@]}" -static -fuse-ld=lld -T shim.ld \
	-Wl,--build-id=none -Wl,-N \
	"$OUT/start.o" "$OUT/shim.o" "$OUT/console.o" "$OUT/mmu.o" \
	"$OUT/fdt.o" "$OUT/loader.o" "$OUT/ufs.o" -o "$OUT/payload-probe.elf"
llvm-objcopy -O binary "$OUT/payload-probe.elf" "$OUT/payload-probe.Image"
end_hex=$(llvm-nm "$OUT/payload-probe.elf" | awk '$3 == "_end" { print $1 }')
test -n "$end_hex"
truncate -s "$((16#$end_hex))" "$OUT/payload-probe.Image"
lz4 -l -12 -f "$OUT/payload-probe.Image" "$OUT/payload-probe.Image.lz4"
mkbootimg --header_version 4 --kernel "$OUT/payload-probe.Image.lz4" \
	--pagesize 4096 --cmdline "console=null" \
	-o "$OUT/dali-payload-probe-boot.img"
ls -lh "$OUT/payload-probe.Image" "$OUT/dali-payload-probe-boot.img"
