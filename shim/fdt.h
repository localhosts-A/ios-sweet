#pragma once
#include <stdint.h>

/* Read a u32/u64 (cell-size agnostic) property of /chosen. Returns 0 if absent. */
int fdt_chosen_u64(uintptr_t fdt, const char *prop, uint64_t *out);
/* Replace an existing u32/u64 in RAM; refuses a u32 overflow or absent field. */
int fdt_set_chosen_u64(uintptr_t fdt, const char *prop, uint64_t value);
/* Check an exact 64-bit address/size pair under /reserved-memory. */
int fdt_reserved_region(uintptr_t fdt, uint64_t start, uint64_t size);
