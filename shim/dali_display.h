#pragma once
#include <stdint.h>

/* Retained LK pipeline only: no panel reset, clocks, IOMMU or storage setup. */
int dali_display_init(uintptr_t fdt);
int dali_display_flush(void);
uintptr_t dali_display_ovl(void);
uint32_t dali_display_status(void);
