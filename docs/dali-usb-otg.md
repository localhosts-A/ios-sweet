# Dali USB OTG root bring-up

## Current status

The static PID1 probe runs from an HFSX memory disk. **XNU USB host and USB
mass-storage boot are not implemented or verified yet.** The existing
`sweet_usb.c` is a Qualcomm DWC3 device/gadget driver, not a MediaTek host
controller. A root image on a USB stick alone does not add this support.

The first native platform stage now shares PMIF channel2 between RTC and USB
under one transaction lock. `daliusb=1` enables an MT6379 identity check and
a bounded read-only Type-C/charger/repeater status service. It does not
switch roles, enable connector power or touch a gated xHCI block. The first hardware trial completed all 90 status samples without a visible
transaction error; the memory-root static PID1 also continued to run.
The original boot/init_boot partitions were then restored and their full
physical SHA256 values verified from rooted Android.

The intended path is the phone's internal boot/init_boot loader, then the
native Dali USB host controller, a USB mass-storage block device and its HFSX
root. Cold boot directly from a stick through the stock bootloader has not
been established. Keep the original boot/init_boot pair and recovery path.

## Verified native PMIC observation

Photo125352 from the CI39544763251 trial shows samples1,10,...90 with
`role=0a cc=10 power=08 otg=60 vrt=14 rx=60`, followed by
`bounded read-only capture ended`. The newest rows appear at the top after
framebuffer wrap. The end message is the intentional sampling bound.
Memory-root mount, BSD init, custom PID1 exec and periodic console writes
also completed. The UTC banner itself is outside this photo.

Using the exact vendor TCPCI and charger definitions:

| Report | Interpretation |
| --- | --- |
| ROLE_CTRL `0a` | DRP disabled, both CC terminations Rd (sink configuration) |
| CC_STATUS `10` | Not toggling, CC1 and CC2 status fields both zero/open |
| POWER_STATUS `08` | VBUS detection enabled, VBUS-present/source status bits clear |
| CHG_TOP2 `60` | Regulator OTG enable bit0 clear |
| Repeater `vrt=14 rx=60` | Raw configuration observations; not link/USB enumeration evidence |

These are controller reports, not an independent voltage measurement.
Attachment of the OTG adapter during this XNU trial was not confirmed by
the user. Do not infer a broken adapter from these values: the current
service never enables source mode. Shared PMIF reads are now demonstrated;
Type-C source policy, power routing and USB host/storage remain unfinished.

## Native charge-pump transport stage

Patches0094/0095 add an optional `dalicp=1` probe to the existing `daliusb=1`
service. It requires the Dali-only `cp-i2c-reg` property to identify I2C5 at
`0x120a0000/0x20000`. The stock SC858x driver is on I2C5 address `0x6e` and
requires identity byte `0x85` from register `0x6e` (SC8585).

The transport checks retained SCL5/SDA5 GPIO176/177 mode1 and high input
levels, adopts firmware timing/open-drain configuration only when idle,
and uses the MT6991 v2 32-bit FIFO registers for a combined one-byte
register-index write / one-byte read. No DMA buffers or engines are used.
It temporarily enables only the existing parent gate (with its completion
fence), IMPE I2C5 bit0 and APDMA bus bit26. PLL and mux selection, pinctrl,
other channels and global resets are preserved. The parent selection must
be a documented I2C parent, and an uninitialized timing state is rejected.

Only identity/version, control, mode and non-clearing presence status are
read. **No charge-pump configuration or VBUS changes are made.** On normal
completion the saved controller configuration and changed clock gates are
restored; a failed transaction retains the clock/configuration state until
reboot and the once-only probe cannot retry. Clearing START alone would
not prove the electrical bus is idle. This is transport bring-up before
implementing source power, not a functioning OTG host driver.

Stock Android reports a 124.8MHz I2C5 parent. Its existing debugfs/pinctrl
views confirm SCL5/SDA5 in mode1. The source path uses SC8585 reverse1:1 mode
and manual ACDRV before MT6379 regulator enable, with the corresponding
normal mode/regulator/ACDRV cleanup. These power changes remain future work.

Photo132222 with the stick attached confirms a completed native ID read
(`irq=1`, value `85`) and scoped controller/clock restoration. The initial
probe expected SC8581 ID81 and stopped before further reads. Both vendor
chip drivers publish the same Linux name `sc858x`, so that name alone
cannot identify the silicon. The live Android driver module resolves to
`sc8585_charger`, independently confirming the variant. Patch0095 checks
SC8585 ID85 twice; unknown identities remain rejected. Photo133652 confirms
all common status reads completed: `id=85 ver=a0 ctrl=00 mode=00 present=08`.
The read-only probe and scoped controller/clock restoration completed.
Control00 has CP/QB/manualACDRV/gates clear; mode00 is forward4:1; presence08
has VOUT insert set and VBUS/VUSB/WPC presence clear. These are controller
reports, not independent connector-voltage measurements. The original
boot/init_boot partitions were subsequently restored and both full physical
SHA256 values verified from rooted Android.
SC8585 protection fields and IBUS ADC scale differ from SC8581; do not
reuse the older chip's complete configuration.

## Bounded source diagnostic (initial snapshot rejected)

Patch0096 adds an independently opt-in `daliotg=1` state machine. It also
requires `daliusb=1 dalicp=1` and the exact Dali `otg-source-config` ADT
property (5V,500mA,SC8585). Existing read-only observations remain the
default. This stage does not start a PHY, xHCI or mass-storage stack.

- Stay in the original Rd sink configuration while the PC cable supplies
  VBUS. Allow40seconds to remove it; require VBUS<=800mV and absent power
  status for at least200ms before fixed default-current Rp.
- Accept one stable Rd with the unused contact open/Ra. Reject toggling,
  ambiguous dual Rd/accessories and unexpected external VBUS. No PD/VCONN.
- Recheck SC8585 ID85 twice, versiona0 and the proven idle routing state.
  Set only mode[2:0] to reverse1:1 and manual-ACDRV bit5, with readback.
  No CP/QB enable or protection/ADC fields are changed.
- Set MT6379 voltage selector26 in big-endian order (5V), current index0
  (500mA), clear protected PP_CV_FLOW_IDLE using the vendor TM passcode and
  relock, recheck CC/VBUS, then enable OTG. The optional USB-killer ADC
  policy is absent from the live Dali board DT, matching stock behavior.
- Check TCPC VBUS voltage (25mV units) and presence within1second. Observe
  source power for at most15seconds, or until detach/status error. Whole
  operation is bounded to60seconds using the physical13MHz counter.
- Always attempt source disable even if the protected off sequence fails.
  Restore saved voltage/current, CP mode/manual bit and Type-C role after
  confirmed regulator-off; observe passive decay for up to500ms. A bus or
  cleanup failure is logged as requiring reboot and never automatically
  retried. Service stop/free also requests serialized cleanup.

I2C uses the proven FIFO path for short reads/writes and preserves scoped
controller/clock state. A failed transaction latches a permanent refusal
to reuse that bus until reboot. Source-off is independently attempted over
PMIF. Neither software logging nor a source enable bit alone establishes
physical VBUS or working USB enumeration; the hardware trial is pending.

### First source trial, 2026-10-21

CI39551734817/1251c67 reached the memory-root PID1, but the source service
reported `end=status/fault error`, `valid=0`, and OTG=`60` before visible
Rp/CP/source-enable messages. The voltage value is invalid, so this does
not establish physical0V. The old `restored` labels also include untouched
components and do not establish a successful source-on/off cycle. Original
boot_b and init_boot_b were restored, Android returned, and both complete
physical partition SHA256 values matched the saved originals.

Patch0097 reports bounded register/PMIF read failures, the initial or
rejected raw TCPC snapshot, ending state/touched flags, and explicit
`untouched` labels. All fault/voltage/power guards, bus operations and
lease limits remain unchanged. The exact initial rejection is still
unknown; no faults are cleared or ignored to bypass it.

### Detailed snapshot and guarded initial acknowledgement

Photo141351 from CI39553228931/98f3936 shows initial tick1/state0,
CC=`10`, power=`08`, OTG=`60`, fault=`80`, raw voltage=`0000`, PMIF=`0`.
The service exits with all three components `untouched`. The nonzero
fault byte is the initial blocker; no source-on or physical cleanup cycle
occurred. Original Android boot/init_boot were fully restored and their
complete physical SHA256 values verified after the trial.

The exact vendor header calls bit7 VCONN_OV with a warning comment, while
[Linux TCPCI](https://github.com/torvalds/linux/blob/master/include/linux/usb/tcpci.h)
names it ALL_REG_RST_TO_DEFAULT and acknowledges it during initialization.
This naming conflict does not establish a physical reset or overvoltage.
The immutable [vendor fault handler](https://github.com/MiCode/mtk_kernel_device_modules/blob/e45c3199537fbba9aae5fb06c31fc8dbd3c1dcee/drivers/misc/mediatek/typec/tcpc/tcpc_mt6379.c#L1545)
writes the observed status back (W1C).

Patch0098 permits one exact `80` acknowledgement on initial tick1 only:
original Rd/Rd, no role/CP/charger changes, VCONN and source off, only
monitor/sink/presence power flags, valid voltage encoding no higher than
5.5V. A fresh fault read must still be exactly `80`; one fixed W1C write
must succeed and read back zero. It is never retried, and every later or
additional fault remains fatal. Existing idle/CC debounce and voltage
checks still precede source enable. Hardware acknowledgement is unverified.

### Initial acknowledgement works; fixed Rp still reports open

Photo142735 from CI39554602650/8cfb5ef confirms the exact initial W1C write
and zero fault readback. Fixed default Rp was programmed, but CC remained
`00` throughout the40second attach window; no CP/charger source writes
occurred. Timeout cleanup restored the original Type-C role. Original
Android partitions were then restored and both complete physical SHA256
values verified.

Patch0099 adds the missing vendor detection-wake steps after confirming
absent VBUS and opening both CC pins: save SYSCTRL1/2 at physical48f/490,
set SHIPPING_OFF bit5, set SYSCTRL2 BMCIOOSC/VBUSDET and clear LPWR using
mask0b/value03. Preserve other fields, settle and read back, check VBUS
again before Rp. Periodic logs guard actual role/SYSCTRL values. After
confirmed source-off, restore only the changed control bits. Failed
read/write/cleanup is reported explicitly. This does not program PD,
DRP toggling, USB PHY, thresholds, resets or protection masks. Whether
this explains the open CC observation is still hardware-unverified.

### CC wake and source control verified; TCPCI voltage remains zero

Photo144332 from CI39556194747/7a0d69e confirms SYSCTRL1 `12` to `32`,
SYSCTRL2 `03`, CC=`02`, successful SC8585 reverse1:1/manual preparation
and MT6379 OTG enable. During voltage rise, TCPCI power=`0c` (VBUS presence
and detection enabled), OTG=`61`, but its voltage register still reads0mV.
The exact ending is `VBUS rise timeout`. Charger, CP, role and detection
control restoration were logged and original Android partitions physically
restored and hash-verified afterward. This is source-control proof, not
an independently measured5V or USB enumeration milestone.

The vendor TCPCI voltage callback is marked TODO. The actual
[MT6379 IIO ADC driver](https://github.com/MiCode/mtk_kernel_device_modules/blob/e45c3199537fbba9aae5fb06c31fc8dbd3c1dcee/drivers/iio/adc/mt6379-adc.c)
uses single conversions at14f, report selector155, and a big-endian16-bit
result at156/157. Voltage raw units are mV. Dali's live DT has no
`replace-chgvin`; its charger consumes channel1 CHGVIN. Channel11 is
PDVBUS at the Type-C side. On original Android with PC power, ordinary
IIO reads returned CHGVIN4661mV and PDVBUS4647mV, both scale1000 to uV.

Patch0100 samples both channels under the native power client's lock.
Only idle conversions are adopted; conversion completion is bounded,
the prior report selector is restored, and any failure permanently
blocks ADC reuse until reboot. Battery monitoring, ZCV and protection
configuration are untouched. PDVBUS supplies the existing absence/rise/
high/low voltage guards, with the same thresholds and lease; CHGVIN and
legacy TCPCI values are logged for comparison. The native ADC path is
hardware-unverified until its next trial.

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
- `drivers/power/supply/{sc858x_charger.c,sc8585_charger.c}`
- `drivers/i2c/busses/i2c-mt65xx.c`
- `drivers/clk/mediatek/{clk-mt6991.c,clk-mt6991-peri.c,clk-mux.c}`
- `drivers/pinctrl/mediatek/pinctrl-mt6991.c`
- `drivers/misc/mediatek/typec/tcpc/tcpc_mt6379.c`
- [TCPCI register and bit definitions](https://github.com/MiCode/mtk_kernel_device_modules/blob/e45c3199537fbba9aae5fb06c31fc8dbd3c1dcee/drivers/misc/mediatek/typec/tcpc/inc/std_tcpci_v10.h)

The vendor extcon path checks `cp_master`, programs reverse charge-pump mode
and ACDRV/gates, and controls the MT6379 regulator. The regulator enable path
also has a protected charger-register sequence; its optional ADC guard is
controlled by `usb-killer-detect`, which is absent from the live Dali FDT. The user has
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
2. Keep the now-verified shared PMIF/SPMI lock for RTC and USB clients.
   Add board I2C5/SC858x charge-pump support required by the source path;
   do not bypass the charger ADC checks or protected-register sequence.
3. Implement xHCI DMA mapping/coherency, rings, completion and port events.
4. Enumerate the device and implement mass-storage BOT/SCSI reads, including
   reset recovery, bounded retries and media removal handling.
5. Expose read-only block media/partition root selection, verify HFSX reads
   on the actual stick, then boot it. Add writes only with correct cache and
   filesystem semantics. No existing USB-root boot argument is functional.
