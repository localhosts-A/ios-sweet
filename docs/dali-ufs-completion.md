# Dali SCSI completion correctness

Patch0166 closes a concrete block-I/O correctness gap. It is not a claim that
short I/O or a stale UPIU has already been observed during the desktop failure.
A real SpringBoard screen and stability are still required for acceptance.

## Defect and narrow fix

The prior serialized SCSI path checked doorbell completion, OCS, transaction
type, response and SCSI status, but did not compare response LUN/task tag to
the current command and did not reject residual data on a GOOD fixed-length
READ(10)/WRITE(10)/READ CAPACITY(10). The buffer strategy would then copy the
requested bytes and clear residual as if an entire VM page had arrived.

Actual old/new command-tail C fixtures reproduce the defect: old code returns0
for a GOOD short16KiBREAD or wrongtag/LUN; new code returnsEIO. Full-success,
existing CHECK CONDITION sense handling and the original Sweet path are retained.
The repository test compiles the actual policy header, including malformed
lengths, all byte-valued LUN/tag combinations and tag wrap through0, overflow/
underflow flags, big-endian residual, non-GOOD status and variable-length commands.

Only Dali OCS-success responses enter the new policy. Invalid transaction type
or LUN/tag mismatch poisons the command channel so the existing no-reuse guard
prevents a subsequent request on an unsynchronized slot. An incomplete fixed
transfer returnsEIO without poisoning the channel; it does not copy unverified
bounce bytes into the caller's page. There is no implicit hardware reset/retry.
Existing SCSI errors still use the original sense/status path. INQUIRY and
other variable-length commands keep their prior short-response policy. This
patch does not change DMA mapping/cache policy, sector size, allocation, HFS
rename/journal logic, root/log partition ranges or original Sweet behavior.

## Relationship to the desktop blocker

The latest same-session trace has230completed user faults and one final
unmatched libobjc read fault. Its backing16KiB lies inside one continuous
extent in shared cache.15; local and Android-read page bytes match the embedded
CodeDirectorySHA256. Three other pages in that file faulted successfully.
The mapping is RX and has no slide-info blob, so blindly changing pointer
sliding, replacing the cache, disabling signing or skipping init_featureflags
is not supported by that evidence. A missing return remains a lead, not proof
of an exact wait or deadlock.

The completion gap is real independently of whether it explains that last
fault. A future test must distinguish a new completion rejection/exit from
continued VM/HFS paging or GUI progress; do not announce the desktop fixed
from this patch, host tests or a successful kernel link.

## Desktop-first candidate policy

Keep the established GUI canvas/physical scanout and original root seed.
Use `serial=1` only to route early normal output to the existing framebuffer
serial console before readonly-late lockdown. It is output-only, not a serial
keyboard, and not a separate log-display acceptance target. Normal console
writes are logged before screen suppression, so old status lines in persistent
logs did not establish that the panel displayed them.

Restore `sweetfb_noconsole=1`, set `sweetffscreen=0` and keep the existing
one-shot `sweethidswipe=60` waiting for a client AND actual successful GUI frame.
This allows a real GUI to own the panel after first swap instead of repeatedly
drawing diagnostic text over it. Retain bounded RAM fault evidence and disable
the old user-stack sampler. There is no claim of physical touch support.

Before a new device test, verify the full new kernel link, exact source,
images/bootargs/AVB, complete clean and virtual seeded root hashes and sufficient
space for another actual16GiB capture. New exact-pair approval is mandatory.
After a trial, preserve complete actual root and logs before rollback; use the
reviewed binary shell-v2 transport and remote exit status. On any new mismatch,
stop for read-only review, never automatically retry. Original Android, GPT,
userdata, partition tails, recovery and backups remain protected.
