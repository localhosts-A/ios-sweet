# Dali USB OTG root bring-up

## Current status

The static PID1 probe runs from an HFSX memory disk. **XNU USB host and USB
mass-storage boot are not implemented or verified yet.** The existing
`sweet_usb.c` is a Qualcomm DWC3 device/gadget driver, not a MediaTek host
controller. A root image on a USB stick alone does not add this support.

The first native platform stage now shares PMIF channel2 between RTC and USB
under one transaction lock. `daliusb=1` enables an MT6379 identity check and
a bounded read-only Type-C/charger/repeater status service. It does not
switch roles, enable connector power or touch a gated xHCI block. Local
compilation has passed; hardware verification of this new stage is pending.

The intended path is the phone's internal boot/init_boot loader, then the
native Dali USB host controller, a USB mass-storage block device and its HFSX
root. Cold boot directly from a stick through the stock bootloader has not
been established. Keep the original boot/init_boot pair and recovery path.

## Hardware facts

Live stock Android FDT, cross-checked against MiCode's device-module source
at `e45c3199537fbba9aae5fb06c31fc8dbd3c1dcee`:

| Component | Dali configuration |
| --- | --- |
| SSUSB0 | `mediatek,mt6991-mtu3`, OTG, maximum high-speed |
| Device MAC / IPPC | `0x16701000 / 0x2e00`, `0x16703e00 / 0x100` |
| xHCI host | `0x16700000 / 0x1000`, SPI600 (INTID632) |
| USB2 XS PHY | `0x16730000 / 0x400`, 26MHz reference |
| DT repeater references | NXP at I2C address `0x4f`, MT6379 at SPMI SID14, base `0xd00` |
| Main clocks | VLP `sys_ck`, `host_ck`; 48MHz `frmcnt_ck` |
| Power domains | Separate USB2 and USB3 domains |
| Type-C and VBUS | MT6379 TCPC/5V regulator, plus board charge-pump routing |

Native Android device-mode baseline: IPPC PW_CTRL0=`10411020`,
CTRL1=`1`, CTRL2=`0`, CTRL3=`1`, STS1=`01013d0f`, U2_CTRL0=`1848`,
HW_ID=`20180724`. These are observations, not values to write blindly.

Stock Android currently publishes a USB2 XS PHY and an MT6379 repeater PHY.
It does **not** publish an NXP repeater PHY, although I2C6-004f has a bound
driver. The vendor NXP probe returns success even if its chip-ID check fails,
without publishing a PHY; XS PHY treats unavailable repeaters as optional.
Thus the DT list does not prove both repeaters are populated or active.
Check actual PHY providers and board hardware before implementing NXP I2C.

## Verified stock Android OTG baseline

With the written stick and the user's ordinary phone-powered OTG adapter:

- Data role `host`, power role `source`, configured regulator5V/1.4A;
  the reported VBUS reading was approximately5.003V.
- DataTraveler enumerated at480Mbps, VID:PID `1f75:0903`.
- USB disk249905152sectors, partition1start2048/size249903071sectors,
  matching the stick written on the PC.
- Host IPPC CTRL1=`0`, CTRL2=`1`, STS1=`01013f0f`, U3_CTRL0=`20c`,
  U2_CTRL0=`a4c`; the corresponding device-mode values are above.
- No xHCI capability/operational debug views were exposed in this capture.
  DMA64 capability is still to be checked when the native controller starts.

This verifies the adapter, phone power and stock Linux enumeration, not
XNU USB support or execution of the stick's Apple userspace. The charger
power-supply `typec_mode` string said "Nothing attached" despite the actual
Type-C class roles and enumerated block device; prefer the latter evidence.

### Source references

All paths below are within
[MiCode/mtk_kernel_device_modules](https://github.com/MiCode/mtk_kernel_device_modules/tree/e45c3199537fbba9aae5fb06c31fc8dbd3c1dcee):

- `drivers/usb/mtu3/{mtu3_plat.c,mtu3_dr.c,mtu3_host.c,mtu3_hw_regs.h}`
- `drivers/misc/mediatek/usb/usb_xhci/{xhci-mtk.c,xhci-mtk.h}`
- `drivers/phy/mediatek/phy-mtk-xsphy.c`
- `drivers/phy/mediatek/phy-mtk-{mt6379,nxp}-eusb2-repeater.c`
- `drivers/misc/mediatek/extcon/extcon-mtk-usb.c`
- `drivers/power/supply/mt6379-charger.{c,h}`
- `drivers/misc/mediatek/typec/tcpc/tcpc_mt6379.c`

The vendor extcon path checks `cp_master`, programs reverse charge-pump mode
and ACDRV/gates, and controls the MT6379 regulator. The regulator enable path
also has ADC checks and a protected charger-register sequence. The user has
an ordinary phone-powered OTG adapter. Do not assume an externally powered
hub, equate a port-power bit with connector VBUS, force source mode against
an attached source, or reuse Qualcomm addresses.

## Native host baseline capture

`tools/dali-usb-host-capture.sh` runs as root on stock Dali Android while the
ADB cable is disconnected. It waits up to ten minutes for the kernel's
existing USB role to become host and records three bounded snapshots of
the existing IPPC/xHCI, clock, regulator, Type-C, USB and block-device views.
It does not change USB roles, registers, regulators, filesystems or mounts.
Root debugfs must already be mounted normally at `/sys/kernel/debug`.

Push the script to `/data/local/tmp`, run it with a fresh output directory
using root `nohup`, disconnect the PC cable, and attach the USB stick through
the OTG adapter. Leave it connected for at least 20 seconds. If Android asks
to format the HFSX stick, decline. Reconnect the PC cable and retrieve the
output under the ignored `research/` tree. Hardware dumps stay out of Git.

## Root media

The requested host `/dev/sdc1` was identified as the removable Kingston
119.2GiB partition. Its approximately 1.1GiB of FAT32 files were archived and
compared with `tar --compare`; SHA256, partition-table text, disk prefix and
partition prefix backups are in ignored `backup/usb-root-20261021/`.
**This is a file backup plus metadata, not a full raw partition backup.**

The 16GiB `iOSRoot` image contains real Apple launchd and SpringBoard. The
original journaled image is retained. An independent USB bring-up copy has
its empty journal disabled and passed both full repair and independent
read-only `fsck.hfsplus` checks. Journaled and original copies are retained;
production writable-root journaling policy remains separate work.

The guarded writer checks device identity, removable USB transport, target
size, saved FAT UUID, backup hashes and source filesystem before unmounting.
It writes only the specified partition, fsyncs, flushes buffers and directly
reads back all 16GiB for SHA256 comparison. It also verifies the disk prefix
and partition table remain unchanged. `write-state.json` and `write.log` in
the backup directory are authoritative for write completion; do not treat
the presence of an image or a partially written filesystem as success.

## Remaining driver work

1. Compare Android device/host baselines and port domain/clock, Type-C/VBUS
   and repeater transitions with bounded failure and shutdown cleanup.
2. Coordinate PMIF/SPMI ownership with the existing RTC driver; do not race
   independent users of AP software channel2.
3. Implement xHCI DMA mapping/coherency, rings, completion and port events.
4. Enumerate the device and implement mass-storage BOT/SCSI reads, including
   reset recovery, bounded retries and media removal handling.
5. Expose read-only block media/partition root selection, verify HFSX reads
   on the actual stick, then boot it. Add writes only with correct cache and
   filesystem semantics. No existing USB-root boot argument is functional.
