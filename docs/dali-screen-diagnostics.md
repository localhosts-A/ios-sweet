# Visible startup diagnostics

Patch0165 adds two independent, default-off controls to diagnose the next
FeatureFlags boundary. It does not draw a fake home screen or bypass any boot
task. Physical output still needs a real-device photo/video; host tests and
linked strings do not prove that pixels reached the panel.

## Visible status: sweetffscreen=1

The existing normal sampler thread prints three concise status lines after
the normal call-ring dump and heartbeat, using the existing framebuffer console
and its newline-triggered retained DSI refresh. The opt-in mode clears the GUI
console-mute flag on each tick. It can keep the thread running even with
`sweetsample=-`, no heartbeat and the call recorder disabled. It does not turn
on user-stack sampling.

Typical output is:

```
SWEET SCREEN: tick=9 calls=450 faults=84 lost=0 full=0
SWEET SCREEN: last-call=-12 phase=1 pc=0x...
SWEET SCREEN: last-fault phase=1 pc=0x... esr=0x... result=0
```

These are examples, not captured results. The counters and registers are the
**last recorded transitions**, not a live sample of where the CPU is currently
executing. `faults=0` means there is no recorded fault; zero-initialized register
fields are not evidence of a successful fault. For call phases see
[dali-featureflags-trace.md](dali-featureflags-trace.md).

Use `sweetfb_noconsole=0` in the diagnostic candidate and `sweethidswipe=0`
to leave synthetic input disabled while observing text. This intentionally
keeps text over the framebuffer rather than preserving a clean GUI; it is not
the final desktop acceptance mode. Leave logical/physical geometry, root seed,
HFS semantics and other hardware exclusions unchanged. Recheck all bootargs
bytes and embedded ADT after packaging.

Output uses the existing configurable delay and interval and the existing
40-sample limit. No new timer, polling loop or redraw is installed in exception
context. A stale/frozen tick is an observation, not proof of a kernel deadlock.

## User-abort breadcrumbs: sweetfffault=1

Only current process `init_featureflag` (MAXCOMLEN16) and kernel-resident64-bit
saved user registers are selected. The helper copies pid/tid, pc/lr/sp, ESR,
fault address, phase and VM result. It never dereferences fault addresses or
user stacks and does not change the supplied registers or VM-fault result.

Phase0 is entry to handle_user_abort after its interrupt-context check. Phase1
is after a VM ref/modify-fault or vm_fault attempt, before the original result
handling. Result=-1 at entry is a sentinel, not a returned Mach error. Some
non-VM alignment/exception branches do not take the VM phase1 hook; absence of
a phase1 record alone does not prove that vm_fault blocked. Page-fault records
do not cover every synchronous exception class or prove the cause of a reset.

The producer formats at most256bytes on its kernel stack, then calls the
existing nonblocking `sweet_ramoops_append` RAM-only interface. No printf,
framebuffer/DSI, UFS operation, heap allocation, user copyin or process iteration
is performed by the fault helper. It shares the observer's try-CAS guard and
never waits for a preempted owner. Formatting and RAM output happen after
release. Contending producers set a loss flag; the underlying RAM appender can
also drop a record under contention without a callback. Therefore a missing
line is not proof that a transition never occurred.

At most4,096fault events are accepted. This bounds appended text to less than
1MiB, inside the existing2MiB console region; the pmsg/dump/ftrace areas are not
touched. Once exhausted, no newer fault is accepted: the status line reports
`full=1` and the retained last fault is explicitly stale. Sequence gaps and
cross-thread ordering must be considered. No storage/logger semantics change
is included, and RAM breadcrumbs are not assumed present in the UFS stream.

## Evidence and recovery constraints

The completed0164trial has different UUIDs in pstore and SWEET. The user
reported entering Fastboot only after the second restart. Later HFS errors
cannot establish the first cold boot's failure cause. The earlier trace's last
retained VM calls returned0 and map to dyld's wrappers, not a proven blocked
shm_open. No main screen was verified. Do not merge the two sessions.

Original Android/physical boot pair and clean16GiB prefix were restored. A
first chunk13readback mismatch was preserved and reviewed with repeat reads
and an exact whole-prefix comparison. Chunk13 was already clean and was NOT
rewritten; only the still-unwritten ranges continued using shell-v2 with remote
exit-status checks. Full final clean-prefix and stock hashes passed. A future
trial needs its own approval, full actual-root capture space and recovery plan.

## Verification

`python3 tools/test_dali_screen_diagnostics.py` compiles the actual new C header
and helper with adapters: quota/format boundaries, process/default filtering,
register immutability, trylock/loss, RAM-only faults and console output only
from the explicit normal-thread status helper. Hook placement and opt-in controls
also have structural guards. Local ARM64 objects and zero-fuzz replay supplement,
not replace, a new complete CI build/link and real screen evidence.
