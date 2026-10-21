# Dali watchdog observations and boot timing

## Confirmed interface

Read-only Android inspection finds platform device `1c010000.watchdog`, driver
`mtk-wdt` and module `mtk_wdt`. Its live DT has a 0x1000-byte region at
0x1c010000 and compatibles `mediatek,mt6991-wdt`, `mediatek,mt6589-wdt`,
`syscon`, `simple-mfd`. The watchdog class exists, but its usual state/timeout
attributes are absent. Opening `/dev/watchdog` can start a watchdog; this audit
does not open it.

Primary vendor source, pinned to Dali release commit
[e45c319](https://github.com/MiCode/mtk_kernel_device_modules/blob/e45c3199537fbba9aae5fb06c31fc8dbd3c1dcee/drivers/watchdog/mtk_wdt.c),
provides the MT6589-compatible path. Mode is offset0x00, length0x04,
reload0x08, status0x0c (used by its hypervisor configuration), and software
reset0x14. Mode bit0 enables the watchdog. Linux's driver default/max timeout31s
is not evidence for the firmware's configuration at the shim entry.

The diagnostic shim reads mode/length/status at loader entry and after payload
copying. It prints raw values; it does not reload, disable, clear or reconfigure
the watchdog. The status word is preserved uninterpreted. No MT6685 watchdog
configuration has been verified; RTC PMIF knowledge does not establish PMIC
watchdog registers or policy.

## Saved reboot record differs from live status

Vendor [mboot_params.c](https://github.com/MiCode/mtk_kernel_device_modules/blob/e45c3199537fbba9aae5fb06c31fc8dbd3c1dcee/drivers/misc/mediatek/aee/mrdump/mboot_params.c)
copies the boot-parameter buffer into `mboot_params_old` and exposes the saved
preloader `wdt_status` (or an old-layout byte) in `/proc/aed/reboot-reason`.
The observed decimal6 is not a direct read of TOPRGU status. Intervening manual
bootloader entry and stock restoration reboot also prevent attributing it to
the earlier XNU reset. Do not interpret it as a watchdog diagnosis.

## Time domain

Shim `COUNTER ms/hz` and XNU `T...ms` use `CNTPCT_EL0` and `CNTFRQ_EL0` from
nonsecure EL1. They do not depend on RTC or initialized XNU calendar time.
Counter milliseconds use quotient/remainder arithmetic to avoid multiplication
overflow. Compare stamps within one boot; do not assume a reset keeps the same
counter origin. Shim values use the existing hexadecimal console format; XNU
stamps are decimal.

The first timed trial exposed a diagnostic code-generation error: Darwin's
assembler treated the semicolon in `isb; mrs ...` as a comment. The linked
helper contained ISB and the frequency read, but no physical counter read;
its timestamp used an undefined register. Photo060140 has nonmonotonic and
huge values, so none of that trial's XNU timestamps is usable. Use an escaped
newline between the instructions. `tools/inspect-boot-counter.py` checks the
actual linked helper for ordered ISB, CNTPCT_EL0 and CNTFRQ_EL0 instructions.

Patch0090 timestamps existing kernel/bootstrap/BSD messages and brackets Skywalk
memory initialization and root-device lookup. It retains all initialization,
security policy, timeout and recovery behavior. That trial stopped with the
latest visible line PE_lockdown_iokit and then automatically rebooted; the
reset mechanism remains unknown. Original partitions were restored and
physically hash verified. No reset cause is established merely by the last
printed call.

## Bounded boot keepalive

Photos104915/104926 show the corrected counter progressing normally, loader
entry mode0x17d, length0xf000 and status0. Enable, IRQ and dual mode are set.
The vendor length encoding corresponds to a30-second stage interval; this is
not a remaining-time measurement. User reports the stopped screen then an
automatic reboot. Enabled hardware alone does not establish reset attribution.

Patch0091 services the existing watchdog using the vendor reload key0x1971 at
offset0x08. Shim polls between LZ4 blocks. XNU validates an explicit
`dali-watchdog` DT compatible and exact0x1c010000/0x1000 region, maps it during
the VM-initialized platform setup, and polls at bootstrap/constructor progress
and boot-CPU timer interrupts. Each stage permits at most one reload per second
for120seconds after that stage starts; after the lease expires, reloads stop.
No disabled watchdog is armed. Mode, length, status, software reset and PMIC
registers are not written. This is a finite diagnostic boot lease, not a
complete production watchdog/watchdogd implementation. Actual reset behavior
and new native reload path require a temporary hardware trial.
