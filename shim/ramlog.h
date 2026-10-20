#pragma once
#include <stdint.h>

#if defined(DALI_RAMLOG)
int ramlog_init(uintptr_t dtb);
void ramlog_write(const char *s);
void ramlog_commit(void);
#endif
