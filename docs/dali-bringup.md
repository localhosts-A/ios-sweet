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
in `vendor_boot`:

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

The current local payload still uses the existing Sweet XNU binary with the
Dali ADT. Patch `0082-dali-dynamic-framebuffer.patch` makes the early XNU
diagnostics use the framebuffer geometry from `boot_args.Video`; it has been
syntax-checked, but no kernel has yet been rebuilt from that patch. Do not
treat the current package as a bootable Dali XNU image.

The host build uses Clang/LLD. Sweet remains the default target and keeps its
boot v2 packaging. `shim/out-dali/` and `shim/out/` are ignored build output.

## Boot test and recovery record

This bootloader rejects `fastboot boot` with `unknown command`, but fastboot
reports that flashing is allowed. Before testing, both 64 MiB boot slots were
saved under `backup/dali-25060RK16C-20261020/` and verified against hashes read
from the phone. The probe was written only to active `boot_b`; after the test,
the saved `boot_b.img` was restored. Android booted afterward and the restored
partition hash matched the backup. No other partition was written.

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
`init_boot`. That mismatch is a plausible cause of the logo-only result, but
it is not confirmed as the only cause. This turn built the corrected
kernel-only `boot` and separate `init_boot` images locally; neither was
flashed.

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

- Rebuild XNU from the current patch series, including dynamic early framebuffer
  diagnostics and the Dali UFS LUN property. No Dali-specific XNU build exists
  yet; the local init_boot package uses the existing Sweet kernel artifact.
- Validate the MTK UFSHCI takeover and DMA behavior. The existing XNU block
  driver assumes the standard UFSHCI register interface; the ADT address alone
  does not prove that it works on MT6991. Dali's user LUN is 2, so the ADT now
  supplies `lun=2` and patch `0028` reads it instead of hard-coding LUN 0.
- Validate XNU memory allocation against the reserved-memory map and the
  candidate high-RAM interval.
- Keep the Qualcomm SPMI/RPMh/GENI touch and button drivers disabled on Dali.
  Dali uses a MediaTek SPI touchscreen and a different panel pipeline.
- Confirm the Dali framebuffer text path; only the handset logo was observed
  during the XNU RAM-disk test.
- Rebuild XNU with the Dali UFS LUN property, and distinguish kernel entry from
  root mount and PID 1 before creating any userdata partition.
