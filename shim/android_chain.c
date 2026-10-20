/* Diagnostic recovery without a firmware reset: let stock Linux expose the
 * shim's reserved-RAM log through pstore, then restore both original images
 * from root ADB. All modifications here are RAM-only.
 */
#include <stdint.h>
#include "android_chain.h"
#include "console.h"
#include "fdt.h"
#include "loader.h"

#define STOCK_KERNEL_PA 0x80000000ULL
#define SHIM_HIGH_PA 0x200000000ULL

extern const uint8_t stock_kernel_start[], stock_kernel_end[];
extern const uint8_t stock_ramdisk_start[], stock_ramdisk_end[];
extern const uint8_t _head[], _end[];
extern void android_chain_enter(uint64_t dtb, uint64_t entry) __attribute__((noreturn));

static uint64_t get_le64(const uint8_t *p)
{
	uint64_t v = 0;
	for (unsigned i = 0; i < 8; i++)
		v |= (uint64_t)p[i] << (i * 8);
	return v;
}

static void copy_ram(uint64_t dst, const uint8_t *src, uint64_t n)
{
	if (!((dst | (uintptr_t)src) & 7)) {
		while (n >= 8) {
			*(volatile uint64_t *)dst = *(const uint64_t *)src;
			dst += 8;
			src += 8;
			n -= 8;
		}
	}
	while (n--) {
		*(volatile uint8_t *)dst++ = *src++;
	}
}

const char *android_chain_boot(uintptr_t dtb)
{
	uint64_t start, end, chosen_end;
	uint64_t kernel_size = stock_kernel_end - stock_kernel_start;
	uint64_t ramdisk_size = stock_ramdisk_end - stock_ramdisk_start;
	uint64_t shim_size = _end - _head;
	if ((uintptr_t)_head != SHIM_HIGH_PA || kernel_size < 64 ||
	    stock_kernel_start[56] != 'A' || stock_kernel_start[57] != 'R' ||
	    stock_kernel_start[58] != 'M' || stock_kernel_start[59] != 0x64 ||
	    !ramdisk_size || kernel_size > shim_size ||
	    get_le64(stock_kernel_start + 16) > shim_size)
		return "stock chain: invalid source or relocation";
	if (!loader_generic_range(&start, &end) ||
	    !fdt_chosen_u64(dtb, "linux,initrd-end", &chosen_end) || end > chosen_end ||
	    chosen_end - end > (2ULL << 20) ||
	    start >= end || ramdisk_size > end - start ||
	    dtb < STOCK_KERNEL_PA + get_le64(stock_kernel_start + 16))
		return "stock chain: unsafe initrd/FDT range";
	uint64_t trailer_size = chosen_end - end;
	/* Set the bounded field first; the setter refuses a 32-bit overflow. */
	if (!fdt_set_chosen_u64(dtb, "linux,initrd-end", start + ramdisk_size + trailer_size))
		return "stock chain: cannot update initrd-end";
	con_hex("STOCK_INITRD", start);
	con_hex("STOCK_RD_END", start + ramdisk_size + trailer_size);
	con_hex("BOOTCONFIG", trailer_size);
	/* Preserve LK's exact bootconfig + padding + trailer after shrinking the
	 * generic ramdisk. Source is above destination; the forward copy is safe.
	 */
	copy_ram(start + ramdisk_size, (const uint8_t *)end, trailer_size);
	copy_ram(start, stock_ramdisk_start, ramdisk_size);
	con_hex("STOCK_KERNEL", STOCK_KERNEL_PA);
	con_hex("STOCK_BYTES", kernel_size);
	copy_ram(STOCK_KERNEL_PA, stock_kernel_start, kernel_size);
	con_str("CHAIN", "entering original Android kernel; no firmware reset");
	android_chain_enter(dtb, STOCK_KERNEL_PA);
}
