# Dali integrated USB root bring-up

## Status

Patch0113 integrates a polling xHCI controller, directly attached USB2 BOT
storage, a read-only BSD block device and explicit USB root selection. This is
an implementation under review, not hardware-verified USB boot support.
The stock Android boot pair remains restored. No new trial is active.

The old halted PORTSC power probe has been removed. Intel xHCI1.22 section5.4.8
requires the controller to be running before software writes PORTSC. Earlier
CCS0 observations do not establish the PHY as the sole remaining blocker.

## Ownership and I/O

- The power workloop checks CC, fault state and fresh PDVBUS/CHGVIN ADC data
  every100ms. A separate worker owns the PHY, MTU3 clocks, xHCI and DMA arena.
- DMA uses one contiguous256KiB arena and an explicit uncached CPU mapping.
  Controller halt plus reset must complete before that arena can be released.
  Failure pins its allocations and retains the platform resources until reboot.
- Initialization performs halt/reset, DCBAA/scratchpad/ring setup, Run and a
  No-op command before USB2 port power, debounce, reset and enumeration.
- Endpoint zero enumerates one directly attached full/high-speed BOT/SCSI
  interface. Only TEST UNIT READY, REQUEST SENSE, INQUIRY, READ CAPACITY10 and
  READ10 CDBs are accepted. No raw command or write passthrough exists.
- Clean HFS+/HFSX contents are recognized at raw-medium or primary MBR offsets.
  The existing stick retains its original MBR type, so contents are validated.
  GPT, extended partitions, hubs, UAS, multiple LUNs and bulk-stall recovery
  are currently unsupported. Errors terminate the session.
- The BSD bridge publishes only the chosen partition as du0/rdu0, rejects
  writes and reports read-only geometry. Root selection is explicit, refuses
  RAM/UFS fallback and overrides rootrw. Normal boot behavior is unchanged
  when daliusbroot is absent.
- Root mode retains default500mA source power while its worker and electrical
  monitoring remain healthy. Diagnostic mode retains its15s source lease.
  The existing boot watchdog remains a separate bounded bring-up mechanism.

## Calibration and default refusal

Integrated mode includes the chip74 MT6379 normal power path, shadow-efuse
copy, native defaults/oscillator settings and Dali host eye properties. It
never programs fuses. Modified operational fields are saved and restored.
SoC software trims, silicon revision and native26MHz/coefficient17 slew-rate
calibration are handled separately from basic observation mode.

Existing shim trees intentionally omit both of these required properties:

| Property | uint32 words | Required evidence |
| --- | --- | --- |
| host-dma-policy | version1, audited address bits32..36, identity1, uncached1 | Native host DMA address/mapping and IOMMU ownership audit |
| host-u2-calibration | version1, HSRX_TERM, HSTX_IMPN, HSTX_IMPP, silicon SW revision | Read-only native nvmem/chip metadata for this phone |

AC64 capability and absence of an FDT iommus property do not establish
identity DMA. The current code refuses engine startup without these
properties. Do not insert guessed values to bypass the gate.

The live ancestor soc dma-ranges covers the first64GiB with equal bus/physical
base0, so the platform refuses widths above36 bits even though xHCI advertises
AC64. The allocator applies the audited physical-address mask and64KiB
alignment before publishing any DMA address. The device mask may be narrower.

The Android metadata audit has completed: the correct nvmem provider and
silicon tag were read, USB nodes have no IOMMU-group membership, and the exact
vendor generic xHCI source selects DMA64 for AC64 hardware. The local candidate
profile constrains this to the parent36-bit window and supplies measured PHY
values. These device dumps remain outside Git. Actual DMA No-op completion,
USB enumeration, storage reads and root mounting remain hardware-unverified.

Patch0115 also handles the native MTK short-transfer quirk: an extra successful
event is ignored only for the exact previous Short Packet TRB, endpoint and
slot. Reusing that ring address clears the old match.

## First integrated device result

The 2026-10-21 integrated candidate completed native repeater calibration and
SoC PHY setup, then reported xHCI startup failure and successful shutdown.
No DMA No-op completion, USB enumeration, storage read or USB root mount was
established. Original Android boot partitions were restored and their full
physical sizes and SHA256 hashes verified.

Patch0116 records the exact startup stage, capability/offset values, reset
wait failures and DMA allocation/mapping rejection. It preserves existing
hardware guards; this diagnostic change does not establish a fix.

The next diagnostic boot localized rejection to the health callback while
waiting for controller reset, before DMA publication. Patch0117 logged the
precise IPPC status: `STS1=0x210f`, failing mask `0x501` because bit10 was low.
That bit is SYS125_RST_B, also a reset-deassertion indication. Treating it as
an invariant clock condition during HCRST was incorrect.

Patch0118 excludes all REF/SYS125/xHCI/U2 reset-ready indications only during
the bounded HCRST/CNR wait. PLL stability, no IP sleep, stop, PMIF and supply
fault checks remain required. Independently mapped always-on domain, bus
protection, VLP/top clock gates/fences and USB AO gates are rechecked before
each engine poll/transfer. Full reset readiness is required before HCRST
and again after CNR clears, before DMA allocation. The correction still
needs hardware verification; DMA No-op and USB storage remain unverified.

## High physical address cache synchronization

The independent AO guard candidate completed controller reset, then panicked
in `phystokv_range` while preparing the uncached arena. The cache attribute
transition calls `pmap_sync_page_attributes_phys`. Its original expression
shifted a 32-bit `ppnum_t` before casting, losing address bits above 4 GiB;
the actual kernel contains `lsl w0, w0, #14`. Patch0119 uses `ptoa_64(pp)` so
the widening precedes the shift. The locally compiled replacement contains
`ubfiz x0, x0, #14, #32`, preserving the full page address. Physical RAM range
checks and uncached mapping remain enabled. DMA execution and storage access
still need hardware verification.

## Primary references

- [Intel xHCI1.22 specification](https://www.intel.com/content/dam/www/public/us/en/documents/technical-specifications/extensible-host-controler-interface-usb-xhci.pdf), sections4/5/6.
- [Linux6.6 xHCI ring handling](https://github.com/torvalds/linux/blob/v6.6/drivers/usb/host/xhci-ring.c), control TD publication and transfer completion rules.
- MiCode device modules e45c319: mtu3, xhci-mtk, phy-mtk-xsphy and
  phy-mtk-mt6379-eusb2-repeater, cross-checked with this phone's live FDT.
- Native XNU IOBufferMemoryDescriptor/IOMemoryDescriptor, BSD memdev and
  HFS sources in the exact CI build tree.
