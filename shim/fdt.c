/*
 * Just enough flattened-device-tree parsing to find what ABL put in
 * /chosen (initrd location). Big-endian, 4-byte aligned tokens.
 */
#include "fdt.h"

#define FDT_MAGIC	0xd00dfeed
#define FDT_BEGIN_NODE	1
#define FDT_END_NODE	2
#define FDT_PROP	3
#define FDT_NOP		4
#define FDT_END		9

static uint32_t be32(uintptr_t a)
{
	return __builtin_bswap32(*(volatile uint32_t *)a);
}

static int streq(const char *a, const char *b)
{
	while (*a && *a == *b)
		a++, b++;
	return *a == *b;
}

static uintptr_t align4(uintptr_t a)
{
	return (a + 3) & ~3UL;
}

static uintptr_t skip_str(uintptr_t p)
{
	/* byte loads are fine on Device memory as long as they are aligned to 1 */
	while (*(volatile char *)p)
		p++;
	return p + 1;
}

static int fdt_chosen_cell(uintptr_t fdt, const char *prop, uintptr_t *cell,
	    uint32_t *cell_len)
{
	if (!fdt || be32(fdt) != FDT_MAGIC)
		return 0;
	uintptr_t p = fdt + be32(fdt + 8);		/* off_dt_struct */
	uintptr_t strings = fdt + be32(fdt + 12);	/* off_dt_strings */
	int depth = 0, in_chosen = 0;

	for (;;) {
		uint32_t tok = be32(p);
		p += 4;
		switch (tok) {
		case FDT_BEGIN_NODE: {
			const char *name = (const char *)p;
			depth++;
			in_chosen = depth == 2 && streq(name, "chosen");
			p = align4(skip_str(p));
			break;
		}
		case FDT_END_NODE:
			if (in_chosen)
				return 0;
			depth--;
			break;
		case FDT_PROP: {
			uint32_t len = be32(p), nameoff = be32(p + 4);
			uintptr_t val = p + 8;
			if (in_chosen && streq((const char *)(strings + nameoff), prop)) {
				if (len != 4 && len != 8)
					return 0;
				*cell = val;
				*cell_len = len;
				return 1;
			}
			p = align4(val + len);
			break;
		}
		case FDT_NOP:
			break;
		default:	/* FDT_END or garbage */
			return 0;
		}
	}
}

int fdt_chosen_u64(uintptr_t fdt, const char *prop, uint64_t *out)
{
	uintptr_t val;
	uint32_t len;
	if (!fdt_chosen_cell(fdt, prop, &val, &len))
		return 0;
	*out = len == 4 ? be32(val) : (uint64_t)be32(val) << 32 | be32(val + 4);
	return 1;
}

int fdt_set_chosen_u64(uintptr_t fdt, const char *prop, uint64_t value)
{
	uintptr_t val;
	uint32_t len;
	if (!fdt_chosen_cell(fdt, prop, &val, &len) || (len == 4 && value > 0xffffffffULL))
		return 0;
	if (len == 8) {
		*(volatile uint32_t *)val = __builtin_bswap32((uint32_t)(value >> 32));
		val += 4;
	}
	*(volatile uint32_t *)val = __builtin_bswap32((uint32_t)value);
	return 1;
}

int fdt_reserved_region(uintptr_t fdt, uint64_t start, uint64_t size)
{
	if (!fdt || be32(fdt) != FDT_MAGIC)
		return 0;
	uint32_t total = be32(fdt + 4), off = be32(fdt + 8), bytes = be32(fdt + 36);
	uint32_t soff = be32(fdt + 12), sbytes = be32(fdt + 32);
	if (off > total || bytes > total - off || soff > total || sbytes > total - soff)
		return 0;
	uintptr_t p = fdt + off, end = p + bytes, strings = fdt + soff;
	int depth = 0, in_reserved = 0;
	while (p + 4 <= end) {
		uint32_t tok = be32(p);
		p += 4;
		if (tok == FDT_BEGIN_NODE) {
			depth++;
			if (depth == 2)
				in_reserved = streq((const char *)p, "reserved-memory");
			p = align4(skip_str(p));
		} else if (tok == FDT_END_NODE) {
			if (depth == 2 && in_reserved)
				return 0;
			depth--;
		} else if (tok == FDT_PROP) {
			if (p + 8 > end)
				return 0;
			uint32_t len = be32(p), nameoff = be32(p + 4);
			p += 8;
			if (len > end - p || nameoff >= sbytes)
				return 0;
			if (in_reserved && depth == 3 && !(len & 15) &&
			    streq((const char *)(strings + nameoff), "reg")) {
				for (uint32_t i = 0; i < len; i += 16) {
					uint64_t a = (uint64_t)be32(p + i) << 32 | be32(p + i + 4);
					uint64_t n = (uint64_t)be32(p + i + 8) << 32 | be32(p + i + 12);
					if (a == start && n == size)
						return 1;
				}
			}
			p = align4(p + len);
		} else if (tok != FDT_NOP) {
			return 0;
		}
	}
	return 0;
}
