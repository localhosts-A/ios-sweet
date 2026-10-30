# Dali desktop diagnosis: bounded HFS and boot-task context

## Current milestone (2026-10-30)

Patch0160 is a diagnostic-only continuation of CI40927108866 / commit313baa5.
It has passed exact incremental patch replay, three single-file syntax checks,
three real ARM64 object compiles, and host policy tests. It has **not** passed a
new full kernel build/link or hardware trial. There is no new flashable pair yet.

The goal remains a real SpringBoard home screen. Kernel text, `DONE UMD_INIT`,
registered AppleCLCD services and a lock screen are separate milestones, not
proof of a desktop.

## What is preserved

The first diagnostic candidate keeps the existing binary persona manifest,
empty SystemPersonaServices list, Preboot layout, exact baseline ADT and existing
root-write policy. It does not change HFS allocation/rename semantics, journal,
sector geometry, cache strategy or GUI behavior. Existing uncommitted mkadt and
mkiosroot Preboot changes are not included in this diagnostic.

Verified local roots:

- Clean base: `0984638dc5b16a2284681bba408977c521c7532366f2886ad6e48802ad427751`
- Seeded target: `eb3192d474ac6aa3cf2ed060f0ea47a3c26adb23dad316fcaa26bc863b10dd44`
- Seeded delta: `256bbdf284a89414dc35018cec2373514bac724a1f4e66220f7a2b118226b571`
- Delta expands to16GiB with9 RAW runs /11 blocks /45,056 written bytes.
- Read-only fsck of the actual target reports non-journaled case-sensitive iOSRoot OK.

The approved root view is only the first16GiB of `unixnu_data`. SWEET logging is
only `unixnu_logs` at +16MiB for4,198,400 bytes; it is not the whole second half
of that partition. Boot/init_boot writes require separate exact-pair approval.
GPT, Android userdata, calibration partitions and the unbacked tail stay untouched.

## Diagnostic controls and interpretation

- `sweethfsdiag=1`: explicitly enable the new logs; absent/other values are inert.
- `sweethfsblock=3059250`: allocation-block target, configurable for later evidence.
- Each instrumented translation unit has64 normal event tickets and16 independent
  error tickets. Normal trace exhaustion cannot consume the bitmap-error budget.
  Fork snapshots may emit up to eight extent lines per ticket. Ticket numbers
  are local to the translation unit, not a global clock or cross-thread order.
- `SWEET HFSDIAG` records targeted allocations, release before/after with fileID
  and fork, exchange before/after under existing cnode locks, fork extents and
  the first bounded bitmap mismatch contexts. An error on another block still
  gets error-context logging; the old1966891-only probes are not the new gate.
- The exchange/unlink/truncate hypothesis is not a diagnosed double-free fix.
  HFS's consistency EIO is not proof of a physical UFS failure.

Sampler proposal: start after5seconds, interval15seconds, existing40-sample cap.
Names: `launchd,usermanagerd,xpcroleaccountd,init_featureflag,backboardd,SpringBoard,notifyd`.
The spelling `init_featureflag` is deliberate: proc_name reads p_comm, whose
MAXCOMLEN is16. IOKit trace already defaults to backboardd/SpringBoard; do not
set `sweetiolog=1`, which would select a process literally named1.

The proposed command is393 UTF-8 bytes. mkpayload now rejects over607 bytes or
embedded NUL instead of silently truncating. Repacking the baseline kernel and
ADT with its old arguments reproduces the original payload byte-for-byte;
changing to proposed arguments changes only the command field. This is a local
packaging test, **not** a kernel containing patch0160.

## Evidence and restoration gates

The retained SWEET active ring (total39637/seq49) belongs to UUID602C8DC7...;
the retained ramoops belongs to UUID5E451CCD.... Keep those histories separate.
No raw-log concatenation, stale ring tail, or matching filename establishes a
single boot. The latest actual dirty root was overwritten during restoration;
a clean-root owner lookup cannot reconstruct its runtime bitmap/extent state.

Before each separately authorized trial:

1. Bind exact kernel/ADT/bootargs, boot/init_boot, root base/target/delta and
   original backups in the new candidate's manifest. Check complete target
   prefix hash after applying/readback of the reviewed RAW ranges.
2. Use a fresh identity/model/slot/partition-size and stock-pair preflight, not
   a stale device_pair manifest. Verify the iOS root is not mounted in Android.
3. Reserve space for a real dirty-root capture. At implementation baseline,
   about28GiB was free; do not automatically delete existing artifacts.
4. Agree one observation window covering slow startup and downstream sampling,
   a visible trial ID/time, and recovery conditions. Do not loop boots on error.

After the trial, stock init_boot/boot can be restored first to regain Android
ADB. Then immediately save pstore and the exact SWEET raw range. **Before any
root repair/restore**, capture the actual16GiB root or all differing raw chunks
against an exact pretrial base. A differential capture must examine all256
64-MiB chunks, preserve every changed byte and reconstruct to the same full-prefix
hash as the device. Analyze a read-only copy; repair only a separate copy.

The previous restore-clean-root.py only restores six fixed chunks and omits
identity/full-prefix checks. It is not a generic rollback. Restore based on the
actual complete diff, read back each written range, then verify the entire16GiB
clean-prefix hash plus stock boot/init_boot and Android completion.

## Next gates after usable root I/O

- Confirm xpcroleaccountd/init_featureflags completion/exit or the precise wait;
  `Doing boot task` is not completion. Pair stack samples with exec/exit/crash.
- Only if required, compare the existing services-only/no-seed root with all
  other behavior fixed; do not simultaneously change persona format and HFS.
- Fix GUI separately: unify D22 logical1125x2436 with physical1280x2772,
  surface/stride/format and HID coordinates; connect present/fade to a serialized,
  bounded retained DSI refresh rather than console-newline refresh.
- The actual baseline ADT has **no /product node**. The ramoops ADT verifier
  passes with a missing-D22-source warning, not a complete identity guarantee.
  Preserve this baseline for storage diagnosis; obtain/verify the proper D22
  identity for the later GUI candidate without mixing causes.
- Reuse Setup/activation preferences. If physical input is absent, a separately
  gated bounded synthetic swipe may unlock D22; existing Home injection is not
  evidence that D22 unlock already works.
- Final acceptance: real icons/Dock and one real UI transition, correlated to
  one boot's GUI/surface/DSI evidence, stable for120seconds. Explicitly report
  whether synthetic rather than physical input was used.

## Local checks

Run from the repository root:

```
python3 tools/test_mkpayload_cmdline.py
python3 tools/test_dali_hfs_diagnostics.py
python3 tools/test_dali_fbcon_refresh.py
python3 tools/verify-dali-ramoops-adt.py
python3 tools/validate-repo.py
```

The HFS host test compiles the actual added policy header with host atomic/API
adapters, including concurrent saturating tickets and independent error budgets.
It does not emulate HFS or prove runtime filesystem integrity. Single-file/object
compiles use the exact40927108866 headers/source and prior CI compile flags;
full build/link and source/linked-marker gates remain mandatory before packaging.
