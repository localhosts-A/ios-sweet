#pragma once
#include <stdint.h>

/* Boot the SWEETXNU payload found in the ABL ramdisk; returns only on error. */
const char *loader_boot(uintptr_t dtb);
#if defined(DALI_ANDROID_CHAIN)
int loader_generic_range(uint64_t *start, uint64_t *end);
#endif
