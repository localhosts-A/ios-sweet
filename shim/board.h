#pragma once

/* Hardware values confirmed from the live Dali FDT or the Dali Linux map. */
#if defined(BOARD_DALI)
#define GICD_BASE		0x0c400000UL
#define GICR_BASE		0x0c440000UL
#define GICR_SIZE		0x200000UL
#define GICR_PE_SIZE		0x20000UL
#define VTIMER_INTID		27	/* FDT PPI 11 -> GIC INTID 16 + 11 */
#define PTIMER_INTID		30	/* FDT PPI 14 -> GIC INTID 16 + 14 */
#define TIMER_HZ		13000000UL
#define UFS_BASE		0x16810000UL	/* MediaTek UFSHCI */
#define WDT_BASE		0x1c010000UL	/* live watchdog FDT, MT6589-compatible TOPRGU */
#define BOARD_MAP_END		0x400000000ULL	/* identity-map through 16 GiB */
#define NR_CPUS			1	/* initial Dali boot is single-core */
#else
#define GICD_BASE		0x17a00000UL
#define GICR_BASE		0x17a60000UL
#define GICR_SIZE		0x100000UL
#define GICR_PE_SIZE		0x20000UL
#define VTIMER_INTID		19	/* Qualcomm timer PPI */
#define PTIMER_INTID		18
#define TIMER_HZ		19200000UL
#define UFS_BASE		0x01d84000UL	/* Qualcomm UFSHCI */
#define BOARD_MAP_END		(8ULL << 30)
#define NR_CPUS			8
#endif
