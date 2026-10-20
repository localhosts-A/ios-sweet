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

## Parse-only diagnostics (2026-10-21)

The simple legacy-LZ4 probe powers off after about 20 seconds even though the
panel retains the handset logo. This confirms early shim execution, timer
access and PSCI system-off; visible framebuffer updates remain unconfirmed.
The active panel is a DSC command-mode panel in the runtime FDT. Missing
refresh is a possible cause of the retained logo, not an established diagnosis.

A parse-only build reads the concatenated initrd and validates the payload but
skips all writes to XNU segment/ADT destinations and never enters XNU. It also
installs exception vectors for EL1 or EL2 and reads exception registers for
the current EL. Linux permits either entry EL; this does not establish that
XNU's entry code can accept EL2.

```sh
# Original checkpoints: errors 10–80s, success 90s, exception 110s.
PROBE_MODE=stages bash shim/build-dali-payload-probe.sh
# Validation reason codes below (default mode).
bash shim/build-dali-payload-probe.sh
```

Deadlines are measured from shim_main entry, excluding bootloader time. Earlier
40/50-second results used a shared exception delay and included processing
time, so they did not identify a specific returned loader error. The corrected
checkpoint image (`c33c47b6…e851ce2`) powered off at about 60 seconds: the base
SWEETXNU header passed but payload_validate() rejected metadata/ranges.
The same actual ARM64 binary passes under QEMU at EL2 with the reconstructed
73,915,162-byte initrd. An injected BRK reaches the distinct 110-second
exception path. The target-only difference is not yet understood.

The validation-reason probe retains every validation condition:

| Shutdown deadline | Result |
| --- | --- |
| 10 seconds | Header fields, entry bounds, ADT offset or physical-range overflow |
| 20 seconds | Kernel span, ADT size or ADT file bounds |
| 30 seconds | Source initrd overlaps the proposed XNU RAM interval |
| 40 seconds | Boot-arguments alignment/space checks |
| 50 seconds | A segment's virtual range or memory/file size relation |
| 60 seconds | A segment's file offset or file range |
| 70 seconds | A segment's destination memory bounds |
| 80 seconds | Another loader error outside payload_validate() |
| 90 seconds | Complete parse succeeds |
| 110 seconds | Exception handler |

The validation-reason image (`fbb4d929…c0ad3e2`) shut down 57 seconds after
logo appearance, consistent with the 60-second segment-file rejection path.
Both originals were restored, Android returned, and root-side hashes matched.
This remains a timing inference rather than a captured error string.
`PROBE_MODE=files bash shim/build-dali-payload-probe.sh` further separates
file-offset addition overflow (10s), offsets before the metadata end (30s),
and file ends beyond the payload size (50s); other errors are 80s, success
90s and exceptions 110s. Moving the deadline also checks timing causality.

The file-check image (`c95fcfc4…21fefeb`) shut down after about 52 seconds,
consistent with its 50-second file-end-bounds path. The originals were restored
and root-side hashes matched. Local payload bounds are valid, so a separate
comparison probe checks size and all metadata bytes against the exact packaged
source, then stops before destination writes or XNU entry:

```sh
PROBE_MODE=metadata PAYLOAD_REFERENCE=out-dali/gh-39425210357/dali-payload.bin \
    bash shim/build-dali-payload-probe.sh
```

Metadata-mode deadlines: size mismatch 10s, header mismatch 30s, segment-table
mismatch 50s, exact match 70s, earlier loader error 80s, exception 110s.

After the corrected checkpoint test, both stock partitions were restored and
root-side hashes matched their saved full-partition backups exactly. No
userdata, super or GPT changes occurred. Never infer XNU entry from a timed
parse-only success.

## Improving observability (2026-10-21)

**Timing-only results are provisional.** The metadata comparison probe shut
down after 57 seconds, not a clean outcome-code match. An absolute deadline
also stops immediately if parsing already exceeded it, and external shutdown
has not been ruled out. The previously inferred validation error is not a
captured error string. Both stock partitions are restored and their hashes
have been verified. User requests visible output and better diagnostics.

Confirmed display source is Xiaomi's
[`mtk_kernel_device_modules`, `bsp-dali-v-oss`](https://github.com/MiCode/mtk_kernel_device_modules/tree/e45c3199537fbba9aae5fb06c31fc8dbd3c1dcee).
The matching panel driver is
[`panel-o12-42-02-0a-dsc-cmd.c`](https://github.com/MiCode/mtk_kernel_device_modules/blob/e45c3199537fbba9aae5fb06c31fc8dbd3c1dcee/drivers/gpu/drm/panel/panel-o12-42-02-0a-dsc-cmd.c).
It selects four DSI lanes, RGB888, LPM/no-EOT/non-continuous-clock flags and
DSC parameters. Runtime `/proc/mtkfb` confirms this panel at 1280x2772,
CMDQ enabled and command-mode transactions. Display remains unimplemented in
the shim: writes to presumed splash memory have not visibly refreshed it.
Read the vendor `mediatek_v2/mtk_dsi.c`, `mtk_drm_crtc.c` and
`mtk_disp_ovl_exdma.c` for a minimal retained-configuration refresh path;
older mainline DSI offsets alone do not validate a Dali sequence.

Android has an active ramoops backend in reserved RAM:

| Parameter | Read from the running device |
| --- | --- |
| Base / total | 0xaec00000 / 0x410000 |
| Console / pmsg | 0x200000 each |
| Record / ftrace | 0x1000 each |
| ECC / mem_type | 0 / 0 |

In the matching Linux `fs/pstore/ram.c`, dump records precede console, pmsg,
and ftrace. This gives a candidate console base 0xaec0f000. Buffer header is
12 bytes: signature `0x43474244`, 32-bit start and size, then data. Confirm
warm-reset retention before implementing a shim writer. Power-off cannot be
used as a RAM-retention workflow. A benign marker through `/dev/pmsg0` and
stock Android warm reboot can check the backend without flashing a probe.

Six UARTs are registered at 0x16000000 through 0x16050000; ttyS3 is active.
Board pin routing and external access remain unknown. These UARTs are not
established as accessible through the attached USB cable.

### Reserved log retention and no-reset handoff

A benign /dev/pmsg0 marker was recovered after a stock Android warm reboot.
The same test through fastboot returned an empty pstore. Therefore a
firmware-resetting ramlog probe is insufficient for the present restore path.
Stock CONFIG_KEXEC/CONFIG_KEXEC_FILE are disabled and /dev/mem is absent.

`bash shim/build-dali-chain-probe.sh` builds a diagnostic from the full stock
backups. It embeds the exact stock raw kernel and generic ramdisk, preserves
the stock boot cmdline, and produces a legacy-LZ4 boot-v4 image. The shim
relocates the bundle to the verified high-RAM candidate; after parse-only
payload diagnostics, it restores the stock generic ramdisk in RAM, moves
LK's bootconfig trailer intact, updates chosen initrd-end, copies original
Linux to 0x80000000 and jumps without invoking firmware. Linux should then
expose the shim log as console-ramoops-0. Restore original boot/init_boot from
root ADB immediately after collecting logs. If the chain fails, manual
fastboot restoration remains the recovery path, but loses the RAM log.

The opt-in logger checks the exact reserved-memory region in the live FDT
before initializing the stock-format console header (including after fastboot
cleared it). No storage access exists in the shim. It limits output to 16KiB
and commits header counters after data with a DSB. In this diagnostic,
framebuffer writes are skipped so the unconfirmed panel path cannot hide
or interfere with the RAM log. XNU destination writes and entry stay disabled.

Actual ARM64 QEMU checks relocation, valid and invalid payload outcomes,
exact stock kernel/generic-ramdisk copies, bootconfig preservation, chosen
end correction and x0=FDT/x1..x3=0 at the original kernel entry. This confirms
the handoff mechanics in emulation, not Android booting on Dali hardware.

## Work still needed for an iOS boot

- Diagnose the target-only payload validation failure. Shim execution is
  confirmed by automatic shutdown, while XNU entry remains unconfirmed.
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
