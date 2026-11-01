# Bounded GUI RW-wait provenance

This development-only observer identifies the **acquiring kernel call site** associated with a sampled exclusive RW wait. It does not fix a lock, identify its owner, prove deadlock, or establish a running SpringBoard desktop.

## Enablement and scope

The implementation is in `0179-sweet-gui-rw-wait-provenance.patch` and builds on the existing GUI load sampler. It is compiled only for the XNU-private SWEET development view. It requires **both** `sweetload=1` and `sweetloadrw=1`, plus the existing Dali/UFS eligibility check. Missing, zero, and other values of `sweetloadrw` leave it off. Configuration is atomically published during sampler initialization; the RW path does not parse boot arguments.

This describes source behavior, not authorization to deploy it. The current delivery scope is host implementation/testing only: no commit, push, CI dispatch, deployable candidate, device operation, or root-image change. A previous spent trial cannot be resumed by changing its manifest or permissions.

## Capture and identity

The registry is in `bsd/dev/sweet_sample.c`, **not** the UFS driver. Its two once-admitted task slots retain references until reboot; their registration-time PID, unique ID, and version are release-published with readiness. The observer neither replaces those slots nor adds references.

Admission reads at most two ready slots and matches the current task, then checks the current process's PID/unique-ID/version against that immutable registration. It rejects unavailable/mismatched identities, closed admission, and interrupt context. The current-process identity accessors are scalar reads; this is not a `proc_find` or process-list lookup. The ARM interrupt check reads the interrupt-enabled state and current CPU interrupt-state pointer without acquiring another lock.

One 32-byte record is attached to **every SWEET development thread**, not just the sampled threads. It contains:

- an atomic generation;
- the canonical writer-event cookie;
- the acquisition wrapper's return PC;
- a packed target/site key;
- a sticky poison word.

No owner CTID, thread pointer, stack, history ring, timestamp, or extra lock pointer is retained. A canonical event is captured from the valid lock argument; readers must never reverse-convert or dereference it.

The only publication sites are the two waits in `lck_rw_lock_exclusive_gen`:

1. waiting for an existing exclusive-writer intent;
2. waiting for shared readers or an upgrade to drain.

The separate shared-to-exclusive upgrade wait is unchanged. The external lock APIs are unchanged. Existing wrapper callers are forwarded through the static slowpath; the check-contended API captures its own entry return address. There is no higher-frame return-address builtin or stack walk.

## Wait lifecycle and concurrency

Publication occurs **after** `assert_wait` returns `THREAD_WAITING`, before releasing the existing RW interlock. Rejected assertions publish nothing. The record is invalidated immediately after `thread_block` returns, before retrying acquisition, even if the sampling window has closed. Final sampling closure does not erase records of outstanding long waits.

Publication means “a wait was registered,” not “a context switch occurred.” A wake between wait registration and `thread_block` is legitimate; the scheduler-state checks below reject the stale association.

Only the attached thread writes its record. IRQ admission is explicitly rejected, and readers do not write it. All concurrent fields use real `os_atomic_load/store` operations with `seq_cst` ordering. A sequence around ordinary concurrent struct stores would not be sufficient. Publication and clearing bracket payload updates with odd/even generations; neither spins nor uses CAS. Each reader attempts one snapshot without retrying.

An active-record overwrite, unexpected nesting, clear-token mismatch, invalid producer inputs, odd writer generation, or impending generation wrap permanently poisons provenance for that thread. The poison word is independent and is never reset: even an unexpectedly interrupted outer publication cannot make a poisoned record usable again. Exhausted views report `UINT64_MAX` as a terminal **status sentinel**, not a newly accepted wait generation.

Thread storage starts at zero through the existing thread template. Task-locked thread-list ownership and the existing scheduler try-lock protect sampling lifetime. No record or thread pointer survives collection. A retained task address alone is not treated as a refreshed process identity.

## Sampling and output

The collector brackets scheduler state/hint/event observation with the record's sequence checks while holding its existing scheduler lock. A match requires:

- coherent, nonzero even generation and no poison;
- an active record for the expected admitted target and attached thread;
- `TH_WAIT` and neither `TH_RUN` nor `TH_WAKING`;
- `kThreadWaitKernelRWLockWrite`;
- exact equality of the live wait event and stored canonical cookie.

Different attempts on the same event receive different generations. Repeated matches in discrete samples do **not** prove an uninterrupted wait between them. A match is not an owner relationship or elapsed sleep measurement.

`LOADT` formatting remains byte-for-byte unchanged. The new independent row is emitted only after locks have been released:

```text
SWEET LOADRW: v=1 s=3 g=1 j=0 th=0x2 q=0 gn=0x2 ev=0x1001 ca=0x2000 site=1
```

The numbers above are synthetic. Join rows by boot UUID, sample, target slot, thread index and TID, retaining the corresponding `LOADP` registration unique ID/version. Do not join by PID, index, event address or generation alone, and never join across boots. Caller values are acquiring-kernel return positions; they are not user PCs or last retired instructions. Symbolication must use the exact kernel UUID/slide, not addresses borrowed from another boot or a blind subtraction of four.

| `q` | Meaning | Additional interpretation |
| --- | --- | --- |
| 0 | Matched snapshot association | Includes `ev`, `ca`, `site`; not owner/deadlock proof |
| 1 | Unpublished | Generation zero; no usable source yet |
| 2 | Writer busy | Odd generation, no usable source |
| 3 | Publication crossed the read | No coherent source; no retry |
| 4 | Poisoned/exhausted | Terminal generation sentinel; unknown thereafter |
| 5 | Inactive record | Prior wait cleared; not evidence of no other activity |
| 6 | State/event/target mismatch | Source must not be attributed to the sampled wait |
| 7 | Unavailable | For example, scheduler try-lock unavailable or no matching source |
| 8 | Formatter failure | Bounded fallback row, not silently missing evidence |

Nonmatched rows omit event/caller/site; stale payload fields must not become zero-valued observations. The host parser rejects unknown versions, duplicate/missing fields, invalid generations, malformed matches and oversized/incomplete lines. Old logs without this observer remain `rw_support=unknown` rather than “no RW waits.”

There is at most one extra row per inspected thread, within the existing 40 rounds × 2 targets × 8 threads: **640 data rows**. Actual worst-width successful formatting is **130 bytes**, including newline, or **83,200 extra data bytes** at that bound, plus a fixed configuration line. The existing console rule remains `n > 0 && n <= 255 && n < bytes`; no unbounded event stream or new sampling thread is added.

## Host validation and limits

`tools/test_dali_gui_load_progress.py` executes the production header, actual wait fragments and actual collector with controlled host boundaries. Coverage includes disabled modes, registration/version/IRQ rejection, final closure, failed assertions, wake/state mismatches, same-event re-waits, poisoning and exhaustion, deterministic partial/crossed publication, one real writer racing a reader, and the real console framing path. The original GUI/UFS tests are retained; warnings remain errors.

Independent verification replays the patch with zero fuzz and zero offset against the exact previously accepted CI archive, checks changed and unchanged source bytes, and reverses only the observer/caller additions to confirm unchanged lock primitive bytes. Formal private headers are refreshed according to `XNU_ONLY_EXPORTS`, not by ad hoc include search paths. Real ARM64 objects include the HFS cnode object using its original KPI compatibility flags. The small atomic object probe is host-only and is **not** part of the kernel Makefile or patch.

Actual ARM64 layouts are `sizeof(thread)` **1528 → 1560**, alignment **8 → 8**. The existing rounded `uthread_size` addend therefore does not change; the requested thread-zone element size increases by 32 bytes. Live zone bucket, page and resident-memory rounding has **not** been measured and must not be reported as exactly 32 physical bytes per thread.

Local object generation and host concurrency tests do not establish complete native linkage, actual scheduling correctness, firmware behavior or GUI success. A future full native CI and any integrated device trial require their own approval and exact candidate identity. Desktop acceptance still requires the visible icon grid and Dock, a real UI update, same-boot surface/DSI evidence and 120 seconds of stable visibility.
