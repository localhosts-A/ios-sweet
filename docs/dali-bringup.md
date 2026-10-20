# Dali / MT6991 bring-up

This is an early platform port for the Redmi K80 Ultra (`dali`, MT6991). It is
not a bootable iOS image yet. The existing XNU patches and iPhone X userland
remain the starting point; this file records the Dali-specific work and its
current limits.

## Confirmed device facts

| Item | Dali value | Source |
| --- | --- | --- |
| SoC / product | MT6991 / `25060RK16C` | Android properties |
| Current boot slot | `b` | fastboot |
| Bootloader | unlocked | fastboot `getvar unlocked` |
| RAM candidate for XNU | `0x200000000` + `0x13fe00000` bytes | live FDT and `/proc/iomem`; verify before using for a real kernel |
| GICv3 distributor | `0x0c400000`, size `0x40000` | live FDT |
| GICv3 redistributor | `0x0c440000`, size `0x200000` | live FDT |
| Generic timer | 13 MHz; virtual timer PPI 11 = GIC INTID 27 | live FDT |
| UFSHCI | `0x16810000`, size `0x2a00` | live FDT |
| UFS logical unit | `2` | Linux block-device path `target0:0:0:2` |
| Boot framebuffer | `0xfc990000`, size `0x3660000` | live FDT |
| Display dimensions | 1280 × 2772; stride 5120 assumed | Android display size and live FDT |

The stock chain follows the Android 13+ GKI layout: `boot_b` is a v4
kernel-only image, `init_boot_b` carries the generic LZ4 ramdisk, and
`vendor_boot_b` carries the DTB and vendor ramdisk fragments. The stock
`init_boot_b` and `vendor_boot_b` both use v4 headers. See the [AOSP generic
boot](https://source.android.com/docs/core/architecture/partitions/generic-boot)
and [vendor boot](https://source.android.com/docs/core/architecture/partitions/vendor-boot-partitions)
layout documentation.

The current ADT starts XNU on CPU 0 only and includes the D22AP `/product`
node extracted from the iPhone X IPSW. It keeps the `xiaomi,sweet`
compatibility entry so the existing Sweet XNU platform expert can match, while
adding `xiaomi,dali` and `mediatek,mt6991` for the board identity.
For the first XNU attempt, use boot arguments
`cpus=1 sweettouch=0 sweetkeys=0 sweetbattery=0`. The existing Sweet input and
PMIC/RPMh paths contain Qualcomm-specific MMIO and must remain disabled on Dali.

## Local builds

Build the Dali ADT:

```sh
python3 tools/mkadt.py shim/out-dali/dali.adt --board dali
```

Build the Dali shim as a kernel-only Android boot image v4. The generic
ramdisk is separate in `init_boot`; the vendor DTB and vendor ramdisk remain
in `vendor_boot`. MTK LK expects the Linux kernel compressed with the same
legacy LZ4 format as the stock image, so the Dali build compresses `Image`
before packaging:

```sh
TARGET_BOARD=dali bash shim/build.sh
```

Build an XNU payload without an embedded root image, then package it into the
generic ramdisk in `init_boot`:

```sh
python3 tools/mkpayload.py xnu/out/kernel.development.sweet shim/out-dali/dali.adt \
    shim/out-dali/dali-payload.bin --cmdline 'debug=0xc cpus=1 keepsyms=1'
bash shim/build-dali-initboot.sh out-dali/dali-payload.bin
```

`build-dali-initboot.sh` makes a newc CPIO containing `sweet.payload`, wraps
it in a 64 KiB independent-block LZ4 frame, and adds an AVB v1 hash footer
using algorithm `NONE` and the stock init_boot properties. The current
8 MiB `init_boot` partition accepts a 7.47 MiB ramdisk plus the v4 header and
footer. `shim/loader.c` removes an optional Android bootconfig trailer,
finds the final LZ4 frame from the bootloader's concatenated initrd, extracts
the CPIO entry, and streams XNU segments into their final memory addresses.

The XNU build completed successfully in GitHub Actions run
[39425210357](https://github.com/localhosts-A/ios-sweet/actions/runs/39425210357)
from commit `e811978` using macOS 13.5, DEVELOPMENT, and SWEET. The resulting
`kernel.development.sweet` is 17,206,368 bytes with SHA-256
`5a5dbfb4c72fc71f49b24ec92de6005cb407fcfdd7acdc097b4147c25cce2a42`.
The workflow applies all patches, including
`0082-dali-dynamic-framebuffer.patch`, and the XNU build step succeeded.
This is a Dali-configured XNU build, not proof that the kernel boots on the
phone; UFS takeover, framebuffer handoff, and memory-map assumptions still
need an on-device boot test.

The new kernel was packaged with the Dali ADT as a 16,608,064-byte
`sweet.payload`. The paired `boot` image is kernel-only Android boot v4, and
the `init_boot` image is exactly 8 MiB with a 7,467,394-byte LZ4 CPIO ramdisk
and AVB v1 hash footer using algorithm `NONE`. The CPIO's `sweet.payload`
entry was extracted and compared byte-for-byte with the payload source. The
pair was tested temporarily on the device and then restored; it did not
confirm an XNU boot.

The host build uses Clang/LLD. Sweet remains the default target and keeps its
boot v2 packaging. `shim/out-dali/` and `shim/out/` are ignored build output.

## Boot test and recovery record

This bootloader rejects `fastboot boot` with `unknown command`, but fastboot
reports that flashing is allowed. Before the paired test, `boot_b` (64 MiB)
and `init_boot_b` (8 MiB) were saved under
`backup/dali-25060RK16C-pairtest-20261020/` and verified against hashes read
from the phone. The probe was written only to active `boot_b`; after each
test, the saved `boot_b.img` was restored. Android booted afterward and the
restored partition hashes matched the backups. No other partition was written.

The probe's display was not observed, so the x0 FDT value has not yet been
visually confirmed. The phone disconnected from USB about 22 seconds after the
probe reboot, consistent with its PSCI system-off timer.

A test packaged the existing Sweet XNU kernel artifact with the Dali ADT and a
32 MiB HFS+ RAM disk, then temporarily flashed that 50,352,128-byte boot-v4
image to `boot_b`. The user saw only the handset logo; PID 1 and XNU entry were
not confirmed. The original `boot_b` backup was restored, and its on-device
hash again matched the saved image. The tests did not write `userdata`,
`super`, or the partition table.

The earlier XNU test put the custom payload in `boot`'s ramdisk, which does
not match this phone's GKI layout: the generic ramdisk is loaded from
`init_boot`. The corrected kernel-only `boot` and separate `init_boot` images
were flashed together for a temporary test. The first attempt used an
uncompressed Linux kernel; `expdb` recorded `No magic number of compression is
found!` after LK began decompression. The Dali build scripts were then changed
to use legacy LZ4, matching the stock kernel magic `02 21 4c 18`. The second
paired attempt logged successful kernel decompression and
`lk finished --> jump to linux kernel 64Bit`, but the handset still showed
only its logo. No XNU entry or framebuffer text was confirmed.

A separate 3,716-byte compressed early probe also showed only the handset
logo. The stock `boot_b` and `init_boot_b` are currently restored and were
read back through root ADB. Their hashes match the saved backups:
`boot_b` is `6dc40de74fa7de4f435c513047c51fe10634c1891666031039c52a08b27a3cd5`;
`init_boot_b` is `08794a432befcf0504ee4933a51260cb6ccc4785b69f345c4b5968e8e6053d2d`.

The stock `boot_b` contains an AVB footer v1 with an OEM `SHA256_RSA4096`
vbmeta descriptor. Its Android boot-v4 `boot_signature` field is zero; that
field is separate from the AVB footer. I added a test AVB footer signed with a
local RSA-4096 key and matching the stock boot properties. LK logged that the
key did not match the OEM chain, but also logged `img_auth_required = 0` and
`avb_ret = 0`, then continued to the logo. The result remained logo-only.
There is no Xiaomi private signing key available, so the local test signature
cannot be trusted by a locked OEM chain.

MTK `expdb` is a 128 MiB partition and contains LK/AVB records; `/sys/fs/pstore`
was empty. The logs include `invalid boot image header` around the attempts,
but the same message appears in older records too. A later build corrected the
GKI boot-v4 OS version and patch-level fields to zero, matching stock; that
image also remained logo-only.

## Work still needed for an iOS boot

- Trace the shim's first instructions and framebuffer initialization. LK
  decompresses and jumps to the corrected kernel, but neither the full image
  nor the minimal early probe displayed its diagnostic text.
- Validate the MTK UFSHCI takeover and DMA behavior. The existing XNU block
  driver assumes the standard UFSHCI register interface; the ADT address alone
  does not prove that it works on MT6991. Dali's user LUN is 2, so the ADT now
  supplies `lun=2` and patch `0028` reads it instead of hard-coding LUN 0.
- Validate XNU memory allocation against the reserved-memory map and the
  candidate high-RAM interval.
- Keep the Qualcomm SPMI/RPMh/GENI touch and button drivers disabled on Dali.
  Dali uses a MediaTek SPI touchscreen and a different panel pipeline.
- Confirm the Dali framebuffer text path; only the handset logo was observed
  during the XNU pair and the compressed probe.
- Distinguish kernel entry from root mount and PID 1 before creating any
  userdata partition.
