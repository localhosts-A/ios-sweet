# Dali opt-in GUI and boot-task bring-up

## Status and known limits

The latest hardware run was patch0160 / CI41003130969. It loaded the binary
persona manifest and finished UMD, but did not prove GUI spawn or a home screen.
Both logs belong to one boot. No in-boot allocation mismatch appeared; the
actual16GiB posttrial image nevertheless fails readonly fsck after the forced
stop (hardlink inode318422 firstlink0, allocation bitmap and counters). The
non-journaled root is not production-reliable. Do not reuse that dirty root.
Stock Android/physical boot pair and complete clean root were restored/verified;
that trial is consumed and all its write authorizations are closed.

Patches0161-0163 below have passed local syntax/ARM64-object compiles and host
unit tests, **not yet a new full kernel link or hardware validation**. They are
not included in the already-approved0160 image pair. A real desktop remains the
acceptance criterion; neither these tests nor a simulated input prove it.

## Bounded startup evidence (0161)

- `sweettasktrace=1`: at most64 selected exit-request/exit-final records, including
  wait status, process and PID, for early tasks and GUI services. These distinguish
  an entered task from an actual exit. No user-stack read is required.
- `sweetheartbeat=1 sweetsample=-`: reuse the existing bounded40-iteration sampler
  thread as a heartbeat without proc iteration or faultable user-map reads.
- Keep the existing delay/interval controls, initially5/15seconds. Existing stack
  sampling remains available but is not required for next startup progress.

The old sampler can wait for proc transitions and reads user frames while
holding task_lock. This is an observer-effect concern, **not a proven cause** of
the observed final task. Last-task log silence alone does not diagnose a hang.

## Logical rendering and retained scanout (0162)

`daligui=1` enables the new path only for the known1280x2772 video geometry;
absent/other values keep the legacy path. No panel cold initialization, PHY/DSC,
routing, IOMMU or new storage driver behavior is introduced.

1. Keep D22 logical1125x2436 for the provider canvas and GetDisplaySize.
2. Publish a separate BGRA buffer-backed logical default IOSurface; retain the
   actual physical scanout/stride rather than lying about its memory geometry.
3. Copy/scale enabled layers using bounded source/target rectangles, full memory
   length/stride checks and the adopted32-bit hardware channel/byte order.
4. GUI present and power-fade submit refresh through one workloop. Early console
   and GUI share a nonblocking atomic guard. Busy GUI work is coalesced/retried
   on the next vsync; console/panic callers never wait on a preempted owner.
5. Preserve the existing bounded DSI transaction and timeout-disable policy.
   CPU writes target the existing write-combine mapping, followed by the existing
   ordering barrier before the command transaction. No undocumented cache knobs.
6. `sweetfb_noconsole=1` stops ordinary console drawing after the first GUI swap;
   GUI refresh no longer relies on a later console newline. Initial copy rate
   stays `sweetfb_hz=10`. Refresh failures/presents/successes are bounded logs.

Hardware format support deliberately matches the two validated32-bit packing
modes and byte/channel swaps of the existing shim console. Unknown format or
missing adopted pipeline fails visibly, rather than guessing a framebuffer.
A completed DSI transaction still needs a real screen photo/video as proof.

## One controlled diagnostic unlock attempt (0163)

`daligui=1 sweethidswipe=60` opts into one delayed synthetic sequence. With no
parameter, no new input is emitted. Physical touchscreen/buttons remain disabled
by the existing test arguments and are **not claimed to work**.

- Dali uses full1280x2772 physical normalization into the logical canvas. Sweet
  retains its original centered1080x2400 to1125x2436 mapping; native mode remains.
- Wait for a touch client under the client lock **and a real frame whose DSI
  refresh completed**, then the configured1..300second
  delay. Stop waiting after600one-second checks; do not interrupt a held finger0.
- Emit a short Menu wake hint (not a power toggle), then one bottom-up finger
  sequence using the existing HID event/queue implementation. Mark complete and
  never re-arm. This is not a verified D22 unlock until tested on the screen.

The screen may still remain Setup/lock screen; record those separately. Do not
render substitute icons or report a successful home screen from a timer event.

## Identity material and packaging

The baseline ADT lacks /product. A separate preview copies61 static properties
from exact iPhone10,3 iOS16.7.16/20H392 D22 DeviceTree, using the existing filter
and excluding5flagged placeholders/bookkeeping. All baseline nodes/properties
are preserved. That Apple asset/preview stays local and out of git; CI tests do
not fabricate it or treat missing-product warnings as complete identity proof.

A GUI candidate must verify that product preview and logical geometry explicitly,
then bind it with the exact successful new CI kernel and bootargs. Reuse the
verified root base/seed delta, not the dirty capture. CI success, candidate hash
approval and a new one-shot hardware authorization are separate gates.

## Evidence preservation and storage

The actual0160 image is preserved with SHA256
`dabbb9453b59bfdd724ce1937e1e590832db699a2527093d12f8613f79f62cee`.
A35-block143,868-byte sparse delta plus the retained clean base reconstructs that
entire hash. A standalone COW reconstruction was also fully hash-verified; it
holds complete file contents independently of the base pathname. The user approved replacing the redundant original inode with the complete
hash-identical standalone COW image at the same root.raw path. All16GiB, metadata
and logs are retained; before/after full hashes match. This released duplicate
storage for another capture, not the only copy of failure evidence.

## Tests and final acceptance

Host tests compile the actual added C geometry/pixel and task-trace policies:
1200 seeded renderer reference cases,1600 HID normalization cases, rejected
lengths/dimensions/formats, pixel roundtrips and bounded/gated trace behavior.
Kernel syntax/object compiles and zero-fuzz patch replay complement these tests;
full CI/link/source-marker checks and actual hardware are still required.

A real successful run must show:

1. Early tasks either complete or have an attributable exit/wait, in one UUID.
2. backboardd/SpringBoard open the expected services and submit real surfaces.
3. Dali GUI present corresponds to refresh and real screen changes without console.
4. Setup/activation/lock transitions lead to real icons/Dock; record synthetic input.
5. At least one real UI update and120seconds of home-screen stability, no respawn.

A kernel stage20 screen, heartbeat, registered service, lock screen, successful
swipe timer or build is not a home screen. Continue to preserve actual posttrial
root before recovery, enforce bounded write regions, and reverify all16GiB plus
physical stock boot hashes when ending a trial. No repeat trial is implicit.
