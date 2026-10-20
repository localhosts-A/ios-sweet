/*
 * Dali retained-display probe.
 *
 * Reuses LK's validated framebuffer and command-mode display pipeline. Writes
 * volatile display registers and framebuffer RAM, never storage or panel NVM.
 */
#include <stdint.h>
#include "console.h"
#include "fdt.h"
#include "dali_display.h"

#define FDT_MAGIC 0xd00dfeedU
#define PSCI_SYSTEM_OFF 0x84000008UL

static uint32_t fdt_be32(uint64_t pa)
{
	return __builtin_bswap32(*(volatile uint32_t *)(uintptr_t)pa);
}

static uint64_t read_cntpct(void)
{
	uint64_t value;
	__asm__ volatile("mrs %0, cntpct_el0" : "=r"(value));
	return value;
}

static uint64_t read_cntfrq(void)
{
	uint64_t value;
	__asm__ volatile("mrs %0, cntfrq_el0" : "=r"(value));
	return value;
}

static void delay(unsigned int seconds)
{
	uint64_t frequency = read_cntfrq(), start = read_cntpct();
	if (frequency)
		while (read_cntpct() - start < frequency * seconds)
			__asm__ volatile("nop");
}

static void poweroff(void)
{
	register uint64_t x0 __asm__("x0") = PSCI_SYSTEM_OFF;
	__asm__ volatile("smc #0" : "+r"(x0) : : "x1", "x2", "x3", "memory");
	for (;;) {
		__asm__ volatile("wfe");
	}
}

void shim_main(uint64_t dtb)
{
	uint64_t initrd_start = 0, initrd_end = 0;

	if (!dali_display_init(dtb)) {
		delay(20);
		poweroff();
	}
	/* Visually distinguish actual refresh from the retained boot logo. */
	con_clear(RED);
	delay(2);
	con_clear(GREEN);
	delay(2);
	con_clear(BLUE);
	delay(2);
	con_clear(0xff101820U);
	con_str("DALI", "DISPLAY PROBE 2");
	con_str("SCREEN", "RED - GREEN - BLUE - TEXT");
	con_hex("OVL", dali_display_ovl());
	con_hex("FB", (uintptr_t)con_fb);
	con_hex("DISPLAY", dali_display_status());
	con_hex("FDT address", dtb);
	con_hex("FDT magic", dtb ? fdt_be32(dtb) : 0);
	con_hex("Expected", FDT_MAGIC);
	if (dtb && fdt_chosen_u64(dtb, "linux,initrd-start", &initrd_start) &&
	    fdt_chosen_u64(dtb, "linux,initrd-end", &initrd_end)) {
		con_hex("initrd start", initrd_start);
		con_hex("initrd end", initrd_end);
	} else {
		con_str("initrd", "not provided");
	}
	con_str("Framebuffer", "1280 x 2772");
	con_str("Fastboot", "ARM64 handoff reached");
	con_str("Next", "power off in 20s");
	con_str("Restore", "hold Vol- + Power for fastboot");
	for (unsigned int y = FB_H - 240; y < FB_H; y++)
		for (unsigned int x = 0; x < FB_W; x++)
			con_pixel(x, y, x < FB_W / 3 ? RED : x < FB_W * 2 / 3 ? GREEN : 0xff0000ffU);
	dali_display_flush();
	__asm__ volatile("dsb sy" ::: "memory");
	delay(20);
	poweroff();
}

void secondary_main(uint64_t cpu)
{
	(void)cpu;
	for (;;) {
		__asm__ volatile("wfe");
	}
}

void exc_handler(uint64_t slot, uint64_t *regs)
{
	(void)regs;
	(void)slot;
	/* An MMIO fault must not recursively enter the display driver. */
	delay(5);
	poweroff();
}
