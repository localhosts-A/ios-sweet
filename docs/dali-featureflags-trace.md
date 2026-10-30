# Bounded FeatureFlags call observation

Patch0164 is a diagnostic, not a FeatureFlags workaround. It does not skip the
boot task, change its arguments, alter shared memory or fix HFS. Default behavior
is unchanged. `sweetfftrace=1` requires a newly verified candidate and a separately
authorized trial; no old approval is reusable.

## Runtime boundary

The completed CI41016180407 trial has matching SWEET/pstore session
BCF5B14A-4B62-44C4-B6BF-6BD4AD931E03. usermanagerd, dirs_cleaner and
xpcroleaccountd report final exit0. init_featureflags execs without an observed
exit before heartbeat9. Display geometry adoption is logged but no actual GUI
service/frame, completed synthetic gesture or main screen is verified. The
cause of Fastboot return is unknown. Full actual16GiB capture preceded clean
root and original physical boot-pair restoration; readonly posttrial fsck exits8.
No allocator-fault or physical-UFS-failure conclusion follows from this alone.

## Recorded state and paths

Only current process `init_featureflag` (the16-character p_comm) is selected.
The observer copies kernel-resident saved64-bit user pc/lr/sp/x0/cpsr plus
pid/tid, resolved call number and phase. It never dereferences user addresses,
walks stacks, iterates processes or takes task_lock. The SVC path allocates
nothing and prints nothing to the console.

BSD calls are positive, Mach calls negative. Indirect BSD syscall0 is resolved
from original x0 before it becomes a result. Platform and fast time traps are
excluded. Phase0 is entry,1 ordinary return,2 BSD continuation return after
result/carry preparation,3 Mach continuation return after result assignment.
Both continuations bypass the original C caller; the Mach hook is independent
of kdebug_enable. Missing these paths would create false missing-return evidence.

Configuration is set by the UFS sampler startup before root-backed userland
can run and remains immutable. Disabled/non-selected processes do not record.

## Bounds and interpretation

-64records in a fixed ring; older undrained records may be overwritten.
-65,536accepted events maximum; sequence numbers never wrap. A full ring is
  distinct from the accepted-event quota being exhausted.
- Public OSAtomic CAS is a nonblocking guard. Contending producers mark
  `dropped=1`, a loss flag rather than an exact count. Contending dumps report
  `busy=1`; neither path spins waiting for the guard.
- The existing sampler drains into static storage, releases the guard, then
  prints. Its40-sample bound allows at most40headers and2,560event lines.
- Keep total/count, sequence gaps, dropped and exhausted flags when analyzing
  incomplete pairs. Recording can continue to its quota after the final dump,
  but there is no promise that those later records will be printed.

For a future candidate retain `sweetsample=-`; the opt-in ring can keep the
dumper thread alive without either the old stack sampler or heartbeat output.
Use existing configurable delay/interval and recheck complete bootargs length.
A last entry without a return is only a lead: loss/quota, exit/exec/noreturn,
capture cutoff or async process changes can explain it. Registers describe a
recorded transition, not a live sample of a userspace loop. Symbolication still
requires exact binaries/cache and load slides; raw addresses do not prove that
a particular instruction in main executed.

## Host verification

`python3 tools/test_dali_featuretrace.py` compiles actual added C ring/helper
code: full quota, wrap/drain order, target/default filtering, process/register
immutability, trylock/loss and concurrent producers. Structural tests guard the
ordinary and both continuation hooks. Local ARM64 objects and matching C symbols
do not replace full kernel link/source/linked-marker gates or real hardware.
