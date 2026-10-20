#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"

OUT=out-dali
TARGET=(--target=aarch64-none-elf)
CFLAGS=(-O2 -ffreestanding -fno-builtin -nostdlib -fno-pic -mgeneral-regs-only
	-mstrict-align -fno-stack-protector -Wall -Wextra -DBOARD_DALI)
mkdir -p "$OUT"

for f in start.S dali_probe.c console.c fdt.c; do
	clang "${TARGET[@]}" "${CFLAGS[@]}" -c "$f" -o "$OUT/${f%.*}.o"
done

clang "${TARGET[@]}" "${CFLAGS[@]}" -static -fuse-ld=lld -T shim.ld \
	-Wl,--build-id=none -Wl,-N \
	"$OUT/start.o" "$OUT/dali_probe.o" "$OUT/console.o" "$OUT/fdt.o" \
	-o "$OUT/shim.elf"
llvm-objcopy -O binary "$OUT/shim.elf" "$OUT/Image"
end_hex=$(llvm-nm "$OUT/shim.elf" | awk '$3 == "_end" { print $1 }')
test -n "$end_hex"
truncate -s "$((16#$end_hex))" "$OUT/Image"

mkbootimg --header_version 4 --kernel "$OUT/Image" --pagesize 4096 \
	--cmdline "console=null" -o "$OUT/dali-probe-boot.img"

ls -lh "$OUT/Image" "$OUT/dali-probe-boot.img"
