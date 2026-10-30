# macOS 15 Arm64 CI

The macOS 14 runner cancelled both attempts of run 41120231736 before any
compilation. Check annotations explicitly identify the scheduled macOS 14 image
brownout and retirement on 2026-11-02; these were not source compile failures.
The user selected macOS 15 Arm64 instead. The old deferred retry is stopped.

## Runner and toolchain

The current official runner-images table maps standard `macos-15` to Arm64;
Intel uses `macos-15-intel`. Use the explicit Arm64 label, not `macos-latest`.
The workflow verifies `RUNNER_ARCH=ARM64`, `uname -m=arm64` and macOS version 15
before dependency installation. This prevents accidentally running the wrong
architecture or silently migrating to macOS 26.

Pin the preinstalled `/Applications/Xcode_16.4.app`, verify its Developer directory,
and report the actual Xcode/iig paths. The official image lists Xcode 16.4 and
native Xcodes. Remove the old `/usr/local/bin/xcodes` no-op shim; the pinned
upstream builder already adds `/opt/homebrew/bin` on an Arm64 host. Fail if the
real `xcodes` command is absent instead of pretending installation succeeded.
The compiler-cache key already includes runner architecture and toolchain identity.

References:
- https://github.com/actions/runner-images#available-images
- https://github.com/actions/runner-images/blob/main/images/macos/macos-15-arm64-Readme.md
- https://github.com/actions/runner-images/issues/13518

## Kernel target and remaining compatibility risk

`macos_version=13.5`, `DEVELOPMENT`, and `SWEET` remain the kernel build target.
Changing the build host does not upgrade the XNU source, iOS root, device tree,
boot arguments or deployment policy. The 0171 SVC-only backboardd selector and
all existing source/linked-marker gates remain.

Xcode 16's iig may generate references to `IORPC.kernelContent`, absent from the
8796 IORPC layout. This migration does **not** add fields to that kernel ABI,
strip generated accesses, reuse frozen generated Apple files, or assume the
problem is solved. The first complete native CI must reveal the actual compiler
or generator failures; any compatibility fix follows that concrete evidence.
The known successful Xcode 15 buildtree contains generated iig sources, not an
old standalone iig executable suitable for silently installing on the new runner.

Five local tests execute the exact runner guard with fake uname/sw_vers commands
and assert pinned toolchain/target/cache/gate policy. They cover accepted Arm64
macOS 15 and rejected Intel, other runner architecture, and OS versions. These
are host-selection tests, not an actual macOS build or iig compatibility proof.
The complete existing regression suite must also pass before publication.

A new commit and full CI are required; rerunning the old commit still selects
macOS 14. No new kernel is usable until full build/link, kernel manifest and exact
source/marker checks pass. Hardware writes and evidence replacement remain
separately authorized. The phone is restored to original Android/physical boot
pair and full clean root; no desktop has been verified.
