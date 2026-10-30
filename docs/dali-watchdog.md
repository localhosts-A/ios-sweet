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


## Slow iOS startup: explicitly bounded lease override (patch0167)

The original native120-second diagnostic lease was retained through the UFS
root bring-up. Recent iOS early tasks take longer than that; this creates a
budget mismatch independent of whether a particular last recorded user fault
is truly blocked. The last recorded fault also changes between trials, so it
must not automatically be labelled a stable failing page.

This does **not** establish current reset attribution. The confirmed firmware
mode/length values above came from historical photos, not a fresh post-reset
register capture. Host flash-to-recovery intervals include bootloader entry and
human action; they are not precise fault/reset timestamps. No panic is observed
in the recent retained windows. The UFS completion guard produced no rejection
in the latest window, so it is not evidence of short IO in that trial.

Patch0167 introduced `daliwdtlease=N` in120..600seconds; patch0170 extends the
accepted ceiling to1200 while preserving the120-second default. For0167,
missing,0,negative-as-unsigned,too-small or too-large values retain120. The
shim's own120-second decompression lease is unchanged. The override does not
modify the hardware's timeout or mode, enable a disabled watchdog, touch PMIC,
clear status, reset the device, or introduce an unbounded keepalive. The sole
MMIO write remains vendor reload0x1971 at+0x08, at most once per second, only
on the validated boot CPU while firmware mode.EN is already set. Configuration
is parsed once at init, never in the interrupt path. Invalid clock arithmetic,
backwards time or elapsed lease makes the policy refuse a reload.

The init snapshot now uses the normal console/persistent-log path once during
platform init, outside the polling IRQ. It reports mode,length and selected
lease to help distinguish disabled hardware and the selected startup budget.
This is not a production watchdogd handoff; after the finite lease expires,
feeding stops, and hardware reset can follow. A hung/non-interruptible kernel
can still reset earlier because the existing IRQ poll may not run.

A future `daliwdtlease=600` candidate must still be separately approved with a
maximum600-second observed trial, first-reset Fastboot return, exact boot/root
bounds, full actual-root capture before rollback and final original/clean
hashes. Extending a native boot budget does not authorize longer unattended
experiments or permanently hide failure. Keep GUI-first behavior, not another
logging-only trial; require actual SpringBoard grid/Dock and120seconds stable
with a UI update to report desktop success.

`python3 tools/test_dali_watchdog_lease.py` compiles actual policy/poll code with
only MMIO/register-read assembly adapters: default/bounds/expiry,1Hz rate, clock
overflow/backwards time, non-boot CPUs, disabled firmware and unchanged register
sentinels. The actual native ARM64 object and zero-fuzz replay supplement these
host tests; full new kernel link and runtime progression remain separate gates.


## Late GUI: matched finite boot and input budgets (patch0170)

The0169 trial has positive user-origin SpringBoard traps in boot
`5A6B48B4-F264-4569-9377-31C54EC1CB9C`. Its valid spawn flags0x460c do not
request START_SUSPENDED. The first backboardd exec completed its kernel stages
only after sampler39 and `diagnostic swipe expired without a ready client`.
That is an observed input-deadline mismatch: the one-shot gesture had permanently
expired before the GUI service began. It is not proof of a deadlocked last page
or of the reset cause, and no actual GUI frame/desktop has been verified.

Patch0170 makes a narrow functional change rather than adding trace machinery:

- Native watchdog default remains120seconds. An explicit `daliwdtlease=N` can
  select120..1200; outside that range it falls back to120. The shim's120-second
  loader lease and all firmware-enabled/boot-CPU/1Hz/overflow/expiry guards stay.
- The existing Dali-only opt-in `sweethidswipe=1..300` can additionally select
  `sweethidwait=600..1200`; absent/invalid wait values retain600. A wait argument
  alone does not arm input, nor does it affect the Sweet/non-Dali path.
- This is a total pre-injection readiness/delay/busy-finger budget, measured by
  the existing one-second timer callbacks. Callback scheduling is not a strict
  real-time clock. A real HID client AND a successfully refreshed real GUI frame
  are still mandatory, followed by the configured delay. No occupied finger is
  taken over. The Menu hint/one swipe sequence is unchanged and never re-arms.
- Expiry stops the timer sequence rather than silently extending or retrying it.
  Once the one gesture starts, only its existing bounded steps remain.
- No additional trace hook/thread/proc walk, task-resume call, signing change or
  launchd policy modification is introduced. Hardware watchdog timeout, mode,
  status, software reset and PMIC registers remain untouched; reload+0x08 is
  still the only watchdog register write. This is not watchdogd handoff.

A proposed future candidate changes only `daliwdtlease=600` to1200 and adds
`sweethidwait=1200` to0169's verified args (511 UTF-8 bytes). A new maximum1200-second
host observation window, first-reset Fastboot return, exact images and complete
capture/recovery must be separately authorized. This does NOT extend the already
ended600-second0169 trial. Leave time within any approved window for the gesture
and120-second desktop observation; a late lock screen alone is not completion.

`python3 tools/test_dali_late_gui_budget.py` has five tests executing the actual C
policy/poll and C++ HID startup/timer bodies. They cover defaults/invalid values,
1Hz/clock/finite expiry, unchanged register sentinels and disabled watchdogs,
wait-only not arming input, default600 expiry, a real-frame-ready signal at720
plus delay/busy-finger protection, exactly one gesture, and explicit1200 expiry.
These mocked callbacks are not physical-input or display verification. All56
policy tests plus2 existing fbcon tests pass; three-source fuzz0 replay and real
ARM64 watchdog/HID objects pass locally. New full link/runtime remain unverified.
