#!/bin/bash
# Build the shim. TARGET_BOARD=sweet emits boot v2; TARGET_BOARD=dali emits boot v4.
set -euo pipefail
cd "$(dirname "$0")"
TARGET_BOARD=${TARGET_BOARD:-sweet}
TARGET=()
LINKFLAGS=()
DISPLAY_OBJECTS=()
DISPLAY_SOURCES=()
case "$TARGET_BOARD" in
	sweet)
		CC=aarch64-linux-gnu-gcc
		OBJCOPY=aarch64-linux-gnu-objcopy
		READELF=aarch64-linux-gnu-readelf
		NM=aarch64-linux-gnu-nm
		CFLAGS=(-O2 -ffreestanding -nostdlib -fno-pic -mgeneral-regs-only -mstrict-align -fno-stack-protector -Wall -Wextra)
		;;
	dali)
		CC=clang
		OBJCOPY=llvm-objcopy
		READELF=llvm-readelf
		NM=llvm-nm
		TARGET=(--target=aarch64-none-elf)
		LINKFLAGS=(-fuse-ld=lld)
		CFLAGS=(-O2 -ffreestanding -fno-builtin -fno-jump-tables -nostdlib -fno-pic -mgeneral-regs-only -mstrict-align -fno-stack-protector -Wall -Wextra -DBOARD_DALI -DDALI_DISPLAY)
		DISPLAY_SOURCES=(dali_display.c)
		DISPLAY_OBJECTS=(out/dali_display.o)
		;;
	*)
		echo "unsupported TARGET_BOARD=$TARGET_BOARD" >&2
		exit 2
		;;
esac
mkdir -p out
for f in start.S shim.c console.c mmu.c fdt.c loader.c ufs.c "${DISPLAY_SOURCES[@]}"; do
	"$CC" "${TARGET[@]}" "${CFLAGS[@]}" -c "$f" -o "out/${f%.*}.o"
done
# The image runs wherever ABL puts it with no relocation pass: only the
# header's image_size (start.o, linked at 0) may be an absolute address.
abs=$("$READELF" -r out/shim.o out/console.o out/mmu.o out/fdt.o out/loader.o out/ufs.o "${DISPLAY_OBJECTS[@]}" | grep -c ABS64 || true)
[ "$abs" = 0 ] || { echo "error: $abs absolute relocations in C objects" >&2; exit 1; }

"$CC" "${TARGET[@]}" "${CFLAGS[@]}" "${LINKFLAGS[@]}" -static -T shim.ld -Wl,--build-id=none -Wl,-N \
	out/start.o out/shim.o out/console.o out/mmu.o out/fdt.o out/loader.o out/ufs.o "${DISPLAY_OBJECTS[@]}" -o out/shim.elf
$OBJCOPY -O binary out/shim.elf out/Image
# ABL copies the file; pad to image_size so .bss/stacks exist as zeroed bytes.
end_hex=$("$NM" out/shim.elf | awk '$3=="_end"{print $1}')
[ -n "$end_hex" ] || { echo "error: _end symbol missing" >&2; exit 1; }
size=$((16#$end_hex))
truncate -s "$size" out/Image
KERNEL_IMAGE=out/Image
if [ "$TARGET_BOARD" = dali ]; then
	# This phone uses the Android 13+ GKI layout: boot is kernel-only;
	# generic ramdisk and the Sweet payload belong in init_boot. LK expects
	# the Linux kernel in the legacy LZ4 format used by the stock boot image.
	lz4 -l -12 -f out/Image out/Image.lz4
	KERNEL_IMAGE=out/Image.lz4
	if [ -n "${1:-}" ]; then
		echo "Dali boot.img is kernel-only; package payload separately with build-dali-initboot.sh" >&2
		exit 2
	fi
	mkbootimg --header_version 4 --kernel "$KERNEL_IMAGE" --pagesize 4096 \
		--cmdline "console=null" -o out/shim-boot.img
else
	# Sweet uses the older boot v2 layout with the XNU payload in ramdisk.
	if [ -n "${1:-}" ]; then
		cp "$1" out/ramdisk.bin
	else
		(cd out && rm -rf rd && mkdir rd && cd rd && find . | cpio -o -H newc --quiet | gzip -9 > ../ramdisk.bin)
	fi
	mkbootimg --header_version 2 --kernel "$KERNEL_IMAGE" --ramdisk out/ramdisk.bin \
		--dtb ../dtb/boot_unpacked/dtb --base 0 --kernel_offset 0x8000 --ramdisk_offset 0x1000000 \
		--tags_offset 0x100 --dtb_offset 0x1f00000 --pagesize 4096 \
		--os_version 17.0.0 --os_patch_level 2035-06 \
		--cmdline "console=null" -o out/shim-boot.img
fi
# Host render of the Sweet console layout (the host has no Dali MMIO).
if [ "$TARGET_BOARD" = sweet ]; then
	gcc -O1 -Wall -o out/host_test console.c host_test.c && ./out/host_test
fi
ls -l out/Image out/shim-boot.img
