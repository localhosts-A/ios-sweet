/*
 * Minimal text console on the ABL splash framebuffer. With the MMU off
 * every access is Device memory, so stay aligned (-mstrict-align).
 */
#include "console.h"
#include "font.h"
#include "ramlog.h"
#if defined(DALI_DISPLAY)
#include "dali_display.h"
#endif

#define SCALE		3
#define CH_W		(8 * SCALE)
#define CH_H		(16 * SCALE)
#define MARGIN		24
#define LABEL_COLS	8

volatile uint32_t *con_fb = (volatile uint32_t *)FB_BASE;
static uint32_t con_bg;
static int row;

#if defined(DALI_DISPLAY)
static uint32_t surface_pitch = FB_STRIDE;
static uint32_t surface_format = 2;
static unsigned int surface_bpp = 4;

void con_set_surface(uintptr_t pa, uint32_t pitch, uint32_t format)
{
	con_fb = (volatile uint32_t *)pa;
	surface_pitch = pitch;
	surface_format = format;
	unsigned int code = format & 0xf;
	surface_bpp = code == 0 ? 2 : code == 1 ? 3 : 4;
}

void con_pixel(unsigned int x, unsigned int y, uint32_t color)
{
	if (!con_fb || x >= FB_W || y >= FB_H)
		return;
	uint32_t r = (color >> 16) & 255, g = (color >> 8) & 255, b = color & 255;
	if (surface_format & (1U << 17)) {
		uint32_t swap = r; r = b; b = swap;
	}
	uintptr_t p = (uintptr_t)con_fb + y * surface_pitch + x * surface_bpp;
	uint32_t value = 0xff000000U | (r << 16) | (g << 8) | b;
	if (surface_bpp == 2) {
		uint16_t c = ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3);
		if (surface_format & (1U << 16))
			c = __builtin_bswap16(c);
		*(volatile uint16_t *)p = c;
	} else if (surface_bpp == 3) {
		if (surface_format & (1U << 16))
			value = (b << 16) | (g << 8) | r;
		for (unsigned int i = 0; i < 3; i++)
			*(volatile uint8_t *)(p + i) = value >> (i * 8);
	} else {
		if ((surface_format & 0xf) == 3)
			value = (value << 8) | 255;
		if (surface_format & (1U << 16))
			value = __builtin_bswap32(value);
		*(volatile uint32_t *)p = value;
	}
}
#endif

void con_clear(uint32_t bg)
{
#if defined(DALI_RAMLOG_PROBE)
	/* Isolate logging from the currently unconfirmed panel/framebuffer path. */
	con_bg = bg;
	row = 0;
	return;
#endif
	for (int y = 0; y < FB_H; y++) {
#if defined(DALI_DISPLAY)
		for (int x = 0; x < FB_W; x++)
			con_pixel(x, y, bg);
#else
		volatile uint32_t *line = con_fb + y * (FB_STRIDE / 4);
		for (int x = 0; x < FB_W; x++)
			line[x] = bg;
#endif
	}
	con_bg = bg;
	row = 0;
#if defined(DALI_DISPLAY)
	dali_display_flush();
#endif
}

static void put_char(int col, char ch, uint32_t fg)
{
	if (ch < 0x20 || ch > 0x7e)
		ch = '?';
	const unsigned char *g = font8x16[ch - 0x20];
	int x0 = MARGIN + col * CH_W, y0 = MARGIN + row * CH_H;
	if (x0 + CH_W > FB_W || y0 + CH_H > FB_H)
		return;
	for (int gy = 0; gy < 16; gy++)
		for (int gx = 0; gx < 8; gx++) {
			uint32_t c = (g[gy] & (0x80 >> gx)) ? fg : con_bg;
			for (int sy = 0; sy < SCALE; sy++) {
#if defined(DALI_DISPLAY)
				for (int sx = 0; sx < SCALE; sx++)
					con_pixel(x0 + gx * SCALE + sx, y0 + gy * SCALE + sy, c);
#else
				volatile uint32_t *p = con_fb + (y0 + gy * SCALE + sy) * (FB_STRIDE / 4)
						       + x0 + gx * SCALE;
				for (int sx = 0; sx < SCALE; sx++)
					p[sx] = c;
#endif
			}
		}
}

static int put_str(int col, const char *s, uint32_t fg)
{
	while (*s)
		put_char(col++, *s++, fg);
	return col;
}

static void fmt_hex(char *buf, uint64_t v, int digits)
{
	static const char hex[] = "0123456789ABCDEF";
	for (int i = 0; i < digits; i++)
		buf[i] = hex[(v >> (4 * (digits - 1 - i))) & 0xf];
	buf[digits] = 0;
}

void con_skip(void)
{
	row++;
}

void con_str(const char *label, const char *s)
{
#if defined(DALI_RAMLOG)
	ramlog_write(label);
	ramlog_write(": ");
	ramlog_write(s);
	ramlog_write("\n");
	ramlog_commit();
#endif
#if defined(DALI_RAMLOG_PROBE)
	return;
#endif
	put_str(0, label, YELLOW);
	put_str(LABEL_COLS, s, WHITE);
	row++;
#if defined(DALI_DISPLAY)
	dali_display_flush();
#endif
}

void con_hex(const char *label, uint64_t v)
{
	char buf[17];
	fmt_hex(buf, v, 16);
	con_str(label, buf);
}

void con_hex2(const char *label, const char *a, uint32_t va, const char *b, uint32_t vb)
{
	char buf[9];
#if defined(DALI_RAMLOG)
	ramlog_write(label);
	ramlog_write(": ");
	ramlog_write(a);
	fmt_hex(buf, va, 8);
	ramlog_write(buf);
	if (b) {
		ramlog_write(" ");
		ramlog_write(b);
		fmt_hex(buf, vb, 8);
		ramlog_write(buf);
	}
	ramlog_write("\n");
	ramlog_commit();
#endif
#if defined(DALI_RAMLOG_PROBE)
	return;
#endif
	int col = put_str(0, label, YELLOW);
	col = col < LABEL_COLS ? LABEL_COLS : col + 1;
	col = put_str(col, a, YELLOW);
	fmt_hex(buf, va, 8);
	col = put_str(col, buf, WHITE);
	if (b) {
		col = put_str(col + 1, b, YELLOW);
		fmt_hex(buf, vb, 8);
		put_str(col, buf, WHITE);
	}
	row++;
#if defined(DALI_DISPLAY)
	dali_display_flush();
#endif
}
