#pragma once
#include <stdint.h>

#if defined(BOARD_DALI)
#define FB_BASE		0xfc990000UL	/* live Dali bootloader framebuffer */
#define FB_W		1280
#define FB_H		2772
#define FB_STRIDE	(FB_W * 4)
#else
#define FB_BASE		0x9c000000UL	/* sweet reserved-memory cont_splash_region */
#define FB_W		1080
#define FB_H		2400
#define FB_STRIDE	(FB_W * 4)	/* a8r8g8b8, matches mainline simplefb */
#endif

#define RED	0xffff0000U
#define BLUE	0xff0000a0U
#define WHITE	0xffffffffU
#define YELLOW	0xffffff00U
#define GREEN	0xff00ff00U

extern volatile uint32_t *con_fb;

#if defined(DALI_DISPLAY)
void con_set_surface(uintptr_t pa, uint32_t pitch, uint32_t format);
void con_pixel(unsigned int x, unsigned int y, uint32_t color);
#endif
void con_clear(uint32_t bg);
void con_skip(void);
void con_str(const char *label, const char *s);
void con_hex(const char *label, uint64_t v);
/* label, then "a=xxxxxxxx b=xxxxxxxx" style pairs of 32-bit values */
void con_hex2(const char *label, const char *a, uint32_t va, const char *b, uint32_t vb);
