# Legacy IORPC generation

Native macOS 15 Arm64 / Xcode 16.4 run 41128102930 passed all policy tests, then
failed kernel compilation with 132 generated DriverKit errors: 93 designated
initializers and 39 accesses require `IORPC.kernelContent`. The old four-field
8796 IORPC has message/reply/sendSize/replySize only. No kernel was linked.

This is more than a missing declaration. New iig separates Mach descriptors from
content and extends invocation unions too. Adding a field, dropping initializer
members alone, or replacing every access with rpc.message would be incorrect.
Two actual paths refer to **reply** content, not request content.

## Build-only adapter

`tools/iig_legacy_rpc.py` invokes the real Xcode iig, then selects its already
emitted inline-message alternatives for this legacy kernel. It does not modify
IORPC.h, add a kernel field, install an old Apple executable, or reuse generated
Apple assets in Git. Each build generates fresh headers and implementation.

The wrapper checks the actual header has exactly the legacy four-field layout
before invoking iig. It accepts only the observed DriverKit-427.100.5 banner and
strict known patterns. It then:

- Uses the inline `_Msg_With_Content` pointer in invocation unions, removing the
  extra content pointer so the union remains the old IORPC size.
- Uses each generated alternative's correct IORPCMessageFromMach request/reply
  path, rather than guessing which pointer to reinterpret.
- Uses inline message access in invoke helpers and MESSAGE_CONTENT.
- Selects the existing four-field initializer and removes the one observed
  separately assigned kernelContent field.
- Removes the new `__builtin_assume(reply != NULL)` optimization hints so legacy
  null/error checks are not optimized away. Runtime checks are not disabled.

The real output audit covers 17 implementations, 94 invocation layouts, 93
initializers, 38 dispatch/read branches, 18 content macros, one separate
assignment, and 86 optimizer assumptions. Any remaining content reference,
unknown assumption or inconsistent invocation count rejects generation. The
adapted implementation is atomically published only after validation. Native iig
failures are propagated. New versions/layouts need inspection, not broad regexes.

## Workflow and validation

The workflow exports a shell-quoted IIG command; XNU's existing undefined-origin
check preserves this environment override, including recursive make. A real
make regression covers that behavior and paths containing spaces. The adapter
hash joins the compiler-cache identity. Full CI requires the legacy IORPC header
and all 17 generated implementations to remain free of kernelContent references.

Eight tests include:
- Original new-generation fixture fails against the legacy layout (negative control).
- Adapted C++ executes request/reply dispatch, validates the 24-byte invocation
  layout and inline content offset, and exercises descriptor/object counts,
  short request/reply sizes, callback errors and null reply on an error path.
- Unknown generator/pattern/layout rejects rather than publishing a partial fix.
- Generator argument forwarding, atomic output and failure propagation.
- Actual make preserves the exported wrapper command without a fallback tool.

All 17 real adapted generated sources compile as ARM64 objects using the legacy
headers. All 17 old-generator references also compile with the same flags; their
global defined and undefined symbol sets exactly match the adapted objects.
This is strong local ABI/build evidence, **not** full linking or runtime proof.
The remaining kernel sources, GUI probes, budgets, storage and input policies
are unchanged. A separately approved complete CI must pass build/link and exact
kernel/source/generated-output gates before a device candidate is made. Device
trial and evidence replacement permissions are not granted by this build fix.
