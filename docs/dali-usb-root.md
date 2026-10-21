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
| host-dma-policy | version1, address bits48, identity1, uncached1 | Native host DMA address/mapping and IOMMU ownership audit |
| host-u2-calibration | version1, HSRX_TERM, HSTX_IMPN, HSTX_IMPP, silicon SW revision | Read-only native nvmem/chip metadata for this phone |

AC64 capability and absence of an FDT iommus property do not establish
identity DMA. The current code refuses engine startup without these
properties. Do not insert guessed values to bypass the gate.

The next device interaction is read-only Android metadata collection, followed
by completing the address/calibration audit and candidate packaging. It is
not another isolated PHY/port photograph trial.

## Primary references

- [Intel xHCI1.22 specification](https://www.intel.com/content/dam/www/public/us/en/documents/technical-specifications/extensible-host-controler-interface-usb-xhci.pdf), sections4/5/6.
- [Linux6.6 xHCI ring handling](https://github.com/torvalds/linux/blob/v6.6/drivers/usb/host/xhci-ring.c), control TD publication and transfer completion rules.
- MiCode device modules e45c319: mtu3, xhci-mtk, phy-mtk-xsphy and
  phy-mtk-mt6379-eusb2-repeater, cross-checked with this phone's live FDT.
- Native XNU IOBufferMemoryDescriptor/IOMemoryDescriptor, BSD memdev and
  HFS sources in the exact CI build tree.
