# Dali retained display probe

This first display implementation reuses the panel, PHY, DSC, routing, clocks
and memory translation left by LK. It is not a cold panel driver. Probe 2 visibly showed colors and text on the handset, explicitly confirmed by
the user. Probe 1 had produced no visible output. Stock boot was restored after
both trials and both original boot partition hashes were verified.

Build with `bash shim/build-dali-probe.sh`. The kernel-only boot v4 image is
`shim/out-dali/dali-probe-boot.img` (legacy LZ4). Keep stock `init_boot_b`.
The probe requests red, green and blue frames for two seconds each, then text
and bottom color bars for twenty seconds, and requests PSCI power-off. An init
validation failure powers off after twenty seconds. An exception requests
power-off after five seconds without recursively accessing display registers.

## Retained pipeline checks

- Require the exact FDT reserved framebuffer region `0xfc990000 + 0x3660000`.
- Require ungated DSI0 and a retained single-port command-mode configuration.
- Discover a command-mode mutex containing DSI0 MAC0 (MOD0 bit 23).
- Read only clocked EXDMA instances included in the corresponding OVL mutex.
- Adopt an enabled uncompressed physical memory layer with 1280 x 2772 source
  size, no clipping/offset, supported format and a complete surface inside the
  reserved region. Use its actual address, pitch and pixel format.
- Preserve panel commands, DSC parameters, surface address, clock state,
  routing and IOMMU configuration. No storage or panel NVM writes.

The console handles RGB565, packed RGB888 and the two 32-bit hardware formats,
including their byte/channel swaps. A DSI completion timeout disables further
refresh attempts. A completed hardware transaction does not prove that the
panel displayed it; the user must confirm the color/text output.

## Vendor register source

Definitions were checked against MiCode's Dali vendor source at commit
`e45c3199537fbba9aae5fb06c31fc8dbd3c1dcee`:

- [DSI definitions and MT6991 driver data](https://github.com/MiCode/mtk_kernel_device_modules/blob/e45c3199537fbba9aae5fb06c31fc8dbd3c1dcee/drivers/gpu/drm/mediatek/mediatek_v2/mtk_dsi.c)
- [Mutex membership and trigger](https://github.com/MiCode/mtk_kernel_device_modules/blob/e45c3199537fbba9aae5fb06c31fc8dbd3c1dcee/drivers/gpu/drm/mediatek/mediatek_v2/mtk_drm_ddp.c)
- [EXDMA surface and boot framebuffer adoption](https://github.com/MiCode/mtk_kernel_device_modules/blob/e45c3199537fbba9aae5fb06c31fc8dbd3c1dcee/drivers/gpu/drm/mediatek/mediatek_v2/mtk_disp_ovl_exdma.c)
- [Display clock gates](https://github.com/MiCode/mtk_kernel_device_modules/blob/e45c3199537fbba9aae5fb06c31fc8dbd3c1dcee/drivers/clk/mediatek/clk-mt6991-mmsys.c)

| Block | Retained register offsets |
| --- | --- |
| DSI0 at `0x32490000` | START `0x00`, INTSTA `0x0c`, CON `0x30`, MODE `0x34`, CMDQ_CON `0x44`, TYPE1_HS `0x50`, CMDQ0 `0x400` |
| Mutex | CFG `0x08`; EN `0x20`, SOF `0x30`, MOD0 `0x34`, MOD1 `0x38`, plus `id * 0x20` |
| EXDMA | EN `0x20`, L_EN `0x40`, size `0x48`, format `0x50`, pitch `0x2f4` / MSB `0x2f0`, address `0xf40` / MSB `0xf4c` |

Refresh follows LK's CPU command-mode path: wait idle, clear frame-done,
enable the participating retained mutexes, retain HS blanking, queue `0x002c3909`
(DCS memory write), select one command, assert START, wait for frame-done and
idle, then clear START and restore command queue settings. Polls are bounded at
200 ms.

A read-only copy of this handset's original `lk_b` was inspected locally after
probe 1. Its `ddp_dsi_trigger` at LK payload offset `0x52468` queues the same
command and leaves START asserted during transfer (`0x5257c..0x5259c`). It does
not require the Linux DSI_EN bit before that trigger. Probe 2 removes that
unjustified validation prerequisite and avoids immediately clearing START as
the Linux GCE path does. Probe 2 succeeded after these changes; the individual contribution of each
change was not isolated. The firmware dump stays outside Git.
This probe does not yet implement the vendor GCE TE-wait loop, cold display
initialization or an XNU refresh service. The normal Dali shim and non-RAMLOG payload probes now enable `DALI_DISPLAY`
for loading/error/exception text. RAM-only logging and stock-chain builds keep
their previous no-display behavior. The adopted framebuffer address, pitch and
depth are passed in XNU boot video args. This does not supply an XNU refresh
service; kernel output after the handoff still needs its own refresh calls.

After a temporary trial, restore the full original `boot_b` backup, reboot
Android, and verify both boot partition hashes. Never format userdata for this
display probe.
