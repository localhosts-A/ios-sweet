# Bounded Dali GUI load progress

Patches 0175–0176 provide a development-only, default-off observer (`sweetload=1`).
It does not repair scheduling, IPC, VM, HFS, or UFS. A successful build or
observation is not proof that SpringBoard has rendered a desktop.

## Why this observer

The 0174 trial completed its 40 snapshots without a GUI present/refresh. The
selected backboardd stream exported 92 of 140 accepted SVC events, ending with
`map_with_linking_np` (BSD 550) and `close` (6) returning zero. BSD `getentropy`
is 500, not 550. There was no eligible exported Machmsg2 entry in that trial;
this does not prove absence of IPC. Three sampled VM calls returned, but their
30-second admission interval does not describe every fault or wait.

The missing evidence is whether the GUI tasks accumulate CPU time, what states
a bounded subset of their threads occupy at sample time, and how much elapsed
time returned global UFS operations consumed. None of these observations alone
identifies a causal desktop blocker.

## 0176: specific wait identity and observable counters

The 0175 trial identified sampled kernel-mutex waits in backboardd and later
port-receive waits in SpringBoard. All 40 UFS snapshot attempts lost the I/O mutex,
so no cumulative counts were observed. The persisted logger reached flush 40:
this is not evidence of one continuously owned mutex or deadlock. Patch 0176
repairs only these visibility gaps; the prior trial and its recovery are closed.

## Admission and ownership

The option accepts only value 1. The runtime must be Dali with a ready UFS root.
`sweetload` does not enable the old `sweetsample` user-stack backtrace. Keep
`sweetsample=-`. The existing sampler thread opens observation before its initial
sleep, and its existing 40 rounds own output and closure. Candidate delay/interval
remain 5/30 seconds; source defaults remain 90/120. Thread creation failure leaves
both new gates closed. No new thread, scheduler hook, timer, or process iterator
is introduced.

The existing `sweet_ff_trace` entry checks the new observer before applying the
old SVC mode and target filters. Only phase-0, 64-bit EL0 states qualify. Exact
current process names are `backboardd` and `SpringBoard`. The current-process
`proc_ident` contract supplies PID, unique ID and ID version; current task
ownership supplies a live object from which to take one task reference.

Two fixed slots admit at most one task each. One atomic control word contains
OPEN, CLOSED and both claim bits. A producer makes one compare-and-swap attempt,
then retains the task, fills immutable metadata, and release-publishes its ready
bit. The consumer acquire-loads readiness before reading a slot. A rejected or
busy claim never retains an object. Once both slots are claimed, registration
short-circuits before new current-object queries or clocks.

Coverage is **first successfully admitted observed user SVC after arming**, not
necessarily first syscall or first boot generation. The existing entry omits
abstime/conttime fast traps. No process scan fills a missing slot. No exit, exec,
or restart replaces a claimed slot. A missing target is not proven absent.

**At most two task references remain until reboot.** They do not keep a process
runnable or prevent termination, but delay final task/resource destruction. The
number of references is bounded; the indirect resources retained by them are
not included in the explicit diagnostic buffer budget. There is deliberately no
potentially blocking task deallocation in the SVC observer or finite consumer.
The configuration record declares `reference_policy=until_reboot`.

Final closure sets CLOSED in the same atomic claim word. No claim succeeds after
closure. A producer that claimed before closure may finish publishing afterward;
final reads readiness once and reports the unpublished slot as pending. It does
not wait, retry, replace a generation, reopen, or print post-window catch-up data.

## Scalar collection

`exception.c:sweet_load_collect` attempts each target task lock once. Under the
task lock it checks active state, copies task thread count and recorded CPU
counters, and inspects no more than eight attached threads. Removal from the task
thread list takes that same task lock. The existing `task_vtimer_set` path also
uses task-lock → `splsched` → thread-lock ordering.

Each thread scheduler lock gets one `simple_lock_try` under `splsched`. The
collector copies raw state, block hint, scheduler/base priority, wait result and
recorded CPU counters, then releases the lock and restores SPL. It holds no
additional per-thread reference. Task lock remains held until bounded collection
finishes. Formatting and output happen afterward.

There is no allocation, blocking lock acquisition, user-memory read, saved-user-PC
inspection, stack walk, or print while those locks are held. No waitq, runqueue,
turnstile, processor, or IPC object is followed. Busy, inactive and truncated
coverage are distinct from zero CPU or a waiting state.

`recount_task_times` / `recount_thread_times` return recorded cumulative Mach
absolute ticks without the retrying usage-reader seqlock. They do not force a
running thread's uncharged tail into the counters or freeze other CPUs. The
user/system split depends on `PRECISE_USER_KERNEL_TIME`; their sum is the main
execution measure. `b` and `z` bracket collection/accounting; state and accounting
are not an atomic system-wide snapshot. Regressions or overlapping observation
brackets make a derived delta unknown, never negative execution.

`TH_RUN` means running **or** queued; `TH_WAIT` can coexist with transition bits.
A block hint describes a primitive category, not a peer or storage cause. Do not
subtract CPU from wall time or `runnable_timer` to invent exact runnable delay.

The v2 thread record adds `uw`: 0 unknown, 1 not applicable, 2 another kernel
mutex, 3 the known UFS mutex. A successful stable WAIT without RUN/WAKING and
with KernelMutex hint permits a scalar event comparison while holding the same
scheduler lock. Other transient/unknown combinations remain unknown. No event is
dereferenced or reverse-converted, and no event/mutex address or owner is logged.

The UFS mutex remains static. A typed internal accessor returns only that known
boot-lifetime object. A guarded diagnostic helper beside `LCK_MTX_EVENT` in
`lock_mtx.c` uses the canonical event expression; it is not the mutex base address.
Existing mutex primitives are unchanged. A match identifies the registered wait
at that snapshot, not its owner, duration, or cause. A mismatch excludes only that
specific event at that instant, not all UFS-related waits.

## Global UFS aggregates

The existing `sweet_ufs_lock` serializes all post-ready normal SCSI wrapper calls.
No new producer lock is added. Five groups are maintained:

| g | Scope | Fields beyond version/sample/group |
|---|---|---|
| 0 | Returned READ(10) wrapper calls | n calls, b requested bytes, e errors, vld valid durations, d elapsed sum, m maximum, x bad clocks |
| 1 | Returned WRITE(10) wrapper calls | same |
| 2 | Returned SYNCHRONIZE CACHE wrapper calls | same |
| 3 | Measured read strategy requests | n requests, vld valid envelopes, w lock-acquisition sum, wm maximum, d owned-service sum, m maximum, x bad clocks |
| 4 | Measured write strategy requests | same |

These are **returned wrapper calls**, not necessarily completed hardware commands.
Timeouts and poisoned early returns also count. Bytes are requested, not verified
transferred bytes. Initialization—including initialization wrapper calls—is
outside the gate. Existing CDBs, DMA maintenance, MMIO, timeout, completion/sense
validation, poisoned behavior, buffer operations and lock scope are unchanged.

The wrapper adds at most two clock reads. Strategy adds at most three, immediately
before its old mutex acquisition, after acquisition, and before release. Clock
rollback increments x without a valid duration. Counters saturate, setting a
sticky overflow status. A saturated or invalid denominator must not be used for
an apparently precise average.

Only starts admitted in the open window can be recorded. Completion rechecks the
gate before and after the end-clock read, while holding the existing mutex. A
completion already admitted immediately before closure may finish updating its
scalars and mirror while still owning the mutex. Operations starting before
opening or completing after admission closes are not retroactively recorded.

0176 keeps those plain counters protected by the original mutex but publishes an
independent atomic mirror. The serialized publisher SC-loads the generation,
SC-stores an odd generation, the changed group's nine fields, global saturation,
the existing source end timestamp, and the next even generation. Other groups
retain their most recent values. This is 14 mirror atomic operations per ordinary
publication (one load, thirteen stores), with no additional clock or producer lock.
It adds memory-ordering overhead; it is not a zero-perturbation performance fix.

The reader makes one bounded pass: SC-load generation, 45 group scalars and two
metadata scalars, then SC-load generation again (49 loads maximum). All concurrent
mirror accesses are atomic. It accepts only an equal, nonzero, even generation.
A single SC order and absence of ABA exclude an intervening publication bracket;
the result is a coherent cumulative state, not five equally fresh measurements.
No retry loop, mutex acquisition, plain concurrent memcpy, `_wide` atomic helper,
or new worker is used. Output consumes only a private accepted snapshot.

Generation zero means unpublished, not measured zero I/O. UINT64_MAX is terminal
exhaustion: after the last valid even generation, the next publisher latches that
terminal odd value before touching payload or performing overflowing arithmetic.
Further mirror publication stops, but true I/O and original counters continue.
Never clear/reuse the mirror or rearm the observer while readers can exist.

The source timestamp is the existing end-clock reading, not the publication
instant. Final closure still precedes one read; a pre-close admitted publication
may complete afterward without catch-up output. Even a successful final copy has
`pending=unknown`: it is last-published returned-only data, not evidence that no
outstanding request remains. Invalid clocks prevent invented freshness estimates.

WRITE includes root and logger operations; SYNC includes ioctl and both logger
payload/header syncs. Partial writes may perform READ then WRITE. Wrapper time is
nested in strategy service time. Do not add/subtract these aggregates to derive
exclusive time, CPU usage, pure device latency, interval utilization, or per-task
I/O. All elapsed values include software and possible preemption.

## Versioned output and hard bounds

- `SWEET LOAD CONFIG`: source, limits, recorded CPU units, reference policy.
- `SWEET LOAD`: s round, at timestamp, claims/ready masks, sticky missed-claim
  indicator (not a count), final. `claims & ~ready` is pending publication.
- `SWEET LOADP`: g target, p/id/iv identity, rg registration timestamp, q status.
  q=0 also includes n total threads, c inspected, b/z accounting bracket, u/k
  raw recorded user/system ticks. q=1 means task busy; q=2 means inactive.
- `SWEET LOADT`: v2 adds uw wait classification to successful records, retaining
  g target, j subset index, th identity, raw st/h, pr/bp, wr and b/z/u/k. Busy q=1
  still omits unavailable measurements. Legacy v1 does not contain uw; never
  synthesize it from a hint alone.
- `SWEET LOADIO CONFIG/STATUS`: v2 status q=0 last-published, 1 unpublished,
  2 writer-active, 3 crossed-write, 4 exhausted. All carry gn observed generation;
  q=0 additionally carries z read end, src existing source end and ov saturation.
  at precedes the mirror read. Only q=0 emits groups; pending=unknown always.
  Legacy v1 busy remains busy, with no invented generation or source timestamp.
- `SWEET LOADIO`: one of the five aggregates above.

All lines, including newline, must be at most 255 bytes. Truncation is not accepted
as a complete record. At most 40 rounds produce 761 GUI lines plus 241 UFS lines:
**1,002 lines / 255,510 bytes**. Explicit GUI control/records/formatter scratch are
bounded at 8 KiB and UFS at 2 KiB. Per round there are at most two task try-locks,
16 thread try-locks, two task and 16 thread recount calls, and one bounded UFS
mirror read with no I/O-mutex try-lock.
These are count/storage/output bounds, not a hard wall-clock deadline. Additional
output can change log-ring retention and scheduling overhead.

`tools/test_dali_gui_load_progress.py` contains strict raw parsers and a same-boot
parser that preserves malformed rows, duplicate records and missing coverage.
Consumers must join process/thread accounting only by boot and stable generation
identity. A different generation, missing field or regressing counter is not zero.

## Validation and approval boundaries

Host tests execute the final patched producer, collector, UFS wrapper/strategy,
startup and consumer bodies. Differential tests preserve the old SVC/ring/IPC and
UFS behavior. Concurrent registration, final pending publication, failed startup,
40-round closure, lock busy/inactive/truncation, arithmetic boundaries, strict
parsing and real console255 negative/positive paths are covered. Darwin fortified
bzero and strlcpy negative/guarded dual-positive controls retain `-Werror`.
Tests now integrate the real canonical comparator with the real collector and use
a deliberately nonzero mutex-data offset and nondereferenceable sentinel events.
Serialized concurrent publishers and deterministic odd/crossed/wrap interleavings
exercise the actual mirror helpers with faithful seq_cst host atomics. A held-I/O-
lock negative control reproduces the old busy copy while the new reader succeeds.

Set `SWEET_LOAD_SOURCE_DIR` to a materialized XNU source root to require byte-for-byte
identity for all seven final files (explicit0175 base plus the five-file0176
overlay, including lock_mtx.c; the existing Makefile remains unchanged). CI performs policy tests before Build and a
separate final-source invocation after Build. The shared header is explicitly
listed in `osfmk/kern/Makefile` XNU_ONLY_EXPORTS; local success must not depend on
an extra include flag missing from native CI.

Four real affected objects include lock_mtx.c and the sampler using the changed
shared formatter header. Local objects are not full-link proof. Publication/full native CI and a new exact
hardware candidate each require separate approval. Preserve the clean b4ee root,
original boot pair, complete raw scenes, logs, unbacked tail and existing partition
layout. Never use these diagnostics as permission to prolong a trial or bypass
full scene capture before restoration. Real desktop acceptance still requires a
visible SpringBoard grid/Dock, an actual UI update, 120 stable seconds, and matching
GUI submit plus successful DSI refresh in the same boot. Synthetic input must be
identified; physical touch support is not implied.
