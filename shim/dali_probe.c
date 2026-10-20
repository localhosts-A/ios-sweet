/*
 * Read-only Dali boot handoff probe.
 *
 * This does not touch peripheral registers or storage. It checks whether the
 * MediaTek fastboot path enters an ARM64 Image with a live FDT in x0, then
 * paints a small message into the bootloader's reserved framebuffer.
 */
#include <stdint.h>
#include "console.h"
#include "fdt.h"

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

	con_clear(0xff101820U);
	con_str("DALI", "MT6991 boot probe");
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
	__asm__ volatile("dsb sy" ::: "memory");
	uint64_t frequency = read_cntfrq();
	uint64_t start = read_cntpct();
	if (frequency) {
		while (read_cntpct() - start < frequency * 20) {
			__asm__ volatile("nop");
		}
	}
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
	con_clear(0xff900000U);
	con_str("DALI", "exception during entry probe");
	con_hex("vector", slot);
	for (;;) {
		__asm__ volatile("wfe");
	}
}
