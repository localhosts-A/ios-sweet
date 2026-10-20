#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
if [ "$#" -ne 1 ]; then
	echo "usage: $0 SWEETXNU_PAYLOAD" >&2
	exit 2
fi

python3 ../tools/mkinitboot.py "$1" out-dali/dali-init_boot.img
unpack_bootimg --boot_img out-dali/dali-init_boot.img --out out-dali/initboot-unpacked >/dev/null
avbtool info_image --image out-dali/dali-init_boot.img > out-dali/initboot-avb.txt
ls -lh out-dali/dali-init_boot.img
