# Bounded backboardd VM calls

Desktop is not achieved. Trial0172 (CI41174432129/commitcf8372a), boot
`5B3CA7D0-EE34-4D17-88EF-21454878DCDD`, recorded four actual GIC timer EL0 PCs
from backboardd57/TID315. Three resolve independently to distinct dyld LDRs;
PC4 occurred between stable selected-SVC snapshots. Those facts do not prove a
page fault, bad pointer or deadlock. No GUI present/refresh or real GUI was seen.

The exact dyld version is1067. Its trap10/12/14 cluster proves dyld slide
0x2e0f4000 for that boot, not a shared-cache slide. The last selected SVC was a
successful close, not the previous boot's getentropy. Never merge those boots.
The real syscall550 calls vm_map_with_linking: it is not a success-only stub.

## The one question

Patch0173 observes the immediate boundary around actual `vm_fault()` in
`handle_user_abort`, after the fast-fault decision. It asks whether an accepted
call boundary and its return were observed, with raw result and bracketed elapsed
time. It does not observe every abort or claim physical I/O. Fast-fault success,
non-VM aborts and unrelated processes are excluded.

Default off: explicit `sweetbbvm=1` and `sweetfftrace=3` are both required, plus
exact backboardd and a real64-bit AArch64 EL0 saved state. The producer never
retains process/thread/map/frame pointers, reads user memory, allocates, waits,
prints, appends retained-RAM logs, or walks/locks tasks. A local integer token
crosses VM; no observer guard or new preemption disable crosses it. VM's returning
THREAD_CONTINUE_NULL blocking preserves that ordinary caller stack.

Entry is immediately *before* the call: preemption between those instructions
is possible. Duration includes scheduling, VM work/retries, applicable cleanup
and I/O throttling, and small observer-boundary overhead. KERN_ABORTED is not
classified as successful resolution. KERN_SUCCESS alone is not proof that the
faulting instruction subsequently completed, main ran or GUI progressed.

## Fixed budget and honest losses

- Independent40 never-reused slots for the entire boot; accepted entries at
  least30seconds apart, globally, even across PID/TID changes and snapshots.
- Zero token means unaccepted; timestamps may legitimately be zero. Normal
  startup performs checked round-up to absolute ticks; use subtraction only
  after ordered timestamps. Overflow/invalid intervals reject configuration.
- Entry stores scalar PID/TID, PC/LR/SP/CPSR, ESR, original FAR, effective VM
  address, protection and start time. Raw FAR and TBI-cleared VM address differ.
- Return updates its owning token/TID's slot, without process-name reselection,
  a new quota charge or interval check. Out-of-order returns are allowed;
  duplicate/wrong-owner/invalid tokens do not mutate records.
- Quota or backwards entry-clock stops *new entries*, not accepted completions.
  A backwards end-clock retains result/end time with invalid/zero duration and
  stops further entry admission. No wrapped or negative duration is invented.
- Both producers make one independent CAS guard attempt. Busy entry costs no
  quota/time; busy return can lose a real completion. Separate sticky begin/end
  busy flags indicate possible loss, not its count or which call was affected.
- Per-slot exported versions preserve a late completion after an entry dump.
  Completion before first dump still exports entry and return separately once.
  No PC total-minus-dumped cursor is reused for these mutable records.

The existing normal sampler alone outputs `SWEET BBVM`. It snapshots under its
independent guard and prints after releasing it. At most40 existing iterations:
no new timer/thread/drain loop. Capture activates only when that thread starts;
creation failure never arms a producer. At final sample39 capture closes before
one snapshot attempt. A previously admitted guard owner can finish its bounded
update; a busy final attempt reports incomplete evidence, not a global freeze.
Late ends do not update after closure; no reopening or guaranteed final export.

Therefore an entry with no exported return means **completion not observed by
the snapshot/cutoff**, not "VM still running" or "deadlocked". No accepted record
also does not prove no VM calls: filtering, rate, activation, contention, budget
and cutoff matter. Raw absolute timestamps share the0172 PC/SVC-progress clock;
printed batch order is not time ordering.

Hard bounds: persistent recorder+snapshot storage≤32KiB (compile-time asserted),
40slots,80transition exports,1config+40summary/busy+80data≤121newlines.
0174 compact v2 entry/return lines, including newline, are at most255bytes:
at most30,855 VM log bytes. The earlier512-byte formatter bound did not cover
the real console's255-byte limit; two0173 return tails were clipped. The original
partial records remain incomplete; never infer their missing flags. Compact
entries carry register/ESR/FAR/effective-address/start context; returns carry only
token/PID/TID/start/end/result/duration/validity and are joined by explicit identity.
The pure policy and producer/consumer state machine remain unchanged. Worst-value
formats now run through the real console_printbuf_putc/clear path without
SYNCDRAIN; the old long format is a negative control. No repeated pending prints.

Old SVC/PC/screen layouts, old4096fault recorder and fault3-off semantics, IRQ
routing/prologue/timer/EOI, scheduling, WDT and GUI/HID behavior are unchanged.
`ufscache` is a DMA-maintenance switch, not a speculative read-cache speed fix.

## Validation and deployment boundary

Tests execute final0174 C policy/producers/consumer and the actual VM branch:
default filters, register/argument/result transparency, actual-call versus fast
success, blocked mock VM entry export, rate/quota/clock bounds, wrong tokens,
late/out-of-order completion, concurrent admission/consumer, final overlap/busy
cutoff, finite startup/creation-failure handling, and line/memory/export limits.
Mocks use real acquire/release CAS, thread-local ownership and deterministic
barriers; they do not prove a kernel fault or runtime fix. Keep warnings as errors
and Darwin macro isolation. 0174 format base is successfulCI41181923386/8081a41; the unchanged VM-call
observation began in0173. See dali-backboard-ipc-context.md for the new IPC scope.
Local replay/objects/full regression and remote build/link must be reported
separately; no host test is desktop acceptance.

A future separately approved candidate adds only sweetbbvm=1, keeping0172 PC,
SVC3/fault0,5s initial delay/30s sampler interval, no old user-stack walker,
serial0/log30, GUI/HID/WDT and the1200s external limit. Do not extend that limit
to finish a VM call or120s desktop observation. Publication/full CI/device trial
are separate approvals; host-plan approval authorizes none of them.

Use the retained NEW b4ee clean16GiB, verified07ba seed and3c522fd5 sparse delta;
never old098 or its block map. Recheck all hashes/capacity. Preserve original
760aec98 and0adb complete scenes without repairs/replacements. Trial ends with
stock physical-pair/Android verification, full actual16GiB capture/triple hashes,
all256chunk comparison and pinned-source NEWb4ee recovery/readbacks/fullhash;
then close all four write flags. Root writes stay within the first16GiB and logs
within their existing bounded window; no GPT/userdata/unbacked-tail changes.

Only matching real desktop grid/Dock, a UI update,120seconds stable and same-boot
GUI submission/successful DSI refresh satisfy the goal. Lock/Setup/logs and these
VM records do not. State explicitly if only synthetic input was used.
