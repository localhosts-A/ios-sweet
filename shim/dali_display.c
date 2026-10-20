/*
 * Dali / MT6991 retained command-mode display.
 * Register definitions and trigger ordering: MiCode/mtk_kernel_device_modules,
 * bsp-dali-v-oss, e45c3199537fbba9aae5fb06c31fc8dbd3c1dcee:
 * mediatek_v2/mtk_{dsi,drm_ddp,disp_ovl_exdma}.c and
 * drivers/clk/mediatek/clk-mt6991-mmsys.c. See docs/dali-display.md.
 */
#include "dali_display.h"
#include "console.h"
#include "fdt.h"

#define BIT(n) (1U << (n))
#define FB_RESERVED_SIZE 0x3660000UL
#define DSI 0x32490000UL
#define DSI_INTSTA 0x0c
#define DSI_CON 0x30
#define DSI_MODE 0x34
#define DSI_CMDQ_CON 0x44
#define DSI_HS 0x50
#define DSI_CMDQ0 0x400
#define DSI_BUSY BIT(31)
#define DSI_FRAME_DONE BIT(4)

static uintptr_t active_ovl;
static uintptr_t mutex_regs[4];
static unsigned int mutex_count;
static unsigned int mutex_id;
static int ready;
static uint32_t status;

static uint32_t rd(uintptr_t p)
{
	return *(volatile uint32_t *)p;
}

static void wr(uintptr_t p, uint32_t v)
{
	*(volatile uint32_t *)p = v;
	__asm__ volatile("dsb sy" ::: "memory");
}

static uint64_t ticks(void)
{
	uint64_t v;
	__asm__ volatile("mrs %0, cntpct_el0" : "=r"(v));
	return v;
}

static int wait_bits(uintptr_t p, uint32_t mask, uint32_t value)
{
	uint64_t freq;
	__asm__ volatile("mrs %0, cntfrq_el0" : "=r"(freq));
	if (!freq)
		return 0;
	uint64_t start = ticks();
	do {
		if ((rd(p) & mask) == value)
			return 1;
	} while (ticks() - start < freq / 5); /* 200 ms maximum */
	return 0;
}

static int adopt_surface(uintptr_t ovl)
{
	uint32_t src = rd(ovl + 0x40);
	if (!(rd(ovl + 0x20) & 1) || !(src & 1) ||
	    (src & (BIT(4) | BIT(8) | BIT(9))) || !(rd(ovl + 0x100) & 1))
		return 0; /* disabled, compressed, or not a memory layer */
	uint64_t pa = rd(ovl + 0xf40) | ((uint64_t)(rd(ovl + 0xf4c) & 0xf) << 32);
	uint32_t size = rd(ovl + 0x48), pitch = rd(ovl + 0x2f4) & 0xffff;
	pitch |= (rd(ovl + 0x2f0) & 0xf) << 16;
	uint32_t fmt = rd(ovl + 0x50);
	unsigned int code = fmt & 0xf;
	unsigned int bpp = code == 0 ? 2 : code == 1 ? 3 : 4;
	uint64_t length = (uint64_t)pitch * FB_H;
	if (code > 3 || (fmt & (BIT(4) | (3U << 8))) ||
	    (size & 0xffff) != FB_W || (size >> 16) != FB_H ||
	    rd(ovl + 0x44) || rd(ovl + 0x4c) || pitch < FB_W * bpp ||
	    pitch > FB_W * 4 + 256 || (pa & 3) || (pitch & 3) ||
	    pa < FB_BASE || pa >= FB_BASE + FB_RESERVED_SIZE ||
	    length > FB_BASE + FB_RESERVED_SIZE - pa)
		return 0;
	con_set_surface(pa, pitch, fmt);
	active_ovl = ovl;
	return 1;
}

int dali_display_init(uintptr_t fdt)
{
	ready = 0;
	mutex_count = 0;
	active_ovl = 0;
	status = 1;
	con_fb = 0; /* Refuse to paint an unvalidated address. */
	if (!fdt_reserved_region(fdt, FB_BASE, FB_RESERVED_SIZE))
		return 0;
	/* DSI0 and its DISPSYS1 mutex must already be clocked by LK. */
	status = 2;
	if (rd(0x32400100UL) & (BIT(22) | BIT(2)))
		return 0;
	status = 3;
	if (!(rd(DSI + DSI_CON) & BIT(1)) || (rd(DSI + DSI_CON) & BIT(4)) ||
	    (rd(DSI + DSI_MODE) & 3))
		return 0; /* Only the single DSI0 command-mode panel is supported. */

	/* Find the retained mutex that includes DSI0 MAC0, not a writeback path. */
	status = 4;
	for (mutex_id = 0; mutex_id < 5; mutex_id++) {
		uintptr_t off = mutex_id * 0x20;
		if ((rd(0x32420034UL + off) & BIT(23)) &&
		    !(rd(0x32420030UL + off) & 0xf))
			break;
	}
	if (mutex_id == 5)
		return 0;
	mutex_regs[mutex_count++] = 0x32420000UL;

	/* Scan only ungated EXDMA instances participating in that same mutex. */
	status = 5;
	for (unsigned int bank = 0; bank < 2; bank++) {
		uintptr_t config = bank ? 0x32c00000UL : 0x32800000UL;
		uint32_t gates = rd(config + 0x100);
		if (gates & BIT(3))
			continue;
		uintptr_t mutex = config + 0x20000;
		uintptr_t off = mutex_id * 0x20;
		uint32_t modules = rd(mutex + 0x34 + off);
		if (!(modules | rd(mutex + 0x38 + off)) ||
		    (rd(mutex + 0x30 + off) & 0xf))
			continue;
		mutex_regs[mutex_count++] = mutex;
		for (unsigned int id = 0; id < 10; id++) {
			if (id == 1 || !(modules & BIT(id)) || (gates & BIT(id + 4)))
				continue;
			uintptr_t ovl = config + 0x30000 + id * 0x10000;
			if (adopt_surface(ovl))
				break;
		}
		if (active_ovl)
			break;
	}
	if (!active_ovl)
		return 0;
	/* DISPSYS0 can contain the retained color/relay stages as well. */
	if (!(rd(0x32000100UL) & BIT(2))) {
		uintptr_t off = mutex_id * 0x20;
		if ((rd(0x32020034UL + off) | rd(0x32020038UL + off)) &&
		    !(rd(0x32020030UL + off) & 0xf))
			mutex_regs[mutex_count++] = 0x32020000UL;
	}
	status = 0;
	ready = 1;
	return 1;
}

int dali_display_flush(void)
{
	if (!ready)
		return 0;
	if (!wait_bits(DSI + DSI_INTSTA, DSI_BUSY, 0)) {
		status = 6;
		ready = 0;
		return 0;
	}
	uint32_t hs = rd(DSI + DSI_HS);
	uint32_t cmd = rd(DSI + DSI_CMDQ0);
	uint32_t cmdcon = rd(DSI + DSI_CMDQ_CON);
	/* FB stores are noncacheable with the entry MMU off. */
	__asm__ volatile("dsb sy" ::: "memory");
	wr(DSI + DSI_INTSTA, rd(DSI + DSI_INTSTA) & ~DSI_FRAME_DONE);
	for (unsigned int i = 0; i < mutex_count; i++) {
		uintptr_t m = mutex_regs[i];
		wr(m + 8, rd(m + 8) & ~1U);
		wr(m + 0x20 + mutex_id * 0x20, 0);
		wr(m + 0x20 + mutex_id * 0x20, 1);
	}
	wr(DSI + DSI_HS, hs | BIT(16)); /* panel lp_perline_en = 0 */
	wr(DSI + DSI_CMDQ0, 0x002c3909); /* DCS WRITE_MEMORY_START */
	wr(DSI + DSI_CMDQ_CON, (cmdcon & ~0xffU) | BIT(15) | 1);
	wr(DSI, 0);
	wr(DSI, 1);
	wr(DSI, 0);
	int done = wait_bits(DSI + DSI_INTSTA, DSI_FRAME_DONE, DSI_FRAME_DONE);
	int idle = wait_bits(DSI + DSI_INTSTA, DSI_BUSY, 0);
	if (idle) {
		wr(DSI + DSI_HS, hs);
		wr(DSI + DSI_CMDQ0, cmd);
		wr(DSI + DSI_CMDQ_CON, cmdcon);
	}
	status = done && idle ? 0 : 7;
	if (status)
		ready = 0; /* A failed transaction must not stall every console line. */
	return !status;
}

uintptr_t dali_display_ovl(void) { return active_ovl; }
uint32_t dali_display_status(void) { return status; }
