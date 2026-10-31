# Backboardd interrupted user PCs

The latest 0171 trial, boot `C33A157A-325D-4322-B429-F6D252DE9CCB`, reached
backboardd user-origin traps (PID57/TID312, 320 accepted SVC events, 192 dumped).
Its last getentropy entry/return succeeded. Samples 30–39 observed no new selected
SVC events, but did not establish a deadlock or even absence of all SVCs: the old
hook deliberately skips absolute/continuous-time traps and platform syscalls.
No GUI client/frame/refresh or real GUI was observed. More waiting is not a fix.

## One proven source, independent finite budget

Patch 0172 adds one observation call in the existing SWEET GIC virtual-timer IRQ
branch, after its prologue and before `rtclock_intr`. The IRQ entry assembly
publishes this frame in CPU_INT_STATE while ordinary preemption is disabled;
the epilogue clears it. The call passes this IRQ frame, the published frame and
CPU number. Admission rejects NULL, mismatched frames, non-64-bit saved states,
non-AArch64-EL0 CPSR, and any current process except exact `backboardd`.

The PC is the exception-entry ELR/user resumption address, not the last retired
instruction. Do not apply an SVC PC-minus-four adjustment. There is no fallback
to find_user_regs/upcb, no process lookup/walk, task lock or user stack read.
The constant TRUE passed to rtclock_intr is not a user-mode test. Other IRQ/IPI,
spurious interrupts and generic FIQ paths do not call this observer.

- Default off; both `sweetffpc=1` and `sweetfftrace=3` are required.
- Independent fixed 40-event boot-lifetime buffer and CAS guard, at least 30
  seconds between accepted PCs. PID/TID changes and snapshots do not reset it.
- It does not change the old 64-entry SVC ring, 65,536 accepted SVC ceiling,
  4,096 fault ceiling, screen last-call meaning or 40-iteration sampler.
- It uses one nonblocking guard attempt; busy drops without consuming success
  quota or last timestamp. No IRQ output, allocation, waiting, UFS or ramoops I/O.
- Tick conversion/round-up occurs in normal startup and is checked by a reverse
  conversion. Zero/overflow is rejected. First absolute time zero is valid;
  a backwards clock stops admission. Delta subtraction avoids deadline overflow.
- Configuration is published with OSAtomic acquire/release CAS operations. IRQ
  admission stays off until the existing normal sampler thread begins; thread
  creation failure never enables it. At quota or clock stop it closes early;
  final sample39 closes before a single final snapshot attempt. A busy final
  snapshot explicitly reports incomplete evidence, not guaranteed export.

`SWEET BBPC` records carry explicit absolute time, CPU, PID/TID, PC/LR/SP/CPSR and
`source=gic-vtimer-el0`. No SVC phase/call/x0 field is overloaded. Old SVC log
format stays intact. Only when PC is configured, an extra normal-context SVC
snapshot marker gives selected_svc_total and its snapshot absolute time. Compare
these timestamps, not the order of two batches of printed lines.

## Candidate and interpretation

A future separately approved candidate adds only `sweetffpc=1`, retaining
`sweetsampledelay=5 sweetsampleinterval=30 sweetsample=- sweetfftrace=3
sweetfffault=0 serial=0 ufslogsecs=30`, GUI/HID/watchdog settings and the fixed
1200-second observation limit. The last nominal dump is at1175 seconds; actual
scheduling can be late. Do not extend the trial to finish a sample or a desktop
observation. Default-off behavior and non-DEVELOPMENT/SWEET builds remain isolated.

Accepted is not dumped or persisted. Busy is a flag, not a total loss counter.
No sample can mean no matching user-mode timer interrupt, kernel execution,
sleep, scheduling/filtering, contention, quota or consumer timing. Repeated PCs
support exact symbolication/inspection, not a deadlock verdict. A PC after a
stable SVC snapshot can establish user execution beyond that marker, not main,
check-in, responsiveness, GUI or physical touch support. Use the exact cache,
per-boot aligned slide and original PC semantics before any functional patch.

## Source and validation

Base: exact successful native CI41159220926, commit90f71b2. Both original sampler
and sleh bytes match that independently verified archive. The new pure-C policy
header owns the independent buffer/rate/quota logic. Ten tests execute final
policy, producer, startup/consumer/SVC dump and the real timer-branch fragment.
They cover defaults/modes/EL0 provenance, malformed frames, rounding and clock
bounds, register invariance, PID changes, independent SVC guard/buffer, final busy
closure, thread failure, finite schedule, two-CPU producer/consumer contention
and disabled-build call-site isolation. Source scope assertions supplement—not
replace—these executable checks. All fixtures keep warnings as errors and
Darwin-safe macros. Mock contexts do not replace the assembly provenance audit.

Real ARM64 sampler and sleh objects compile after zero-fuzz/zero-offset replay.
All 86 local regression cases, repo validation and ramoops ADT checks pass;
remote full-link checks remain outstanding. The native
macOS15Arm64/Xcode16.4 legacy-iig adapter and four-field IORPC ABI are unchanged.
No new kernel/link/runtime/desktop success follows from a host test alone.

## New storage authority

The old098 clean image was deleted without backup. After separate explicit
approval, the phone was restored to healthy new baseline
`b4ee77882c02dc0074da4dba0a229e682f2ab437cdd23b7f02c6012ebd8683bd`;
original Android/physical boot pair and the full16GiB hash passed, all device
write flags closed. A complete independent recovery copy is retained outside
Git and .tmp in `backup/dali-ios-baseline-20261031-b4ee/`.

Future roots must reapply only the prior two logical persona edits to a private
copy of this new baseline, verify file semantics/permissions/fsck, and derive
sparse runs by full16GiB comparison. The fresh seed independently verifies to
`07ba90d78e3255b768d014f0836298c04dca291a77186be0772069d35c591a4e`;
its sparse SHA-256 is
`3c522fd51d3cc06945f44522b7c85701936c702a522dd9444e5c1bfcd9201b18`.
The independently rederived delta has 9 RAW runs / 11 blocks / 45,056 bytes and
replays to the full seeded hash; read-only fsck returns 0 and file checks permit
only the original two logical edits. Equal block counts do not mean equal deltas.
Never reuse the old098 root's block map, seeded hash or restore hardcodes.
Recovery must hold its verified source file
descriptor open through writes/readbacks; a missing pathname must not lead to a
new unverified source. Latest complete actual scene0adb... remains unrepaired.
Publication, full CI, exact candidate trial and evidence replacement require
separate approval. Desktop remains real grid/Dock, a UI update, same-boot GUI/DSI
correlation and120seconds of stability, not these diagnostic records.
