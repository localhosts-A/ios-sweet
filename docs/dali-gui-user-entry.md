# Dali SpringBoard user entry

0168's actual boot `5C687EAE-CEC6-4AFC-B53B-3C8E0451C577` showed SpringBoard
PID 26 completing image activation, process/thread signature setup and final
return-wait clearing with result 0. Neither retained stream contained backboardd
or an actual GUI frame. The two streams have the same UUID, not two trials.

This narrows the observed boundary beyond those kernel checks. It does not prove
SpringBoard executed a user instruction, was runnable, or drew a frame. Existing
XNU can honor POSIX_SPAWN_START_SUSPENDED before clearing the final wait. The
recorded imageflags 0x41c are image flags, not posix_spawn attributes. There is no
basis to force-resume SpringBoard, bypass signing, or disable RunningBoard.

## Minimal follow-up

Patch 0169 reuses the existing 0164/0165 current-thread SVC and user-abort recorders.
It adds no syscall/exception hook, tracing subsystem, thread, process walk, task
lock or user-memory/stack read. All existing recorder and display limits remain.

The two existing arguments select independently:

| Argument value | SVC target (`sweetfftrace`) / abort target (`sweetfffault`) |
| --- | --- |
| absent, 0, invalid | Off |
| 1 | Existing exact truncated name `init_featureflag`, unchanged |
| 2 | Exact `SpringBoard`, explicitly requested |

The names of the historic `SWEET FFSVC` and `SWEET FFFAULT` log records remain;
they do not mean FeatureFlags was selected when the configured mode is 2.
A normal-context `SWEET UENTRY CONFIG` line identifies the selected modes.
`sweetffscreen` remains an independent 0/1 option and stays 0 in proposed GUI args.

SVC recording still uses the 64-entry ring / 65,536-event ceiling and the existing
40 sampler iterations, at most 2,560 dumped event lines. Abort recording still
uses the 4,096-record ceiling and the existing retained-console region only.
The same nonblocking guards, drop behavior, kernel-resident saved-state fields,
continuation handling and fault-return semantics are preserved.

## Owned spawn attributes

The existing GUI exec observer additionally prints `spawnflags` and
`spawnflags_valid`. Only `phase=spawnattr-return` has valid spawn attributes,
read at the existing caller's live kernel-owned `_posix_spawnattr` scope. The
16-bit flags are zero-extended. Other phases pass 0 and mark the field invalid;
that 0 must not be interpreted as an observed absence of START_SUSPENDED.

The observer never follows an attribute pointer during final-wait cleanup,
where attributes may already have been released. It does not read lock-protected
p_stat without its lock, acquire new locks, or change suspend/resume state.
All twelve existing observation calls retain their original phase/error inputs;
per-name 32-record quotas and all original exec/signature/control flow remain.

POSIX_SPAWN_START_SUSPENDED is bit 0x80. Its presence records an initial request,
not proof that the process remained stopped later. Do not remove the flag or
force task_resume based on this observation alone.

## Interpret the next evidence

- A selected current-thread SVC or user-abort event is positive evidence of
  reaching that user-origin trap path; it is not evidence of GUI completion.
- Correlate PC/LR, syscall, entry/return phases and PID within the same boot UUID.
  Use exact matching dyld/cache slides if symbolication is needed.
- Missing events can mean no hook reached, filtering, contention, exhaustion or
  lost logs. A last entry without a return is not by itself a deadlock.
- If a syscall/bootstrap dependency is identified, repair that narrow issue;
  do not repeat the old FeatureFlags diagnosis or broadly disable daemons.
- Actual grid/Dock, a UI update, same-boot GUI/DSI evidence and 120 seconds of
  stable observation are still required for the desktop milestone.

## Validation and state

Base: commit `d27162ecc57bc833ecd4b8ac8dc8ec559821449e`, successful full CI
`41070495854`. All three baseline files match that CI archive exactly.

Five new tests compile and execute actual C selection, SVC/abort producers,
startup configuration and GUI observer code. They cover default/invalid modes,
legacy target preservation, exact SpringBoard matching, exhaustion and drops,
state invariance, independent screen opt-in, owned flag reporting and quotas.
The complete policy suite has 51 passing tests, plus two existing fbcon tests.
Three files replay with fuzz 0 and match the intended sources byte-for-byte;
real ARM64 sweet_sample.c and kern_exec.c objects compile. New object marker
checks pass. New full linking, remote CI and runtime are not yet verified.

The ramoops policy checker retains its existing missing `d22ap.adt` warning; it
is not a new candidate ADT check. Proposed arguments stay 492 UTF-8 bytes and only
change `sweetfftrace=1` / `sweetfffault=1` to value 2 from the verified 0168 args.
This is a targeted diagnostic follow-up, not a demonstrated desktop fix.

0168 recovery is complete: original Android/physical boot pair/full clean root
verified, all write flags closed. Its complete actual 16-GiB scene has SHA-256
`877ceaff556442d7bbf76a67fb5d96235c2d732ef3031993f6bd11ccd50cefe8` and is
preserved with raw logs and sidecar. Read-only fsck found hardlink/directory/header
issues; the evidence was not repaired. The user subsequently authorized this five-file source batch for commit/push/full
CI and separately approved an equivalent COW replacement of this exact scene;
execution/results are pending. No new device trial or device write is authorized.
