# Dali memory-root and init diagnostics

CI39536532295's hardware trial reached memory root md0 (major1/minor0),
passed vfs_mountroot, VFS_ROOT and devfs initialization, and returned from
bsd_init at physical-counter36492ms. Photo111408's following
vm_free_delayed_pages message belongs to normal bootstrap/pageout work.
There is no PID1 banner or heartbeat in that photo. The user reports the
screen then stayed on those lines. Original boot/init_boot were subsequently
restored and both full physical partition hashes matched the saved originals.

Patch0092 adds SWEET DEVELOPMENT diagnostics to distinguish the next stages:

- Clone/initproc, ASTBSD and return-wait release in bsd_utaskbootstrap.
- bsdinit_task entry, exception handler setup and VM preparation.
- Initial executable path copy, scratch allocation and execve return code.
- The first16 PID1 UNIX syscall entries and normal-path results, with no
  syscall argument data. These entries provide EL0 execution evidence even
  when the probe's /dev/console text is invisible. Blocking syscalls can
  return through continuation paths without the normal-return marker.

No initialization, scheduling, syscall filter, executable validation, fallback
path or return value is bypassed. Root image, shim, ADT, arguments and finite
watchdog lease remain those of the successful memory-root trial. Absence of
the console banner alone does not establish that PID1 failed to run.

The unchanged static probe first opens /dev/console (UNIX syscall5). Its error
branch is a silent self-loop, so an open failure alone explains a missing
banner without implying exec or EL0 failure. On success it writes (4), gets
its PID (20), then waits with select (93) and writes heartbeats. These are
disassembly observations, not hardware syscall results yet.

## Hardware result from CI39538593047

Photo113942 shows /sbin/launchd exec returning0, console open returning fd0
without error, getpid returning1, and successful writes42/1/5bytes. Repeated
select waits and24byte heartbeat writes continue every approximately5seconds
for at least25seconds. This proves EL0 execution of the signed static PID1
probe. It does not establish boot of the full iOS desktop or Apple launchd.

The last displayed entry is sequence15 (select), exactly the first16-call
trace budget. This is not a halt marker. Console-open failure was not observed;
the text remains invisible despite successful writes. Inspect console route
cnwrite -> kmoutput -> console_write/cons_ops and video silent/graphics/refresh
behavior next. Original full boot/init_boot were restored, Android completed
boot, and both physical hashes matched the originals. No active trial remains.
