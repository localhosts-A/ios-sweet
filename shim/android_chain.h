#pragma once
#include <stdint.h>

/* Diagnostic-only handoff to the stock Android kernel; returns on error. */
const char *android_chain_boot(uintptr_t dtb);
