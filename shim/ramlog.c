/* Dali opt-in diagnostic sink, matching the running stock ramoops console.
 * No storage access. Require the exact reserved region in the live FDT.
 * The stock parameters and Linux fs/pstore/ram.c place this console after
 * 0xf000 bytes of dump records; ftrace takes 0x1000 at the region's end.
 */
#include <stdint.h>
#include "ramlog.h"
#include "fdt.h"

#if defined(DALI_RAMLOG)
#if !defined(BOARD_DALI)
#error DALI_RAMLOG requires BOARD_DALI
#endif

#define RAMLOG_PA 0xaec0f000ULL
#define RAMLOG_BYTES 0x200000U
#define RAMLOG_SIG 0x43474244U
#define RAMLOG_LIMIT (16U << 10)

struct ramlog_header {
	uint32_t sig, start, size;
	uint8_t data[];
};

static volatile struct ramlog_header *log_buffer;
static uint32_t log_used;

int ramlog_init(uintptr_t dtb)
{
	volatile struct ramlog_header *b = (volatile struct ramlog_header *)RAMLOG_PA;
	if (!fdt_reserved_region(dtb, 0xaec00000ULL, 0x410000ULL))
		return 0;
	/* Existing stock log was saved by the host before the diagnostic trial. */
	b->sig = RAMLOG_SIG;
	b->start = 0;
	b->size = 0;
	log_used = 0;
	log_buffer = b;
	ramlog_write("SWEET DALI SHIM RAMLOG v1\n");
	ramlog_commit();
	return 1;
}

void ramlog_write(const char *s)
{
	if (!log_buffer)
		return;
	if (!s)
		s = "(null)";
	for (unsigned i = 0; i < 256 && s[i] && log_used < RAMLOG_LIMIT; i++)
		log_buffer->data[log_used++] = (uint8_t)s[i];
}

void ramlog_commit(void)
{
	if (!log_buffer)
		return;
	__asm__ volatile("dsb sy" ::: "memory");
	log_buffer->start = log_used;
	log_buffer->size = log_used;
	__asm__ volatile("dsb sy" ::: "memory");
}
#endif
