# Incoming Machmsg2 context

Desktop remains unachieved. In0173 boot6F8EBF37-151A-4E3C-992C-705C3D65100E,
backboardd56/TID316 produced411 accepted selected SVCs. The final entry was
mach_msg2(-47), without a later observed ordinary or continuation return through
sample39. The existing thread_syscall_return phase3 already covers receive
continuations. Neither this fact nor the five completed sampled VM envelopes
establishes a deadlock, RPC dependency, or ready service. No GUI present/refresh
or real GUI was observed. That trial was fully captured and restored.

## Observe scalars, not messages or peer objects

0174 adds default-off `sweetbbipc=1`, requiring immutable `sweetfftrace=3`.
Capture opens only when the existing normal sampler begins. Only exact
backboardd,64-bit AArch64 EL0, phase0/call-47 and a **successful old SVC record**
may add metadata. Seven raw64-bit x1…x7 values, PID/TID, absolute time and the old
accepted sequence tag go into an independent64-slot array under the original
SVC guard. No producer rate limiter is added: it could miss the last burst entry.
The original64-slot SVC event layout,65536 cap, counts, formats and SCREEN meaning
remain unchanged. No user-message copy, IPC space/port lookup, retained object
pointer, allocation, new guard/timer/thread, waiting or producer output.

The normal dump first takes the old SVC snapshot. Only its phase0/-47 entries
with matching sequence/PID/TID metadata can be exported. It keeps the newest
four, in ascending old absolute sequence order, then unlocks before output.
These are **not a call chain**. No independent scan or replay of old metadata
occurs after snapshot count clears. A subsequent64-event burst can overwrite an
entry; no guarantee of observing the final IPC call follows.

Each of40 existing dumps adds at most one coverage/busy summary and four data
records; one configuration line: at most201 new IPC lines /51,255 bytes.
All lines include newline and are≤255bytes. New persistent state+static snapshot
is≤8KiB, compile-time asserted. Summary eligible/matched/selected/missing_tag/
limited describes the retained snapshot, not total lifetime overwritten calls.
Finalsample39 closes only new metadata capture before one old-guard attempt;
old SVC capture continues. A busy final dump explicitly reports incomplete
coverage, with no waiting, retry, reopening or extra dump. Creation failure does
not open capture. The original5/30s sampler and1200s external limit stay intact.

## Version1 data fields

`SWEET BBIPC: v=1 s=… n=… p=… th=… at=… o=… b=… p2=… vi=… d=… r=… t=…`

| Key | Source | Raw interpretation |
|---|---|---|
| s/n/p/th/at | sample/accepted SVC sequence/PID/TID/absolute time | joins with the original SVC entry; x0/data pointer is there, never dereferenced |
| o | x1 | requested64-bit options |
| b | x2 | low32 header bits, high32 send size or vector count |
| p2 | x3 | low32 remote, high32 local client-namespace names |
| vi | x4 | low32 voucher, high32 raw message ID bits |
| d | x5 | low32 descriptor count, high32 receive name |
| r | x6 | low32 receive size or vector count, high32 priority |
| t | x7 | raw timeout word; kernel uses low32 |

These ABI words follow mach_traps.h/mach_msg.c. The kernel masks options and
applies vector/size/CFI/header/port validation later. They are **incoming requested
intent**, not validated messages or proof of send completion. If SEND isn't
requested, do not treat the remote/ID words as a transmitted request. Vector
sizes are counts in the corresponding words. Client port names don't identify
a server PID. Receive-only may be an ordinary server loop or a separate RPC
reply receive: it does not prove readiness. SEND+RCV does not prove where an
in-kernel wait is occurring. Timeout is effective only with the relevant flag.
No absence of selected metadata establishes absence of IPC or process death.

The strict offline parsing helpers in test_dali_backboard_ipc_context.py reject
unknown versions, duplicates and incomplete records; their decoder names requested
bits and raw client names, never validated/peer/ready. Local runtime analysis can
reuse them, grouping boot UUIDs before token/sequence association. Printed batch
ordering is not event timing; use at and the same-clock PC/SVC snapshot markers.

## VM output-only correction

0173's512-byte formatter bound exceeded the actual console route. Exact
console_printbuf_putc with SERIAL_CONS_BUF_SIZE256 truncates beyond255 bytes
without SYNCDRAIN. Two original0173 return tails lack duration_valid; those
partial raw records are preserved and not filled retroactively.

0174 uses compact `SWEET BBVM: v=2` entry/return forms. Entry includes all prior
register/ESR/FAR/address/protection/start context; return includes token/PID/TID/
start/end/result/duration/validity without repeating the entry. Join by token,
PID/TID/start within one boot. The VM policy, quotas, pairing, return-loss and
cutoff behavior are unchanged, as is its80-data-record ceiling. Worst scalar
entry/return and IPC lines are tested through real console functions then parsed.
The old long format must reproduce clipping as a negative control. No console
buffer change, drain_uart_sync, UART behavior change or logging-only trial.

## Verification and remaining gates

Base is exact fullCI41181923386 / commit8081a41. Tests execute final patch-chain
policy, producers, finite lifecycle and snapshots with warnings as errors and
Darwin-safe macros. Cases include original mode1/2 isolation, default/invalid
switches, EL0 and signed-call filters, old sequence1/64/65/65536 boundaries,
PID/TID/tag mismatch, latest-four/limited and overwritten entries, no replay,
concurrent producers/normal snapshot, thread creation failure and final busy
closure while old SVC continues. Default-off old SVC snapshot and text compare
byte-for-byte. Fixed formats/bounds, raw scalar/vector/timeout decode and real
console→parser roundtrip complement—not replace—actual kernel validation.

Local full regression, zero-fuzz/no-offset replay, actual ARM64 sampler compile
and object/source markers must pass before requesting publication. Publication/
one full remote CI require exact-file approval; full kernel link/runtime aren't
proved by local tests. Native macOS15Arm64/Xcode16.4 and legacy four-field IORPC
remain unchanged; downloaded artifacts/scripts stay isolated and out of Git.

No device permission is granted by this host implementation. The phone last
verified stock Android/physical original boot pair and full NEWb4ee16GiB root,
all four write flags false. Original6015/760a/0adb complete scenes and dedicated
recovery backup remain unmodified. Only~6.2GB host space was available, below
16GiB+2GiB capture reserve; system/home also lacked a full capture destination.
Stop candidate/device preparation at capacity until an explicit storage decision;
never delete/COW scenes or split capture to bypass the gate implicitly.

Future trials remain separately approved exact pairs, at most1200seconds, same
NEWb4ee/07ba/sparse3c52 contracts, stock restoration/physical hash verification,
complete actual16GiB/triple hashes before root writes, all256chunk actual scan,
verified source FD held through actual-difference recovery/readbacks/full hashes,
and closure of all four write permissions. No GPT/userdata/unbacked-tail changes.
Desktop still requires real grid/Dock, UI update,120seconds stable and same-boot
GUI submission/successful DSI refresh. State synthetic input explicitly.
