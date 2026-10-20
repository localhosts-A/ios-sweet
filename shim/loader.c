/*
 * Milestone 4: act as iBoot for xnu.
 *
 * mkpayload.py already resolved the kernel's chained fixups and built the
 * ADT, so this only has to: find the payload in the ABL ramdisk, copy the
 * segments to phys_base + (vmaddr - virt_base), place the ADT and
 * boot_args behind the kernel, and jump to the entry point with the MMU
 * and caches off, x0 = boot_args (physical).
 */
#include <stdint.h>
#include "console.h"
#include "fdt.h"
#include "loader.h"

#define BOOT_LINE_LENGTH	608
#define PAGE_16K		0x4000UL
#define PAYLOAD_HEADER_SIZE	0x2a8U
#define MAX_PAYLOAD_SEGS	32U
#define MAX_PAYLOAD_SIZE	(32ULL << 20)
#define LZ4_BLOCK_SIZE		(64U << 10)
#define LZ4_FRAME_MAGIC		0x184d2204U
#define INITBOOT_SEARCH_WINDOW	(16ULL << 20)

struct payload_seg {
	uint64_t vmaddr, vmsize, file_off, file_size;
};

struct payload {
	char magic[8];
	uint32_t version, nsegs;
	uint64_t virt_base, entry_va, adt_off, adt_size, kernel_end;
	uint64_t phys_base, mem_size;
	char cmdline[BOOT_LINE_LENGTH];
	struct payload_seg segs[];
};

/* pexpert/pexpert/arm64/boot.h, Revision/Version 2 */
struct boot_video {
	uint64_t v_baseAddr, v_display, v_rowBytes, v_width, v_height, v_depth;
};

struct boot_args {
	uint16_t Revision, Version;
	uint64_t virtBase, physBase, memSize, topOfKernelData;
	struct boot_video Video;
	uint32_t machineType;
	uint64_t deviceTreeP;
	uint32_t deviceTreeLength;
	char CommandLine[BOOT_LINE_LENGTH];
	uint64_t bootFlags, memSizeActual;
};

_Static_assert(__builtin_offsetof(struct payload, segs) == 0x2a8, "payload header layout");
_Static_assert(__builtin_offsetof(struct boot_args, Video) == 40, "boot_args layout");
_Static_assert(__builtin_offsetof(struct boot_args, CommandLine) == 108, "boot_args layout");
_Static_assert(sizeof(struct boot_args) == 736, "boot_args layout");

/* MMU off: everything is Device memory, so copy in aligned 8-byte words. */
#if !defined(BOARD_DALI)
static void copy64(uintptr_t dst, uintptr_t src, uint64_t n)
{
	volatile uint64_t *d = (volatile uint64_t *)dst;
	volatile const uint64_t *s = (volatile const uint64_t *)src;
	for (uint64_t i = 0; i < (n + 7) / 8; i++)
		d[i] = s[i];
}
#endif

static int magic_ok(const char *m)
{
	static const char want[] = "SWEETXNU";
	for (int i = 0; i < 8; i++)
		if (m[i] != want[i])
			return 0;
	return 1;
}

typedef void (*xnu_entry_t)(uint64_t boot_args_pa);

static uint8_t payload_meta_bytes[PAYLOAD_HEADER_SIZE + MAX_PAYLOAD_SEGS * sizeof(struct payload_seg)]
	__attribute__((aligned(16)));
static uint8_t lz4_block_out[LZ4_BLOCK_SIZE] __attribute__((aligned(16)));

#if defined(DALI_PAYLOAD_PROBE_STAGED)
volatile uint32_t loader_probe_stage;
#define PROBE_STAGE(n) (loader_probe_stage = (n))
#else
#define PROBE_STAGE(n) ((void)0)
#endif

#if defined(DALI_PAYLOAD_PROBE_VALIDATE)
volatile uint32_t loader_probe_validation;
#define VALIDATION_REJECT(n) (loader_probe_validation = (n), 0)
#else
#define VALIDATION_REJECT(n) 0
#endif

static uint32_t read_le32(uint64_t p)
{
	const volatile uint8_t *b = (const volatile uint8_t *)p;
	return (uint32_t)b[0] | ((uint32_t)b[1] << 8) |
	    ((uint32_t)b[2] << 16) | ((uint32_t)b[3] << 24);
}

static uint64_t read_le64(uint64_t p)
{
	return (uint64_t)read_le32(p) | ((uint64_t)read_le32(p + 4) << 32);
}

#if !defined(DALI_PAYLOAD_PROBE_PARSE_ONLY)
static void copy_bytes(uint64_t dst, const uint8_t *src, uint64_t n)
{
	volatile uint8_t *d = (volatile uint8_t *)dst;
	for (uint64_t i = 0; i < n; i++)
		d[i] = src[i];
}

static void zero_bytes(uint64_t dst, uint64_t n)
{
	volatile uint8_t *d = (volatile uint8_t *)dst;
	for (uint64_t i = 0; i < n; i++)
		d[i] = 0;
}
#endif

static int add_overflow_u64(uint64_t a, uint64_t b, uint64_t *out)
{
	*out = a + b;
	return *out < a;
}

static uint32_t parse_hex8(const uint8_t *s)
{
	uint32_t v = 0;
	for (unsigned int i = 0; i < 8; i++) {
		uint8_t c = s[i];
		uint32_t d;
		if (c >= '0' && c <= '9')
			d = c - '0';
		else if (c >= 'a' && c <= 'f')
			d = c - 'a' + 10;
		else if (c >= 'A' && c <= 'F')
			d = c - 'A' + 10;
		else
			return 0xffffffffU;
		v = (v << 4) | d;
	}
	return v;
}

static int payload_validate(const struct payload *p, uint64_t payload_size,
	    uint64_t rd_start, uint64_t rd_end)
{
	uint64_t phys_end, kernel_span, ba_offset;
	if (!magic_ok(p->magic) || p->version != 1 || !p->nsegs ||
	    p->nsegs > MAX_PAYLOAD_SEGS || p->mem_size == 0 ||
	    p->kernel_end < p->virt_base || p->entry_va < p->virt_base ||
	    p->entry_va >= p->kernel_end || p->adt_size == 0 ||
	    p->adt_off < PAYLOAD_HEADER_SIZE + p->nsegs * sizeof(struct payload_seg) ||
	    add_overflow_u64(p->phys_base, p->mem_size, &phys_end))
		return VALIDATION_REJECT(1);
	kernel_span = p->kernel_end - p->virt_base;
	if (kernel_span >= p->mem_size || p->adt_size > (4ULL << 20) ||
	    p->adt_off > payload_size || p->adt_size > payload_size - p->adt_off)
		return VALIDATION_REJECT(2);
	if (rd_end > p->phys_base && rd_start < phys_end)
		return VALIDATION_REJECT(3);
	if (add_overflow_u64(kernel_span, p->adt_size, &ba_offset) ||
	    add_overflow_u64(ba_offset, PAGE_16K - 1, &ba_offset))
		return VALIDATION_REJECT(4);
	ba_offset &= ~(PAGE_16K - 1);
	if (ba_offset > p->mem_size || PAGE_16K > p->mem_size - ba_offset)
		return VALIDATION_REJECT(4);
	for (uint32_t i = 0; i < p->nsegs; i++) {
		const struct payload_seg *s = &p->segs[i];
		uint64_t va_end, file_end, off;
		if (s->vmaddr < p->virt_base || s->vmsize < s->file_size ||
		    add_overflow_u64(s->vmaddr, s->vmsize, &va_end) ||
		    va_end > p->kernel_end)
			return VALIDATION_REJECT(5);
		if (add_overflow_u64(s->file_off, s->file_size, &file_end) ||
		    s->file_off < PAYLOAD_HEADER_SIZE + p->nsegs * sizeof(struct payload_seg) ||
		    file_end > payload_size)
			return VALIDATION_REJECT(6);
		off = s->vmaddr - p->virt_base;
		if (off > p->mem_size || s->vmsize > p->mem_size - off)
			return VALIDATION_REJECT(7);
	}
	return 1;
}

#if !defined(DALI_PAYLOAD_PROBE)
static uintptr_t payload_phys_base(const struct payload *p)
{
	return (uintptr_t)(p->phys_base - p->virt_base);
}
#endif

static uintptr_t payload_adt_pa(const struct payload *p)
{
	return (uintptr_t)(p->phys_base + (p->kernel_end - p->virt_base));
}

#if !defined(DALI_PAYLOAD_PROBE)
static void launch_xnu(const struct payload *p, uintptr_t adt_pa)
{
	uint64_t pa_of_va_base = payload_phys_base(p);
	uintptr_t ba_pa = (adt_pa + p->adt_size + PAGE_16K - 1) & ~(PAGE_16K - 1);
	uintptr_t top = ba_pa + PAGE_16K;

	zero_bytes(ba_pa, PAGE_16K);
	volatile struct boot_args *ba = (volatile struct boot_args *)ba_pa;
	ba->Revision = 2;
	ba->Version = 2;
	ba->virtBase = p->virt_base;
	ba->physBase = p->phys_base;
	ba->memSize = p->mem_size;
	ba->topOfKernelData = top;
	ba->Video.v_baseAddr = FB_BASE;
	ba->Video.v_display = 1;
	ba->Video.v_rowBytes = FB_STRIDE;
	ba->Video.v_width = FB_W;
	ba->Video.v_height = FB_H;
	ba->Video.v_depth = 32;
	ba->deviceTreeP = adt_pa - pa_of_va_base; /* XNU reads this with the MMU on: a VA. */
	ba->deviceTreeLength = (uint32_t)p->adt_size;
	for (int i = 0; i < BOOT_LINE_LENGTH; i++)
		ba->CommandLine[i] = p->cmdline[i];
	ba->memSizeActual = p->mem_size;
	con_hex2("BOOTARG", "pa=", ba_pa, "top=", top);

	uintptr_t entry_pa = p->entry_va + pa_of_va_base;
	con_hex("JUMP", entry_pa);
	con_str("XNU", "jumping (fbcon clears screen)");
	__asm__ volatile("dsb sy; ic iallu; dsb sy; isb" ::: "memory");
	((xnu_entry_t)entry_pa)(ba_pa);
}
#endif

#if defined(BOARD_DALI)
struct payload_stream {
	uint64_t pos, size, adt_bytes;
	uint32_t meta_target;
	int ready, error;
	const struct payload *p;
	uintptr_t adt_pa;
};

static const char *payload_metadata_ready(struct payload_stream *s,
	    uint64_t rd_start, uint64_t rd_end)
{
	const struct payload *p = (const struct payload *)payload_meta_bytes;
	if (!payload_validate(p, s->size, rd_start, rd_end))
		return "invalid SWEETXNU metadata";
	PROBE_STAGE(6);
	s->p = p;
	s->adt_pa = payload_adt_pa(p);
	uint64_t adt_end;
	if (add_overflow_u64(s->adt_pa, p->adt_size, &adt_end) ||
	    adt_end > p->phys_base + p->mem_size ||
	    (rd_end > s->adt_pa && rd_start < adt_end))
		return "ADT destination outside kernel memory";
	PROBE_STAGE(7);
#if !defined(DALI_PAYLOAD_PROBE_PARSE_ONLY)
	for (uint32_t i = 0; i < p->nsegs; i++) {
		const struct payload_seg *seg = &p->segs[i];
		uint64_t dst = p->phys_base + (seg->vmaddr - p->virt_base);
		if (seg->vmsize > seg->file_size)
			zero_bytes(dst + seg->file_size, seg->vmsize - seg->file_size);
	}
#endif
	s->ready = 1;
	return 0;
}

static const char *payload_stream_bytes(struct payload_stream *s,
	    const uint8_t *data, uint32_t len, uint64_t rd_start, uint64_t rd_end)
{
	while (len) {
		if (!s->ready) {
			uint32_t take = s->meta_target - (uint32_t)s->pos;
			if (take > len)
				take = len;
			for (uint32_t i = 0; i < take; i++)
				payload_meta_bytes[s->pos + i] = data[i];
			s->pos += take;
			data += take;
			len -= take;
			if (s->pos == PAYLOAD_HEADER_SIZE && s->meta_target == PAYLOAD_HEADER_SIZE) {
				const struct payload *p = (const struct payload *)payload_meta_bytes;
				if (!magic_ok(p->magic) || p->version != 1 || !p->nsegs ||
				    p->nsegs > MAX_PAYLOAD_SEGS)
					return "bad SWEETXNU header";
				PROBE_STAGE(5);
				s->meta_target = PAYLOAD_HEADER_SIZE + p->nsegs * sizeof(struct payload_seg);
			}
			if (s->pos == s->meta_target) {
				const char *err = payload_metadata_ready(s, rd_start, rd_end);
				if (err)
					return err;
			}
			continue;
		}

		uint64_t begin = s->pos;
		uint64_t end = begin + len;
		const struct payload *p = s->p;
#if !defined(DALI_PAYLOAD_PROBE_PARSE_ONLY)
		for (uint32_t i = 0; i < p->nsegs; i++) {
			const struct payload_seg *seg = &p->segs[i];
			uint64_t seg_end = seg->file_off + seg->file_size;
			uint64_t lo = begin > seg->file_off ? begin : seg->file_off;
			uint64_t hi = end < seg_end ? end : seg_end;
			if (hi > lo) {
				uint64_t dst = p->phys_base + (seg->vmaddr - p->virt_base) + (lo - seg->file_off);
				copy_bytes(dst, data + (lo - begin), hi - lo);
			}
		}
#endif
		uint64_t adt_end = p->adt_off + p->adt_size;
		uint64_t lo = begin > p->adt_off ? begin : p->adt_off;
		uint64_t hi = end < adt_end ? end : adt_end;
		if (hi > lo) {
#if !defined(DALI_PAYLOAD_PROBE_PARSE_ONLY)
			copy_bytes(s->adt_pa + (lo - p->adt_off), data + (lo - begin), hi - lo);
#endif
			s->adt_bytes += hi - lo;
		}
		s->pos = end;
		return 0;
	}
	return 0;
}

struct cpio_stream {
	uint8_t header[110];
	uint8_t name[32];
	uint32_t header_have, name_have, name_size;
	uint64_t file_size, file_have;
	uint32_t state;
	struct payload_stream payload;
};

static const char *cpio_feed(struct cpio_stream *c, const uint8_t *data, uint32_t len,
	    uint64_t rd_start, uint64_t rd_end)
{
	while (len && c->state != 4) {
		if (c->state == 0) {
			uint32_t n = 110 - c->header_have;
			if (n > len)
				n = len;
			for (uint32_t i = 0; i < n; i++)
				c->header[c->header_have + i] = data[i];
			c->header_have += n;
			data += n;
			len -= n;
			if (c->header_have == 110) {
				uint32_t file_size = parse_hex8(c->header + 54);
				uint32_t name_size = parse_hex8(c->header + 94);
				if (c->header[0] != '0' || c->header[1] != '7' ||
				    c->header[2] != '0' || c->header[3] != '7' ||
				    c->header[4] != '0' || c->header[5] != '1' ||
				    file_size == 0xffffffffU || name_size != 14)
					return "invalid init_boot CPIO header";
				c->file_size = file_size;
				c->name_size = name_size;
				c->state = 1;
			}
		} else if (c->state == 1) {
			uint32_t n = c->name_size - c->name_have;
			if (n > len)
				n = len;
			for (uint32_t i = 0; i < n; i++)
				c->name[c->name_have + i] = data[i];
			c->name_have += n;
			data += n;
			len -= n;
			if (c->name_have == c->name_size) {
				static const char want[] = "sweet.payload";
				for (unsigned int i = 0; i < sizeof(want); i++)
					if (c->name[i] != want[i])
						return "init_boot CPIO lacks sweet.payload";
				if (c->file_size > MAX_PAYLOAD_SIZE)
					return "SWEETXNU payload exceeds loader limit";
				c->payload.size = c->file_size;
				c->payload.meta_target = PAYLOAD_HEADER_SIZE;
				c->state = 2;
				PROBE_STAGE(4);
			}
		} else {
			uint64_t remaining = c->file_size - c->file_have;
			uint32_t n = remaining < len ? (uint32_t)remaining : len;
			const char *err = payload_stream_bytes(&c->payload, data, n, rd_start, rd_end);
			if (err)
				return err;
			c->file_have += n;
			data += n;
			len -= n;
			if (c->file_have == c->file_size)
				c->state = 4;
		}
	}
	return 0;
}

static int lz4_block_decode(const uint8_t *src, uint32_t src_len,
	    uint8_t *dst, uint32_t *dst_len)
{
	uint32_t ip = 0, op = 0;
	while (ip < src_len) {
		uint8_t token = src[ip++];
		uint32_t literals = token >> 4;
		if (literals == 15) {
			uint8_t x;
		do {
			if (ip >= src_len)
				return 0;
			x = src[ip++];
			literals += x;
		} while (x == 255);
		}
		if (literals > src_len - ip || literals > LZ4_BLOCK_SIZE - op)
			return 0;
		for (uint32_t i = 0; i < literals; i++)
			dst[op + i] = src[ip + i];
		ip += literals;
		op += literals;
		if (ip == src_len)
			break;
		if (src_len - ip < 2)
			return 0;
		uint32_t offset = src[ip] | ((uint32_t)src[ip + 1] << 8);
		ip += 2;
		if (!offset || offset > op)
			return 0;
		uint32_t match = token & 15;
		if (match == 15) {
			uint8_t x;
			do {
				if (ip >= src_len)
					return 0;
				x = src[ip++];
				match += x;
			} while (x == 255);
		}
		match += 4;
		if (match > LZ4_BLOCK_SIZE - op)
			return 0;
		for (uint32_t i = 0; i < match; i++)
			dst[op + i] = dst[op + i - offset];
		op += match;
	}
	*dst_len = op;
	return 1;
}

static int frame_layout(uint64_t start, uint64_t limit, uint64_t *frame_end,
	    uint64_t *content_size)
{
	if (start + 15 > limit || read_le32(start) != LZ4_FRAME_MAGIC)
		return 0;
	const volatile uint8_t *p = (const volatile uint8_t *)(start + 4);
	uint8_t flg = p[0], bd = p[1];
	if ((flg & 0xc0) != 0x40 || !(flg & 0x20) || !(flg & 0x08) ||
	    (flg & 0x02) || (flg & 0x01) || ((bd >> 4) & 7) != 4)
		return 0;
	uint64_t cur = start + 6;
	*content_size = read_le64(cur);
	cur += 8;
	cur++; /* Header checksum. The frame writer is local and the fields are bounded. */
	if (*content_size > MAX_PAYLOAD_SIZE + (1ULL << 20))
		return 0;
	for (;;) {
		if (cur + 4 > limit)
			return 0;
		uint32_t block = read_le32(cur);
		cur += 4;
		if (!block)
			break;
		uint32_t size = block & 0x7fffffffU;
		if (!size || size > LZ4_BLOCK_SIZE + 1024 || cur + size > limit)
			return 0;
		cur += size;
		if (flg & 0x10) {
			if (cur + 4 > limit)
				return 0;
			cur += 4;
		}
	}
	if (flg & 0x04) {
		if (cur + 4 > limit)
			return 0;
		cur += 4;
	}
	uint64_t padding = limit - cur;
	if (padding > 4096)
		return 0;
	for (uint64_t i = 0; i < padding; i++)
		if (*(const volatile uint8_t *)(cur + i) != 0)
			return 0;
	*frame_end = cur;
	return 1;
}

/* Android may append [bootconfig][padding][size][checksum][#BOOTCONFIG\n]. */
static const char *strip_bootconfig(uint64_t rd_start, uint64_t rd_end,
	    uint64_t *ramdisk_end)
{
	static const char magic[] = "#BOOTCONFIG\n";
	uint64_t span = rd_end - rd_start;
	if (span < (sizeof(magic) - 1) + 8) {
		*ramdisk_end = rd_end;
		return 0;
	}
	uint64_t max_pad = span > sizeof(magic) - 1 ? span - (sizeof(magic) - 1) : 0;
	if (max_pad > 4096)
		max_pad = 4096;
	for (uint64_t pad = 0; pad <= max_pad; pad++) {
		uint64_t magic_at = rd_end - pad - (sizeof(magic) - 1);
		unsigned int i;
		for (i = 0; i < sizeof(magic) - 1; i++)
			if (*(const volatile uint8_t *)(magic_at + i) != (uint8_t)magic[i])
				break;
		if (i != sizeof(magic) - 1)
			continue;
		for (uint64_t i = 0; i < pad; i++)
			if (*(const volatile uint8_t *)(rd_end - pad + i) != 0)
				return "invalid padding after bootconfig";
		if (magic_at < rd_start + 8)
			return "truncated bootconfig trailer";
		uint64_t size_at = magic_at - 8;
		uint32_t config_size = read_le32(size_at);
		if (config_size > (1U << 20) || config_size > size_at - rd_start)
			return "invalid bootconfig size";
		*ramdisk_end = size_at - config_size;
		return 0;
	}
	*ramdisk_end = rd_end;
	return 0;
}

static int find_initboot_frame(uint64_t rd_start, uint64_t rd_end,
	    uint64_t *frame_start, uint64_t *frame_end, uint64_t *content_size)
{
	if (rd_end < rd_start + 4)
		return 0;
	uint64_t low = rd_end > INITBOOT_SEARCH_WINDOW ? rd_end - INITBOOT_SEARCH_WINDOW : rd_start;
	if (low < rd_start)
		low = rd_start;
	for (uint64_t at = rd_end - 4;; at--) {
		if (read_le32(at) == LZ4_FRAME_MAGIC &&
		    frame_layout(at, rd_end, frame_end, content_size)) {
			*frame_start = at;
			return 1;
		}
		if (at == low)
			break;
	}
	return 0;
}

static const char *decode_initboot(uint64_t start, uint64_t end, uint64_t content_size,
	    struct cpio_stream *cpio, uint64_t rd_start, uint64_t rd_end)
{
	uint64_t cur = start + 4;
	const volatile uint8_t *descriptor = (const volatile uint8_t *)cur;
	uint8_t flg = descriptor[0];
	cur += 2 + 8 + 1; /* FLG, BD, content size, header checksum. */
	uint64_t total_out = 0;
	for (;;) {
		uint32_t block = read_le32(cur);
		cur += 4;
		if (!block)
			break;
		uint32_t compressed_size = block & 0x7fffffffU;
		uint32_t out_size;
		const uint8_t *out;
		if (block & 0x80000000U) {
			out = (const uint8_t *)cur;
			out_size = compressed_size;
		} else {
			if (!lz4_block_decode((const uint8_t *)cur, compressed_size,
			    lz4_block_out, &out_size))
				return "invalid LZ4 block in init_boot";
			out = lz4_block_out;
		}
		if (out_size > content_size - total_out)
			return "init_boot LZ4 output exceeds declared size";
		const char *err = cpio_feed(cpio, out, out_size, rd_start, rd_end);
		if (err)
			return err;
		total_out += out_size;
		cur += compressed_size;
		if (flg & 0x10)
			cur += 4;
	}
	if (flg & 0x04)
		cur += 4;
	if (cur != end || total_out != content_size || cpio->state != 4)
		return "truncated init_boot LZ4/CPIO stream";
	if (!cpio->payload.ready || cpio->payload.pos != cpio->payload.size ||
	    cpio->payload.adt_bytes != cpio->payload.p->adt_size)
		return "incomplete SWEETXNU payload in init_boot";
	return 0;
}

static const char *loader_boot_dali(uintptr_t dtb)
{
	uint64_t rd_start, rd_end, ramdisk_end, frame_start, frame_end, content_size;
	PROBE_STAGE(0);
	if (!fdt_chosen_u64(dtb, "linux,initrd-start", &rd_start) ||
	    !fdt_chosen_u64(dtb, "linux,initrd-end", &rd_end))
		return "no initrd in /chosen";
	if (rd_end <= rd_start)
		return "invalid initrd range";
	PROBE_STAGE(1);
	con_hex2("INITRD", "lo=", rd_start, "hi=", rd_end);
	const char *err = strip_bootconfig(rd_start, rd_end, &ramdisk_end);
	if (err)
		return err;
	PROBE_STAGE(2);
	if (!find_initboot_frame(rd_start, ramdisk_end, &frame_start, &frame_end, &content_size))
		return "no final LZ4 init_boot frame";
	PROBE_STAGE(3);
	con_hex2("INITBOOT", "frame=", frame_start, "size=", content_size);
	static struct cpio_stream cpio;
	err = decode_initboot(frame_start, frame_end, content_size,
	    &cpio, rd_start, rd_end);
	if (err)
		return err;
	PROBE_STAGE(8);
	con_hex2("PAYLOAD", "bytes=", cpio.payload.size, "segs=", cpio.payload.p->nsegs);
	con_hex("PHYS", cpio.payload.p->phys_base);
	con_hex("ADT", cpio.payload.adt_pa);
#if defined(DALI_PAYLOAD_PROBE)
	/* Verify extraction and destination copies without transferring to XNU. */
	return "payload verified";
#else
	launch_xnu(cpio.payload.p, cpio.payload.adt_pa);
	return "xnu returned";
#endif
}
#endif

const char *loader_boot(uintptr_t dtb)
{
#if defined(BOARD_DALI)
	return loader_boot_dali(dtb);
#else
	uint64_t rd_start, rd_end;

	if (!fdt_chosen_u64(dtb, "linux,initrd-start", &rd_start) ||
	    !fdt_chosen_u64(dtb, "linux,initrd-end", &rd_end))
		return "no initrd in /chosen";
	con_hex2("INITRD", "lo=", rd_start, "hi=", rd_end);

	const struct payload *p = (const struct payload *)rd_start;
	if (!magic_ok(p->magic))
		return "no SWEETXNU payload";
	if ((rd_start & 7) || !payload_validate(p, rd_end - rd_start, rd_start, rd_end))
		return "bad payload header";

	uint64_t pa_of_va_base = p->phys_base - p->virt_base;	/* pa = va + this */
	con_hex2("PHYS", "base=", p->phys_base, "size=", p->mem_size);
	con_hex("VIRT", p->virt_base);
	con_hex("ENTRY", p->entry_va);

	/* The payload must not overlap where the kernel goes. */
	if (rd_end > p->phys_base && rd_start < p->phys_base + p->mem_size)
		return "ramdisk overlaps kernel memory";

	for (uint32_t i = 0; i < p->nsegs; i++) {
		const struct payload_seg *s = &p->segs[i];
		uintptr_t dst = s->vmaddr + pa_of_va_base;
		copy64(dst, rd_start + s->file_off, s->file_size);
		if (s->vmsize > s->file_size)
			zero64(dst + ((s->file_size + 7) & ~7UL), s->vmsize - ((s->file_size + 7) & ~7UL));
	}
	con_hex2("SEGS", "n=", p->nsegs, "end=", p->kernel_end + pa_of_va_base);

	/* ADT, then boot_args, behind the kernel. */
	uintptr_t adt_pa = p->kernel_end + pa_of_va_base;
	copy64(adt_pa, rd_start + p->adt_off, p->adt_size);
	launch_xnu(p, adt_pa);
	return "xnu returned";
#endif
}
