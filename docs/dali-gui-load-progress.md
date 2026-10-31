# Bounded Dali GUI load progress

Patch 0175 adds a development-only, default-off observer (`sweetload=1`).
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
scalars while still owning the mutex. The final snapshot either copies consistent
counters after unlock or reports busy; it never waits. Operations starting before
opening or completing after admission closes are not retroactively recorded.

Each consumer round makes one UFS try-lock attempt, copies fixed scalars, releases
the lock, then prints. Busy emits status only, not stale counters. Even a successful
final copy has `pending=unknown`: returned-only aggregation cannot establish that
no outstanding request remains.

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
- `SWEET LOADT`: g target, j subset index, th stable thread ID, q status. q=0 also
  includes st raw state, h block hint, pr/bp priorities, wr wait result, b/z/u/k.
  q=1 omits unavailable state/accounting rather than emitting measured zeros.
- `SWEET LOADIO CONFIG/STATUS`: scope, units, actual snapshot bracket, q result,
  ov saturation, final and pending=unknown. Busy status omits counters.
- `SWEET LOADIO`: one of the five aggregates above.

All lines, including newline, must be at most 255 bytes. Truncation is not accepted
as a complete record. At most 40 rounds produce 761 GUI lines plus 241 UFS lines:
**1,002 lines / 255,510 bytes**. Explicit GUI control/records/formatter scratch are
bounded at 8 KiB and UFS at 2 KiB. Per round there are at most two task try-locks,
16 thread try-locks, two task and 16 thread recount calls, and one UFS try-lock.
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
bzero controls retain `-Werror`.

Set `SWEET_LOAD_SOURCE_DIR` to a materialized XNU source root to require byte-for-byte
identity for all six affected files. CI performs policy tests before Build and a
separate final-source invocation after Build. The shared header is explicitly
listed in `osfmk/kern/Makefile` XNU_ONLY_EXPORTS; local success must not depend on
an extra include flag missing from native CI.

Local objects are not full-link proof. Publication/full native CI and a new exact
hardware candidate each require separate approval. Preserve the clean b4ee root,
original boot pair, complete raw scenes, logs, unbacked tail and existing partition
layout. Never use these diagnostics as permission to prolong a trial or bypass
full scene capture before restoration. Real desktop acceptance still requires a
visible SpringBoard grid/Dock, an actual UI update, 120 stable seconds, and matching
GUI submit plus successful DSI refresh in the same boot. Synthetic input must be
identified; physical touch support is not implied.
