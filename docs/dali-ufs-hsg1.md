# Dali HS-G1 and short read-only speed trial

This is a default-off functional implementation. Host tests and ARM64 objects
are not evidence of physical throughput, link stability, touch, or the desktop.

## Scope

Exact 4-byte little-endian ADT properties under the Dali `sweet-ufs` node:

- `dali-hs-g1 = 1`: request one-lane HS-G1, FASTAUTO, rate B.
- `dali-speed-probe = 1`: run a pre-root read-only comparison and return. This
  requires the HS-G1 flag. Conflicts with `ufspwm` or the older two-lane flag
  are rejected before initialization.
- `dali-dvfsrc-init = 1`: within that HS path only, permit one standard vendor
  firmware INIT attempt under the zero-metadata/no-known-nonzero-AP-field conditions below. This flag alone cannot start
  UFS/HS, and it does not enable the separate START/free-run operation.

Absent/malformed flags and non-Dali compatible do not map the new platform
ranges, issue new firmware requests, change the link, allocate the probe buffer,
or start the short return. Existing PWM/two-lane opt-ins remain separate.

## Platform minimum request

The implementation follows the MT6991 AP minimum-request interface used by the
fixed vendor DVFSRC regulator. It does not directly program a PMIC voltage,
change a PLL/clock parent, force an OPP, or support runtime suspend/DVFS
transitions. The additional INIT entry described below is separately opt-in;
its source-backed call ABI is not a claim of verified secure-firmware behavior.

Fixed public reference:
`MiCode/mtk_kernel_device_modules` commit
`e45c3199537fbba9aae5fb06c31fc8dbd3c1dcee`:

- `drivers/soc/mediatek/mtk-dvfsrc.c`: MT6991 register map, AP request operation,
  resource request/release, force/halt interface and VCORE completion comparison.
- `drivers/regulator/mtk-dvfsrc-regulator.c`: ascending nominal VCORE selectors.
- `drivers/soc/mediatek/dvfsrc-helper.c`: live v4 VCORE gear count and descending
  firmware calibrated voltage table.
- `drivers/ufs/ufs-mediatek.c`: persistent minimum VCORE request before the high
  controller clock and low-gear adaptation behavior.

The actual Dali DT requires 650000 uV for the maximum UFS controller clock.
`SweetUFSHSPlatform.h` checks the already-running 273–499.2 MHz clock using the
existing exact MT6991 decoder, with stable clock/gate/reference snapshots.
It maps only the actual DVFSRC, CKSYS and APMIXED ranges.

`SweetUFSHSFloor.h` first requires the actual v4 helper's nonzero max-OPP
field, `BASIC_CONTROL[26:20]`, before reading the gear header at +0xfc. A zero
header without this precondition is not an established usable one-level table.
The nominal regulator has six possible selectors, a capacity rather than a
fixed live count. Configured counts 3 through 6 query GET_VCORE_UV with the
**raw selector itself**, exactly as the vendor fills and checks its voltage
table. The separately exported logical-OPP conversion reverses its argument;
that conversion must not be applied to a raw regulator/SW_REQ selector. Fewer
than three cannot express nominal selector 2 (650000 uV), and more than six
lacks a known nominal selector map; both remain errors before an AP write.
The basic register/max field, count-read validity, raw count and decoded number
are logged. Successful table entries are logged as firmware calibration values,
not ADC readings. It chooses the lowest configured selector whose nominal and
calibrated values both satisfy 650000 uV, never lowering an inherited AP request.
Unknown, out-of-table and insufficient-voltage states remain errors. It uses the
standard temporary memory-resource request/release and writes only
`SW_REQ[6:4]`, preserving unrelated bits. It waits for target idle and
`CUR_TAR_GEAR[10:8] >= request`, with a 1000-us clock budget and a separate
1001-poll bound. A failed INIT/GET4 status, forced/halted controller,
invalid table, failed write or incomplete hardware transition prevents HS configuration.
The initial health read does not require an idle target: the actual vendor
request path submits the AP minimum vote **before** its temporary memory-resource
request, then waits for idle and sufficient completed gear. A transition in
progress before the vote is not a reason to reject this standard request. The
implementation follows that order without relaxing its bounded post-vote
idle/gear/clock checks. A local resource-transport failure may therefore leave the
higher AP vote in place; a failed write returns before resource acquisition.
There is no lowering rollback, retry, START or fallback. This applies only to
acquisition; a busy target during later HS validation remains a fault.
The AP minimum request stays in the register until reboot; temporary memory
resource release does not withdraw that minimum request. The resource SMC
return values are recorded raw with per-call validity. The fixed MT6991 source
and actual module call 0x40 and 0x41 without checking either a0, including -1;
these are not INIT/GET4 status values. Returned calls remain paired, and a raw
-1 is neither relabeled a grant nor accepted as voltage completion. Only the
actual AP request/readback, idle target, sufficient gear and unchanged clock
can activate the floor. Local transport errors still stop the path and a failed
release transport is recorded as a pending pair, not as a successful release. A 1-us delay after an idle observation preserves the vendor's
separation from a prior transition before completed gear is accepted.

The UFS mutex serializes this tree's sole AP-request writer. There is no new
lowering/release, competing DVFS driver, suspend, force or clock-change path.
The hardware retained-minimum request is the operating contract, not a claim
that a read-only observation prevents arbitrary external firmware changes.
Before each HS SCSI command, request/clock/completion loss latches an error
before descriptor, DMA, cache or doorbell access. A reset, unsupported power
transition or external request modification is a fault, not a supported mode
change or a reason to retry. Firmware table values are not ADC measurements. The final health snapshot logs
raw request/target/gear/force/halt values with a per-read validity mask (bits
3/2/4/0/1 respectively). These are sequential reads from the last health check,
not an atomic snapshot or extra diagnostic reads; absent bits must not be
interpreted as observed zero values. Firmware state and floor/configuration
records use separate short lines; the actual adapter fixture enforces every
formatted printf below 256 bytes, including -1/opaque resource values and error
paths, to avoid the retained early-console line truncation observed on hardware.

## Optional standard firmware initialization

The fixed MT6991 parent driver and actual module call SMC `0xc2000506`, command
0, with x2=flag and x3=vmode. Both properties are absent on this board, so both
arguments and x4–x7 are zero. `dali-dvfsrc-init=1` permits that call once only
when the observed max-OPP field and whole gear header are zero, the known AP
VCORE[6:4], DRAM[15:12] and EMI[3:0] request fields are all zero, and
force/halt/target-busy are absent with the checked clock unchanged. A configured
controller is adopted without INIT, even with the property enabled. Nonzero
known AP request fields prevent INIT; they are not cleared to make it possible.
Zero metadata is not a proved uninitialized-firmware sentinel, and selector 0
is still a valid request value. These guards do not establish absent owners or
global firmware/SCP/DRAM quiescence; the explicit opt-in permits a managed
bring-up attempt, not an automatically proven safe state transition.

The code records valid before/after basic/count snapshots, whether a call was
attempted, and raw INIT status/DRAM-type returns. A transport error or nonzero
status is terminal. A zero status is **not** accepted as table/voltage success:
the ordinary floor code must independently validate configuration, direct-index
calibration and the retained AP request/completion before HS. An unchanged zero
configuration, unknown table or lost clock still stops the attempt, without a
retry, fallback, diagnostic write on an unknown link, or extra START call.

Command 1, implemented by Linux's separate enable-free-run driver, is not in
the allowed SMC set. The host tests retain the production command/index/NULL
guard and replace only ARM register/assembly/result handling with a firmware
mock. They cover opt-in/disabled behavior, existing configuration/vote,
unsupported/failed/no-effect INIT, changed clock, post-INIT timeout and exactly
one attempt. This source-backed managed INIT interface is not proof of secure
firmware side effects, physical voltage, a working HS transition or performance
on this XNU boot; the opt-in candidate requires its own hardware trial.

## Link transition

The call occurs under the original UFS mutex before root-ready. Both
`PA_MAXRXHSGEAR` capabilities must explicitly support at least G1. The starting
link must be single-lane PWM G1 with no termination. The target uses:

- RX/TX gear 1, one active lane and termination enabled;
- HS series B and `PA_NO_ADAPT = 3`;
- the fixed standard UniPro timeout values;
- one FASTAUTO (`0x44`) PMC with separately checked UCCS and UPMS;
- local mode/gear/lane/termination/series readback and platform revalidation.

PMC is asynchronous. While its UCCS/UPMS events are pending, the completion
accumulator rejects HCS host/device-error indications but does not require DP
on every intermediate sample. In particular, IS=0x400/HCS=0x8/ARG2=0 reports
UCCS without DP; it is not by itself a link-lost indication or successful PMC.
Both completion events, successful ARG2 and PWR_LOCAL remain mandatory. Strict
HCE/list ownership, doorbell and DP/transfer/UIC-readiness guards still run
before submission, after both events before ACK, and after ACK. A final absent
DP, link/UIC/fatal error or missing event still fails without retry; the change
does not wait again after a failed final-readiness check. HS readback and the
platform checks remain required after PMC.

The vendor's optional intermediate FASTAUTO transition is for a FAST/G4+
request and does not apply to this FASTAUTO G1 target. No high gear, second lane,
PHY version-0 programming or speculative fallback is added. Once configuration
is staged, failure is terminal for the boot. The existing first-error and DMA
poison protections remain intact.

Failure-only UIC/PMC observations retain already-read raw values and validity
bits, including phase and the first failed idle guard. They do not issue extra
GETs, clear extra status or retry a failed operation. A log cannot establish an
unobserved 0196 failure stage or prove that PWM/two-lane hardware is unsupported.

## Short trial

With the probe flag, a 4-MiB reference buffer is allocated before the UFS mutex.
After capacity and GPT range validation, four 1-MiB reads are timed on PWM. The
HS transition runs, then the same range is read and compared byte-for-byte.
Reads stay inside the selected `unixnu_data` first-16-GiB view. No file content
is printed and there is no root WRITE command.

Only after successful reads, comparison and healthy link checks does a bounded
console snapshot use the existing separate `unixnu_logs` area, preserving:

1. log payload write;
2. SYNCHRONIZE CACHE;
3. SWEETLOG header publication;
4. SYNCHRONIZE CACHE.

The trial stops before BSD nodes, root-ready, logger/sampler threads or root
mount. Outside the UFS lock it requests the same already-armed one-shot Fastboot
return with a distinct `ufs-probe-prepared` label. It does not call VFS_SYNC or
an asynchronous logger drain. A returned SMC is not success and cannot restart
or mount the probe. Failure uses the existing pre-root return and ramoops only;
an unknown link is never used for diagnostic writes.

A short configuration can use the existing supported after=120/lease=180 policy;
it normally requests the pre-root return earlier, without waiting for a desktop
sampler. This is a configuration budget, not a measured return-time guarantee.
The host runner must observe disappearance immediately after requesting reboot.

The read comparison is not a cold-media benchmark: the second read may benefit
from device cache. Its timings include the actual polled command, cache work
and copy/compare overhead. Only same-boot measured throughput and exact data
comparison can establish the result. A later mounted-root desktop run requires
its own filesystem consistency and real-screen acceptance.

## Validation

`tools/test_dali_ufs_speed.py [MATERIALIZED_XNU]` requires exact patch replay and
compiles actual floor, adapter, HS, read/log, init, root-entry, command and return
functions with host fixtures. Firmware/SMC and MMIO are mocked; the adapter test
replaces only the assembly SMC boundary. Materialized sources must match the
formal patch exactly. The PMC fixture extracts the actual idle, error-latch,
completion and HS caller functions, then compares the final policy against its
exact pre-change header. The first-sample regression uses the observed raw
IS/HCS/ARG2 values; its later UPMS event and restored final readiness are explicit
mocks, not hardware observations. Both versions also cover strict initial/final
readiness, ACK, readback, error priority, bounded missing-event waits and no
second command after a latched failure. Existing default-off dynamic UFS
regressions remain in the full suite, along with independent native compile/link checks. No mock test
is reported as physical voltage, line speed or desktop validation.
