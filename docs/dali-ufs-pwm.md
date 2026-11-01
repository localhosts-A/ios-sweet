# Dali clock-gated PWM initialization

This is a **default-off functional candidate, not a hardware-validated speed fix**.
Production `SWEET_PWM_PLATFORM_CAP` is a ceiling of G2, never an unconditional
permission to change the link. `ufspwm=1` first needs exact read-only cold clock
admission; unknown clock yields effective G1 before any new UIC probe. The
healthy controller, current PA state and both receiver capabilities must then
allow a non-downgrading upgrade. No PLL, voltage,
high-speed mode, lane count, DMA-buffer or normal I/O wait is changed. The new
pre-root failure return reuses the existing armed marker/lease and PSCI helper;
its normal return policy is unchanged. That narrow path is host/object-tested,
not a claim of a new successful hardware recovery.

## Evidence and limits

The cold Dali path resets the controller and performs LINK_STARTUP but did not
previously negotiate PA gears/power mode. The fixed Linux reference records POR
as PWM-G1, one lane, SLOWAUTO; this is a software default, **not a measurement of
this project's live link**. `bCurrentPowerMode=0x11` is a device-power QUERY value,
not the UniPro gear or `PA_PWRMODE`.

One captured boot returned 5,840 reads / 600,039,424 bytes with 1,067.386 seconds
of aggregate command service at the independently checked 13 MHz timebase.
That scope includes cache maintenance, polling, sleep, scheduling and response
validation. It does not establish physical line speed or attribute the GUI
failure to UFS. Persisted critical userspace/cache files matched their baseline;
that does not prove runtime memory or transient I/O integrity.

## Initialization boundary

The new call is after successful Dali device initialization and before
read-capacity/GPT/root access, inside the existing UFS mutex with `su.ready`
false. Its guards require a known cold Dali profile, explicit opt-in and a
verified platform cap greater than G1. A candidate cannot supply its own
unverified cap through a boot argument or device-tree property.

A future enabled path must validate controller/list ownership, both doorbells
idle, no command poison, HCE/DP/UCRDY, IE zero and AHIT readback zero. The
transfer list is already running here; a fully stopped-controller check is not
appropriate. No doorbell is cleared merely to manufacture an idle state.

The pure policy validates current mode, gears, active/connected lanes and
termination. Both directions must be SLOWAUTO (`0x55`) or both SLOW (`0x22`),
with one active lane each. Mixed modes remain healthy skips, not an implicit
mode conversion. The explicit target mode is **SLOW (`0x22`)**, following the
fixed Linux PWM contract; readback must match that target, not the old mode.
Local maximum RX PWM gear constrains host
RX; peer maximum RX PWM gear constrains host TX. Capabilities must be G1..G7;
invalid values are errors, not clamped maxima. The symmetric target is the
minimum of both receiver capabilities and the proven platform cap. It must not
lower either current gear and must improve at least one direction.

Healthy but nonapplicable/no-benefit configurations are skipped without SET.
GET/SET/ACK errors, contradictory values and unhealthy ownership are fatal.
The unchanged ordinary UIC helper is wrapped with stricter pre/post checks; an
error cannot be treated as a clean fallback because a request may remain in
flight or its completion may not have been acknowledged.

## Dedicated power-mode completion

PA power-mode change is not an ordinary `DME_SET` completion:

- UCCS (`0x400`) and UPMS (`0x10`) are separately observed and latched.
- ARG2 is consumed only with valid command completion. HCS.UPMCRS is evaluated
  with power completion. Local-request success is `PWR_LOCAL=1`, **not**
  `PWR_OK=0`. While UPMS stays asserted its result is revalidated, so a later
  bad power result cannot be hidden by an earlier successful observation.
- Completion/error co-occurrence fails. Stale or unowned completion status
  fails rather than being cleared and reused as evidence of a new request.
- Both completion events share one 500 ms deadline; seeing one does not restart
  it. A finite poll count also bounds a broken clock. The complete fixed
  sequence shares a 30-second budget and has no command retry.
- Only owned completion bits are acknowledged, with clean readback before
  configuration readback. Readback alone is not proof of async completion or
  actual PHY rate.

The staged helper performs 16 ordinary SETs before the single power request:
`PA_TXHSADAPTTYPE=PA_NO_ADAPT` (**3**, not 0); RX gear/active lanes/termination;
TX gear/active lanes/termination; `PA_PWRMODEUSERDATA0..5`; and
`DME_LocalFC0/TC0/AFC0Protection/Replay/ReqTimeOutVal`. The timeout sequence is
8191, 65535, 32767 for each channel and the three local values. Lane count and
termination are rewritten to the same validated values. No `PA_HSSERIES` or
peer SET is issued. The fixed sequence is 11 GETs, 16 SETs, one PMC and nine
readback GETs: **37 commands**, no retries, sharing the same 30-second budget.

The exact vendor PWM PRE_CHANGE chooses NO_ADAPT; its optional FASTAUTO
intermediate transition explicitly excludes SLOW. The actual Dali DT's
`mediatek,ufs-pmc-via-fastauto` therefore does not justify an intermediate HS
request for this PWM path. Attribute order/values and every ordinary command
failure are checked against actual staged functions, not an alternative model.

## Read-only clock admission (required for production ceiling G2)

`SweetUFSPWMClock.h` and the actual initializer helper decode two identical
volatile snapshots, separated by full-system barriers. Fixed actual-DT ranges:
CKSYS `0x10000000/0x800`, APMIXED `0x10000800/0x1000`. Reads only:

- CKSYS `CLK_CFG_12`, offset `0xd0`: parent `[10:8]`, gate bit 15.
- APMIXED FENC `0x3c`: MAINPLL bit 7, UNIVPLL bit 6.
- MAINPLL_CON1 `0x254`, UNIVPLL_CON1 `0x268`: PCW `[21:0]`, post-divider
  `[26:24]`; the fixed MT6991 implementation has **14 fractional bits**, not
  the generic default. Reference clock 26 MHz, accepted VCO 1.5–3.8 GHz.
- Only parent 3 (MAINPLL/8) and parent 6 (UNIVPLL/5), selected PLL prepared,
  mux ungated and derived rate within actual DT 273–499.2 MHz are recognized.

Map failure, unknown/gated/unprepared/unstable state or invalid/out-of-range
rate returns cap G1 **before any new UIC probe**. The helper can return at most
G2; it never writes a clock, PLL, regulator or voltage. Missing/invalid opt-in
or a non-Dali profile also prevents the new clock mappings/reads. The production
caller block is exercised exactly in the host fixture, with synthetic register
contents; the rest of the kernel initializer remains stubbed. A register-derived
rate is not an empirical PHY measurement or an already captured cold-XNU value.
No Android sysfs state is substituted for cold readback.

The exact vendor PHY reference matches the actual reused `mt8183-ufsphy`
compatible and `mphy-ver=1`: power_on/off return immediately for nonzero version;
the legacy version-0 PHY programming must not be copied into MT6991. This is
source/DT evidence, not proof of a live analog or regulator state. No higher
controller frequency or voltage is requested: the ceiling G2 is below the fixed
vendor PWM default G4 and is conditional on the already-running clock state,
active single-lane PWM readback and each receiver's advertised maximum. This
supports a bounded integration trial, not a guarantee of physical stability.

Fixed public reference identities:

- MiCode/mtk_kernel_device_modules `e45c3199537fbba9aae5fb06c31fc8dbd3c1dcee`:
  `drivers/ufs/ufs-mediatek.c`, `drivers/phy/mediatek/phy-mtk-ufs.c`,
  `drivers/clk/mediatek/{clk-mt6991.c,clk-pll.c}` and
  `include/dt-bindings/clock/mt6991-clk.h`.
- MiCode/Xiaomi_Kernel_OpenSource `f089d58cab670dc0d75db1203fa63850e57a940d`:
  `drivers/ufs/{core/ufshcd.c,host/ufshcd-pltfrm.c}` and `include/ufs/unipro.h`.
- Actual wrapped Dali DTB SHA256
  `223984cc1daf6f9194ff33e4c674dce9f2de9203dc9b73e72bd1e0754dbf4ae4`,
  unique inner FDT at offset 64. Its UFS clock IDs 42/97/115 match
  `CLK_CK_SEL`/`CLK_CK_MAINPLL_D4_D2`/`CLK_CK_UNIVPLL_D5` in the fixed binding.

## Terminal failure and reentry

A separate atomic word publishes the first terminal errno plus uncertainty in
one operation. A completed policy rejection or a fully completed but incorrect
readback is recorded as known failure. Transport, SET, ACK and timeout errors
are conservatively uncertain; the foundation does not fabricate a clean link
from an errno. Later errors cannot replace the first failure or its certainty.
Both categories prohibit initialization retry for the rest of the boot.

The root-entry check precedes ramoops and any possible MMIO. An opted-in
initialization has a nonblocking atomic claim so another caller cannot restart
reset/UIC while it is active. Success releases the ready-state path, including
an already-ready first guarded entry. This link/init latch is independent of
existing `command_poisoned` DMA-ownership protection.

These fatal points precede the normal sampling thread. Its 1080-second active
return path therefore does not prove early-failure recovery. A narrow new
pre-root API runs only after init failure, with the UFS mutex released and root
not ready. It atomically claims the existing armed state once, checks native
interrupt/preemption context, exact marker/MODE/LENGTH and finite lease, records
to memory, and rechecks deadline/registers after that record before calling the
existing PSCI cold helper. It performs no UFS drain, new thread or transport.
An SMC that returns takes the existing low-bit rollback path without a second
reset. Normal arm/poll/watchdog functions remain byte-identical. Host fixtures
and actual ARM64 objects pass; the new early-failure hardware route is not yet
tested. Unknown-link logging or root access is never continued for evidence.

## Verification

`tools/test_dali_ufs_pwm.py` compiles the actual final-patch header and functions,
including ordinary UIC, dedicated PMC and root entry. Its MMIO fixture models
W1C registers, independently arriving completion events and failure injection.
Higher caps exist only in temporary test headers. It includes no-downgrade,
capability/state errors, all power-result values, deadline/frozen-clock bounds,
per-command failure, ACK/readback errors, reentry and a 32-thread single-claim
check. Only the exact PWM caller block is executed from the initializer; its other
operations remain stubs. This is not hardware or full-kernel scheduling proof.

GUI-load, startup and Fastboot suites use an explicit final0180 overlay.
Startup retains its full-file comparison after stripping only the declared PWM
initialization additions and the already documented older hooks. Existing
SCSI, QUERY, strategy, logger, normal UIC and LINK_STARTUP exception functions
must remain byte-identical.

The PWM suite also replays all six files using the native build's **Git apply**
format in a fresh Git root, then compares every resulting file byte-for-byte.
New private headers carry `new file mode 100644`; removing that metadata must
fail the negative control. GNU patch replay alone is insufficient: a parent
repository prefix can also silently skip a Git apply, so merely observing exit
zero is not acceptance. Final native source equality remains mandatory.

The CI workflow runs the PWM, clock and early-return suites before and after
a native build, alongside the existing source and protocol gates.
Wiring is not execution: neither local objects nor host fixtures substitute for
actual native link/source/archive verification.

## Deployment gates

Do not publish a cap-G1 diagnostic candidate. A deployable integration requires
all of: a proven higher platform PWM cap and prerequisite attributes; complete
host/native/source/object validation; early-fatal recovery coverage; and a
reliable destination with at least 16 GiB capture plus 2 GiB free margin.
Do not delete evidence, weaken capture bounds, assume compression, reuse spent
state or combine unrelated filesystem capacities to pass that gate.

Future candidate parameters may add `ufspwm=1` and remove only the proven
redundant default `sweetffscreen=0`; the expected command line is 594 bytes,
still subject to the actual 607-byte/NUL gates. Keep `sweettouch=0`,
`sweetkeys=0`, `serial=0`, the return token and all return budgets unchanged.
Each new round needs fresh identity/original-pair/full-baseline/clock/space
checks, complete new evidence and same-round stock/full-root restoration.
The project's standing user authorization does not waive these technical gates.

A desktop result still requires the real SpringBoard grid and Dock, a real UI
update, 120 seconds of stable visible output, and matching-boot actual surface
submission and successful DSI refresh. None of this document's checks replaces
that acceptance.
