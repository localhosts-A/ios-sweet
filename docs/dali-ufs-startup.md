# Dali UFS ownership and cooperative waiting

Patch 0177 repairs a post-poison DMA ownership boundary. It does not identify or
promise to fix the cause of the missing SpringBoard desktop. The existing
`ufswait=1` path is tested, not rewritten or enabled by default.

## Evidence and limits

The 0176 trial, boot `C01C071C-0F9D-4C86-A4D1-B92365A57C30`, exported coherent
UFS snapshots in all 40 rounds. Five backboardd snapshots identified the known
UFS mutex; later snapshots identified PortSend. There were no GUI presents or
successful refreshes. The last incoming mach_msg2 words requested a send without
a timeout, but neither the message nor its peer identity was validated. A client
port name is not a PID, and a raw message ID alone does not name a service.

Returned-only global elapsed totals include software, scheduling and logging.
They are not pure device latency, continuous wait duration or a causal explanation
of the missing desktop. No poison was observed in that trial. This patch repairs
a source-proven error path, not a demonstrated cause of that particular black
screen. The original complete scene was preserved and independently rehashed;
the device was restored and verified before this implementation began.

## Preserve DMA after a failed command

A timeout clears the transfer slot and marks Dali `command_poisoned`, but does
not prove that the hardware has stopped accessing the descriptor or bounce
buffer. Response identity desynchronization also poisons the command path. The
existing SCSI and QUERY entrypoints refuse further commands before initializing
their descriptors.

Previously, a full-block strategy write could copy into the bounce buffer before
that refusal. The persistent logger could do the same. Serializing CPU callers
with a mutex does not establish that a previous hardware DMA operation has ended.

0177 checks `su.dali && su.command_poisoned` immediately after the existing I/O
mutex is acquired, before either producer touches bounce memory:

- Strategy records `ETIMEDOUT` and skips the transfer loop. Existing error
  precedence, residual bytes, unlock, unmap and exactly-once biodone remain.
- Logger records the same error and uses its existing no-more-writes/stop path.
  Its successful payload-write → payload-sync → header-write → header-sync order
  is unchanged, with no extra printing under the lock.

The original error from the command that first failed is retained: for example,
response desynchronization still returns `EIO`; later poisoned requests return
`ETIMEDOUT`. Poison is never reset. Clearing a slot is not relabeled as verified
quiescence. The timeout, response checks, cache maintenance, slot ownership,
partition bounds and successful I/O protocol are unchanged. Non-Dali behavior
is preserved.

## Existing cooperative wait

The next separately approved integrated candidate is intended to add only
`ufswait=1` to the previous candidate's complete boot arguments. That is a
candidate configuration change, not a global kernel default change.

The existing SCSI function first spins for 50 microseconds and can then block
against a deadline in intervals of at most 200 microseconds. Blocking requires:

- explicit wait opt-in and the UFS device already ready;
- interrupts enabled;
- not executing in interrupt context;
- preemption level zero.

Early initialization and unsafe contexts continue polling. The caller keeps the
mutex, slot 0 and bounce ownership while blocked. Releasing the mutex during
this interval would allow another request to overwrite active DMA state or
interrupt a partial-block read/modify/write transaction. Early wakeups recheck
the doorbell; the command deadline does not extend. At the exact deadline the
unchanged function may request an already-due timer before its next timeout
check; the test models that boundary explicitly rather than changing production
code to fit a mock.

Cooperative waiting permits other threads to run on the single CPU while I/O is
pending. It does not remove the mutex queue, prove CPU starvation, or guarantee
lower request latency or a desktop.

## Real production-function tests

`tools/test_dali_ufs_startup.py` extracts the final SCSI, both QUERY helpers,
strategy, ioctl, read/write/sync and logger functions, along with the actual DMA
layouts, constants and completion policy. MMIO, time, scheduler and buffer APIs
are mocks; the transfer/wait/error logic is not replaced by a model. Existing
strategy tests that stub the SCSI body remain useful but are not the proof for
this suite.

The suite executes:

- old0176 full-block and logger overwrite negative controls;
- post-poison read, full-block write, partial-block RMW, logger and sync refusal,
  with entire DMA memory unchanged and no new doorbell;
- real SCSI timeout, response desynchronization and both QUERY timeout sources;
- multi-block partial completion followed by desynchronization or timeout;
- normal and each prohibited-sleep context, immediate/spin/block completion,
  early wakeup, non-WAITING assertion and exact-deadline behavior;
- mutex, tag and descriptor ownership across each simulated block;
- cache/barrier/descriptor order and 64-bit physical descriptor addresses;
- logger success and failures at payload, first sync, header and second sync;
- normal Dali/non-Dali transfers and original input-error precedence.

The final-source gate also removes only the two exact added guards and strategy
error condition and requires the rest of the UFS file to equal 0176 byte for
byte. `test_dali_gui_load_progress.py` keeps its explicit 0175/0176 sources and
0176 old-try-busy control, then overlays the final 0177 UFS file. The SCSI and
sync byte-identity assertions stay intact; the logger assertion removes only
its exact new guard. No missing-file fallback is allowed.

Set `SWEET_LOAD_SOURCE_DIR` to the materialized XNU tree to require final-source
identity. CI runs both suites before Build and again against final sources
thereafter. Darwin fortified-macro regressions in the existing suite retain
negative controls, guarded positives and `-Werror`. A local ARM64 object is not
full native-link or runtime evidence.

## Deliberately excluded

Do not simultaneously enable `ufscache`, raise gear, tune PLL/PHY/voltage or
restructure queues/logger locking. The initialization lacks Linux's subsequent
link-mode negotiation, but the current evidence does not establish the actual
mode or every reference-clock, PHY and device-quirk prerequisite for a safe HS
transition. Do not infer measured PWM from source differences or blindly copy a
FASTAUTO/HS register sequence.

Do not disable daemons, fake IPC success, raise port queue limits, repair original
scenes or attribute the final PortSend to a guessed service. Those changes are
not part of this patch.

## Integrated validation and recovery

Prepare one functional candidate with existing bounded diagnostics, not another
pure-observation flash. Publication, one complete macOS 15 Arm64/Xcode 16.4 CI,
and one exact hardware trial each require separate approval. Preserve the
17-generated-IIG/four-field-IORPC ABI contract.

Any approved trial retains fresh target/slot/stock-pair/full-root/recovery-source
checks, at least 18 GiB capture space, a maximum 1200-second window, original
Android/physical-pair recovery, a new complete 16-GiB three-hash capture, actual
comparison of all 256 restore chunks and pinned-source-FD b4ee restoration with
range/full-root/physical-pair verification. No automatic retries, GPT/userdata/
unbacked-tail writes, old-scene replacement or cleanup permission is implied.

Only a real SpringBoard icon grid and Dock, an actual UI update, 120 seconds of
visibility and same-boot frame submission/successful DSI refresh meet the desktop
goal. Synthetic input must be labeled; physical touch remains unverified.
