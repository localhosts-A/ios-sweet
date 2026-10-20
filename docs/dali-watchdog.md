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

Patch0090 timestamps existing kernel/bootstrap/BSD messages and brackets Skywalk
memory initialization and root-device lookup. It retains all initialization,
security policy, timeout and recovery behavior. The first hardware trial is
pending. No reset cause is established merely by the last printed call.
