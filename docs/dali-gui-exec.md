# Dali GUI exec boundary

Patch 0168 narrows the next desktop blocker after the 0167 trial proved
`init_featureflags` exited normally and launchd completed early boot. The existing
`exec SpringBoard` line is inside a platform-permission branch, before process and
thread signature setup. It is not proof that SpringBoard entered userspace.

This patch is observation only, not a GUI-startup fix. It does not change signing,
service policy, task scheduling, signal delivery, exec errors, or return waits.

## Opt-in and bounds

- `sweetguiexec=1` explicitly enables observation; absent, zero and other values
  produce no new observations. Non-Sweet builds compile calls to no-ops.
- Exact kernel process names: `SpringBoard`, `backboardd`, `xpcproxy`, `notifyd`,
  `runningboardd`. No user pathname, argv, user register or stack reads.
- Each name has an independent 32-record budget for the entire boot, across all
  matching processes. Maximum total: 160 lines. Frequent xpcproxy starts cannot
  consume SpringBoard/backboardd quotas.
- One atomic reservation attempt per observation; contention drops the record,
  with no explicit retry/wait loop. `n` is per-name, not per-PID or a timestamp.
  The last accepted record has `exhausted=1`. Records may print out of order.
- No new thread, process enumeration, proc/task lock, allocation or hardware
  access. Uses existing normal printf/console/log infrastructure. GUI console
  suppression after the first real swap remains unchanged.

## Recorded phases

Every line has `name`, `pid`, `phase`, `result`, a 64-bit `csflags` snapshot,
`imageflags`, `n` and `exhausted`.

| Phase | Meaning / limit |
| --- | --- |
| `activate-return` | Image activation returned and any successful exec proc switch completed. Uses the current proc reference, not the original parent after a successful switch. Nonzero activation errors may still have the old process name; this is not a requested-filename trace. |
| `signature-enter` | Reached process_signature; result 0 is an entry placeholder, not a completed check. |
| `proc-signature-return` | Raw errno from proc_process_signature. |
| `thread-signature-return` | Raw kern_return_t from thread_process_signature, before any conversion to errno. |
| `signature-failure-before-kill` | Existing signature failure, before SIGKILL delivery and before the spawn branch can reset error to zero. CS_KILLED is set by the original path unless this is an unexpected failure. |
| `signature-final` | Final process_signature return value. Zero can coexist with a prior kill/failure record. |
| `spawnattr-return` | Existing spawn-attribute policy function returned (spawn path only). |
| `release-before` | About to clear TCRW_CLEAR_FINAL_WAIT, including existing failure cleanup paths. |
| `release-after` | That kernel return-wait clear returned. Not proof of a user instruction, main(), sustained liveness, a GUI client, or a frame. |

Activation and final-wait boundaries cover both posix_spawn and __mac_execve;
signature observations use their shared implementation. All observations occur
before existing proc/thread reference releases. No existing statements are removed.

An activation return of zero can already conceal a failure after the point of no
return in the original XNU path. Do not equate it with a successful launch.
Similarly, missing records can mean filtering, budget exhaustion, contention,
log loss, a different boot, or not reaching the hook; absence alone is not deadlock.

## How the next evidence is used

1. Split retained streams by boot UUID; never join the two 0167 UUIDs.
2. Match phase records by name/PID within one boot and account for quotas.
3. A concrete signature errno/kern_return_t is the narrow next repair target;
   preserve enforcement rather than bypassing it.
4. If signature and final-wait clear complete without an observed failure, inspect
   the next bootstrap/dependency or actual exit; do not keep blaming the earlier
   FeatureFlags fault, and do not forcibly rewrite launchd on-demand policies.
5. Retain real GUI client/surface/DSI evidence and the screen as the desktop test.
   None of these phase records satisfies the grid/Dock/UI-update/120s criterion.

## Local validation (2026-10-30)

Base: commit `6edb68f0d7850a1705893dd5660883df826acbe2`, CI `41055548976`.
The baseline kern_exec.c was compared byte-for-byte with that CI buildtree.

- `python3 tools/test_dali_gui_exec.py`: five tests compile and execute actual C
  policy/observer code, including eight concurrent producers, exact name
  filtering, independent quotas, default-off behavior, state invariance, and the
  actual failure fragment retaining its errno before spawn zeroes it.
- Full existing policy suite plus new tests: 46 tests pass; existing framebuffer
  console refresh checks: two more pass.
- Two source files replay with fuzz 0 and match the intended output exactly.
- Actual ARM64 kern_exec.c object compiles; the new output and phase strings are
  present in the object. Full linked kernel is not yet verified.
- Repository validation passes. Ramoops ADT policy check passes with the existing
  warning that `research/ipsw-x/d22ap.adt` is absent; it is not candidate ADT proof.
- CI workflow includes the new test and source/linked-string gates. They have not
  been run remotely for this patch.

Local evidence: `.tmp/desktop-diagnostics-20261030/gui-exec-local-validation.json`,
`gui-exec-compile-final.log`, `gui-exec-replay-v3.log`,
`gui-exec-all-tests-final.log`, and `gui-exec-tests-final.log`.

A proposed boot-argument file adds only `sweetguiexec=1` to the 0167 GUI-first
arguments (492 UTF-8 bytes, below 608). It is not an approved/deployed candidate.
The user subsequently authorized this five-file source batch for commit, push and
full CI; execution/results are pending. No device attempt, root write or image
flash is authorized.
Original Android/stock boot pair/clean 16-GiB root remain the latest verified state;
all previous trial write permissions are closed. A future full build and exact
single trial require their own approvals.
