# Dali HS-G1 and short read-only speed trial

This is a default-off functional implementation. Host tests and ARM64 objects
are not evidence of physical throughput, link stability, touch, or the desktop.

## Scope

Two exact 4-byte little-endian ADT properties under the Dali `sweet-ufs` node:

- `dali-hs-g1 = 1`: request one-lane HS-G1, FASTAUTO, rate B.
- `dali-speed-probe = 1`: run a pre-root read-only comparison and return. This
  requires the HS-G1 flag. Conflicts with `ufspwm` or the older two-lane flag
  are rejected before initialization.

Absent/malformed flags and non-Dali compatible do not map the new platform
ranges, issue new firmware requests, change the link, allocate the probe buffer,
or start the short return. Existing PWM/two-lane opt-ins remain separate.

## Platform minimum request

The implementation follows the MT6991 AP minimum-request interface used by the
fixed vendor DVFSRC regulator. It does not directly program a PMIC voltage,
change a PLL/clock parent, initialize DVFSRC firmware, force an OPP, or support
runtime suspend/DVFS transitions.

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

`SweetUFSHSFloor.h` reads the live configured gear count and that many
calibrated firmware entries. The nominal regulator has six possible selectors;
this is a ceiling, not proof that the active firmware table always has six
entries. Counts 3 through 6 use the vendor's `count - 1 - selector` reverse
index. Fewer than three cannot express nominal selector 2 (650000 uV), and
more than six lacks a known nominal selector map, so both are rejected before
any request write. The raw count and decoded number are logged even on a
capability rejection. It chooses the lowest configured selector whose nominal
and calibrated values both satisfy 650000 uV, never lowering an inherited AP
request. Unknown, out-of-table and insufficient-voltage states remain errors. It uses the
standard temporary memory-resource request/release and writes only
`SW_REQ[6:4]`, preserving unrelated bits. It waits for target idle and
`CUR_TAR_GEAR[10:8] >= request`, with a 1000-us clock budget and a separate
1001-poll bound. An unsupported firmware command, forced/halted controller,
invalid table, failed write or incomplete transition prevents HS configuration.
The AP minimum request stays in the register until reboot; temporary memory
resource release does not withdraw that minimum request. The resource SMC
return values are recorded raw, not interpreted using GET4's separate status
ABI. Standard SMCCC NOT_SUPPORTED and local transport errors stop the path;
actual AP request/readback/target/gear, not an opaque resource return value,
prove completion. A 1-us delay after an idle observation preserves the vendor's
separation from a prior transition before completed gear is accepted.

The UFS mutex serializes this tree's sole AP-request writer. There is no new
lowering/release, competing DVFS driver, suspend, force or clock-change path.
The hardware retained-minimum request is the operating contract, not a claim
that a read-only observation prevents arbitrary external firmware changes.
Before each HS SCSI command, request/clock/completion loss latches an error
before descriptor, DMA, cache or doorbell access. A reset, unsupported power
transition or external request modification is a fault, not a supported mode
change or a reason to retry. Firmware table values are not ADC measurements.

## Link transition

The call occurs under the original UFS mutex before root-ready. Both
`PA_MAXRXHSGEAR` capabilities must explicitly support at least G1. The starting
link must be single-lane PWM G1 with no termination. The target uses:

- RX/TX gear 1, one active lane and termination enabled;
- HS series B and `PA_NO_ADAPT = 3`;
- the fixed standard UniPro timeout values;
- one FASTAUTO (`0x44`) PMC with separately checked UCCS and UPMS;
- local mode/gear/lane/termination/series readback and platform revalidation.

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
formal patch exactly. Existing default-off dynamic UFS regressions remain in
the full suite, along with independent native compile/link checks. No mock test
is reported as physical voltage, line speed or desktop validation.
