# Dali backboardd user entry

The 0170 finite-budget trial and the same-kernel reduced-diagnostic comparison
both ended without a real GUI. In the comparison, UUID
`0A4B3D91-4D03-4BE3-AAA8-014B37DF146B` appears in both retained streams.
SpringBoard, backboardd, notifyd and runningboardd completed observed kernel exec
stages without errors and without an initial START_SUSPENDED flag. These facts
do not establish responsive userspace or GUI completion. The high-frequency
recorders were off, and no GUI client/frame/refresh was observed.

The frozen launchd log records approximately 505 seconds between backboardd's
xpcproxy spawn and service INIT, then later creation of its PID domain. These
are within-log timestamps, not host monotonic uptime. Neither event proves
backboardd reached its main function or checked in successfully. Reducing four
diagnostic settings did not suffice; this does not prove overhead was zero.

## Narrow extension

Patch 0171 reuses the existing current-thread SVC recorder. It adds no hook,
process walk, task lock, user-memory/stack read, thread or tracing framework.

| Argument | 0/absent/invalid | 1 | 2 | 3 |
| --- | --- | --- | --- | --- |
| sweetfftrace | Off | init_featureflag | SpringBoard | exact backboardd |
| sweetfffault | Off | init_featureflag | SpringBoard | Off |

The existing `sweet_ff_mode` retains only modes 1 and 2. A separate
`sweet_ff_svc_mode` accepts mode 3 for SVC production, draining and startup only.
Fault production/startup continue using the unchanged selector; fault mode 3
returns before `current_proc` or RAM append. Screen logging stays separately
opt-in and should remain 0. Legacy log labels remain `SWEET FFSVC` and
`SWEET FFFAULT`; the normal-context configuration adds
`svc3=backboardd fault3=off` to identify this capability.

All bounds are unchanged: 64-entry ring, 65,536 accepted SVC events, 40 sampler
iterations (at most 2,560 dumped event lines), one nonblocking atomic acquisition
per producer, existing contention/drop behavior, and default-off selection.
The 4,096 fault limit is unchanged, but the proposed follow-up keeps faults off.
No signing, suspension, service policy, UFS synchronization, watchdog register
or input/display behavior changes.

## Timing and interpretation

A future separately approved candidate may use `sweetfftrace=3 sweetfffault=0`
with existing `sweetsampledelay=5 sweetsampleinterval=30 sweetsample=-`. This
spreads the same 40 dumps across late startup instead of ending near half the
window. In a fixture where sleeps advance exactly as requested, the last dump
is at 1,175 seconds; real scheduling can be late, and no dump is guaranteed.
The external trial limit must stay 1,200 seconds, regardless of sampler delay.
Keep the current low-interference serial/log settings, GUI readiness gates and
finite HID/watchdog budgets; none is extended by this patch.

A selected SVC is evidence of a user-origin trap, not main-function completion,
process responsiveness or GUI. Correlate PID/TID/entry/return with the same boot
and exact binary/cache slide. Ring overwrites are not counted by `dropped=0`;
a last unmatched entry can mean sampling loss, exhaustion, contention or a real
wait and does not alone establish deadlock. Missing records do not prove the
process never ran. Repair only the dependency or failure established by the
next evidence, rather than adding generic logging or forcing services to run.

## Validation and deployment

Base: exact source commit `69f6b2ab616655c641135761f171c5a064843a57`, full CI
`41092719703`. Header and sampler baseline bytes were checked against its
independently verified buildtree archive before generating this patch.

Six new tests execute actual C selection/ring, SVC/fault producers, dump, startup
and sampler-thread fragments. They cover independent SVC/fault/screen modes,
exact matching, no register/process mutation, contention, caps, overwritten
ring draining, and the finite late schedule with no process walk for names `-`.
Warnings remain errors. Scope assertions supplement these executable tests.
Local validation passed: zero-fuzz/zero-offset replay of both sources, all 60
policy tests plus two existing framebuffer-console tests, repository/ADT gates,
and an actual ARM64 sampler object with the new mode marker. The other 23 key
source files still match exact CI 0170. Separate commit/push/full-CI approval
is still required. A local object
is not a linked kernel; no new kernel or trial is approved by this document.

The reduced-diagnostic trial has been fully restored: actual seven ranges
read back, complete clean 16-GiB hash and original Android/physical boot pair
verified, all four write flags false. Its full raw scene
`fe1d101a33b0f05f00fd84a09bba306e9c14adefd5ec7d8b2795961d6081ccfc`
is preserved unrepaired, with sidecar and raw logs. Read-only fsck exit 8 reports
hardlink count and bitmap orphaned-block issues, not proof of GUI failure cause.
No replacement permission for this scene or next device-trial authorization
has been granted. Real grid/Dock, an actual UI update, same-boot GUI/DSI evidence
and 120 seconds of stability remain the acceptance criteria.
