/*
 * SWEET internal UFS root device.
 *
 * The Qualcomm UFS host (UFSHCI 2.1) is left enabled with the link up by the
 * bootloader. Dali's MT6991 host is instead accepted only in an exact stopped,
 * idle state; that profile performs a bounded SoC-specific startup before
 * installing XNU-owned request memory. Both paths issue polled SCSI commands,
 * one at a time, to the selected LUN. It finds one GPT partition by name (boot-arg
 * ufsroot=, default "userdata") and exposes it as the BSD disk ufsroot0
 * (rufsroot0), with 512-byte logical sectors over the device's 4 KiB blocks.
 * Root on it with rd=ufsroot0.
 *
 * All DMA goes through one physically contiguous allocation (descriptors and
 * a 1 MiB bounce buffer); the host is not cache coherent, so caches are
 * cleaned/invalidated around each command.
 *
 * Writes are refused unless boot-arg ufsrw=1 is set. Dali exposes only the
 * first 16 GiB of unixnu_data as the root device; its unknown partition tail
 * is outside the device view. Dedicated Dali logs use only unixnu_logs.
 */
#if defined(ARM64_BOARD_CONFIG_SWEET)
#include <sys/param.h>
#include <sys/systm.h>
#include <sys/buf.h>
#include <sys/conf.h>
#include <sys/disk.h>
#include <sys/errno.h>
#include <sys/proc.h>
#include <sys/uio.h>
#include <miscfs/devfs/devfs.h>
#include <kern/locks.h>
#include <kern/clock.h>
#include <kern/sched_prim.h>
#include <machine/machine_routines.h>
#include <arm/caches_internal.h>
#include <vm/pmap.h>
#include <vm/vm_kern.h>
#include <pexpert/pexpert.h>
#include <pexpert/device_tree.h>
#include <libkern/libkern.h>
#include <libkern/crypto/sha2.h>
#include <machine/atomic.h>
#include "SweetUFSCompletion.h"
#include "SweetUFSPWMPolicy.h"
#include "SweetUFSPWMClock.h"

extern ppnum_t pmap_find_phys(pmap_t pmap, addr64_t va);
/* osfmk/arm/machine_routines.h, not exported to the BSD layer */
extern vm_offset_t ml_io_map(vm_offset_t phys_addr, vm_size_t size);
/* The ramoops range is shared DDR; mem_type=0 maps it write-combined. */
extern vm_offset_t ml_io_map_wcomb(vm_offset_t phys_addr, vm_size_t size);
/* osfmk/arm/cpu_data.h, not exported to the BSD layer */
extern int get_preemption_level(void);
extern kern_return_t kernel_thread_start(thread_continue_t, void *, thread_t *);
extern void thread_deallocate(thread_t);
extern int hz;
static void sweet_log_start(void);
extern void sweet_sample_start(void);
extern void dali_fastboot_return_root_failure(void);
extern void dali_fastboot_return_ufs_probe(void);
static int ufs_dali_hs_platform_validate(void);
static int ufs_pwm_reentry_error(void);
static int ufs_pwm_failure(int error);
extern void sweet_keys_init(void);
extern void sweet_touch_init(void);
extern void sweet_usb_probe(void);

#define REG_CAP         0x00
#define REG_VER         0x08
#define REG_IS          0x20
#define REG_IE          0x24
#define REG_HCS         0x30
#define REG_HCE         0x34
#define REG_UTRLBA      0x50
#define REG_UTRLBAU     0x54
#define REG_UTRLDBR     0x58
#define REG_UTRLCLR     0x5c
#define REG_UTRLRSR     0x60
#define REG_UTMRLBA     0x70
#define REG_UTMRLBAU    0x74
#define REG_UTMRLDBR    0x78
#define REG_UTMRLRSR    0x80
#define REG_UICCMD      0x90
#define REG_UICCMDARG1  0x94
#define REG_UICCMDARG2  0x98
#define REG_UICCMDARG3  0x9c
#define REG_AHIT        0x18
#define REG_MEM_CFG     0x300

#define REG_UIC_ERROR_PHY   0x38
#define REG_UIC_ERROR_DL    0x3c
#define REG_UIC_ERROR_N     0x40
#define REG_UIC_ERROR_T     0x44
#define REG_UIC_ERROR_DME   0x48

#define REG_MTK_IP_VERSION 0x2240
#define REG_MTK_REF_CLOCK  0x144
#define REG_MTK_XO_UFS     0x140
#define REG_MTK_OPTIONS    0x160
#define REG_MTK_OUTSTANDING 0x14c

#define DALI_UFS_HCI_BASE 0x16810000u
#define DALI_UFS_AO_BASE  0x168a0000u
#define DALI_UFS_AO_GATE1  0x004
#define DALI_UFS_AO_GATE0  0x104

#define HCS_DP          (1u << 0)
#define HCS_UTRLRDY     (1u << 1)
#define HCS_UCRDY       (1u << 3)
#define IS_UIC_ERROR    (1u << 2)
#define IS_UCCS         (1u << 10)
#define IS_UFS_ERROR_MASK 0x00070884u     /* MT6991 reference UFSHCI error mask */
#define CAP_64AS        (1u << 24)              /* 64-bit DMA addressing */

#define MT6991_UFS_SIP             0xc2000276u
#define MT6991_UFS_SIP_DEVICE_RST  2u
#define MT6991_UFS_SIP_REFCLK      (1u << 3)
#define MT6991_UFS_SIP_MTCMOS      (1u << 9)
#define MT6991_UFS_LINK_RESET_PHY_ERROR 0x80000010u

/* Values independently pinned by HackIOS' MT6991 UFSHCI inventory. */
#define MT6991_UFS_VERSION 0x00000400u
#define MT6991_UFS_CAP     0x55c7051fu
#define SWEET_LOG_SIZE     (4u << 20)
#define SWEET_LOG_BLOCKS   (1 + SWEET_LOG_SIZE / UFS_BLOCK)
#define UFS_SENSE_MAX      18u

#define UFS_BLOCK       4096u
#define BOUNCE_SIZE     (1u << 20)
/* On the G4 root path only, preserve the older logs and bound new slots. */
#define DALI_LOG_OFFSET_BYTES (16ull << 20)
#define DALI_LOG_SPAN_BLOCKS  ((16ull << 20) / UFS_BLOCK)
#define DALI_LOG_SLOT_COUNT    3u
#define DALI_LOG_SCAN_TIMEOUT_NS (90ull * NSEC_PER_SEC)
#define DALI_ROOT_COMPARE_TIMEOUT_NS (90ull * NSEC_PER_SEC)
/* Independently observed Dali LUN 2 unixnu_logs GPT extent, 4 KiB LBAs. */
#define DALI_LOG_LUN           2u
#define DALI_LOG_FIRST_BLOCK   0x3d92ff8ull
#define DALI_LOG_PART_BLOCKS   8192u
#define DALI_ROOT_MAX_BYTES (16ull * 1024ull * 1024ull * 1024ull)
#define DALI_ROOT_MAX_BLOCKS (DALI_ROOT_MAX_BYTES / UFS_BLOCK)
#define PRDT_CHUNK      (256u << 10)            /* PRDT byte count field: 18 bits */
#define PRDT_MAX        (BOUNCE_SIZE / PRDT_CHUNK)
#define CMD_TIMEOUT_NS  (5ull * NSEC_PER_SEC)
#define CMD_SPIN_US     50u
#define DALI_CMD_SPIN_MAX_US 1000u
#define CMD_SLEEP_NS    (200ull * NSEC_PER_USEC)
#define UFS_QUERY_TIMEOUT_NS (200ull * NSEC_PER_USEC * 1000ull)
#define UFS_DEVICE_INIT_TIMEOUT_NS (1500ull * 1000ull * NSEC_PER_USEC)
#define UFS_DEVICE_INIT_POLL_INTERVAL_US 1000u
#define UFS_DEVICE_INIT_MAX_POLLS 1500u
#define UFS_SSU_TIMEOUT_NS (500ull * NSEC_PER_USEC * 1000ull)
#define UFS_LUN_START_TIMEOUT_NS (500ull * NSEC_PER_USEC * 1000ull)
#define UFS_READY_TUR_TIMEOUT_NS (200ull * NSEC_PER_USEC * 1000ull)
#define UFS_READY_POLL_INTERVAL_US 10000u
#define UFS_READY_MAX_POLLS 20u

struct utrd {
	uint32_t dw0, dw1, dw2, dw3;
	uint32_t ucdba, ucdbau;
	uint32_t resp;          /* [31:16] response UPIU offset, [15:0] length, DWORDs */
	uint32_t prdt;          /* [31:16] PRDT offset (DWORDs), [15:0] entries */
};

struct ucd {
	uint8_t cmd[512];
	uint8_t resp[512];
	struct {
		uint32_t addr_lo, addr_hi, rsvd, dbc;
	} prdt[PRDT_MAX];
};

/* Layout of the contiguous DMA allocation. */
#define DMA_UTRL        0x0000                  /* 1 KiB aligned */
#define DMA_UCD         0x1000                  /* 128 byte aligned */
#define DMA_BOUNCE      0x4000
#define DMA_SIZE        (DMA_BOUNCE + BOUNCE_SIZE)

static struct {
	vm_offset_t regs;
	vm_offset_t ao_regs;
	vm_offset_t dma;                        /* kernel VA of the DMA allocation */
	uint64_t dma_pa;
	uint8_t lun;
	uint8_t tag;
	uint64_t first_block, nblocks;          /* exposed root view, in 4 KiB device blocks */
	/* Initial root GPT metadata, checked only by optional G4 logger validation. */
	uint64_t root_gpt_first, root_gpt_last;
	uint8_t root_gpt_type[16], root_gpt_guid[16], gpt_disk_guid[16];
	uint64_t last_lba, capacity_blocks;
	uint64_t log_first_block, log_nblocks;
	uint64_t log_offset_bytes;
	char log_partition_name[64];
	uint32_t secsize;                       /* logical sector size for the BSD layer */
	uint32_t spin_us;                       /* Dali ADT opt-in, otherwise 50 */
	bool writable;                          /* boot-arg ufsrw=1 */
	bool cache;                             /* boot-arg ufscache=1 */
	bool wait;                              /* boot-arg ufswait=1 */
	bool pwm_two_lanes;                     /* Dali ADT opt-in, evaluated before init */
	bool hsg1_requested, hsg1_active;        /* HS active: retained floor checked at I/O */
	bool hsg4_requested;                   /* Explicit G4x2 probe or root opt-in */
	bool hsg4_root, hsg4_root_verified;      /* Root opt-in and pre-write byte comparison */
	bool speed_probe, speed_probe_done;     /* Read-only, pre-root trial */
	bool speed_extended;                   /* Separately opted-in longer reads */
	bool dali;                              /* MT6991 HCI profile from ADT compatible */
	bool capacity_valid;
	uint8_t last_sense_key, last_sense_asc, last_sense_ascq;
	bool last_sense_valid;
	bool command_poisoned;                  /* Dali timeout: never reuse an owned slot/buffer */
	bool log_partition_configured;
	bool log_partition_ready;
	const char *failure_stage;
	int bmajor, cmajor;
	dev_t bdev;
	bool ready;
} su;

static LCK_GRP_DECLARE(sweet_ufs_grp, "sweet_ufs");
static LCK_MTX_DECLARE(sweet_ufs_lock, &sweet_ufs_grp);

#if DEVELOPMENT
#include "SweetUFSLoadPolicy.h"

static uint32_t sweet_ufs_load_state;
static unsigned int sweet_ufs_load_dumped;
static struct sweet_ufs_load_counters sweet_ufs_load_counts, sweet_ufs_load_copy;

static struct sweet_ufs_load_mirror sweet_ufs_load_published;

/* The existing UFS mutex serializes publishers; all shared mirror fields are SC. */
static void
sweet_ufs_load_publish(unsigned int kind, uint64_t source_end)
{
    uint64_t generation = os_atomic_load(&sweet_ufs_load_published.generation, seq_cst);
    if (generation == UINT64_MAX) return;
    if (generation >= UINT64_MAX - 1 || (generation & 1)) {
        os_atomic_store(&sweet_ufs_load_published.generation, UINT64_MAX, seq_cst);
        return;
    }
    os_atomic_store(&sweet_ufs_load_published.generation, generation + 1, seq_cst);
    const struct sweet_ufs_load_group *g = &sweet_ufs_load_counts.groups[kind];
#define SWEET_UFS_PUBLISH_FIELD(field) \
    os_atomic_store(&sweet_ufs_load_published.groups[kind].field, g->field, seq_cst);
    SWEET_UFS_LOAD_FIELDS(SWEET_UFS_PUBLISH_FIELD)
#undef SWEET_UFS_PUBLISH_FIELD
    os_atomic_store(&sweet_ufs_load_published.overflow, sweet_ufs_load_counts.overflow, seq_cst);
    os_atomic_store(&sweet_ufs_load_published.source_end, source_end, seq_cst);
    os_atomic_store(&sweet_ufs_load_published.generation, generation + 2, seq_cst);
}

/* One bounded read; never acquire the I/O mutex or retry across publication. */
static uint32_t
sweet_ufs_load_read(struct sweet_ufs_load_counters *out, uint64_t *generation,
    uint64_t *source_end)
{
    uint64_t begin = os_atomic_load(&sweet_ufs_load_published.generation, seq_cst);
    *generation = begin;
    if (begin == UINT64_MAX) return SWEET_UFS_VIEW_EXHAUSTED;
    if (begin == 0) return SWEET_UFS_VIEW_UNPUBLISHED;
    if (begin & 1) return SWEET_UFS_VIEW_WRITER;
    for (unsigned int i = 0; i < SWEET_UFS_LOAD_GROUPS; ++i) {
#define SWEET_UFS_READ_FIELD(field) \
        out->groups[i].field = os_atomic_load(&sweet_ufs_load_published.groups[i].field, seq_cst);
        SWEET_UFS_LOAD_FIELDS(SWEET_UFS_READ_FIELD)
#undef SWEET_UFS_READ_FIELD
    }
    out->overflow = (uint32_t)os_atomic_load(&sweet_ufs_load_published.overflow, seq_cst);
    uint64_t stamp = os_atomic_load(&sweet_ufs_load_published.source_end, seq_cst);
    uint64_t end = os_atomic_load(&sweet_ufs_load_published.generation, seq_cst);
    *generation = end;
    if (end == UINT64_MAX) return SWEET_UFS_VIEW_EXHAUSTED;
    if (begin != end) return SWEET_UFS_VIEW_CROSSED;
    *source_end = stamp;
    return SWEET_UFS_VIEW_PUBLISHED;
}

_Static_assert(sizeof(sweet_ufs_load_counts) + sizeof(sweet_ufs_load_copy) +
    sizeof(sweet_ufs_load_published) + 2 * sizeof(uint32_t) +
    4 * sizeof(uint64_t) + SWEET_UFS_LOAD_LINE_BYTES <= 2048,
    "bounded UFS load counters and snapshot");
lck_mtx_t *sweet_ufs_load_mutex(void);
bool sweet_ufs_load_eligible(void);
void sweet_ufs_load_begin(void);
void sweet_ufs_load_close(void);
void sweet_ufs_load_dump(unsigned int);

/* A boot-lifetime known object, never a captured or reverse-converted event. */
lck_mtx_t *
sweet_ufs_load_mutex(void)
{
    return su.dali && su.ready ? &sweet_ufs_lock : NULL;
}

bool
sweet_ufs_load_eligible(void)
{
    return su.dali && su.ready;
}

void
sweet_ufs_load_begin(void)
{
    if (!sweet_ufs_load_eligible() ||
        !os_atomic_cmpxchg(&sweet_ufs_load_state, 0u, 1u, acq_rel)) return;
    printf("SWEET LOADIO CONFIG: v=1 groups=5 samples=40 scope=returned-only pending=unknown snapshot=last-published "
        "bytes=requested clock=abs nested=1 logger_included=1\n");
}

void
sweet_ufs_load_close(void)
{
    (void)os_atomic_cmpxchg(&sweet_ufs_load_state, 1u, 2u, acq_rel);
}

static bool
sweet_ufs_load_open(void)
{
    return os_atomic_load(&sweet_ufs_load_state, acquire) == 1u;
}

void
sweet_ufs_load_dump(unsigned int sample)
{
    uint32_t state = os_atomic_load(&sweet_ufs_load_state, acquire);
    if (state == 0 || sample >= 40 || sample != sweet_ufs_load_dumped) return;
    ++sweet_ufs_load_dumped;
    if (sample == 39) sweet_ufs_load_close();
    uint64_t at = mach_absolute_time(), generation = 0, source_end = 0;
    uint32_t status = sweet_ufs_load_read(&sweet_ufs_load_copy, &generation, &source_end);
    if (status != SWEET_UFS_VIEW_PUBLISHED) {
        printf("SWEET LOADIO STATUS: v=2 s=%u at=0x%llx q=%u gn=0x%llx final=%u pending=unknown\n",
            sample, (unsigned long long)at, status, (unsigned long long)generation, sample == 39);
        return;
    }
    uint64_t end = mach_absolute_time();
    printf("SWEET LOADIO STATUS: v=2 s=%u at=0x%llx z=0x%llx q=0 gn=0x%llx src=0x%llx ov=%u final=%u pending=unknown\n",
        sample, (unsigned long long)at, (unsigned long long)end,
        (unsigned long long)generation, (unsigned long long)source_end,
        sweet_ufs_load_copy.overflow, sample == 39);
    for (unsigned int i = 0; i < SWEET_UFS_LOAD_GROUPS; ++i) {
        char line[SWEET_UFS_LOAD_LINE_BYTES];
        if (sweet_ufs_load_format(line, sizeof(line), sample, i,
            &sweet_ufs_load_copy.groups[i])) printf("%s", line);
    }
}
#endif

static inline uint32_t
ufs_rd(uint32_t off)
{
	return *(volatile uint32_t *)(su.regs + off);
}

static inline uint32_t
ufs_ao_rd(uint32_t off)
{
	return *(volatile uint32_t *)(su.ao_regs + off);
}

static inline void
ufs_wr(uint32_t off, uint32_t v)
{
	*(volatile uint32_t *)(su.regs + off) = v;
	__builtin_arm_dsb(0xf);
}

static inline void
ufs_ao_wr(uint32_t off, uint32_t v)
{
	*(volatile uint32_t *)(su.ao_regs + off) = v;
	__builtin_arm_dsb(0xf);
}

static void
dma_sync(uint32_t off, uint32_t len)
{
	flush_dcache64((addr64_t)(su.dma_pa + off), len, TRUE);
}

static void
dma_clean(uint32_t off, uint32_t len)
{
	clean_dcache64((addr64_t)(su.dma_pa + off), len, TRUE);
}

static uint32_t
ufs_be32(const uint8_t *p)
{
	return ((uint32_t)p[0] << 24) | ((uint32_t)p[1] << 16) |
	    ((uint32_t)p[2] << 8) | (uint32_t)p[3];
}

static int
ufs_dt_string(DTEntry node, const char *prop, char *out, size_t out_size,
    const char *fallback)
{
	const char *value = NULL;
	unsigned int size = 0;
	if (SecureDTGetProperty(node, prop, (void const **)&value, &size) != kSuccess) {
		if (strlcpy(out, fallback, out_size) >= out_size) {
			return EINVAL;
		}
		return 0;
	}
	if (!value || size == 0 || size > out_size || value[size - 1] != '\0') {
		return EINVAL;
	}
	size_t length = 0;
	while (length < size && value[length] != '\0') {
		length++;
	}
	if (length != size - 1) {
		return EINVAL;
	}
	memcpy(out, value, size);
	return 0;
}

/* Only the explicit Dali ADT trial can request a two-lane PWM transition. */
static bool
ufs_dali_two_lane_requested(void)
{
	DTEntry node;
	const void *compat = NULL, *prop = NULL;
	unsigned int compat_size = 0, prop_size = 0;
	uint32_t value = 0;
	if (SecureDTLookupEntry(NULL, "/sweet-ufs", &node) != kSuccess ||
	    SecureDTGetProperty(node, "compatible", &compat, &compat_size) != kSuccess ||
	    compat == NULL || compat_size < sizeof("mediatek,mt6991-ufshc") ||
	    memcmp(compat, "mediatek,mt6991-ufshc", sizeof("mediatek,mt6991-ufshc")) != 0 ||
	    SecureDTGetProperty(node, "dali-pwm-two-lanes", &prop, &prop_size) != kSuccess ||
	    prop == NULL || prop_size != sizeof(value)) {
		return false;
	}
	memcpy(&value, prop, sizeof(value));
	return value == 1;
}

/* Default-off functional speed flags use ADT space, not the full bootarg line. */
static bool
ufs_dali_speed_flag(const char *key)
{
	DTEntry node;
	const void *compat = NULL, *prop = NULL;
	unsigned int size = 0;
	uint32_t value = 0;
	if (SecureDTLookupEntry(NULL, "/sweet-ufs", &node) != kSuccess ||
	    SecureDTGetProperty(node, "compatible", &compat, &size) != kSuccess ||
	    compat == NULL || size < sizeof("mediatek,mt6991-ufshc") ||
	    memcmp(compat, "mediatek,mt6991-ufshc", sizeof("mediatek,mt6991-ufshc")) != 0 ||
	    SecureDTGetProperty(node, key, &prop, &size) != kSuccess ||
	    prop == NULL || size != sizeof(value)) return false;
	memcpy(&value, prop, sizeof(value));
	return value == 1;
}

/* Explicit target; 5 is reserved and rejected until a separately reviewed
 * candidate follows a verified G4x2 trial. Absence never changes the link.
 */
static int
ufs_dali_hs_target(uint32_t *target)
{
	DTEntry node;
	const void *compat = NULL, *prop = NULL;
	unsigned int size = 0;
	*target = 0;
	if (SecureDTLookupEntry(NULL, "/sweet-ufs", &node) != kSuccess ||
	    SecureDTGetProperty(node, "compatible", &compat, &size) != kSuccess ||
	    compat == NULL || size < sizeof("mediatek,mt6991-ufshc") ||
	    memcmp(compat, "mediatek,mt6991-ufshc", sizeof("mediatek,mt6991-ufshc")) != 0)
		return 0;
	if (SecureDTGetProperty(node, "dali-hs-target-gear", &prop, &size) != kSuccess)
		return 0;
	if (prop == NULL || size != sizeof(*target)) return EINVAL;
	memcpy(target, prop, sizeof(*target));
	if (*target == 5) return ENOTSUP;
	if (*target != 4) return EINVAL;
	return 0;
}

/* A root trial requires a second, explicit ADT opt-in. Reject malformed values
 * rather than silently turning a requested trial into another link mode. */
static int
ufs_dali_hsg4_root_requested(bool *requested)
{
	DTEntry node;
	const void *compat = NULL, *prop = NULL;
	unsigned int size = 0;
	uint32_t value = 0;
	*requested = false;
	if (SecureDTLookupEntry(NULL, "/sweet-ufs", &node) != kSuccess ||
	    SecureDTGetProperty(node, "compatible", &compat, &size) != kSuccess ||
	    compat == NULL || size < sizeof("mediatek,mt6991-ufshc") ||
	    memcmp(compat, "mediatek,mt6991-ufshc", sizeof("mediatek,mt6991-ufshc")) != 0)
		return 0;
	if (SecureDTGetProperty(node, "dali-hs-g4-root", &prop, &size) != kSuccess)
		return 0;
	if (prop == NULL || size != sizeof(value)) return EINVAL;
	memcpy(&value, prop, sizeof(value));
	if (value > 1) return EINVAL;
	*requested = value == 1;
	return 0;
}

/* A malformed read-only/probe property must not be mistaken for "off" and
 * accidentally turn a requested probe into a writable root boot. This stricter
 * rule applies only to the new G4 root opt-in, not legacy G1/PWM/probe paths.
 */
static bool
ufs_dali_hsg4_root_flag_off(DTEntry node, const char *name)
{
	const void *prop = NULL;
	unsigned int size = 0;
	uint32_t value = 0;
	if (SecureDTGetProperty(node, name, &prop, &size) != kSuccess) return true;
	if (prop == NULL || size != sizeof(value)) return false;
	memcpy(&value, prop, sizeof(value));
	return value == 0;
}

static bool
ufs_dali_hsg4_root_no_conflicts(void)
{
	DTEntry node;
	if (SecureDTLookupEntry(NULL, "/sweet-ufs", &node) != kSuccess) return false;
	return ufs_dali_hsg4_root_flag_off(node, "dali-hs-g1") &&
	    ufs_dali_hsg4_root_flag_off(node, "dali-speed-probe") &&
	    ufs_dali_hsg4_root_flag_off(node, "dali-speed-extended") &&
	    ufs_dali_hsg4_root_flag_off(node, "dali-pwm-two-lanes");
}

/* Bind observations to 32 lowercase ASCII hex bytes without a terminator. */
static bool
ufs_dali_run_id(char run_id[33])
{
	DTEntry node;
	const void *prop = NULL;
	unsigned int size = 0;
	if (SecureDTLookupEntry(NULL, "/sweet-ufs", &node) != kSuccess ||
	    SecureDTGetProperty(node, "dali-ufs-run-id", &prop, &size) != kSuccess ||
	    prop == NULL || size != 32) return false;
	const uint8_t *bytes = prop;
	for (unsigned int i = 0; i < 32; ++i)
		if (!((bytes[i] >= '0' && bytes[i] <= '9') ||
		    (bytes[i] >= 'a' && bytes[i] <= 'f'))) return false;
	memcpy(run_id, bytes, 32);
	run_id[32] = '\0';
	return true;
}

/* Probe configuration retains the existing explicit read-only contract. */
static bool
ufs_dali_extended_configuration(char run_id[33])
{
	uint32_t auto_return = 1, rw = 1, logger = 1;
	if (!PE_parse_boot_argn("dalifastboot", &auto_return, sizeof(auto_return)) || auto_return ||
	    !PE_parse_boot_argn("ufsrw", &rw, sizeof(rw)) || rw ||
	    !PE_parse_boot_argn("sweetlog", &logger, sizeof(logger)) || logger) return false;
	return ufs_dali_run_id(run_id);
}

/* PE_parse_boot_argn also accepts strings and truncates into the numeric
 * destination. For this new write-capable opt-in require unambiguous 0/1
 * string and numeric interpretations; the existing default path is unchanged.
 */
static bool
ufs_dali_root_boot_bool(const char *key, uint32_t *value)
{
	char text[4] = {0};
	uint32_t number = 2;
	if (!PE_parse_boot_arg_str(key, text, sizeof(text)) ||
	    (strcmp(text, "0") != 0 && strcmp(text, "1") != 0) ||
	    !PE_parse_boot_argn(key, &number, sizeof(number)) ||
	    number != (uint32_t)(text[0] - '0')) return false;
	*value = number;
	return true;
}

static bool
ufs_dali_hsg4_root_configuration(char run_id[33], uint32_t *rw, uint32_t *logger)
{
	uint32_t auto_return = 1, pwm = 0;
	char pwm_text[4] = {0};
	if (PE_parse_boot_arg_str("ufspwm", pwm_text, sizeof(pwm_text)) &&
	    (!ufs_dali_root_boot_bool("ufspwm", &pwm) || pwm != 0)) return false;
	return ufs_dali_hsg4_root_no_conflicts() &&
	    ufs_dali_root_boot_bool("dalifastboot", &auto_return) && auto_return == 0 &&
	    ufs_dali_root_boot_bool("ufsrw", rw) &&
	    ufs_dali_root_boot_bool("sweetlog", logger) && ufs_dali_run_id(run_id);
}

/* Optional Dali-only polling budget; malformed input keeps the old default. */
static uint32_t
ufs_dali_spin_us(DTEntry node, bool dali)
{
	const void *prop = NULL;
	unsigned int size = 0;
	uint32_t value;
	if (!dali || SecureDTGetProperty(node, "dali-poll-spin-us", &prop, &size) != kSuccess ||
	    prop == NULL || size != sizeof(value)) {
		return CMD_SPIN_US;
	}
	memcpy(&value, prop, sizeof(value));
	return value >= CMD_SPIN_US && value <= DALI_CMD_SPIN_MAX_US ? value : CMD_SPIN_US;
}

/*
 * One SCSI command in slot 0, data (if any) to/from the bounce buffer
 * (to the device when write is set). Returns 0 or an errno.
 */
static int
ufs_scsi_on_lun_timeout(uint8_t lun, const uint8_t *cdb, unsigned cdb_len,
	uint32_t len, bool write, uint64_t timeout_ns)
{
	/* Fail closed even for an unexpected caller before a G4 transition has
	 * completed. SYNCHRONIZE CACHE also changes persistence without data-out.
	 */
	if (su.hsg4_requested && !su.hsg1_active) {
		int error = ufs_pwm_reentry_error();
		if (error) return error;
	}
	if ((su.speed_probe || (su.hsg4_requested && !su.hsg1_active) ||
	    (su.hsg4_root && !su.hsg4_root_verified)) &&
	    (write || (cdb_len && cdb[0] == 0x35))) return EROFS;
	/* After a verified HS transition retain the adopted AP vote and clock.
	 * Check only at command boundaries: no new DMA/doorbell on a lost floor.
	 */
	if (su.hsg1_active) {
		int error = ufs_pwm_reentry_error();
		if (error) return error;
		error = ufs_dali_hs_platform_validate();
		if (error) return ufs_pwm_failure(error);
	}
	struct utrd *utrd = (struct utrd *)(su.dma + DMA_UTRL);
	struct ucd *ucd = (struct ucd *)(su.dma + DMA_UCD);
	uint64_t ucd_pa = su.dma_pa + DMA_UCD, bounce_pa = su.dma_pa + DMA_BOUNCE;

	assert(len <= BOUNCE_SIZE);
	if (su.dali && su.command_poisoned) {
		return ETIMEDOUT;
	}
	su.last_sense_key = UINT8_MAX;
	su.last_sense_asc = UINT8_MAX;
	su.last_sense_ascq = UINT8_MAX;
	su.last_sense_valid = false;
	bzero(utrd, sizeof(*utrd));
	bzero(ucd, sizeof(*ucd));

	ucd->cmd[0] = 0x01;                     /* COMMAND UPIU */
	ucd->cmd[1] = len ? (write ? 0x20 : 0x40) : 0x00;      /* W: data out, R: data in */
	ucd->cmd[2] = lun;
	ucd->cmd[3] = ++su.tag;
	ucd->cmd[12] = (uint8_t)(len >> 24);
	ucd->cmd[13] = (uint8_t)(len >> 16);
	ucd->cmd[14] = (uint8_t)(len >> 8);
	ucd->cmd[15] = (uint8_t)len;
	memcpy(&ucd->cmd[16], cdb, cdb_len);

	/* DD: 1 = memory to device, 2 = device to memory. */
	utrd->dw0 = (1u << 28) | ((len ? (write ? 1u : 2u) : 0u) << 25) | (1u << 24);
	utrd->dw2 = 0x0f;                       /* OCS invalid until updated */
	utrd->ucdba = (uint32_t)ucd_pa;
	utrd->ucdbau = (uint32_t)(ucd_pa >> 32);
	utrd->resp = (512u / 4) << 16 | (512u / 4);
	if (len) {
		uint32_t n = 0;
		for (uint32_t done = 0; done < len; done += PRDT_CHUNK, n++) {
			uint32_t chunk = MIN(PRDT_CHUNK, len - done);
			ucd->prdt[n].addr_lo = (uint32_t)(bounce_pa + done);
			ucd->prdt[n].addr_hi = (uint32_t)((bounce_pa + done) >> 32);
			ucd->prdt[n].dbc = chunk - 1;
		}
		utrd->prdt = (1024u / 4) << 16 | n;
	}

	if (su.cache) {
		dma_clean(DMA_UTRL, sizeof(*utrd));
		dma_clean(DMA_UCD, 16 + cdb_len);
		dma_sync(DMA_UCD + offsetof(struct ucd, resp), sizeof(ucd->resp));
		if (len) {
			dma_clean(DMA_UCD + offsetof(struct ucd, prdt),
			    (utrd->prdt & 0xffff) * sizeof(ucd->prdt[0]));
		}
	} else {
		dma_sync(DMA_UTRL, sizeof(*utrd));
		dma_sync(DMA_UCD, sizeof(*ucd));
	}
	if (len) {
		if (su.cache && write) {
			dma_clean(DMA_BOUNCE, len);
		} else {
			dma_sync(DMA_BOUNCE, len);
		}
	}
	__builtin_arm_dsb(0xf);

	ufs_wr(REG_UTRLDBR, 1);
	uint64_t deadline;
	nanoseconds_to_absolutetime(timeout_ns, &deadline);
	deadline += mach_absolute_time();
	bool sleepable = su.wait && su.ready && ml_get_interrupts_enabled() &&
	    !ml_at_interrupt_context() && get_preemption_level() == 0;
	uint64_t spin_until = 0, sleep_interval = 0;
	if (sleepable) {
		nanoseconds_to_absolutetime((uint64_t)su.spin_us * NSEC_PER_USEC, &spin_until);
		spin_until += mach_absolute_time();
		nanoseconds_to_absolutetime(CMD_SLEEP_NS, &sleep_interval);
	}
	while (ufs_rd(REG_UTRLDBR) & 1) {
		uint64_t now = mach_absolute_time();
		if (now > deadline) {
			ufs_wr(REG_UTRLCLR, ~1u);
			if (su.dali) {
				su.command_poisoned = true;
			}
			printf("SWEET UFS: command 0x%02x timed out\n", cdb[0]);
			return ETIMEDOUT;
		}
		if (sleepable && now >= spin_until) {
			uint64_t wake = now + MIN(sleep_interval, deadline - now);
			/* Keep the mutex: slot 0 and the bounce buffer are still owned. */
			if (assert_wait_deadline_with_leeway((event_t)&su.wait, THREAD_UNINT,
			    TIMEOUT_URGENCY_SYS_CRITICAL, wake, 0) == THREAD_WAITING) {
				thread_block(THREAD_CONTINUE_NULL);
			}
		} else {
			delay(1);
		}
	}
	__builtin_arm_dsb(0xf);
	ufs_wr(REG_IS, ufs_rd(REG_IS));         /* acknowledge completion status */

	dma_sync(DMA_UTRL, sizeof(*utrd));
	if (su.cache) {
		dma_sync(DMA_UCD + offsetof(struct ucd, resp), sizeof(ucd->resp));
	} else {
		dma_sync(DMA_UCD, sizeof(*ucd));
	}
	if (len && !(su.cache && write)) {
		dma_sync(DMA_BOUNCE, len);
	}

	uint32_t ocs = utrd->dw2 & 0xff;
	if (su.dali && ocs == 0) {
		enum sweet_ufs_response_result result = sweet_ufs_check_response(
		    ucd->resp, sizeof(ucd->resp), lun, ucd->cmd[3], cdb[0]);
		if (result != SWEET_UFS_RESPONSE_OK) {
			/* A response for another transaction must not be followed by slot reuse. */
			if (sweet_ufs_response_desynchronized(result))
				su.command_poisoned = true;
			printf("SWEET UFS: rejected completion op=0x%02x reason=%u "
			    "lun=%u/%u tag=%u/%u flags=0x%02x residual=%u expected=%u\n",
			    cdb[0], (unsigned int)result, ucd->resp[2], lun,
			    ucd->resp[3], ucd->cmd[3], ucd->resp[1],
			    ufs_be32(ucd->resp + 12), len);
			return EIO;
		}
	}
	if (ocs || ucd->resp[0] != 0x21 || ucd->resp[6] || ucd->resp[7]) {
		printf("SWEET UFS: command 0x%02x failed: ocs 0x%x upiu 0x%x response 0x%x status 0x%x\n",
		    cdb[0], ocs, ucd->resp[0], ucd->resp[6], ucd->resp[7]);
		if (ucd->resp[0] == 0x21) {
			uint32_t segment_len = ((uint32_t)ucd->resp[10] << 8) | ucd->resp[11];
			uint32_t sense_len = ((uint32_t)ucd->resp[32] << 8) | ucd->resp[33];
			uint32_t residual = ufs_be32(ucd->resp + 12);
			uint32_t sense_key = UINT32_MAX, asc = UINT32_MAX, ascq = UINT32_MAX;
			char sense_hex[UFS_SENSE_MAX * 2 + 1];
			const char *raw = "none";
			uint32_t raw_bytes = 0;
			bool sense_valid = sense_len == 0 || (segment_len >= 2 &&
			    sense_len <= segment_len - 2 && sense_len <= sizeof(ucd->resp) - 34);
			if (!sense_valid) {
				raw = "invalid";
			} else if (sense_len != 0) {
				const uint8_t *sense = ucd->resp + 34;
				uint32_t response_code = sense[0] & 0x7f;
				if ((response_code == 0x70 || response_code == 0x71) && sense_len >= 14) {
					sense_key = sense[2] & 0x0f;
					asc = sense[12];
					ascq = sense[13];
				} else if ((response_code == 0x72 || response_code == 0x73) && sense_len >= 4) {
					sense_key = sense[1] & 0x0f;
					asc = sense[2];
					ascq = sense[3];
				}
				if (ocs == 0 && ucd->resp[6] == 0x01 && ucd->resp[7] == 0x02 &&
				    sense_key != UINT32_MAX && asc != UINT32_MAX && ascq != UINT32_MAX) {
					su.last_sense_key = (uint8_t)sense_key;
					su.last_sense_asc = (uint8_t)asc;
					su.last_sense_ascq = (uint8_t)ascq;
					su.last_sense_valid = true;
				}
				raw_bytes = MIN(sense_len, UFS_SENSE_MAX);
				static const char hex[] = "0123456789abcdef";
				for (uint32_t i = 0; i < raw_bytes; i++) {
					sense_hex[i * 2] = hex[sense[i] >> 4];
					sense_hex[i * 2 + 1] = hex[sense[i] & 0x0f];
				}
				sense_hex[raw_bytes * 2] = 0;
				raw = sense_hex;
			}
			printf("SWEET UFS: sense segment=%u length=%u residual=0x%x key=0x%x asc=0x%x ascq=0x%x raw_bytes=%u raw=%s\n",
			    segment_len, sense_len, residual, sense_key, asc, ascq, raw_bytes, raw);
		}
		return EIO;
	}
	return 0;
}

static int
ufs_scsi(const uint8_t *cdb, unsigned cdb_len, uint32_t len, bool write)
{
#if DEVELOPMENT
    unsigned int kind = sweet_ufs_load_kind(cdb[0]);
    bool observe = sweet_ufs_load_open() && kind < SWEET_UFS_LOAD_GROUPS;
    uint64_t begin = observe ? mach_absolute_time() : 0;
#endif
    int result = ufs_scsi_on_lun_timeout(su.lun, cdb, cdb_len, len, write, CMD_TIMEOUT_NS);
    /* G4 root I/O errors are terminal for this boot: do not submit another
     * command to an uncertain link or silently retry a possibly partial write.
     * Existing G1 behaviour and read-only probe completion are unchanged.
     */
    if (result && su.hsg4_requested && !su.speed_probe && su.hsg1_active) {
        if (!ufs_pwm_reentry_error())
            printf("SWEET UFS HSG4 IO: op=0x%02x write=%u error=%d poisoned=%u retry=0\n",
                cdb_len ? (unsigned int)cdb[0] : 0u, write ? 1u : 0u, result,
                su.command_poisoned ? 1u : 0u);
        result = ufs_pwm_failure(result);
    }
#if DEVELOPMENT
    if (observe && sweet_ufs_load_open()) {
        uint64_t end = mach_absolute_time();
        if (sweet_ufs_load_open()) {
            sweet_ufs_load_record(&sweet_ufs_load_counts, kind, len, result, begin, begin, end);
            sweet_ufs_load_publish(kind, end);
        }
    }
#endif
    return result;
}

/* Read bCurrentPowerMode using a Query Request UPIU addressed to query LUN 0. */
static int
ufs_query_current_power_mode(uint32_t *value)
{
	struct utrd *utrd = (struct utrd *)(su.dma + DMA_UTRL);
	struct ucd *ucd = (struct ucd *)(su.dma + DMA_UCD);
	uint64_t ucd_pa = su.dma_pa + DMA_UCD;
	uint8_t tag;
	if (!value) {
		return EINVAL;
	}
	if (su.dali && su.command_poisoned) {
		return ETIMEDOUT;
	}
	bzero(utrd, sizeof(*utrd));
	bzero(ucd, sizeof(*ucd));
	tag = ++su.tag;
	ucd->cmd[0] = 0x16;                     /* QUERY REQUEST */
	ucd->cmd[2] = 0;                        /* Device attributes use query LUN 0. */
	ucd->cmd[3] = tag;
	ucd->cmd[5] = 0x01;                     /* standard read request */
	ucd->cmd[12] = 0x03;                    /* READ ATTRIBUTE */
	ucd->cmd[13] = 0x02;                    /* bCurrentPowerMode */

	utrd->dw0 = (1u << 28) | (1u << 24);     /* UPIU, no data direction */
	utrd->dw2 = 0x0f;                       /* OCS invalid until updated */
	utrd->ucdba = (uint32_t)ucd_pa;
	utrd->ucdbau = (uint32_t)(ucd_pa >> 32);
	utrd->resp = (512u / 4) << 16 | (512u / 4);
	if (su.cache) {
		dma_clean(DMA_UTRL, sizeof(*utrd));
		dma_clean(DMA_UCD, 16);
		dma_sync(DMA_UCD + offsetof(struct ucd, resp), sizeof(ucd->resp));
	} else {
		dma_sync(DMA_UTRL, sizeof(*utrd));
		dma_sync(DMA_UCD, sizeof(*ucd));
	}
	__builtin_arm_dsb(0xf);
	ufs_wr(REG_UTRLDBR, 1);
	uint64_t deadline;
	nanoseconds_to_absolutetime(UFS_QUERY_TIMEOUT_NS, &deadline);
	deadline += mach_absolute_time();
	while (ufs_rd(REG_UTRLDBR) & 1) {
		if (mach_absolute_time() > deadline) {
			ufs_wr(REG_UTRLCLR, ~1u);
			su.command_poisoned = true;
			printf("SWEET UFS: QUERY READ ATTRIBUTE bCurrentPowerMode timed out\n");
			return ETIMEDOUT;
		}
		delay(1);
	}
	__builtin_arm_dsb(0xf);
	ufs_wr(REG_IS, ufs_rd(REG_IS));         /* acknowledge completion status */
	dma_sync(DMA_UTRL, sizeof(*utrd));
	if (su.cache) {
		dma_sync(DMA_UCD + offsetof(struct ucd, resp), sizeof(ucd->resp));
	} else {
		dma_sync(DMA_UCD, sizeof(*ucd));
	}

	const uint8_t *rsp = ucd->resp;
	uint32_t ocs = utrd->dw2 & 0xff;
	if (ocs || rsp[0] != 0x36 || rsp[2] != 0 || rsp[3] != tag || rsp[8] != 0 ||
	    rsp[10] != 0 || rsp[11] != 0 || rsp[12] != 0x03 || rsp[13] != 0x02 ||
	    rsp[14] != 0 || rsp[15] != 0) {
		printf("SWEET UFS: QUERY READ ATTRIBUTE malformed response ocs=0x%x type=0x%x lun=0x%x tag=0x%x response=0x%x opcode=0x%x idn=0x%x length=0x%x\n",
		    ocs, rsp[0], rsp[2], rsp[3], rsp[6], rsp[12], rsp[13],
		    ((uint32_t)rsp[10] << 8) | rsp[11]);
		return EIO;
	}
	if (rsp[6] != 0) {
		printf("SWEET UFS: QUERY READ ATTRIBUTE failed idn=0x02 response=0x%x\n", rsp[6]);
		return EIO;
	}
	*value = ufs_be32(rsp + 20);
	return 0;
}

/* Query SET_FLAG/READ_FLAG for fDeviceInit after LINK_STARTUP, matching Linux and HackIOS. */
static int
ufs_query_fdeviceinit_flag(uint8_t opcode, uint32_t *value)
{
	struct utrd *utrd = (struct utrd *)(su.dma + DMA_UTRL);
	struct ucd *ucd = (struct ucd *)(su.dma + DMA_UCD);
	uint64_t ucd_pa = su.dma_pa + DMA_UCD;
	uint8_t tag;
	if ((opcode != 0x05 && opcode != 0x06) ||
	    (opcode == 0x05 && value == NULL) || (opcode == 0x06 && value != NULL)) {
		return EINVAL;
	}
	if (su.dali && su.command_poisoned) {
		return ETIMEDOUT;
	}
	bzero(utrd, sizeof(*utrd));
	bzero(ucd, sizeof(*ucd));
	tag = ++su.tag;
	ucd->cmd[0] = 0x16;                     /* QUERY REQUEST */
	ucd->cmd[2] = 0;                        /* Device flags use query LUN 0. */
	ucd->cmd[3] = tag;
	ucd->cmd[5] = opcode == 0x06 ? 0x81 : 0x01; /* SET uses standard write; READ uses standard read. */
	ucd->cmd[12] = opcode;                  /* SET FLAG (6) or READ FLAG (5). */
	ucd->cmd[13] = 0x01;                    /* fDeviceInit. */

	utrd->dw0 = (1u << 28) | (1u << 24);     /* UPIU, no data direction */
	utrd->dw2 = 0x0f;                       /* OCS invalid until updated */
	utrd->ucdba = (uint32_t)ucd_pa;
	utrd->ucdbau = (uint32_t)(ucd_pa >> 32);
	utrd->resp = (512u / 4) << 16 | (512u / 4);
	if (su.cache) {
		dma_clean(DMA_UTRL, sizeof(*utrd));
		dma_clean(DMA_UCD, 16);
		dma_sync(DMA_UCD + offsetof(struct ucd, resp), sizeof(ucd->resp));
	} else {
		dma_sync(DMA_UTRL, sizeof(*utrd));
		dma_sync(DMA_UCD, sizeof(*ucd));
	}
	__builtin_arm_dsb(0xf);
	ufs_wr(REG_UTRLDBR, 1);
	uint64_t deadline;
	nanoseconds_to_absolutetime(UFS_QUERY_TIMEOUT_NS, &deadline);
	deadline += mach_absolute_time();
	while (ufs_rd(REG_UTRLDBR) & 1) {
		if (mach_absolute_time() > deadline) {
			ufs_wr(REG_UTRLCLR, ~1u);
			su.command_poisoned = true;
			printf("SWEET UFS: QUERY %s fDeviceInit timed out\n", opcode == 0x06 ? "SET_FLAG" : "READ_FLAG");
			return ETIMEDOUT;
		}
		delay(1);
	}
	__builtin_arm_dsb(0xf);
	ufs_wr(REG_IS, ufs_rd(REG_IS));         /* acknowledge completion status */
	dma_sync(DMA_UTRL, sizeof(*utrd));
	if (su.cache) {
		dma_sync(DMA_UCD + offsetof(struct ucd, resp), sizeof(ucd->resp));
	} else {
		dma_sync(DMA_UCD, sizeof(*ucd));
	}

	const uint8_t *rsp = ucd->resp;
	uint32_t ocs = utrd->dw2 & 0xff;
	if (ocs || rsp[0] != 0x36 || rsp[2] != 0 || rsp[3] != tag ||
	    rsp[5] != ucd->cmd[5] || rsp[8] != 0 || rsp[10] != 0 || rsp[11] != 0 ||
	    rsp[12] != opcode || rsp[13] != 0x01 || rsp[14] != 0 || rsp[15] != 0 ||
	    rsp[18] != 0 || rsp[19] != 0) {
		printf("SWEET UFS: QUERY fDeviceInit malformed response opcode=0x%x ocs=0x%x type=0x%x lun=0x%x tag=0x%x response=0x%x function=0x%x idn=0x%x length=0x%x\n",
		    opcode, ocs, rsp[0], rsp[2], rsp[3], rsp[6], rsp[5], rsp[13],
		    (unsigned)(((uint32_t)rsp[18] << 8) | rsp[19]));
		return EIO;
	}
	if (rsp[6] != 0) {
		printf("SWEET UFS: QUERY fDeviceInit opcode=0x%x failed response=0x%x\n", opcode, rsp[6]);
		return EIO;
	}
	if (opcode == 0x05) {
		uint32_t flag = ufs_be32(rsp + 20);
		if (flag > 1) {
			printf("SWEET UFS: QUERY READ_FLAG fDeviceInit returned invalid value=0x%x\n", flag);
			return EIO;
		}
		*value = flag;
	}
	return 0;
}

/* Linux ufshcd_complete_dev_init: one fDeviceInit SET, then bounded READ_FLAG polling. */
static int
ufs_dali_initialize_device(void)
{
	uint32_t flag = 0;
	unsigned int polls = 0;
	int error;

	su.failure_stage = "fdeviceinit-set";
	error = ufs_query_fdeviceinit_flag(0x06, NULL);
	if (error) {
		printf("SWEET UFS: fDeviceInit SET_FLAG failed error=%d\n", error);
		return error;
	}
	printf("SWEET UFS: fDeviceInit SET_FLAG issued after LINK_STARTUP\n");

	uint64_t deadline;
	nanoseconds_to_absolutetime(UFS_DEVICE_INIT_TIMEOUT_NS, &deadline);
	deadline += mach_absolute_time();
	for (unsigned attempt = 0; attempt < UFS_DEVICE_INIT_MAX_POLLS; attempt++) {
		su.failure_stage = "fdeviceinit-read";
		error = ufs_query_fdeviceinit_flag(0x05, &flag);
		polls = attempt + 1;
		if (error) {
			printf("SWEET UFS: fDeviceInit READ_FLAG failed error=%d after %u polls\n", error, polls);
			return error;
		}
		if (flag == 0) {
			printf("SWEET UFS: fDeviceInit cleared after %u READ_FLAG polls\n", polls);
			return 0;
		}
		if (mach_absolute_time() >= deadline) {
			break;
		}
		delay(UFS_DEVICE_INIT_POLL_INTERVAL_US);
	}
	printf("SWEET UFS: fDeviceInit did not clear before timeout after %u READ_FLAG polls\n", polls);
	return ETIMEDOUT;
}

static bool
ufs_sense_is_lun_ready_transition(void)
{
	return su.last_sense_valid &&
	    ((su.last_sense_key == 0x02 && su.last_sense_asc == 0x04 &&
	    (su.last_sense_ascq == 0x01 || su.last_sense_ascq == 0x02)) ||
	    (su.last_sense_key == 0x06 && su.last_sense_asc == 0x29 &&
	    (su.last_sense_ascq == 0x00 || su.last_sense_ascq == 0x02)));
}

static bool
ufs_sense_requires_lun_start(void)
{
	return su.last_sense_valid && su.last_sense_key == 0x02 &&
	    su.last_sense_asc == 0x04 && su.last_sense_ascq == 0x02;
}

/* Linux-style SCSI EH: START UNIT on this LU, then wait for TUR readiness. */
static int
ufs_dali_start_lun_and_wait(void)
{
	static const uint8_t start_unit[6] = { 0x1b, 0, 0, 0, 0x01, 0 };
	static const uint8_t tur[6] = { 0x00 };
	int error;

	su.failure_stage = "lun-start-unit";
	printf("SWEET UFS: sending START UNIT to root LUN %u after NOT READY 04/02\n", su.lun);
	error = ufs_scsi_on_lun_timeout(su.lun, start_unit, sizeof(start_unit), 0, false,
	    UFS_LUN_START_TIMEOUT_NS);
	if (error) {
		printf("SWEET UFS: START UNIT failed for root LUN %u error=%d sense=%u/%u/%u\n",
		    su.lun, error, (unsigned)su.last_sense_key,
		    (unsigned)su.last_sense_asc, (unsigned)su.last_sense_ascq);
		return error;
	}
	for (unsigned attempt = 0; attempt < UFS_READY_MAX_POLLS; attempt++) {
		error = ufs_scsi_on_lun_timeout(su.lun, tur, sizeof(tur), 0, false,
		    UFS_READY_TUR_TIMEOUT_NS);
		if (!error) {
			printf("SWEET UFS: root LUN %u ready after START UNIT and %u TUR polls\n",
			    su.lun, attempt + 1);
			return 0;
		}
		if (error == ETIMEDOUT || !ufs_sense_is_lun_ready_transition()) {
			printf("SWEET UFS: root LUN %u did not become ready after START UNIT error=%d sense=%u/%u/%u\n",
			    su.lun, error, (unsigned)su.last_sense_key,
			    (unsigned)su.last_sense_asc, (unsigned)su.last_sense_ascq);
			return error;
		}
		if (attempt + 1 < UFS_READY_MAX_POLLS) {
			delay(UFS_READY_POLL_INTERVAL_US);
		}
	}
	printf("SWEET UFS: root LUN %u remained not ready after START UNIT and %u TUR polls\n",
	    su.lun, UFS_READY_MAX_POLLS);
	return ETIMEDOUT;
}

/* Read the power state, wake the UFS device WLUN if needed, then probe root LUN. */
static int
ufs_dali_prepare_lun(void)
{
	static const uint8_t ssu[6] = { 0x1b, 0, 0, 0, 0x10, 0 };
	static const uint8_t tur[6] = { 0x00 };
	uint32_t power_mode = 0;
	su.failure_stage = "fdeviceinit";
	int error = ufs_dali_initialize_device();
	if (error) {
		return error;
	}
	su.failure_stage = "device-power-query";
	error = ufs_query_current_power_mode(&power_mode);
	if (error) {
		return error;
	}
	printf("SWEET UFS: bCurrentPowerMode=0x%x\n", power_mode);
	if ((power_mode & 0xff) != 0x11) {
		su.failure_stage = "device-wake";
		printf("SWEET UFS: sending START STOP UNIT ACTIVE to device WLUN 0xd0\n");
		error = ufs_scsi_on_lun_timeout(0xd0, ssu, sizeof(ssu), 0, false,
		    UFS_SSU_TIMEOUT_NS);
		if (error) {
			return error;
		}
		delay(50000);                       /* Match HackIOS' 50 ms post-wake settle. */
	}

	su.failure_stage = "root-lun-ready";
	for (unsigned attempt = 0; attempt < UFS_READY_MAX_POLLS; attempt++) {
		error = ufs_scsi_on_lun_timeout(su.lun, tur, sizeof(tur), 0, false,
		    UFS_READY_TUR_TIMEOUT_NS);
		if (!error) {
			printf("SWEET UFS: root LUN %u ready after %u TEST UNIT READY polls\n",
			    su.lun, attempt + 1);
			return 0;
		}
		if (error == ETIMEDOUT) {
			printf("SWEET UFS: root LUN %u TEST UNIT READY timed out on poll %u\n",
			    su.lun, attempt + 1);
			return error;
		}
		if (!ufs_sense_is_lun_ready_transition()) {
			printf("SWEET UFS: root LUN %u TUR failed outside readiness transition error=%d sense=%u/%u/%u\n",
			    su.lun, error, (unsigned)su.last_sense_key,
			    (unsigned)su.last_sense_asc, (unsigned)su.last_sense_ascq);
			return error;
		}
		if (attempt + 1 < UFS_READY_MAX_POLLS) {
			delay(UFS_READY_POLL_INTERVAL_US);
		}
	}
	printf("SWEET UFS: root LUN %u remained not ready after %u polls\n",
	    su.lun, UFS_READY_MAX_POLLS);
	return ETIMEDOUT;
}

/* READ(10) of count device blocks at lba into the bounce buffer. */
static int
ufs_read_blocks(uint64_t lba, uint32_t count)
{
	if (lba > UINT32_MAX || count == 0 || count > BOUNCE_SIZE / UFS_BLOCK ||
	    (su.capacity_valid && (lba >= su.capacity_blocks ||
	    count > su.capacity_blocks - lba))) {
		return EINVAL;
	}
	uint8_t cdb[10] = {
		0x28, 0,
		(uint8_t)(lba >> 24), (uint8_t)(lba >> 16), (uint8_t)(lba >> 8), (uint8_t)lba,
		0, (uint8_t)(count >> 8), (uint8_t)count, 0
	};
	int error = ufs_scsi(cdb, sizeof(cdb), count * UFS_BLOCK, false);
	if (error) {
		printf("SWEET UFS: READ(10) failed lba=0x%llx count=%u bytes=%u error=%d\n",
		    (unsigned long long)lba, count, count * UFS_BLOCK, error);
	}
	return error;
}

/* READ CAPACITY is a read-only check of the device's actual block geometry. */
static int
ufs_read_capacity(void)
{
	static const uint8_t cdb[10] = { 0x25 };       /* READ CAPACITY(10) */
	int error = ufs_scsi(cdb, sizeof(cdb), 8, false);
	if (error) {
		printf("SWEET UFS: READ CAPACITY(10) failed (%d)\n", error);
		return error;
	}
	const uint8_t *b = (const uint8_t *)(su.dma + DMA_BOUNCE);
	uint32_t last = ufs_be32(b);
	uint32_t block_size = ufs_be32(b + 4);
	if (last == UINT32_MAX || block_size != UFS_BLOCK) {
		printf("SWEET UFS: unsupported geometry last_lba=0x%x block_size=%u expected=%u\n",
		    last, block_size, UFS_BLOCK);
		return ENXIO;
	}
	su.last_lba = last;
	su.capacity_blocks = (uint64_t)last + 1;
	su.capacity_valid = true;
	printf("SWEET UFS: capacity lun=%u last_lba=0x%llx blocks=%llu block_size=%u\n",
	    su.lun, su.last_lba, su.capacity_blocks, block_size);
	return 0;
}

/* Never overwrite an unrecognized pre-existing filesystem/data area. */
static bool
ufs_log_region_available(uint64_t first)
{
	const uint8_t *b = (const uint8_t *)(su.dma + DMA_BOUNCE);
	for (uint32_t done = 0; done < SWEET_LOG_BLOCKS;) {
		uint32_t n = MIN(SWEET_LOG_BLOCKS - done, BOUNCE_SIZE / UFS_BLOCK);
		int error = ufs_read_blocks(first + done, n);
		if (error) {
			printf("SWEET UFS: cannot read log area before write (%d)\n", error);
			return false;
		}
		if (done == 0 && memcmp(b, "SWEETLOG", 8) == 0) {
			uint32_t version, size;
			memcpy(&version, b + 8, sizeof(version));
			memcpy(&size, b + 12, sizeof(size));
			if (version == 1 && size == SWEET_LOG_SIZE) {
				return true;
			}
		}
		for (uint32_t i = 0; i < n * UFS_BLOCK; i++) {
			if (b[i] != 0) {
				printf("SWEET UFS: log area contains unrecognized data; refusing overwrite\n");
				return false;
			}
		}
		done += n;
	}
	return true;
}

/* Exact ASCII partition name encoded as zero-padded GPT UTF-16LE. */
static bool
ufs_gpt_name_exact(const uint8_t *entry, const char *name)
{
	bool ended = false;
	for (unsigned int i = 0; i < 36; i++) {
		uint16_t ch = (uint16_t)(entry[56 + 2 * i] | entry[57 + 2 * i] << 8);
		if (!ended && *name == '\0') ended = true;
		if (ended) {
			if (ch != 0) return false;
		} else if (ch != (uint8_t)*name++) return false;
	}
	return ended || *name == '\0';
}

/* Validate optional logging metadata independently of the legacy root parser.
 * A CRC is corruption detection, not authentication or device identity. This is
 * called with the UFS mutex held, only after the G4 comparison has succeeded.
 */
static int
ufs_dali_validate_log_gpt(void)
{
	uint8_t *b = (uint8_t *)(su.dma + DMA_BOUNCE);
	uint64_t expected_first = su.log_first_block;
	uint64_t entries_lba, first_usable, last_usable, current, backup;
	uint32_t revision, reserved, header_size, header_crc, nent, esz, entries_crc;
	int error = ufs_read_blocks(1, 1);
	if (error) return error;
	if (memcmp(b, "EFI PART", 8) != 0) goto invalid;
	memcpy(&revision, b + 8, 4);
	memcpy(&header_size, b + 12, 4);
	memcpy(&header_crc, b + 16, 4);
	memcpy(&reserved, b + 20, 4);
	memcpy(&current, b + 24, 8);
	memcpy(&backup, b + 32, 8);
	memcpy(&first_usable, b + 40, 8);
	memcpy(&last_usable, b + 48, 8);
	memcpy(&entries_lba, b + 72, 8);
	memcpy(&nent, b + 80, 4);
	memcpy(&esz, b + 84, 4);
	memcpy(&entries_crc, b + 88, 4);
	if (revision != 0x00010000 || reserved != 0 || header_size < 92 ||
	    header_size > UFS_BLOCK || current != 1 || !su.capacity_valid ||
	    backup != su.capacity_blocks - 1 || backup <= last_usable ||
	    first_usable > last_usable || last_usable >= su.capacity_blocks ||
	    esz != 128 || nent == 0 || nent > 256 || (uint64_t)nent * esz > BOUNCE_SIZE) goto invalid;
	bzero(b + 16, 4);
	uint32_t computed = crc32(0, b, header_size);
	if (computed != header_crc) goto invalid;
	if (memcmp(b + 56, su.gpt_disk_guid, sizeof(su.gpt_disk_guid)) != 0) goto root_changed;
	uint64_t entry_bytes = (uint64_t)nent * esz;
	uint32_t entry_blocks = (uint32_t)((entry_bytes + UFS_BLOCK - 1) / UFS_BLOCK);
	if (entries_lba < 2 || entries_lba >= first_usable ||
	    entry_blocks > first_usable - entries_lba) goto invalid;
	error = ufs_read_blocks(entries_lba, entry_blocks);
	if (error) return error;
	if (crc32(0, b, (size_t)entry_bytes) != entries_crc) goto invalid;
	uint64_t log_first = 0, log_last = 0;
	unsigned int roots = 0, logs = 0;
	for (uint32_t i = 0; i < nent; i++) {
		const uint8_t *e = b + (uint64_t)i * esz;
		bool used = false;
		for (unsigned int j = 0; j < 16; j++) used |= e[j] != 0;
		if (!used) continue;
		uint64_t first, last;
		memcpy(&first, e + 32, 8);
		memcpy(&last, e + 40, 8);
		if (last < first || first < first_usable || last > last_usable) goto invalid;
		if (ufs_gpt_name_exact(e, "unixnu_data")) {
			roots++;
			if (first != su.first_block || first != su.root_gpt_first ||
			    last != su.root_gpt_last ||
			    su.nblocks != MIN(last - first + 1, DALI_ROOT_MAX_BLOCKS) ||
			    memcmp(e, su.root_gpt_type, sizeof(su.root_gpt_type)) != 0 ||
			    memcmp(e + 16, su.root_gpt_guid, sizeof(su.root_gpt_guid)) != 0)
				goto root_changed;
		}
		if (ufs_gpt_name_exact(e, "unixnu_logs")) {
			logs++;
			log_first = first;
			log_last = last;
		}
	}
	if (roots != 1) goto root_changed;
	if (logs != 1 || su.lun != DALI_LOG_LUN ||
	    log_first != DALI_LOG_FIRST_BLOCK || log_last - log_first + 1 != DALI_LOG_PART_BLOCKS ||
	    expected_first != log_first + DALI_LOG_OFFSET_BYTES / UFS_BLOCK) goto invalid;
	/* Reject any used-entry overlap, not only overlap with the log. */
	for (uint32_t i = 0; i < nent; i++) {
		const uint8_t *e = b + (uint64_t)i * esz;
		bool used = false;
		for (unsigned int k = 0; k < 16; k++) used |= e[k] != 0;
		if (!used) continue;
		uint64_t first, last;
		memcpy(&first, e + 32, 8);
		memcpy(&last, e + 40, 8);
		for (uint32_t j = i + 1; j < nent; j++) {
			const uint8_t *other = b + (uint64_t)j * esz;
			bool other_used = false;
			for (unsigned int k = 0; k < 16; k++) other_used |= other[k] != 0;
			if (!other_used) continue;
			uint64_t other_first, other_last;
			memcpy(&other_first, other + 32, 8);
			memcpy(&other_last, other + 40, 8);
			if (first <= other_last && other_first <= last) goto invalid;
		}
	}
	return 0;
root_changed:
	su.log_first_block = su.log_nblocks = 0;
	su.log_partition_ready = false;
	printf("SWEET UFS: CRC-valid GPT contradicts selected root identity or extent\n");
	return EPROTO;
invalid:
	su.log_first_block = su.log_nblocks = 0;
	su.log_partition_ready = false;
	printf("SWEET LOG: G4 GPT validation failed; persistent logging disabled\n");
	return 0;
}

/* Recognize an existing slot, but never reuse it or certify its payload. */
static bool
ufs_dali_known_log_header(const uint8_t *b)
{
	uint32_t version, size, check;
	uint64_t total, seq;
	if (memcmp(b, "SWEETLOG", 8) != 0) return false;
	memcpy(&version, b + 8, 4); memcpy(&size, b + 12, 4);
	memcpy(&total, b + 16, 8); memcpy(&seq, b + 24, 8);
	if (version != 1 || size != SWEET_LOG_SIZE || total == 0 || seq == 0) return false;
	bool v1 = memcmp(b + 32, "SWIDv001", 8) == 0;
	bool v2 = memcmp(b + 32, "SWIDv002", 8) == 0;
	if (v1 || v2) {
		for (unsigned int i = 40; i < 72; i++)
			if (!((b[i] >= '0' && b[i] <= '9') || (b[i] >= 'a' && b[i] <= 'f'))) return false;
		uint32_t checksum_offset = v2 ? 88 : 72;
		memcpy(&check, b + checksum_offset, 4);
		return crc32(0, b, checksum_offset) == check;
	}
	for (unsigned int i = 32; i < 92; i++) if (b[i] != 0) return false;
	return true;
}

/* All deadlines are absolute; false rejects an unusable or overflowing clock. */
static bool
ufs_dali_deadline_after(uint64_t timeout_ns, uint64_t *deadline)
{
	uint64_t span = 0;
	nanoseconds_to_absolutetime(timeout_ns, &span);
	uint64_t now = mach_absolute_time();
	if (span == 0 || now > UINT64_MAX - span) return false;
	*deadline = now + span;
	return true;
}

/* A full or visibly unknown region can be disabled after only three header
 * reads; a possible free slot still requires the complete PWM/G4 digest.
 */
static int
ufs_dali_log_candidate_present(bool *maybe, uint64_t deadline)
{
	const uint8_t *b = (const uint8_t *)(su.dma + DMA_BOUNCE);
	*maybe = false;
	if (su.log_nblocks < (uint64_t)DALI_LOG_SLOT_COUNT * SWEET_LOG_BLOCKS) return 0;
	for (uint32_t slot = 0; slot < DALI_LOG_SLOT_COUNT; slot++) {
		if (mach_absolute_time() >= deadline) return ETIMEDOUT;
		int error = ufs_read_blocks(su.log_first_block + (uint64_t)slot * SWEET_LOG_BLOCKS, 1);
		if (error) return error;
		if (mach_absolute_time() >= deadline) return ETIMEDOUT;
		if (ufs_dali_known_log_header(b)) continue;
		if (slot == 0) { *maybe = false; return 0; }
		for (uint32_t i = 0; i < UFS_BLOCK; i++) {
			if (b[i] != 0) { *maybe = false; return 0; }
		}
		*maybe = true;
	}
	return mach_absolute_time() >= deadline ? ETIMEDOUT : 0;
}

/* Full PWM reference over all candidate bytes, with the UFS mutex held. */
static int
ufs_dali_log_pwm_digest(uint8_t digest[SHA256_DIGEST_LENGTH], uint64_t deadline)
{
	const uint8_t *b = (const uint8_t *)(su.dma + DMA_BOUNCE);
	SHA256_CTX context;
	SHA256_Init(&context);
	for (uint32_t done = 0; done < DALI_LOG_SLOT_COUNT * SWEET_LOG_BLOCKS;) {
		if (mach_absolute_time() >= deadline) return ETIMEDOUT;
		uint32_t n = MIN(DALI_LOG_SLOT_COUNT * SWEET_LOG_BLOCKS - done, BOUNCE_SIZE / UFS_BLOCK);
		int error = ufs_read_blocks(su.log_first_block + done, n);
		if (error) return error;
		SHA256_Update(&context, b, n * UFS_BLOCK);
		if (mach_absolute_time() >= deadline) return ETIMEDOUT;
		done += n;
	}
	SHA256_Final(digest, &context);
	return mach_absolute_time() >= deadline ? ETIMEDOUT : 0;
}

/* Only explicit sweetlogslots=1 may claim an unused slot. A recognized slot 0
 * anchors the format; unknown/partial data anywhere in the three-slot extent
 * disables logging. Zero bytes alone do not establish ownership. The G4 digest
 * covers the same bytes used for classification and must match PWM.
 */
static int
ufs_dali_select_log_slot(const uint8_t reference[SHA256_DIGEST_LENGTH], uint64_t deadline)
{
	uint64_t first = su.log_first_block, blocks = su.log_nblocks;
	const uint8_t *b = (const uint8_t *)(su.dma + DMA_BOUNCE);
	uint32_t chosen = DALI_LOG_SLOT_COUNT;
	bool usable = true;
	SHA256_CTX context;
	uint8_t digest[SHA256_DIGEST_LENGTH];
	SHA256_Init(&context);
	su.log_first_block = 0;
	su.log_nblocks = 0;
	su.log_partition_ready = false;
	if (blocks < (uint64_t)DALI_LOG_SLOT_COUNT * SWEET_LOG_BLOCKS) goto unavailable;
	for (uint32_t slot = 0; slot < DALI_LOG_SLOT_COUNT; slot++) {
		uint64_t offset = (uint64_t)slot * SWEET_LOG_BLOCKS;
		bool known = false, empty = true;
		for (uint32_t done = 0; done < SWEET_LOG_BLOCKS;) {
			if (mach_absolute_time() >= deadline) return ETIMEDOUT;
			uint32_t n = MIN(SWEET_LOG_BLOCKS - done, BOUNCE_SIZE / UFS_BLOCK);
			int error = ufs_read_blocks(first + offset + done, n);
			if (error) return error;
			SHA256_Update(&context, b, n * UFS_BLOCK);
			if (mach_absolute_time() >= deadline) return ETIMEDOUT;
			if (done == 0 && ufs_dali_known_log_header(b)) known = true;
			if (!known && empty) {
				for (uint32_t i = 0; i < n * UFS_BLOCK; i++) {
					if (b[i] != 0) { empty = false; break; }
				}
			}
			done += n;
		}
		if ((slot == 0 && !known) || (!known && !empty)) usable = false;
		if (!known && empty && chosen == DALI_LOG_SLOT_COUNT) chosen = slot;
	}
	SHA256_Final(digest, &context);
	if (mach_absolute_time() >= deadline) return ETIMEDOUT;
	if (memcmp(digest, reference, sizeof(digest)) != 0) {
		printf("SWEET LOG: PWM/G4 slot digest mismatch; no log writes\n");
		return EIO;
	}
	if (!usable || chosen == DALI_LOG_SLOT_COUNT) goto unavailable;
	su.log_first_block = first + (uint64_t)chosen * SWEET_LOG_BLOCKS;
	su.log_nblocks = SWEET_LOG_BLOCKS;
	su.log_partition_ready = true;
	printf("SWEET LOG: G4 slot=%u initially empty; old slots retained; ring wraps at 4 MiB\n", chosen);
	return 0;

unavailable:
	printf("SWEET LOG: missing ownership, unknown data, or no empty slot; logging disabled\n");
	return 0;
}

/*
 * WRITE(10) of count device blocks from the bounce buffer to lba. The range
 * must lie inside the exposed root view, which caps Dali unixnu_data at 16 GiB.
 */
static int
ufs_write_blocks(uint64_t lba, uint32_t count)
{
	if (!su.writable || !su.nblocks || count == 0 || count > BOUNCE_SIZE / UFS_BLOCK ||
	    lba < su.first_block || lba - su.first_block >= su.nblocks ||
	    count > su.nblocks - (lba - su.first_block) || lba + count - 1 > UINT32_MAX) {
		printf("SWEET UFS: refusing write of %u blocks at 0x%llx\n", count, lba);
		return EPERM;
	}
	uint8_t cdb[10] = {
		0x2a, 0,
		(uint8_t)(lba >> 24), (uint8_t)(lba >> 16), (uint8_t)(lba >> 8), (uint8_t)lba,
		0, (uint8_t)(count >> 8), (uint8_t)count, 0
	};
	return ufs_scsi(cdb, sizeof(cdb), count * UFS_BLOCK, true);
}

/* Console writes have a separate GPT range and never inherit ufsrw permission. */
static int
ufs_write_log_blocks(uint64_t lba, uint32_t count)
{
	if (!su.log_partition_ready || count == 0 || count > BOUNCE_SIZE / UFS_BLOCK ||
	    lba < su.log_first_block || lba - su.log_first_block >= su.log_nblocks ||
	    count > su.log_nblocks - (lba - su.log_first_block) || lba > UINT32_MAX ||
	    count - 1 > UINT32_MAX - lba) {
		printf("SWEET UFS: refusing log write of %u blocks at 0x%llx\n", count, lba);
		return EPERM;
	}
	uint8_t cdb[10] = {
		0x2a, 0,
		(uint8_t)(lba >> 24), (uint8_t)(lba >> 16), (uint8_t)(lba >> 8), (uint8_t)lba,
		0, (uint8_t)(count >> 8), (uint8_t)count, 0
	};
	return ufs_scsi(cdb, sizeof(cdb), count * UFS_BLOCK, true);
}

static int
ufs_sync_cache(void)
{
	static const uint8_t cdb[10] = { 0x35 };       /* SYNCHRONIZE CACHE(10), whole LUN */
	return ufs_scsi(cdb, sizeof(cdb), 0, false);
}

static int
ufs_find_partitions(const char *root_want, const char *log_want)
{
	const uint8_t *b = (const uint8_t *)(su.dma + DMA_BOUNCE);
	if (!su.capacity_valid || (log_want[0] && strcmp(root_want, log_want) == 0)) {
		return EINVAL;
	}
	int error = ufs_read_blocks(1, 1);
	if (error) {
		if (!su.dali || !ufs_sense_requires_lun_start()) {
			return error;
		}
		printf("SWEET UFS: GPT header read needs LUN start; retrying once after SCSI START UNIT\n");
		int start_error = ufs_dali_start_lun_and_wait();
		if (start_error) {
			return start_error;
		}
		su.failure_stage = "gpt-discovery";
		error = ufs_read_blocks(1, 1);
		if (error) {
			printf("SWEET UFS: GPT header READ(10) retry after START UNIT failed (%d)\n", error);
			return error;
		}
		printf("SWEET UFS: GPT header READ(10) succeeded after START UNIT\n");
	}
	if (memcmp(b, "EFI PART", 8) != 0) {
		printf("SWEET UFS: LUN %u has no GPT\n", su.lun);
		return ENXIO;
	}
	uint64_t entry_lba, first_usable, last_usable;
	uint32_t header_size, nent, esz;
	memcpy(&header_size, b + 12, sizeof(header_size));
	memcpy(&first_usable, b + 40, sizeof(first_usable));
	memcpy(&last_usable, b + 48, sizeof(last_usable));
	memcpy(&entry_lba, b + 72, 8);
	memcpy(&nent, b + 80, 4);
	memcpy(&esz, b + 84, 4);
	if (su.dali && su.hsg4_root) memcpy(su.gpt_disk_guid, b + 56, sizeof(su.gpt_disk_guid));
	uint64_t entry_bytes = (uint64_t)nent * esz;
	if (header_size < 92 || header_size > UFS_BLOCK || first_usable > last_usable ||
	    last_usable >= su.capacity_blocks || esz != 128 || nent == 0 ||
	    entry_bytes == 0 || entry_bytes > BOUNCE_SIZE || entry_lba >= su.capacity_blocks ||
	    (entry_bytes + UFS_BLOCK - 1) / UFS_BLOCK > su.capacity_blocks - entry_lba) {
		printf("SWEET UFS: unsupported GPT entries %u x %u\n", nent, esz);
		return ENXIO;
	}
	uint32_t entry_blocks = (uint32_t)((entry_bytes + UFS_BLOCK - 1) / UFS_BLOCK);
	error = ufs_read_blocks(entry_lba, entry_blocks);
	if (error) {
		return error;
	}
	bool root_found = false, log_found = false, log_duplicate = false;
	uint64_t root_first = 0, root_last = 0, log_first = 0, log_last = 0;
	for (uint32_t i = 0; i < nent; i++) {
		const uint8_t *e = b + i * esz;
		bool used = false;
		for (unsigned j = 0; j < 16; j++) {
			used |= e[j] != 0;
		}
		if (!used) {
			continue;
		}
		char name[37];
		unsigned c;
		for (c = 0; c < 36; c++) {
			uint16_t ch = (uint16_t)(e[56 + 2 * c] | e[57 + 2 * c] << 8);
			if (ch == 0 || ch > 0x7f) {
				break;
			}
			name[c] = (char)ch;
		}
		name[c] = 0;
		bool is_root = strcmp(name, root_want) == 0;
		bool is_log = log_want[0] && strcmp(name, log_want) == 0;
		if (!is_root && !is_log) {
			continue;
		}
		uint64_t first, last;
		memcpy(&first, e + 32, 8);
		memcpy(&last, e + 40, 8);
		if (last < first || first < first_usable || last > last_usable) {
			printf("SWEET UFS: invalid GPT range for partition \"%s\"\n", name);
			return ENXIO;
		}
		if (is_root) {
			if (root_found) {
				printf("SWEET UFS: duplicate root partition name \"%s\"\n", root_want);
				return EEXIST;
			}
			root_found = true;
			root_first = first;
			root_last = last;
			if (su.dali && su.hsg4_root) {
				su.root_gpt_first = first;
				su.root_gpt_last = last;
				memcpy(su.root_gpt_type, e, sizeof(su.root_gpt_type));
				memcpy(su.root_gpt_guid, e + 16, sizeof(su.root_gpt_guid));
			}
		}
		if (is_log) {
			if (log_found) {
				log_duplicate = true;
			} else {
				log_found = true;
				log_first = first;
				log_last = last;
			}
		}
	}
	if (!root_found) {
		printf("SWEET UFS: no GPT root partition named \"%s\"\n", root_want);
		return ENOENT;
	}
	su.first_block = root_first;
	su.nblocks = root_last - root_first + 1;
	if (su.dali && strcmp(root_want, "unixnu_data") == 0) {
		uint64_t gpt_nblocks = su.nblocks;
		su.nblocks = MIN(su.nblocks, DALI_ROOT_MAX_BLOCKS);
		printf("SWEET UFS: Dali unixnu_data view blocks=%llu GPT blocks=%llu hidden tail=%llu\n",
		    su.nblocks, gpt_nblocks, gpt_nblocks - su.nblocks);
	}
	if (log_want[0]) {
		if (log_duplicate) {
			printf("SWEET UFS: duplicate log partition name \"%s\"; logging disabled\n", log_want);
		} else if (!log_found) {
			printf("SWEET UFS: no GPT log partition named \"%s\"; logging disabled\n", log_want);
		} else if ((root_first <= log_last && log_first <= root_last) ||
		    su.log_offset_bytes / UFS_BLOCK >= log_last - log_first + 1 ||
		    log_last - log_first + 1 - su.log_offset_bytes / UFS_BLOCK < SWEET_LOG_BLOCKS) {
			printf("SWEET UFS: unsafe/undersized log partition \"%s\"; logging disabled\n", log_want);
		} else if (su.dali && su.hsg4_root) {
			/* Defer empty-slot reads until the HS compare has succeeded. */
			su.log_first_block = log_first + su.log_offset_bytes / UFS_BLOCK;
			su.log_nblocks = MIN(log_last - log_first + 1 - su.log_offset_bytes / UFS_BLOCK,
			    DALI_LOG_SPAN_BLOCKS);
		} else if (su.dali) {
			/* Legacy Dali would reuse slot 0, destroying a previous boot. */
			printf("SWEET LOG: non-G4 Dali persistent logging disabled to preserve slots\n");
		} else if (!ufs_log_region_available(log_first + su.log_offset_bytes / UFS_BLOCK)) {
			/* The helper reports the read failure or unrecognized content. */
		} else {
			su.log_first_block = log_first + su.log_offset_bytes / UFS_BLOCK;
			su.log_nblocks = log_last - log_first + 1 - su.log_offset_bytes / UFS_BLOCK;
			su.log_partition_ready = true;
			printf("SWEET UFS: log partition \"%s\" data first=0x%llx blocks=%llu offset=%llu MiB\n",
			    log_want, su.log_first_block, su.log_nblocks, su.log_offset_bytes >> 20);
		}
	}
	return 0;
}

static void
ufs_log_host_state(const char *stage)
{
	uint32_t version = ufs_rd(REG_VER), cap = ufs_rd(REG_CAP);
	uint32_t hce = ufs_rd(REG_HCE), hcs = ufs_rd(REG_HCS);
	uint64_t utrl = ((uint64_t)ufs_rd(REG_UTRLBAU) << 32) | ufs_rd(REG_UTRLBA);
	printf("SWEET UFS: %s ver=0x%x cap=0x%x hce=0x%x hcs=0x%x is=0x%x ie=0x%x "
	    "utrl=0x%llx dbr=0x%x rsr=0x%x tm_dbr=0x%x\n",
	    stage, version, cap, hce, hcs, ufs_rd(REG_IS), ufs_rd(REG_IE), utrl,
	    ufs_rd(REG_UTRLDBR), ufs_rd(REG_UTRLRSR), ufs_rd(REG_UTMRLDBR));
}

static int ufs_mtk_check_mt6991_resources(void);

static int
ufs_wait_reg(uint32_t offset, uint32_t mask, uint32_t expected, uint32_t timeout_us)
{
	for (uint32_t elapsed = 0; ; elapsed += 10) {
		uint32_t value = ufs_rd(offset);
		if (value == UINT32_MAX) {
			return ENXIO;
		}
		if ((value & mask) == expected) {
			return 0;
		}
		if (elapsed >= timeout_us) {
			printf("SWEET UFS: timeout waiting offset=0x%x mask=0x%x want=0x%x got=0x%x\n",
			    offset, mask, expected, value);
			return ETIMEDOUT;
		}
		delay(10);
	}
}

static int
ufs_mtk_sip(uint32_t selector, uint32_t arg1, uint32_t arg2)
{
	/* MT6991 UFS SiP ABI, matching the pinned Linux/HackIOS caller. */
	register uint64_t x0 __asm__("x0") = MT6991_UFS_SIP;
	register uint64_t x1 __asm__("x1") = selector;
	register uint64_t x2 __asm__("x2") = arg1;
	register uint64_t x3 __asm__("x3") = arg2;
	register uint64_t x4 __asm__("x4") = 0;
	register uint64_t x5 __asm__("x5") = 0;
	register uint64_t x6 __asm__("x6") = 0;
	register uint64_t x7 __asm__("x7") = 0;
	__asm__ volatile("dsb sy\n\tsmc #0"
	    : "+r"(x0), "+r"(x1), "+r"(x2), "+r"(x3), "+r"(x4), "+r"(x5), "+r"(x6), "+r"(x7)
	    : : "x8", "x9", "x10", "x11", "x12", "x13", "x14", "x15", "x16", "x17", "memory", "cc");
	int64_t status = (int64_t)x0;
	if (status != 0) {
		printf("SWEET UFS: MT6991 SiP selector=0x%x args=0x%x/0x%x status=%lld\n",
		    selector, arg1, arg2, (long long)status);
		return EIO;
	}
	return 0;
}

static int
ufs_mtk_rmw(uint32_t offset, uint32_t clear_mask, uint32_t set_bits)
{
	uint32_t value = ufs_rd(offset);
	if (value == UINT32_MAX) {
		return ENXIO;
	}
	uint32_t expected = (value & ~clear_mask) | set_bits;
	ufs_wr(offset, expected);
	value = ufs_rd(offset);
	return value == UINT32_MAX ? ENXIO : value == expected ? 0 : EIO;
}

static int
ufs_mtk_ao_set(const char *stage, uint32_t offset, uint32_t bits)
{
	uint32_t value = ufs_ao_rd(offset);
	if (value == UINT32_MAX) {
		return ENXIO;
	}
	uint32_t old_value = value;
	uint32_t write_value = old_value | bits;
	/* Match HackIOS/TI syscon reset semantics; readback is diagnostic only. */
	ufs_ao_wr(offset, write_value);
	uint32_t readback = ufs_ao_rd(offset);
	printf("SWEET UFS: AO reset stage=%s offset=0x%x mask=0x%x old=0x%x write=0x%x readback=0x%x\n",
	    stage, offset, bits, old_value, write_value, readback);
	return 0;
}

static int
ufs_mtk_check_stopped(void)
{
	uint32_t hce = ufs_rd(REG_HCE), hcs = ufs_rd(REG_HCS);
	if (hce == UINT32_MAX || hcs == UINT32_MAX) {
		return ENXIO;
	}
	/* HCS[3:0] are status/readiness bits, not proof of active DMA ownership. */
	if (hce != 0 || (hcs & ~0xfu) != 0) {
		printf("SWEET UFS: refusing startup from non-stopped HCI hce=0x%x hcs=0x%x\n",
		    hce, hcs);
		return EBUSY;
	}
	const uint32_t idle_regs[] = {
		REG_UTRLDBR, REG_UTRLRSR, REG_UTMRLDBR, REG_UTMRLRSR, REG_MEM_CFG, REG_IE
	};
	for (unsigned i = 0; i < sizeof(idle_regs) / sizeof(idle_regs[0]); i++) {
		uint32_t value = ufs_rd(idle_regs[i]);
		if (value == UINT32_MAX) {
			return ENXIO;
		}
		if (value != 0) {
			printf("SWEET UFS: refusing startup with active queue state offset=0x%x value=0x%x\n",
			    idle_regs[i], value);
			return EBUSY;
		}
	}
	/* Keep the handoff guard strict; the following write also pins polled mode. */
	ufs_wr(REG_IE, 0);
	if (ufs_rd(REG_IE) != 0) {
		return EIO;
	}
	return 0;
}

static int
ufs_mtk_clear_queue_bases(void)
{
	const uint32_t bases[] = {REG_UTRLBA, REG_UTRLBAU, REG_UTMRLBA, REG_UTMRLBAU};
	for (unsigned i = 0; i < sizeof(bases) / sizeof(bases[0]); i++) {
		uint32_t old = ufs_rd(bases[i]);
		if (old == UINT32_MAX) {
			return ENXIO;
		}
		if (old != 0) {
			ufs_wr(bases[i], 0);
			old = ufs_rd(bases[i]);
			if (old == UINT32_MAX) {
				return ENXIO;
			}
			if (old != 0) {
				printf("SWEET UFS: cannot clear stopped queue base offset=0x%x\n", bases[i]);
				return EIO;
			}
		}
	}
	return 0;
}

static int
ufs_mtk_device_reset(void)
{
	int error;
	bool release_needed = true;
	su.failure_stage = "device-reset-assert";
	error = ufs_mtk_sip(MT6991_UFS_SIP_DEVICE_RST, 0, 0);
	if (error) {
		goto release;
	}
	su.failure_stage = "device-reset-stop-hce";
	ufs_wr(REG_HCE, 0);
	error = ufs_wait_reg(REG_HCE, 1, 0, 100000);
	delay(10);
	if (error) {
		goto release;
	}
	su.failure_stage = "device-reset-release";
	error = ufs_mtk_sip(MT6991_UFS_SIP_DEVICE_RST, 1, 0);
	release_needed = false;
	if (!error) {
		delay(15000);
	}
	return error;

release:
	if (release_needed) {
		const char *failed_stage = su.failure_stage;
		su.failure_stage = "device-reset-cleanup";
		int release_error = ufs_mtk_sip(MT6991_UFS_SIP_DEVICE_RST, 1, 0);
		su.failure_stage = failed_stage;
		if (!release_error) {
			delay(15000);
		} else {
			printf("SWEET UFS: device-reset cleanup release failed after stage=%s\n", failed_stage);
		}
	}
	return error;
}

static int
ufs_mtk_host_reset(void)
{
	static const uint32_t assert_bits[] = {4, 2, 1};
	static const uint32_t release_bits[] = {1, 2, 4};
	static const char *const assert_stages[] = {"assert-hci", "assert-crypto", "assert-unipro"};
	static const char *const release_stages[] = {"release-unipro", "release-crypto", "release-hci"};
	int error = 0;
	su.failure_stage = "host-reset-assert";
	for (unsigned i = 0; i < sizeof(assert_bits) / sizeof(assert_bits[0]); i++) {
		error = ufs_mtk_ao_set(assert_stages[i], 0x148, assert_bits[i]);
		if (error) {
			goto release;
		}
	}
	delay(100);
	su.failure_stage = "host-reset-release";
	for (unsigned i = 0; i < sizeof(release_bits) / sizeof(release_bits[0]); i++) {
		error = ufs_mtk_ao_set(release_stages[i], 0x14c, release_bits[i]);
		if (error) {
			goto release;
		}
	}
	return 0;

release:
	/* A partial reset assertion must not leave one of the host blocks held down. */
	for (unsigned i = 0; i < sizeof(release_bits) / sizeof(release_bits[0]); i++) {
		int cleanup_error = ufs_mtk_ao_set("cleanup-release", 0x14c, release_bits[i]);
		if (cleanup_error != 0) {
			printf("SWEET UFS: host-reset cleanup release bit=0x%x failed error=%d\n",
			    release_bits[i], cleanup_error);
		}
	}
	return error;
}

static void
ufs_mtk_log_uic_error(const char *operation, uint32_t status)
{
	printf("SWEET UFS: UIC %s error IS=0x%x HCS=0x%x layers=0x%x/0x%x/0x%x/0x%x/0x%x\n",
	    operation, status, ufs_rd(REG_HCS), ufs_rd(REG_UIC_ERROR_PHY),
	    ufs_rd(REG_UIC_ERROR_DL), ufs_rd(REG_UIC_ERROR_N), ufs_rd(REG_UIC_ERROR_T),
	    ufs_rd(REG_UIC_ERROR_DME));
}

static int
ufs_mtk_ack_uic(uint32_t mask, bool strict_zero)
{
	ufs_wr(REG_IS, mask);
	uint32_t status = ufs_rd(REG_IS);
	if (status == UINT32_MAX) {
		return ENXIO;
	}
	if ((strict_zero && status != 0) ||
	    (!strict_zero && (status & (IS_UFS_ERROR_MASK | IS_UCCS)) != 0)) {
		printf("SWEET UFS: UIC acknowledgement mask=0x%x left IS=0x%x\n", mask, status);
		return EIO;
	}
	return 0;
}

/* Match HackIOS' narrowly qualified MT6991 PHY line-reset event at LINK_STARTUP. */
static int
ufs_mtk_link_reset_event(uint32_t status)
{
	uint32_t hce = ufs_rd(REG_HCE), hcs = ufs_rd(REG_HCS);
	uint32_t errors[] = {
		ufs_rd(REG_UIC_ERROR_PHY), ufs_rd(REG_UIC_ERROR_DL), ufs_rd(REG_UIC_ERROR_N),
		ufs_rd(REG_UIC_ERROR_T), ufs_rd(REG_UIC_ERROR_DME)
	};
	printf("SWEET UFS: LINK_STARTUP reset IS=0x%x HCE=0x%x HCS=0x%x layers="
	    "0x%x/0x%x/0x%x/0x%x/0x%x\n", status, hce, hcs,
	    errors[0], errors[1], errors[2], errors[3], errors[4]);
	if (hce == UINT32_MAX || hcs == UINT32_MAX) {
		return ENXIO;
	}
	for (unsigned i = 0; i < sizeof(errors) / sizeof(errors[0]); i++) {
		if (errors[i] == UINT32_MAX) {
			return ENXIO;
		}
	}
	if (status != (IS_UIC_ERROR | IS_UCCS) || hce != 1 || (hcs & 0x38) != HCS_UCRDY ||
	    errors[0] != MT6991_UFS_LINK_RESET_PHY_ERROR || errors[1] != 0 ||
	    errors[2] != 0 || errors[3] != 0 || errors[4] != 0) {
		return EIO;
	}
	return 0;
}

static int
ufs_mtk_uic(const char *stage, uint32_t opcode, uint32_t attribute, uint32_t *value)
{
	int error;
	bool link_startup = opcode == 0x16; /* DME_LINK_STARTUP */
	su.failure_stage = stage;
	error = ufs_wait_reg(REG_HCS, HCS_UCRDY, HCS_UCRDY, 500000);
	if (error) {
		return error;
	}
	uint32_t hcs = ufs_rd(REG_HCS);
	if (hcs == UINT32_MAX) {
		return ENXIO;
	}
	if (hcs & 0x30) {
		printf("SWEET UFS: UIC %s saw unsupported HCS=0x%x\n", stage, hcs);
		return EIO;
	}
	uint32_t status = ufs_rd(REG_IS);
	if (status == UINT32_MAX) {
		return ENXIO;
	}
	if (status & IS_UFS_ERROR_MASK) {
		ufs_mtk_log_uic_error("stale", status);
		return EIO;
	}
	if ((error = ufs_mtk_ack_uic(IS_UCCS, link_startup)) != 0) {
		return error;
	}
	ufs_wr(REG_UICCMDARG1, attribute << 16);
	ufs_wr(REG_UICCMDARG2, 0);
	ufs_wr(REG_UICCMDARG3, value ? *value : 0);
	ufs_wr(REG_UICCMD, opcode);
	bool link_reset_pending = false;
	for (uint32_t elapsed = 0; ; elapsed += 10) {
		status = ufs_rd(REG_IS);
		if (status == UINT32_MAX) {
			return ENXIO;
		}
		if (link_startup) {
			if (status & ~(IS_UIC_ERROR | IS_UCCS)) {
				printf("SWEET UFS: UIC %s unexpected interrupt status=0x%x\n", stage, status);
				return EIO;
			}
			/* Do not read/clear layer registers until UCCS is observed. */
			link_reset_pending |= (status & IS_UIC_ERROR) != 0;
		} else if (status & IS_UFS_ERROR_MASK) {
			ufs_mtk_log_uic_error("completion", status);
			return EIO;
		}
		if (status & IS_UCCS) {
			break;
		}
		if (elapsed >= 500000) {
			printf("SWEET UFS: UIC %s opcode=0x%x timed out IS=0x%x\n",
			    stage, opcode, status);
			return ETIMEDOUT;
		}
		delay(10);
	}
	if (link_startup && link_reset_pending &&
	    (error = ufs_mtk_link_reset_event(status)) != 0) {
		printf("SWEET UFS: UIC %s rejected PHY reset event (%d)\n", stage, error);
		return error;
	}
	uint32_t result = ufs_rd(REG_UICCMDARG2);
	if (result == UINT32_MAX) {
		return ENXIO;
	}
	if ((result & 0xff) != 0) {
		printf("SWEET UFS: UIC %s opcode=0x%x attribute=0x%x result=0x%x\n",
		    stage, opcode, attribute, result);
		return EIO;
	}
	uint32_t returned = ufs_rd(REG_UICCMDARG3);
	if (returned == UINT32_MAX) {
		return ENXIO;
	}
	uint32_t ack = IS_UCCS | ((link_startup && link_reset_pending) ? IS_UIC_ERROR : 0);
	if ((error = ufs_mtk_ack_uic(ack, link_startup || (ack & IS_UIC_ERROR))) != 0) {
		return error;
	}
	if (value) {
		*value = returned;
	}
	return 0;
}


/* MT6991 exact fixed address contract from actual Dali DT. No register writes.
 * Unknown/unready/unstable state keeps capG1, before any new UIC command.
 */
static uint32_t
ufs_pwm_clock_cap(void)
{
    vm_offset_t top = ml_io_map(0x10000000ULL, 0x800);
    vm_offset_t pll = ml_io_map(0x10000800ULL, 0x1000);
    if (!top || !pll) return 1;
    struct sweet_pwm_clock_snapshot a, b;
    a.mux = *(volatile uint32_t *)(top + 0xd0);
    a.pll_status = *(volatile uint32_t *)(pll + 0x3c);
    a.main_con1 = *(volatile uint32_t *)(pll + 0x254);
    a.univ_con1 = *(volatile uint32_t *)(pll + 0x268);
    __builtin_arm_dsb(0xf);
    b.mux = *(volatile uint32_t *)(top + 0xd0);
    b.pll_status = *(volatile uint32_t *)(pll + 0x3c);
    b.main_con1 = *(volatile uint32_t *)(pll + 0x254);
    b.univ_con1 = *(volatile uint32_t *)(pll + 0x268);
    __builtin_arm_dsb(0xf);
    uint64_t rate = sweet_pwm_clock_hz(&a, &b);
    printf("SWEET UFS PWM CLOCK: v=1 mux=0x%x status=0x%x hz=%llu cap=%u\n",
        b.mux, b.pll_status, (unsigned long long)rate, rate ? 2u : 1u);
    return rate ? 2u : 1u; /* ConservativeG2, below fixed vendor default PWMG4. */
}

/* Clock-gated Dali PWM-only initialization. Hardware speed/desktop unverified.
 * Ceiling G2 is reachable only after exact live read-only clock admission.
 * Unknown clock stays G1 before new UIC; no PLL/voltage/HS or recovery retry.
 */
static uint32_t sweet_ufs_pwm_terminal;
static uint32_t sweet_ufs_pwm_root_control; /* 0 unclaimed, 1 active, 2 complete */

/* HSG4_DIAG_BEGIN
 * G4 probe or root trial; transition updates hold sweet_ufs_lock, and its one terminal
 * re-emission is after unlock under the retained root-entry claim. Values are
 * already-observed, never additional UIC or post-error MMIO reads.
 * Mode masks use bit0=G1B/0x44 and bit1=G4B/0x11.
 */
struct sweet_hsg4_diagnostic {
    const char *phase, *operation, *endpoint, *expectation;
    uint32_t attribute, expected_min, expected_max, observed;
    bool expected_valid, observed_valid;
    bool first_error_valid, terminal_reemitted;
    int first_error;
    uint32_t avail_rx[2], avail_tx[2], unipro_version[2];
    uint32_t pmc_submitted, pmc_completed, pmc_acknowledged, pmc_idle_ok;
    uint32_t pmc_mode, pmc_polls, pmc_status, pmc_hcs, pmc_arg2, pmc_ack_status;
    const char *pmc_failure_stage;
    int pmc_error;
    bool pmc_sampled, pmc_arg2_valid, pmc_uccs, pmc_upms, pmc_ack_status_valid;
};
static struct sweet_hsg4_diagnostic sweet_hsg4_diag;
/* HSG4_DIAG_END */
static void ufs_hsg4_report(int error);
static void ufs_hsg4_post_probe_error(int error);

/* One indivisible, first-error-wins publication: errno<<1 | uncertain.
 * Known failure does not authorize retry. This is independent of DMA poison.
 */
static int
ufs_pwm_latch(int error, bool uncertain)
{
    if (error <= 0 || (uint32_t)error > UINT32_MAX / 2) error = EIO;
    uint32_t encoded = ((uint32_t)error << 1) | (uncertain ? 1u : 0u);
    if (os_atomic_cmpxchg(&sweet_ufs_pwm_terminal, 0u, encoded, acq_rel)) {
        printf("SWEET UFS PWM: v=1 failed=%d uncertain=%u retry=0\n",
            error, uncertain ? 1u : 0u);
    }
    return (int)(os_atomic_load(&sweet_ufs_pwm_terminal, acquire) >> 1);
}

static int
ufs_pwm_failure(int error)
{
    /* A transport/SET/ACK error is conservatively unknown, never clean fallback. */
    return ufs_pwm_latch(error, true);
}

static int
ufs_pwm_reentry_error(void)
{
    return (int)(os_atomic_load(&sweet_ufs_pwm_terminal, acquire) >> 1);
}

/* One nonblocking claim. Do not reset/UIC again after any opted-in init failure. */
static int
ufs_pwm_root_enter(bool guard)
{
    int error = ufs_pwm_reentry_error();
    if (error) return error;
    if (!guard) return 0;
    uint32_t state = os_atomic_load(&sweet_ufs_pwm_root_control, acquire);
    if (state == 2) return 0;
    if (state != 0 || !os_atomic_cmpxchg(&sweet_ufs_pwm_root_control, 0u, 1u, acq_rel))
        return EBUSY;
    /* A failed prior caller must not be masked by a concurrent successful claim. */
    return ufs_pwm_reentry_error();
}

static void
ufs_pwm_root_leave(bool guard, int error)
{
    if (!guard) return;
    if (error) {
        (void)ufs_pwm_failure(error); /* root control remains claimed until reboot. */
        /* Ramoops drops contended chunks. Re-emit the same frozen first error
         * once on the terminal path, without any UFS I/O, retry or wait. */
        if (su.hsg4_requested && sweet_hsg4_diag.first_error_valid &&
            !sweet_hsg4_diag.terminal_reemitted) {
            sweet_hsg4_diag.terminal_reemitted = true;
            ufs_hsg4_report(sweet_hsg4_diag.first_error);
        }
    } else {
        os_atomic_store(&sweet_ufs_pwm_root_control, 2u, release);
    }
}

/* Failure-only instrumentation; read each register exactly where the old guard did. */
static int
ufs_pwm_idle_expect(uint32_t reg, uint32_t expected)
{
    uint32_t value = ufs_rd(reg);
    if (value == expected) return 0;
    printf("SWEET UFS PWM RAW: idle reg=0x%x value=0x%x expected=0x%x\n",
        reg, value, expected);
    return EIO;
}

static int
ufs_pwm_idle(void)
{
    int error = ufs_pwm_reentry_error();
    if (error) return error;
    if (!su.dali || su.ready || su.command_poisoned) {
        printf("SWEET UFS PWM RAW: idle flags dali=%u ready=%u poisoned=%u\n",
            su.dali ? 1u : 0u, su.ready ? 1u : 0u, su.command_poisoned ? 1u : 0u);
        return EIO;
    }
    uint32_t hce = ufs_rd(REG_HCE), hcs = ufs_rd(REG_HCS);
    if (hce != 1 || hcs == UINT32_MAX || (hcs & 0x3bu) != 0x0bu) {
        printf("SWEET UFS PWM RAW: idle hce=0x%x hcs=0x%x\n", hce, hcs);
        return EIO;
    }
    if (ufs_pwm_idle_expect(REG_IE, 0) || ufs_pwm_idle_expect(REG_AHIT, 0) ||
        ufs_pwm_idle_expect(REG_UTRLDBR, 0) || ufs_pwm_idle_expect(REG_UTMRLDBR, 0) ||
        ufs_pwm_idle_expect(REG_UTRLRSR, 1) || ufs_pwm_idle_expect(REG_UTMRLRSR, 0))
        return EIO;
    uint64_t list = su.dma_pa + DMA_UTRL;
    if (list < su.dma_pa) {
        printf("SWEET UFS PWM RAW: idle list overflow\n");
        return EIO;
    }
    if (ufs_pwm_idle_expect(REG_UTRLBA, (uint32_t)list) ||
        ufs_pwm_idle_expect(REG_UTRLBAU, (uint32_t)(list >> 32))) return EIO;
    return 0;
}

static int
ufs_pwm_observe_error(const char *operation, const char *stage, uint32_t attribute, int error)
{
    printf("SWEET UFS PWM RAW: op=%s stage=%s attr=0x%x error=%d\n",
        operation, stage, attribute, error);
    return error;
}

static int
ufs_pwm_observe_guard(const char *operation, const char *stage, uint32_t attribute)
{
    int error = ufs_pwm_idle();
    if (error) return ufs_pwm_observe_error(operation, stage, attribute, error);
    uint32_t status = ufs_rd(REG_IS);
    if (status != 0) {
        printf("SWEET UFS PWM RAW: guard IS=0x%x\n", status);
        return ufs_pwm_observe_error(operation, stage, attribute, EIO);
    }
    return 0;
}

/* Strict wrapper for healthy, owned, completed GETs only. No optional fallback. */
static int
ufs_pwm_get(bool peer, uint32_t attribute, uint32_t *value, uint64_t until)
{
    const char *op = peer ? "peer-get" : "get";
    if (mach_absolute_time() >= until) return ufs_pwm_observe_error(op, "deadline-pre", attribute, ETIMEDOUT);
    int error = ufs_pwm_observe_guard(op, "guard-pre", attribute);
    if (error) return error;
    error = ufs_mtk_uic("pwm-get", peer ? 3u : 1u, attribute, value);
    if (error) return ufs_pwm_observe_error(op, "uic", attribute, error);
    if (mach_absolute_time() >= until) return ufs_pwm_observe_error(op, "deadline-post", attribute, ETIMEDOUT);
    return ufs_pwm_observe_guard(op, "guard-post", attribute);
}

static int
ufs_pwm_snapshot(struct sweet_pwm_snapshot *s, uint64_t until)
{
    const uint32_t attrs[] = { 0x1571, 0x1583, 0x1568, 0x1580, 0x1560,
        0x1581, 0x1561, 0x1584, 0x1569 };
    uint32_t *values[] = { &s->mode, &s->rx_gear, &s->tx_gear, &s->rx_active,
        &s->tx_active, &s->rx_connected, &s->tx_connected, &s->rx_term, &s->tx_term };
    for (unsigned int i = 0; i < sizeof(attrs) / sizeof(attrs[0]); ++i) {
        int error = ufs_pwm_get(false, attrs[i], values[i], until);
        if (error) return error;
    }
    return sweet_pwm_snapshot_valid(s) ? 0 : EIO;
}

/* Ordinary attribute SET: do NOT use this helper for PA_PWRMODE. */
static int
ufs_pwm_set(uint32_t attribute, uint32_t value, uint64_t until)
{
    if (mach_absolute_time() >= until) return ufs_pwm_observe_error("set", "deadline-pre", attribute, ETIMEDOUT);
    if (attribute == 0x1571) return ufs_pwm_observe_error("set", "bad-attribute", attribute, EINVAL);
    int error = ufs_pwm_observe_guard("set", "guard-pre", attribute);
    if (error) return error;
    error = ufs_mtk_uic("pwm-set", 2, attribute, &value);
    if (error) return ufs_pwm_observe_error("set", "uic", attribute, error);
    if (mach_absolute_time() >= until) return ufs_pwm_observe_error("set", "deadline-post", attribute, ETIMEDOUT);
    return ufs_pwm_observe_guard("set", "guard-post", attribute);
}

/* The vendor sync-length fix needs DME_PEER_SET, with the same guards and
 * completion/ACK rules as a local SET. PA_PWRMODE is never a peer SET. */
static int
ufs_pwm_peer_set(uint32_t attribute, uint32_t value, uint64_t until)
{
    if (mach_absolute_time() >= until) return ufs_pwm_observe_error("peer-set", "deadline-pre", attribute, ETIMEDOUT);
    if (attribute == 0x1571) return ufs_pwm_observe_error("peer-set", "bad-attribute", attribute, EINVAL);
    int error = ufs_pwm_observe_guard("peer-set", "guard-pre", attribute);
    if (error) return error;
    error = ufs_mtk_uic("hs-peer-set", 4, attribute, &value);
    if (error) return ufs_pwm_observe_error("peer-set", "uic", attribute, error);
    if (mach_absolute_time() >= until) return ufs_pwm_observe_error("peer-set", "deadline-post", attribute, ETIMEDOUT);
    return ufs_pwm_observe_guard("peer-set", "guard-post", attribute);
}

static int
ufs_pwm_pmc_observe(const char *stage, uint32_t mode, unsigned int polls,
    bool sampled, uint32_t status, uint32_t hcs, bool arg2_read, uint32_t arg2,
    const struct sweet_pwm_completion *completion, int error)
{
    if (su.hsg4_requested) {
        sweet_hsg4_diag.pmc_failure_stage = stage;
        sweet_hsg4_diag.pmc_error = error;
        sweet_hsg4_diag.pmc_mode = mode;
        sweet_hsg4_diag.pmc_polls = polls;
        sweet_hsg4_diag.pmc_sampled = sampled;
        sweet_hsg4_diag.pmc_status = status;
        sweet_hsg4_diag.pmc_hcs = hcs;
        sweet_hsg4_diag.pmc_arg2_valid = arg2_read;
        sweet_hsg4_diag.pmc_arg2 = arg2;
        sweet_hsg4_diag.pmc_uccs = completion->uccs;
        sweet_hsg4_diag.pmc_upms = completion->upms;
    }
    printf("SWEET UFS PWM RAW: pmc stage=%s mode=0x%x error=%d polls=%u "
        "sampled=%u IS=0x%x HCS=0x%x arg2_read=%u ARG2=0x%x uccs=%u upms=%u\n",
        stage, mode, error, polls, sampled ? 1u : 0u, status, hcs,
        arg2_read ? 1u : 0u, arg2, completion->uccs ? 1u : 0u,
        completion->upms ? 1u : 0u);
    return error;
}

/* Exactly one PA_PWRMODE request. Both completions share one absolute deadline.
 * Poll count also bounds broken clocks/mocks; no retry, no speculative ACK.
 */
static int
ufs_pwm_pmc(uint32_t mode, uint64_t until)
{
    uint32_t hsg4_bit = mode == 0x44 ? 1u : mode == 0x11 ? 2u : 0u;
    if (su.hsg4_requested && hsg4_bit) {
        sweet_hsg4_diag.pmc_mode = mode;
        sweet_hsg4_diag.pmc_failure_stage = "none";
        sweet_hsg4_diag.pmc_error = 0;
        sweet_hsg4_diag.pmc_polls = sweet_hsg4_diag.pmc_status = 0;
        sweet_hsg4_diag.pmc_hcs = sweet_hsg4_diag.pmc_arg2 = 0;
        sweet_hsg4_diag.pmc_ack_status = 0;
        sweet_hsg4_diag.pmc_sampled = sweet_hsg4_diag.pmc_arg2_valid = false;
        sweet_hsg4_diag.pmc_uccs = sweet_hsg4_diag.pmc_upms = false;
        sweet_hsg4_diag.pmc_ack_status_valid = false;
    }
    if (mach_absolute_time() >= until) {
        if (su.hsg4_requested && hsg4_bit) {
            sweet_hsg4_diag.pmc_failure_stage = "deadline-pre";
            sweet_hsg4_diag.pmc_error = ETIMEDOUT;
        }
        return ufs_pwm_observe_error("pmc", "deadline-pre", 0x1571, ETIMEDOUT);
    }
    if (mode != 0x22 && mode != 0x55 && mode != 0x44 && mode != 0x11) return ufs_pwm_observe_error("pmc", "bad-mode", 0x1571, EINVAL);
    int error = ufs_pwm_observe_guard("pmc", "guard-pre", 0x1571);
    if (error) {
        if (su.hsg4_requested && hsg4_bit) {
            sweet_hsg4_diag.pmc_failure_stage = "guard-pre";
            sweet_hsg4_diag.pmc_error = error;
        }
        return error;
    }
    uint64_t span = 0, now = mach_absolute_time();
    nanoseconds_to_absolutetime(500ull * NSEC_PER_MSEC, &span);
    if (span == 0 || now > UINT64_MAX - span) {
        if (su.hsg4_requested && hsg4_bit) {
            sweet_hsg4_diag.pmc_failure_stage = "bad-deadline";
            sweet_hsg4_diag.pmc_error = ETIMEDOUT;
        }
        return ufs_pwm_observe_error("pmc", "bad-deadline", 0x1571, ETIMEDOUT);
    }
    uint64_t deadline = now + span;
    if (until < deadline) deadline = until;
    struct sweet_pwm_completion completion = {0};
    uint32_t last_status = 0, last_hcs = 0, last_arg2 = 0;
    bool sampled = false, arg2_read = false;
    unsigned int polls;
    ufs_wr(REG_UICCMDARG1, 0x1571u << 16);
    ufs_wr(REG_UICCMDARG2, 0);
    ufs_wr(REG_UICCMDARG3, mode);
    ufs_wr(REG_UICCMD, 2); /* DME_SET */
    if (su.hsg4_requested && hsg4_bit) {
        /* Record the MMIO submission without console I/O in the polling window. */
        sweet_hsg4_diag.pmc_submitted |= hsg4_bit;
    }
    for (polls = 0; polls < 50001; ++polls) {
        if (mach_absolute_time() >= deadline)
            return ufs_pwm_pmc_observe("deadline", mode, polls, sampled, last_status, last_hcs,
                arg2_read, last_arg2, &completion, ETIMEDOUT);
        uint32_t status = ufs_rd(REG_IS), hcs = ufs_rd(REG_HCS);
        sampled = true; last_status = status; last_hcs = hcs;
        uint32_t result = UINT32_MAX;
        if ((status & SWEET_PWM_UCCS) && !completion.uccs && status != UINT32_MAX) {
            result = ufs_rd(REG_UICCMDARG2);
            arg2_read = true; last_arg2 = result;
        }
        uint32_t q = sweet_pwm_complete_step(&completion, status, result, hcs);
        if (q == SWEET_PWM_STEP_ERROR)
            return ufs_pwm_pmc_observe("completion", mode, polls, sampled, last_status, last_hcs,
                arg2_read, last_arg2, &completion, EIO);
        if (q == SWEET_PWM_STEP_DONE) {
            if (su.hsg4_requested && hsg4_bit) sweet_hsg4_diag.pmc_completed |= hsg4_bit;
            /* Check ownership/readiness again before consuming our completion. */
            error = ufs_pwm_idle();
            if (error)
                return ufs_pwm_pmc_observe("idle-pre-ack", mode, polls, sampled, last_status, last_hcs,
                    arg2_read, last_arg2, &completion, error);
            ufs_wr(REG_IS, SWEET_PWM_UCCS | SWEET_PWM_UPMS);
            status = ufs_rd(REG_IS);
            if (su.hsg4_requested && hsg4_bit) {
                sweet_hsg4_diag.pmc_ack_status = status;
                sweet_hsg4_diag.pmc_ack_status_valid = true;
            }
            if (status != 0) {
                printf("SWEET UFS PWM RAW: pmc ack IS=0x%x\n", status);
                return ufs_pwm_pmc_observe("ack", mode, polls, sampled, last_status, last_hcs,
                    arg2_read, last_arg2, &completion, EIO);
            }
            if (su.hsg4_requested && hsg4_bit) sweet_hsg4_diag.pmc_acknowledged |= hsg4_bit;
            error = ufs_pwm_idle();
            if (error)
                return ufs_pwm_pmc_observe("idle-post-ack", mode, polls, sampled, last_status, last_hcs,
                    arg2_read, last_arg2, &completion, error);
            if (su.hsg4_requested && hsg4_bit) {
                sweet_hsg4_diag.pmc_idle_ok |= hsg4_bit;
                sweet_hsg4_diag.pmc_polls = polls;
                sweet_hsg4_diag.pmc_sampled = sampled;
                sweet_hsg4_diag.pmc_status = last_status;
                sweet_hsg4_diag.pmc_hcs = last_hcs;
                sweet_hsg4_diag.pmc_arg2_valid = arg2_read;
                sweet_hsg4_diag.pmc_arg2 = last_arg2;
                sweet_hsg4_diag.pmc_uccs = completion.uccs;
                sweet_hsg4_diag.pmc_upms = completion.upms;
                printf("SWEET UFS HSG4 PMC: event=completed mode=0x%x "
                    "polls=%u IS=0x%x HCS=0x%x ARG2=0x%x uccs=%u upms=%u ack=0x%x idle=1\n",
                    mode, polls, last_status, last_hcs, last_arg2,
                    completion.uccs ? 1u : 0u, completion.upms ? 1u : 0u, status);
            }
            return 0;
        }
        delay(10);
    }
    return ufs_pwm_pmc_observe("poll-limit", mode, polls, sampled, last_status, last_hcs,
        arg2_read, last_arg2, &completion, ETIMEDOUT);
}

/* Called before read-capacity/GPT. Only host fixtures may pass cap > G1 until
 * the immutable production platform contract is independently closed.
 * This foundation preserves mode/lanes/termination and only sets the two gears;
 * missing platform-specific prerequisite preparation blocks a production cap bump.
 */
static int
ufs_dali_prepare_pwm(uint32_t requested, bool cold, uint32_t platform_cap)
{
    if (!sweet_pwm_admitted(requested, su.dali, cold, platform_cap)) return 0;
    /* At most 37 UIC requests, no retries; all share this stage wall budget. */
    uint64_t span = 0, now = mach_absolute_time();
    nanoseconds_to_absolutetime(30ull * NSEC_PER_SEC, &span);
    if (span == 0 || now > UINT64_MAX - span) return ufs_pwm_latch(ETIMEDOUT, false);
    uint64_t until = now + span;
    struct sweet_pwm_snapshot before = {0}, after = {0};
    uint32_t local_max = 0, peer_max = 0, target = 0;
    int error = ufs_pwm_snapshot(&before, until);
    if (!error) error = ufs_pwm_get(false, 0x1586, &local_max, until);
    if (!error) error = ufs_pwm_get(true, 0x1586, &peer_max, until);
    if (error) return ufs_pwm_failure(error);
    uint32_t decision = sweet_pwm_choose(&before, local_max, peer_max, platform_cap, &target);
    printf("SWEET UFS PWM INIT: v=2 mode=0x%x rx=%u tx=%u lanes=%u/%u connected=%u/%u term=%u/%u local=%u peer=%u cap=%u decision=%u target=%u\n",
        before.mode, before.rx_gear, before.tx_gear, before.rx_active, before.tx_active,
        before.rx_connected, before.tx_connected, before.rx_term, before.tx_term,
        local_max, peer_max, platform_cap, decision, target);
    if (decision == SWEET_PWM_INVALID) return ufs_pwm_latch(EIO, false);
    if (decision == SWEET_PWM_SKIP) return 0;
    /* Fixed vendor PWM PRE_CHANGE: PA_NO_ADAPT is3. Never select a HS mode.
     * Same lanes/termination, standard UniPro timeout defaults; no peer SET.
     */
    const uint32_t attributes[] = {
        0x15d4, 0x1583, 0x1580, 0x1584, 0x1568, 0x1560, 0x1569,
        0x15b0, 0x15b1, 0x15b2, 0x15b3, 0x15b4, 0x15b5,
        0xd041, 0xd042, 0xd043
    };
    const uint32_t values[] = {
        3, target, before.rx_active, before.rx_term,
        target, before.tx_active, before.tx_term,
        8191, 65535, 32767, 8191, 65535, 32767, 8191, 65535, 32767
    };
    _Static_assert(sizeof(attributes) == sizeof(values), "PWM attribute/value mismatch");
    for (unsigned int i = 0; i < sizeof(attributes) / sizeof(attributes[0]); ++i) {
        error = ufs_pwm_set(attributes[i], values[i], until);
        if (error) break;
    }
    if (!error) error = ufs_pwm_pmc(0x22, until); /* SLOW RX/TX, no HS series. */
    if (!error) error = ufs_pwm_snapshot(&after, until);
    if (!error && !sweet_pwm_readback_matches(&before, &after, target))
        return ufs_pwm_latch(EIO, false); /* All completions ACKed, observed config wrong. */
    if (error) return ufs_pwm_failure(error);
    printf("SWEET UFS PWM: v=2 mode=0x%x rx=%u tx=%u local=%u peer=%u target=%u old=0x%x/%u/%u verified=1\n",
        after.mode, after.rx_gear, after.tx_gear, local_max, peer_max, target,
        before.mode, before.rx_gear, before.tx_gear);
    return 0;
}

/* Optional standard PWM G1 two-lane PMC. No HS, gear, clock or voltage changes. */
static int
ufs_dali_prepare_two_lanes(bool requested)
{
    if (!requested || !su.dali) return 0;
    uint64_t span = 0, now = mach_absolute_time();
    nanoseconds_to_absolutetime(30ull * NSEC_PER_SEC, &span);
    if (span == 0 || now > UINT64_MAX - span) return ufs_pwm_latch(ETIMEDOUT, false);
    uint64_t until = now + span;
    struct sweet_pwm_snapshot before = {0}, after = {0};
    uint32_t local_max = 0, peer_max = 0, peer_rx = 0, peer_tx = 0;
    uint32_t peer_rx_active = 0, peer_tx_active = 0;
    int error = ufs_pwm_snapshot(&before, until);
    if (!error) error = ufs_pwm_get(false, 0x1586, &local_max, until);
    if (!error) error = ufs_pwm_get(true, 0x1586, &peer_max, until);
    if (!error) error = ufs_pwm_get(true, 0x1581, &peer_rx, until);
    if (!error) error = ufs_pwm_get(true, 0x1561, &peer_tx, until);
    if (error) return ufs_pwm_failure(error);
    if (local_max < 1 || local_max > 7 || peer_max < 1 || peer_max > 7 ||
        peer_rx < 1 || peer_rx > 2 || peer_tx < 1 || peer_tx > 2 ||
        (((before.mode >> 4) == 2 || (before.mode >> 4) == 5) && before.rx_gear > local_max) ||
        (((before.mode & 0xf) == 2 || (before.mode & 0xf) == 5) && before.tx_gear > peer_max))
        return ufs_pwm_latch(EIO, false);
    printf("SWEET UFS 2LANE: before mode=0x%x gear=%u/%u active=%u/%u connected=%u/%u peer=%u/%u max=%u/%u\n",
        before.mode, before.rx_gear, before.tx_gear, before.rx_active, before.tx_active,
        before.rx_connected, before.tx_connected, peer_rx, peer_tx, local_max, peer_max);
    if (before.mode != 0x55 ||
        before.rx_gear != 1 || before.tx_gear != 1 ||
        before.rx_active != 1 || before.tx_active != 1 ||
        before.rx_connected != 2 || before.tx_connected != 2 || peer_rx != 2 || peer_tx != 2) {
        printf("SWEET UFS 2LANE: healthy link outside scope; unchanged\n");
        return 0;
    }
    /* Stage only the two lane attributes; preserve existing PWM mode and gear. */
    error = ufs_pwm_set(0x1580, 2, until); /* PA_ACTIVERXDATALANES */
    if (!error) error = ufs_pwm_set(0x1560, 2, until); /* PA_ACTIVETXDATALANES */
    if (!error) error = ufs_pwm_pmc(before.mode, until);
    if (!error) error = ufs_pwm_snapshot(&after, until);
    if (!error) error = ufs_pwm_get(true, 0x1580, &peer_rx_active, until);
    if (!error) error = ufs_pwm_get(true, 0x1560, &peer_tx_active, until);
    if (error) return ufs_pwm_failure(error);
    if (after.mode != before.mode || after.rx_gear != before.rx_gear || after.tx_gear != before.tx_gear ||
        after.rx_active != 2 || after.tx_active != 2 || peer_rx_active != 2 || peer_tx_active != 2 ||
        after.rx_connected != before.rx_connected || after.tx_connected != before.tx_connected ||
        after.rx_term != before.rx_term || after.tx_term != before.tx_term)
        return ufs_pwm_latch(EIO, false);
    printf("SWEET UFS 2LANE: verified mode=0x%x gear=%u/%u active=%u/%u peer=%u/%u\n",
        after.mode, after.rx_gear, after.tx_gear, after.rx_active, after.tx_active,
        peer_rx_active, peer_tx_active);
    return 0;
}

#include "SweetUFSHSPlatform.h"
#include "SweetUFSHSG1.h"

static int
ufs_mtk_dme_rmw(const char *get_stage, const char *set_stage, uint32_t attribute,
	uint32_t clear_mask, uint32_t set_bits)
{
	uint32_t value = 0;
	int error = ufs_mtk_uic(get_stage, 1, attribute, &value); /* DME_GET */
	if (error) {
		return error;
	}
	value = (value & ~clear_mask) | set_bits;
	return ufs_mtk_uic(set_stage, 2, attribute, &value); /* DME_SET */
}

static int
ufs_mtk_stop_host(void)
{
	ufs_wr(REG_IE, 0);
	ufs_wr(REG_UTRLRSR, 0);
	ufs_wr(REG_UTMRLRSR, 0);
	ufs_wr(REG_HCE, 0);
	int error = ufs_wait_reg(REG_HCE, 1, 0, 100000);
	return error ? error : ufs_mtk_check_stopped();
}

static int
ufs_mtk_start_host(void)
{
	int error;
	su.failure_stage = "controller-stopped-check";
	error = ufs_mtk_check_stopped();
	if (error) {
		return error;
	}
	su.failure_stage = "controller-clear-old-queues";
	error = ufs_mtk_clear_queue_bases();
	if (error) {
		return error;
	}
	uint32_t is = ufs_rd(REG_IS);
	if (is == UINT32_MAX) {
		return ENXIO;
	}
	if (is != 0) {
		ufs_wr(REG_IS, is); /* W1C stale, idle status only. */
	}
	ufs_wr(REG_AHIT, 0);
	su.failure_stage = "mt6991-power-restore";
	if ((error = ufs_mtk_sip(MT6991_UFS_SIP_MTCMOS, 0, 0)) != 0 ||
	    (error = ufs_mtk_sip(MT6991_UFS_SIP_MTCMOS, 1, 0)) != 0) {
		goto failed;
	}
	su.failure_stage = "controller-stopped-after-power";
	if ((error = ufs_mtk_check_stopped()) != 0) {
		goto failed;
	}
	su.failure_stage = "reference-clock";
	uint32_t ref_clock = ufs_rd(REG_MTK_REF_CLOCK);
	if (ref_clock == UINT32_MAX) {
		error = ENXIO;
		goto failed;
	}
	if ((ref_clock & 3) != 3) {
		if ((error = ufs_mtk_sip(MT6991_UFS_SIP_REFCLK, 1, 0)) != 0) {
			goto failed;
		}
		ufs_wr(REG_MTK_REF_CLOCK, 1);
		if ((error = ufs_wait_reg(REG_MTK_REF_CLOCK, 3, 3, 3000)) != 0) {
			int cleanup_error = ufs_mtk_sip(MT6991_UFS_SIP_REFCLK, 0, 1);
			if (cleanup_error) {
				printf("SWEET UFS: reference-clock cleanup notification failed (%d)\n", cleanup_error);
			}
			goto failed;
		}
		delay(32);
		if ((error = ufs_mtk_sip(MT6991_UFS_SIP_REFCLK, 1, 1)) != 0) {
			goto failed;
		}
	}
	if ((error = ufs_mtk_check_mt6991_resources()) != 0) {
		goto failed;
	}
	if ((error = ufs_mtk_device_reset()) != 0) {
		goto failed;
	}
	if ((error = ufs_mtk_host_reset()) != 0) {
		goto failed;
	}
	su.failure_stage = "mt6991-hci-config";
	ufs_wr(REG_IE, 0);
	ufs_wr(REG_AHIT, 0);
	if ((error = ufs_mtk_rmw(REG_MTK_XO_UFS, 0, 0x80)) != 0 ||
	    (error = ufs_mtk_rmw(REG_MTK_OPTIONS, (0x7fffu << 8) | (1u << 28),
	    0x453000u | (1u << 25))) != 0) {
		goto failed;
	}
	su.failure_stage = "hce-enable";
	ufs_wr(REG_HCE, 1);
	delay(600);
	if ((error = ufs_wait_reg(REG_HCE, 1, 1, 50000)) != 0) {
		goto failed;
	}
	su.failure_stage = "hce-post-enable-state";
	uint32_t post_hce = ufs_rd(REG_HCE), post_hcs = ufs_rd(REG_HCS);
	if (post_hce == UINT32_MAX || post_hcs == UINT32_MAX) {
		error = ENXIO;
		goto failed;
	}
	if (post_hce != 1 || (post_hcs & ~0xfu) != 0) {
		error = EIO;
		goto failed;
	}
	const uint32_t post_enable_idle_regs[] = {
		REG_UTRLBA, REG_UTRLBAU, REG_UTRLDBR, REG_UTRLRSR,
		REG_UTMRLBA, REG_UTMRLBAU, REG_UTMRLDBR, REG_UTMRLRSR, REG_MEM_CFG, REG_IE
	};
	for (unsigned i = 0; i < sizeof(post_enable_idle_regs) / sizeof(post_enable_idle_regs[0]); i++) {
		uint32_t value = ufs_rd(post_enable_idle_regs[i]);
		if (value == UINT32_MAX || value != 0) {
			error = value == UINT32_MAX ? ENXIO : EBUSY;
			goto failed;
		}
	}
	su.failure_stage = "uic-local-version";
	uint32_t local_version = 0;
	if ((error = ufs_mtk_uic("uic-local-version", 1, 0x15a9, &local_version)) != 0) { /* DME_GET */
		goto failed;
	}
	printf("SWEET UFS: UFS local version=0x%x\n", local_version);
	su.failure_stage = "uic-prelink-lcc";
	uint32_t value = 0;
	if ((error = ufs_mtk_uic("uic-prelink-lcc", 2, 0xd0a8, &value)) != 0) {
		goto failed;
	}
	su.failure_stage = "uic-prelink-hibern8";
	value = 0;
	if ((error = ufs_mtk_uic("uic-prelink-hibern8", 2, 0x155e, &value)) != 0) {
		goto failed;
	}
	su.failure_stage = "uic-prelink-deep-stall";
	if ((error = ufs_mtk_dme_rmw("uic-prelink-deep-stall-get", "uic-prelink-deep-stall-set",
	    0xd0a6, 1u << 6, 0)) != 0) {
		goto failed;
	}
	su.failure_stage = "uic-link-startup";
	value = 0;
	if ((error = ufs_mtk_uic("uic-link-startup", 0x16, 0, &value)) != 0) { /* DME_LINK_STARTUP */
		goto failed;
	}
	su.failure_stage = "uic-link-device-present";
	if ((error = ufs_wait_reg(REG_HCS, HCS_DP, HCS_DP, 50000)) != 0) {
		goto failed;
	}
	su.failure_stage = "uic-postlink-pa-init";
	if ((error = ufs_mtk_dme_rmw("uic-postlink-pa-init-get", "uic-postlink-pa-init-set",
	    0xd09e, 0, 0x100)) != 0 ||
	    (error = ufs_mtk_dme_rmw("uic-postlink-deep-stall-get", "uic-postlink-deep-stall-set",
	    0xd0a6, 0, 0xd)) != 0 ||
	    (error = ufs_mtk_dme_rmw("uic-postlink-config-get", "uic-postlink-config-set",
	    0xd0a1, 1u << 5, 0)) != 0) {
		goto failed;
	}
	return 0;

failed:
	{
		const char *failed_stage = su.failure_stage;
		int stop_error = ufs_mtk_stop_host();
		su.failure_stage = failed_stage;
		printf("SWEET UFS: MT6991 host startup failed stage=%s error=%d stop=%d\n",
		    failed_stage, error, stop_error);
	}
	return error;
}

static int
ufs_mtk_install_transfer_list(uint64_t list)
{
	int error;
	uint32_t hce, hcs, utrl_dbr, utmrl_dbr, value;
	su.failure_stage = "utrl-idle-check";
	hce = ufs_rd(REG_HCE);
	hcs = ufs_rd(REG_HCS);
	utrl_dbr = ufs_rd(REG_UTRLDBR);
	utmrl_dbr = ufs_rd(REG_UTMRLDBR);
	if (hce == UINT32_MAX || hcs == UINT32_MAX || utrl_dbr == UINT32_MAX ||
	    utmrl_dbr == UINT32_MAX) {
		return ENXIO;
	}
	if (hce != 1 || !(hcs & HCS_DP) || utrl_dbr != 0 || utmrl_dbr != 0) {
		return EBUSY;
	}
	su.failure_stage = "utrl-list-stop";
	ufs_wr(REG_IE, 0);
	ufs_wr(REG_UTRLRSR, 0);
	ufs_wr(REG_UTMRLRSR, 0);
	uint32_t utrl_rsr = ufs_rd(REG_UTRLRSR), utmrl_rsr = ufs_rd(REG_UTMRLRSR);
	uint32_t ie = ufs_rd(REG_IE);
	if (utrl_rsr == UINT32_MAX || utmrl_rsr == UINT32_MAX || ie == UINT32_MAX) {
		return ENXIO;
	}
	if (utrl_rsr != 0 || utmrl_rsr != 0 || ie != 0) {
		return EIO;
	}
	ufs_wr(REG_UTMRLBA, 0);
	ufs_wr(REG_UTMRLBAU, 0);
	ufs_wr(REG_UTRLBA, (uint32_t)list);
	ufs_wr(REG_UTRLBAU, (uint32_t)(list >> 32));
	uint32_t utrlba = ufs_rd(REG_UTRLBA), utrlbau = ufs_rd(REG_UTRLBAU);
	uint32_t utmrlba = ufs_rd(REG_UTMRLBA), utmrlbau = ufs_rd(REG_UTMRLBAU);
	if (utrlba == UINT32_MAX || utrlbau == UINT32_MAX || utmrlba == UINT32_MAX ||
	    utmrlbau == UINT32_MAX) {
		return ENXIO;
	}
	if (utrlba != (uint32_t)list || utrlbau != (uint32_t)(list >> 32) ||
	    utmrlba != 0 || utmrlbau != 0) {
		return EIO;
	}
	__builtin_arm_dsb(0xf);
	su.failure_stage = "utrl-list-start";
	ufs_wr(REG_UTRLRSR, 1);
	error = ufs_wait_reg(REG_HCS, HCS_UTRLRDY, HCS_UTRLRDY, 50000);
	if (error) {
		return error;
	}
	value = ufs_rd(REG_UTRLRSR);
	hce = ufs_rd(REG_HCE);
	hcs = ufs_rd(REG_HCS);
	utrlba = ufs_rd(REG_UTRLBA);
	utrlbau = ufs_rd(REG_UTRLBAU);
	if (value == UINT32_MAX || hce == UINT32_MAX || hcs == UINT32_MAX ||
	    utrlba == UINT32_MAX || utrlbau == UINT32_MAX) {
		return ENXIO;
	}
	if (value != 1 || hce != 1 || !(hcs & HCS_DP) ||
	    utrlba != (uint32_t)list || utrlbau != (uint32_t)(list >> 32)) {
		printf("SWEET UFS: controller did not accept XNU transfer list rsr=0x%x\n", value);
		return EIO;
	}
	return 0;
}

/* Read the MT6991 AO clocks before touching HCI registers, like HackIOS. */
static int
ufs_mtk_check_mt6991_resources(void)
{
	su.failure_stage = "ao-gate-read";
	uint32_t gate0 = ufs_ao_rd(DALI_UFS_AO_GATE0);
	uint32_t gate1 = ufs_ao_rd(DALI_UFS_AO_GATE1);
	if (gate0 == UINT32_MAX || gate1 == UINT32_MAX) {
		printf("SWEET UFS: invalid MT6991 AO MMIO gate0=0x%x gate1=0x%x\n", gate0, gate1);
		return ENXIO;
	}
	printf("SWEET UFS: MT6991 AO gate0=0x%x gate1=0x%x\n", gate0, gate1);
	su.failure_stage = "ao-gates-enabled";
	if ((gate0 & 0x3) || (gate1 & 0x11f)) {
		printf("SWEET UFS: MT6991 HCI/UniPro clock gates are off\n");
		return ENXIO;
	}
	su.failure_stage = "mtk-refclk-read";
	uint32_t ip_version = ufs_rd(REG_MTK_IP_VERSION);
	uint32_t ref_clock = ufs_rd(REG_MTK_REF_CLOCK);
	uint32_t xo_ufs = ufs_rd(REG_MTK_XO_UFS);
	uint32_t options = ufs_rd(REG_MTK_OPTIONS);
	uint32_t outstanding = ufs_rd(REG_MTK_OUTSTANDING);
	printf("SWEET UFS: MT6991 ip=0x%x refclk=0x%x xo=0x%x options=0x%x outstanding=0x%x\n",
	    ip_version, ref_clock, xo_ufs, options, outstanding);
	su.failure_stage = "mtk-refclk-check";
		if (ip_version == UINT32_MAX || ref_clock == UINT32_MAX || xo_ufs == UINT32_MAX ||
		    options == UINT32_MAX || outstanding == UINT32_MAX || ip_version != 0x10470000u ||
		    (ref_clock & 3) != 3) {
		printf("SWEET UFS: MT6991 resources are not in the expected active state\n");
		return ENXIO;
	}
	return 0;
}

static int
ufs_take_over_host(void)
{
	if (su.dali) {
		su.failure_stage = "mt6991-resource-check";
		int error = ufs_mtk_check_mt6991_resources();
		if (error) {
			return error;
		}
	}
	uint32_t version = ufs_rd(REG_VER), cap = ufs_rd(REG_CAP);
	uint32_t hce = ufs_rd(REG_HCE), hcs = ufs_rd(REG_HCS);
	ufs_log_host_state("pre-takeover");
	su.failure_stage = "hci-identity";
	if (su.dali && (version != MT6991_UFS_VERSION || cap != MT6991_UFS_CAP)) {
		printf("SWEET UFS: MT6991 HCI identity mismatch: ver=0x%x cap=0x%x expected=0x%x/0x%x\n",
		    version, cap, MT6991_UFS_VERSION, MT6991_UFS_CAP);
		return ENXIO;
	}
	/* Dali LK can hand off a fully stopped HCI; initialize only that exact idle state. */
	if (su.dali) {
		if (hce == UINT32_MAX || hcs == UINT32_MAX) {
			return ENXIO;
		}
		if (((su.dma_pa + DMA_SIZE - 1) >> 32) && !(cap & CAP_64AS)) {
			printf("SWEET UFS: DMA memory at 0x%llx but the host has no 64-bit addressing\n",
			    su.dma_pa);
			return ENXIO;
		}
		su.failure_stage = "hci-stopped-state";
		if (hce != 0) {
			printf("SWEET UFS: refusing MT6991 startup from active/invalid HCE=0x%x HCS=0x%x\n",
			    hce, hcs);
			return EBUSY;
		}
		int error = ufs_mtk_start_host();
		if (error) {
			return error;
		}
		su.failure_stage = "utrl-idle-check";
		uint32_t utrl_dbr = ufs_rd(REG_UTRLDBR), utmrl_dbr = ufs_rd(REG_UTMRLDBR);
		if (utrl_dbr == UINT32_MAX || utmrl_dbr == UINT32_MAX) {
			error = ENXIO;
		} else if (utrl_dbr != 0 || utmrl_dbr != 0) {
			error = EBUSY;
		}
		if (error) {
			const char *failed_stage = su.failure_stage;
			int stop_error = ufs_mtk_stop_host();
			su.failure_stage = failed_stage;
			printf("SWEET UFS: request list was not idle before takeover dbr=0x%x/0x%x stop=%d\n",
			    utrl_dbr, utmrl_dbr, stop_error);
			return error;
		}
		uint64_t list = su.dma_pa + DMA_UTRL;
		su.failure_stage = "utrl-list-install";
		error = ufs_mtk_install_transfer_list(list);
		if (error) {
			const char *failed_stage = su.failure_stage;
			int stop_error = ufs_mtk_stop_host();
			su.failure_stage = failed_stage;
			printf("SWEET UFS: transfer-list install failed stage=%s error=%d stop=%d\n",
			    failed_stage, error, stop_error);
			return error;
		}
		ufs_log_host_state("post-takeover");
		return 0;
	}
	su.failure_stage = "hci-ready";
	if (!(hce & 1) || !(hcs & HCS_DP) || !(hcs & HCS_UTRLRDY)) {
		printf("SWEET UFS: host not ready (hce 0x%x hcs 0x%x)\n", hce, hcs);
		return ENXIO;
	}
	su.failure_stage = "doorbells-idle";
	if (ufs_rd(REG_UTRLDBR) || ufs_rd(REG_UTMRLDBR)) {
		printf("SWEET UFS: transfer/task requests outstanding from the bootloader\n");
		return EBUSY;
	}
	/* With RAM above 4 GiB (mkadt.py --highmem) the DMA memory can be there too. */
	su.failure_stage = "dma-64as-check";
	if (((su.dma_pa + DMA_SIZE - 1) >> 32) && !(cap & CAP_64AS)) {
		printf("SWEET UFS: DMA memory at 0x%llx but the host has no 64-bit addressing\n",
		    su.dma_pa);
		return ENXIO;
	}
	ufs_wr(REG_IE, 0);                      /* polled operation */
	ufs_wr(REG_IS, ufs_rd(REG_IS));
	ufs_wr(REG_UTRLRSR, 0);
	uint64_t list = su.dma_pa + DMA_UTRL;
	ufs_wr(REG_UTRLBA, (uint32_t)list);
	ufs_wr(REG_UTRLBAU, (uint32_t)(list >> 32));
	ufs_wr(REG_UTRLRSR, 1);
	/* Never ring a doorbell unless the host uses our list. */
	su.failure_stage = "utrl-ownership-check";
	if (ufs_rd(REG_UTRLBA) != (uint32_t)list || ufs_rd(REG_UTRLBAU) != (uint32_t)(list >> 32) ||
	    !(ufs_rd(REG_UTRLRSR) & 1)) {
		printf("SWEET UFS: host did not take our request list\n");
		return EIO;
	}
	ufs_log_host_state("post-takeover");
	return 0;
}

static int
sweet_ufs_open(__unused dev_t dev, int flags, __unused int devtype, __unused struct proc *p)
{
	if (!su.ready) {
		return ENXIO;
	}
	return ((flags & FWRITE) && !su.writable) ? EROFS : 0;
}

static int
sweet_ufs_close(__unused dev_t dev, __unused int flags, __unused int devtype, __unused struct proc *p)
{
	return 0;
}

static void
sweet_ufs_strategy(buf_t bp)
{
	int error = 0;
	caddr_t vaddr = NULL;
	uint64_t off = (uint64_t)buf_blkno(bp) * su.secsize;
	uint32_t count = buf_count(bp);
	uint64_t part_bytes = su.nblocks * UFS_BLOCK;

	bool rd = buf_flags(bp) & B_READ;

	buf_setresid(bp, count);
	if (!rd && !su.writable) {
		error = EROFS;
		goto done;
	}
	if (off > part_bytes || count > part_bytes - off) {
		error = EINVAL;
		goto done;
	}
	if (count == 0) {
		goto done;
	}
	if (buf_map(bp, &vaddr)) {
		error = EIO;
		goto done;
	}

#if DEVELOPMENT
    bool observe = sweet_ufs_load_open();
    uint64_t begin = observe ? mach_absolute_time() : 0;
#endif
	lck_mtx_lock(&sweet_ufs_lock);
#if DEVELOPMENT
    uint64_t acquired = observe ? mach_absolute_time() : 0;
#endif
	/* A poisoned command may still own DMA; do not touch its bounce buffer. */
	if (su.dali && su.command_poisoned) {
		error = ETIMEDOUT;
	}
	for (uint32_t done = 0; done < count && !error;) {
		uint64_t pos = off + done;
		uint64_t lba = pos / UFS_BLOCK;
		uint32_t skip = (uint32_t)(pos % UFS_BLOCK);
		uint32_t want = MIN(count - done, BOUNCE_SIZE - skip);
		uint32_t blocks = (skip + want + UFS_BLOCK - 1) / UFS_BLOCK;
		void *bounce = (void *)(su.dma + DMA_BOUNCE);
		if (rd) {
			error = ufs_read_blocks(su.first_block + lba, blocks);
			if (!error) {
				memcpy(vaddr + done, (const char *)bounce + skip, want);
			}
		} else {
			/* Partial device blocks at either end: read, merge, write back. */
			if (skip || (want % UFS_BLOCK)) {
				error = ufs_read_blocks(su.first_block + lba, blocks);
			}
			if (!error) {
				memcpy((char *)bounce + skip, vaddr + done, want);
				error = ufs_write_blocks(su.first_block + lba, blocks);
			}
		}
		if (error) {
			break;
		}
		done += want;
		buf_setresid(bp, count - done);
	}
#if DEVELOPMENT
    if (observe && sweet_ufs_load_open()) {
        uint64_t end = mach_absolute_time();
        if (sweet_ufs_load_open()) {
            unsigned int kind = rd ? 3 : 4;
            sweet_ufs_load_record(&sweet_ufs_load_counts, kind, 0, 0, begin, acquired, end);
            sweet_ufs_load_publish(kind, end);
        }
    }
#endif
	lck_mtx_unlock(&sweet_ufs_lock);
	buf_unmap(bp);

done:
	if (error) {
		buf_seterror(bp, error);
	}
	buf_biodone(bp);
}

static int
sweet_ufs_ioctl(__unused dev_t dev, u_long cmd, caddr_t data, __unused int flag,
    __unused struct proc *p, int is_char)
{
	uint32_t *f = (uint32_t *)data;
	uint64_t *o = (uint64_t *)data;

	switch (cmd) {
	case DKIOCGETBLOCKSIZE:
		*f = su.secsize;
		return 0;
	case DKIOCSETBLOCKSIZE:
		if (is_char) {
			return ENODEV;
		}
		if (*f < DEV_BSIZE || *f > UFS_BLOCK || (*f & (*f - 1))) {
			return EINVAL;
		}
		su.secsize = *f;
		return 0;
	case DKIOCGETPHYSICALBLOCKSIZE:
		*f = UFS_BLOCK;
		return 0;
	case DKIOCGETBLOCKCOUNT:
		*o = su.nblocks * UFS_BLOCK / su.secsize;
		return 0;
	case DKIOCISWRITABLE:
		*f = su.writable;
		return 0;
	case DKIOCSYNCHRONIZE:
	case DKIOCSYNCHRONIZECACHE: {
		if (!su.writable) {
			return 0;
		}
		lck_mtx_lock(&sweet_ufs_lock);
		int error = ufs_sync_cache();
		lck_mtx_unlock(&sweet_ufs_lock);
		return error;
	}
	case DKIOCGETFEATURES:
		/* No FUA, barrier, or UNMAP support is exposed. */
		*f = 0;
		return 0;
	case DKIOCGETMAXBLOCKCOUNTREAD:
		*o = BOUNCE_SIZE / su.secsize;
		return 0;
	case DKIOCGETMAXBLOCKCOUNTWRITE:
		*o = su.writable ? BOUNCE_SIZE / su.secsize : 0;
		return 0;
	case DKIOCGETMAXBYTECOUNTREAD:
		*o = BOUNCE_SIZE;
		return 0;
	case DKIOCGETMAXBYTECOUNTWRITE:
		*o = su.writable ? BOUNCE_SIZE : 0;
		return 0;
	case DKIOCGETMAXSEGMENTCOUNTREAD:
	case DKIOCGETMAXSEGMENTCOUNTWRITE:
		/* buf_map and the bounce copy accept noncontiguous input pages. */
		*o = BOUNCE_SIZE / PAGE_SIZE + 1;
		return 0;
	case DKIOCGETMAXSEGMENTBYTECOUNTREAD:
	case DKIOCGETMAXSEGMENTBYTECOUNTWRITE:
		*o = BOUNCE_SIZE;
		return 0;
	case DKIOCGETMINSEGMENTALIGNMENTBYTECOUNT:
		*o = 1;
		return 0;
	case DKIOCGETCOMMANDPOOLSIZE:
		*f = 1;
		return 0;
	default:
		return ENOTTY;
	}
}

static int
sweet_ufs_bioctl(dev_t dev, u_long cmd, caddr_t data, int flag, struct proc *p)
{
	return sweet_ufs_ioctl(dev, cmd, data, flag, p, 0);
}

static int
sweet_ufs_cioctl(dev_t dev, u_long cmd, caddr_t data, int flag, struct proc *p)
{
	return sweet_ufs_ioctl(dev, cmd, data, flag, p, 1);
}

static int
sweet_ufs_psize(__unused dev_t dev)
{
	return su.ready ? (int)MIN(su.nblocks * UFS_BLOCK / su.secsize, INT_MAX) : -1;
}

static int
sweet_ufs_read(dev_t dev, struct uio *uio, __unused int ioflag)
{
	return physio(sweet_ufs_strategy, NULL, dev, B_READ, minphys, uio, (int)su.secsize);
}

static int
sweet_ufs_write(dev_t dev, struct uio *uio, __unused int ioflag)
{
	if (!su.writable) {
		return EROFS;
	}
	return physio(sweet_ufs_strategy, NULL, dev, B_WRITE, minphys, uio, (int)su.secsize);
}

static const struct bdevsw sweet_ufs_bdevsw = {
	.d_open     = sweet_ufs_open,
	.d_close    = sweet_ufs_close,
	.d_strategy = sweet_ufs_strategy,
	.d_ioctl    = sweet_ufs_bioctl,
	.d_dump     = eno_dump,
	.d_psize    = sweet_ufs_psize,
	.d_type     = D_DISK,
};

static const struct cdevsw sweet_ufs_cdevsw = {
	.d_open       = sweet_ufs_open,
	.d_close      = sweet_ufs_close,
	.d_read       = sweet_ufs_read,
	.d_write      = sweet_ufs_write,
	.d_ioctl      = sweet_ufs_cioctl,
	.d_stop       = eno_stop,
	.d_reset      = eno_reset,
	.d_ttys       = NULL,
	.d_select     = eno_select,
	.d_mmap       = eno_mmap,
	.d_strategy   = eno_strat,
	.d_reserved_1 = eno_getc,
	.d_reserved_2 = eno_putc,
	.d_type       = D_DISK,
};

#include "SweetUFSSpeedRead.h"

static int
sweet_ufs_init(void)
{
	DTEntry node;
	uintptr_t const *reg = NULL;
	uintptr_t const *ao_reg = NULL;
	uint64_t const *log_offset_prop = NULL;
	unsigned int size = 0;
	unsigned int ao_size = 0;
	unsigned int log_offset_size = 0;
	char want[64] = "userdata";
	char log_want[64] = "";
	const char *compatible = NULL;
	unsigned int compatible_size = 0;
	uint32_t lun = 0;
	uint32_t const *lun_prop = NULL;
	unsigned int lun_size = 0;
	uint8_t log_pwm_digest[SHA256_DIGEST_LENGTH];
	uint64_t log_scan_until = 0;
	bool log_slot_optin = false;
	int error;

	su.failure_stage = "adt-node";
	if (SecureDTLookupEntry(NULL, "/sweet-ufs", &node) != kSuccess ||
	    SecureDTGetProperty(node, "reg", (void const **)&reg, &size) != kSuccess ||
	    size < 2 * sizeof(uintptr_t)) {
		printf("SWEET UFS: no /sweet-ufs node in the device tree\n");
		return ENXIO;
	}
	su.failure_stage = "adt-properties";
	if (ufs_dt_string(node, "root-partition", want, sizeof(want), "userdata") ||
	    ufs_dt_string(node, "log-partition", log_want, sizeof(log_want), "")) {
		printf("SWEET UFS: invalid partition-name property\n");
		return EINVAL;
	}
	PE_parse_boot_argn("ufsroot", want, sizeof(want));
	uint32_t rw = 0;
	PE_parse_boot_argn("ufsrw", &rw, sizeof(rw));
	/* Neither probe persists a UFS log; avoid even probing the log area. */
	if (su.speed_probe) log_want[0] = '\0';
	uint32_t cache = 0, wait = 0;
	PE_parse_boot_argn("ufscache", &cache, sizeof(cache));
	PE_parse_boot_argn("ufswait", &wait, sizeof(wait));
	su.cache = (cache == 1);
	su.wait = (wait == 1);
	su.log_partition_configured = log_want[0] != '\0';
	if (su.log_partition_configured &&
	    strlcpy(su.log_partition_name, log_want, sizeof(su.log_partition_name)) >=
	    sizeof(su.log_partition_name)) {
		printf("SWEET UFS: log partition name is too long\n");
		return EINVAL;
	}
	if (SecureDTGetProperty(node, "compatible", (void const **)&compatible,
	    &compatible_size) == kSuccess &&
	    compatible != NULL &&
	    compatible_size >= sizeof("mediatek,mt6991-ufshc") &&
	    memcmp(compatible, "mediatek,mt6991-ufshc", sizeof("mediatek,mt6991-ufshc")) == 0) {
		su.dali = true;
	}
	if (su.dali && su.hsg4_root) {
		uint32_t logger = 0, slots = 0;
		log_slot_optin = ufs_dali_root_boot_bool("sweetlog", &logger) && logger == 1 &&
		    ufs_dali_root_boot_bool("sweetlogslots", &slots) && slots == 1;
		if (logger == 1 && !log_slot_optin)
			printf("SWEET LOG: explicit sweetlogslots=1 required; logging disabled\n");
	}
	su.spin_us = ufs_dali_spin_us(node, su.dali);
	if (su.dali && su.wait) {
		printf("SWEET UFS: bounded command poll spin=%u us\n", su.spin_us);
	}
	if (su.dali && strcmp(want, "unixnu_data") != 0) {
		su.failure_stage = "dali-root-partition";
		printf("SWEET UFS: refusing Dali root partition \"%s\"; only unixnu_data is allowed\n", want);
		return EINVAL;
	}
	if (su.dali && su.log_partition_configured && strcmp(log_want, "unixnu_logs") != 0) {
		printf("SWEET UFS: refusing Dali log partition \"%s\"; only unixnu_logs is allowed\n",
		    log_want);
		su.log_partition_configured = false;
		su.log_partition_name[0] = '\0';
		log_want[0] = '\0';
	}
	if (su.dali) {
		su.failure_stage = "dali-log-offset";
		bool offset_present = SecureDTGetProperty(node, "log-offset", (void const **)&log_offset_prop,
		    &log_offset_size) == kSuccess && log_offset_prop != NULL &&
		    log_offset_size == sizeof(su.log_offset_bytes);
		if (offset_present) memcpy(&su.log_offset_bytes, log_offset_prop, sizeof(su.log_offset_bytes));
		bool offset_valid = offset_present && su.log_offset_bytes % UFS_BLOCK == 0 &&
		    (!su.hsg4_root || su.log_offset_bytes == DALI_LOG_OFFSET_BYTES);
		if (!offset_valid) {
			if (!su.hsg4_root) return offset_present ? EINVAL : ENXIO;
			/* Optional logging metadata must not prevent the normal G4 root. */
			printf("SWEET LOG: invalid Dali offset; persistent logging disabled\n");
			su.log_partition_configured = false;
			su.log_partition_name[0] = log_want[0] = '\0';
		} else {
			printf("SWEET UFS: Dali log data offset=%llu MiB\n", su.log_offset_bytes >> 20);
		}
	}
	su.failure_stage = "hci-range";
	if (su.dali && (reg[0] != DALI_UFS_HCI_BASE ||
	    reg[1] < REG_MTK_IP_VERSION + sizeof(uint32_t))) {
		printf("SWEET UFS: invalid MT6991 HCI range base=0x%llx size=0x%llx\n",
		    (uint64_t)reg[0], (uint64_t)reg[1]);
		return EINVAL;
	}

	su.failure_stage = "hci-map";
	su.regs = ml_io_map(reg[0], reg[1]);
	if (su.regs == 0) {
		printf("SWEET UFS: cannot map HCI registers\n");
		return ENOMEM;
	}
	if (su.dali) {
		su.failure_stage = "ao-reg-property";
		if (SecureDTGetProperty(node, "ao-reg", (void const **)&ao_reg, &ao_size) != kSuccess ||
		    ao_reg == NULL || ao_size < 2 * sizeof(uintptr_t) ||
		    ao_reg[0] != DALI_UFS_AO_BASE ||
		    ao_reg[1] < DALI_UFS_AO_GATE0 + sizeof(uint32_t)) {
			printf("SWEET UFS: missing MT6991 AO register range\n");
			return ENXIO;
		}
		su.failure_stage = "ao-map";
		su.ao_regs = ml_io_map(ao_reg[0], ao_reg[1]);
		if (su.ao_regs == 0) {
			printf("SWEET UFS: cannot map MT6991 AO registers\n");
			return ENOMEM;
		}
	}
	su.failure_stage = "dma-allocate";
	if (kmem_alloc_contig(kernel_map, &su.dma, DMA_SIZE, PAGE_MASK, 0, 0,
	    KMA_DATA | KMA_ZERO | KMA_PERMANENT, VM_KERN_MEMORY_IOKIT) != KERN_SUCCESS) {
		printf("SWEET UFS: no contiguous DMA memory\n");
		return ENOMEM;
	}
	su.failure_stage = "dma-physical-address";
	ppnum_t pn = pmap_find_phys(kernel_pmap, (addr64_t)su.dma);
	if (!pn) {
		return ENOMEM;
	}
	su.dma_pa = ptoa_64(pn);
	su.failure_stage = "lun-property";
	if (SecureDTGetProperty(node, "lun", (void const **)&lun_prop,
	    &lun_size) == kSuccess) {
		if (lun_size < sizeof(*lun_prop)) {
			printf("SWEET UFS: invalid LUN property size %u\n", lun_size);
			return EINVAL;
		}
		memcpy(&lun, lun_prop, sizeof(lun));
		if (lun > 0xff) {
			printf("SWEET UFS: invalid LUN %u\n", lun);
			return EINVAL;
		}
	}
	su.lun = (uint8_t)lun;
	su.secsize = DEV_BSIZE;

	/* Allocate before the UFS lock; probe mode keeps its old permanent 4/64MiB
	 * allocation. G4 root uses a temporary 4MiB CPU-only comparison buffer; the
	 * controller DMA points at the separate permanent bounce buffer, never this
	 * reference, so it remains safe to free even if a command times out.
	 */
	vm_offset_t speed_reference = 0;
	size_t reference_size = 0;
	uint64_t speed_until = 0;
	struct dali_speed_sample speed_before = {0}, speed_after = {0};
	if (su.speed_probe || su.hsg4_root) {
		su.failure_stage = "speed-probe-allocate";
		reference_size = su.speed_extended ? DALI_EXT_WINDOW_BYTES : DALI_SPEED_BYTES;
		if (kmem_alloc(kernel_map, &speed_reference, reference_size,
		    KMA_DATA | KMA_ZERO | (su.speed_probe ? KMA_PERMANENT : 0),
		    VM_KERN_MEMORY_IOKIT) != KERN_SUCCESS)
			return ENOMEM;
		/* Preserve the legacy start point unless the optional log scan is on. */
		if (!log_slot_optin &&
		    !ufs_dali_deadline_after(DALI_ROOT_COMPARE_TIMEOUT_NS, &speed_until)) {
			if (su.hsg4_root) kmem_free(kernel_map, speed_reference, reference_size);
			return ETIMEDOUT;
		}
	}

	lck_mtx_lock(&sweet_ufs_lock);
	su.failure_stage = "host-takeover";
	error = ufs_take_over_host();
	if (!error) {
		if (su.dali) {
			error = ufs_dali_prepare_lun();
		} else {
			su.failure_stage = "scsi-tur";
			static const uint8_t tur[6] = { 0x00 };
			/* The first command after the bootloader may report a unit attention. */
			for (int i = 0; i < 3 && (error = ufs_scsi(tur, sizeof(tur), 0, false)); i++) {
			}
		}
	}
	if (!error) {
        uint32_t pwm_requested = 0;
        (void)PE_parse_boot_argn("ufspwm", &pwm_requested, sizeof(pwm_requested));
        /* Reached only after the exact Dali stopped-host cold takeover succeeded. */
        uint32_t pwm_cap = SWEET_PWM_PLATFORM_CAP;
        if (pwm_requested == 1 && su.dali && pwm_cap > 1)
            pwm_cap = ufs_pwm_clock_cap();
        error = ufs_dali_prepare_pwm(pwm_requested, su.dali, pwm_cap);
    }
	if (!error && su.pwm_two_lanes) {
		su.failure_stage = "pwm-two-lanes";
		error = ufs_dali_prepare_two_lanes(true);
	}
	if (!error) {
		su.failure_stage = "read-capacity";
		error = ufs_read_capacity();
	}
	if (!error) {
		su.failure_stage = "gpt-discovery";
		error = ufs_find_partitions(want, log_want);
	}
	if (!error && log_slot_optin && su.log_nblocks >= SWEET_LOG_BLOCKS) {
		su.failure_stage = "pwm-log-gpt-read";
		if (!ufs_dali_deadline_after(DALI_LOG_SCAN_TIMEOUT_NS, &log_scan_until)) {
			error = ETIMEDOUT;
		} else {
			error = ufs_dali_validate_log_gpt();
			if (!error && mach_absolute_time() >= log_scan_until) error = ETIMEDOUT;
			if (!error && su.log_nblocks >= SWEET_LOG_BLOCKS) {
				bool maybe = false;
				su.failure_stage = "pwm-log-candidates";
				error = ufs_dali_log_candidate_present(&maybe, log_scan_until);
				if (!error && !maybe) {
					su.log_first_block = su.log_nblocks = 0;
					printf("SWEET LOG: no usable candidate; persistent logging disabled\n");
				} else if (!error) {
					su.failure_stage = "pwm-log-digest";
					error = ufs_dali_log_pwm_digest(log_pwm_digest, log_scan_until);
				}
			}
		}
		/* A completed PWM EIO with no poisoned command or sticky link
		 * failure affects optional logging only. Timeouts and protocol
		 * contradictions still stop root publication.
		 */
		if (error == EIO && !su.command_poisoned && !ufs_pwm_reentry_error()) {
			su.log_first_block = su.log_nblocks = 0;
			su.log_partition_ready = false;
			printf("SWEET LOG: completed PWM read failed; logging disabled\n");
			error = 0;
		}
	}
	/* Give the opt-in comparison its own budget after optional logging reads. */
	if (!error && log_slot_optin &&
	    !ufs_dali_deadline_after(DALI_ROOT_COMPARE_TIMEOUT_NS, &speed_until)) {
		su.failure_stage = "hsg4-root-compare-deadline";
		error = ETIMEDOUT;
	}
	if (!error && (su.speed_probe || su.hsg4_root)) {
		su.failure_stage = su.hsg4_root ? "hsg4-root-pwm-read" : "speed-probe-pwm-read";
		error = ufs_dali_speed_read_pass((void *)speed_reference, DALI_SPEED_BYTES,
		    false, speed_until, &speed_before);
	}
	if (!error && su.hsg1_requested) {
		su.failure_stage = "hsg1-transition";
		error = ufs_dali_prepare_hsg1(true);
		if (!error) su.hsg1_active = true;
	}
	if (!error && su.hsg4_requested) {
		su.failure_stage = "hsg4x2-transition";
		error = ufs_dali_prepare_hsg4(true);
		if (!error) su.hsg1_active = true; /* Same retained-floor command guard. */
	}
	if (!error && (su.speed_probe || su.hsg4_root)) {
		su.failure_stage = su.hsg4_root ? "hsg4-root-hs-read" : "speed-probe-hs-read";
		error = ufs_dali_speed_read_pass((void *)speed_reference, DALI_SPEED_BYTES,
		    true, speed_until, &speed_after);
		if (error) {
			if (su.hsg4_requested && su.hsg1_active) ufs_hsg4_post_probe_error(error);
			(void)ufs_pwm_failure(error);
		}
	}
	if (!error && su.hsg4_root) {
		su.hsg4_root_verified = true;
		printf("SWEET UFS HSG4 ROOT: compare=1 bytes=%u root-mounted=0 root-writes=0 ufs-log-writes=0 physical-write-stability=unproven\n",
		    DALI_SPEED_BYTES);
		if (log_slot_optin && su.log_nblocks >= SWEET_LOG_BLOCKS) {
			su.failure_stage = "hsg4-log-gpt-read";
			if (!ufs_dali_deadline_after(DALI_LOG_SCAN_TIMEOUT_NS, &log_scan_until)) {
				error = ETIMEDOUT;
			} else {
				error = ufs_dali_validate_log_gpt();
				if (!error && mach_absolute_time() >= log_scan_until) error = ETIMEDOUT;
				/* This GPT already validated under PWM. A newly invalid G4
				 * reread is evidence of inconsistent storage, not a mere
				 * optional logging configuration failure.
				 */
				if (!error && su.log_nblocks < SWEET_LOG_BLOCKS) error = EIO;
				if (!error) {
					su.failure_stage = "hsg4-log-slot-read";
					error = ufs_dali_select_log_slot(log_pwm_digest, log_scan_until);
				}
			}
			if (error) (void)ufs_pwm_failure(error);
		}
	}
	if (!error && su.speed_probe) {
		printf("SWEET UFS SPEED: v=1 bytes=%llu pwm-ticks=%llu hs-ticks=%llu "
		    "commands=%u/%u exact-byte-match=1 root-mounted=0 root-writes=0\n",
		    (unsigned long long)speed_after.bytes,
		    (unsigned long long)(speed_before.end - speed_before.begin),
		    (unsigned long long)(speed_after.end - speed_after.begin),
		    speed_before.commands, speed_after.commands);
	}
	if (!error && su.speed_extended) {
		su.failure_stage = "speed-probe-extended";
		for (unsigned int window = 0; window < DALI_EXT_WINDOWS && !error; ++window) {
			for (unsigned int pass = 0; pass < 2 && !error; ++pass) {
				struct dali_speed_sample sample = {0};
				error = ufs_dali_extended_read_pass((void *)speed_reference,
				    DALI_EXT_WINDOW_BYTES, window, pass != 0, speed_until, &sample);
				uint64_t ticks = sample.end >= sample.begin ? sample.end - sample.begin : 0;
				uint64_t ns = 0;
				absolutetime_to_nanoseconds(ticks, &ns);
				printf("SWEET UFS EXT: v=1 window=%u pass=%u bytes=%llu commands=%u "
				    "ticks=%llu ns=%llu error=%d poisoned=%u\n", window, pass,
				    (unsigned long long)sample.bytes, sample.commands,
				    (unsigned long long)ticks, (unsigned long long)ns,
				    error, su.command_poisoned ? 1u : 0u);
			}
		}
		if (error) {
			if (su.hsg4_requested && su.hsg1_active) ufs_hsg4_post_probe_error(error);
			(void)ufs_pwm_failure(error);
		} else printf("SWEET UFS EXT: v=1 complete=1 windows=%u window-bytes=%u "
		    "exact-byte-match=1 root-mounted=0 data-writes=0 ufs-log-writes=0\n",
		    DALI_EXT_WINDOWS, DALI_EXT_WINDOW_BYTES);
	}
	/* Retained RAM console only: neither probe calls the UFS log writer. */
	if (!error && su.speed_probe) su.speed_probe_done = true;
	lck_mtx_unlock(&sweet_ufs_lock);
	if (su.hsg4_root && speed_reference)
		kmem_free(kernel_map, speed_reference, reference_size);
	if (error) {
		return error;
	}
	if (su.speed_probe) {
		su.failure_stage = "speed-probe-complete";
		return ECANCELED; /* Stop before device nodes, ready, threads or root mount. */
	}

	su.failure_stage = "bsd-block-device";
	su.bmajor = bdevsw_add(-1, &sweet_ufs_bdevsw);
	if (su.bmajor < 0) {
		return ENXIO;
	}
	su.failure_stage = "bsd-character-device";
	su.cmajor = cdevsw_add_with_bdev(-1, &sweet_ufs_cdevsw, su.bmajor);
	if (su.cmajor < 0) {
		return ENXIO;
	}
	su.bdev = makedev(su.bmajor, 0);
	devfs_make_node(su.bdev, DEVFS_BLOCK, UID_ROOT, GID_OPERATOR, 0640, "ufsroot0");
	devfs_make_node(makedev(su.cmajor, 0), DEVFS_CHAR, UID_ROOT, GID_OPERATOR, 0640, "rufsroot0");
	/* Root writes require explicit ufsrw=1 and the selected Dali root must be our HFSX volume. */
	su.writable = (rw != 0);
	if (su.dali && su.writable && strcmp(want, "unixnu_data") != 0) {
		printf("SWEET UFS: refusing Dali root writes to unexpected partition \"%s\"\n", want);
		su.writable = false;
	}
	if (su.dali && su.writable) {
		printf("SWEET UFS: Dali root writes explicitly enabled for partition \"%s\"\n", want);
		printf("SWEET UFS: Dali root write prefix cap=%llu blocks\n", su.nblocks);
	}
	su.ready = true;
	su.failure_stage = "root-ready";
	printf("SWEET UFS: \"%s\" on LUN %u: first block 0x%llx, %llu MiB, %s, DMA at 0x%llx\n",
	    want, su.lun, su.first_block, su.nblocks * UFS_BLOCK >> 20,
	    su.writable ? "read-write" : "read-only", su.dma_pa);
	sweet_log_start();
	sweet_sample_start();
	/* Input drivers (HID services in iokit/Kernel/arm/hid/SweetHID.cpp). */
	sweet_keys_init();
	sweet_touch_init();
	if (su.dali) {
		printf("SWEET USB: skipped Qualcomm DWC3 probe on Dali MT6991\n");
	} else {
		sweet_usb_probe();
	}
	return 0;
}

/*
 * Persistent console log ("sweetlog"). Every character that reaches the
 * console is buffered in a 4 MiB ring. A Dali ADT names a separate raw GPT
 * partition for logs; writes are restricted to that partition's bounds and
 * never require ufsrw=1. Non-Dali configurations retain the legacy root-tail
 * log area. Device block 0 of the selected area is a header:
 *   "SWEETLOG" | u32 version 1 | u32 ring size | u64 total bytes written
 *   | u64 flush sequence
 * ring offset of byte n = n % ring size. Read it from a recovery with
 * tools/sweet-log-pull.sh. Disabled with boot-arg sweetlog=0.
 */
#define SWEET_LOG_SECS          10u

static char sweet_log_ring[SWEET_LOG_SIZE];
static uint64_t _Atomic sweet_log_total;
/* Independent cumulative counters; separately sampled, modulo 2^64. */
static uint64_t _Atomic sweet_log_dropped_chunks, sweet_log_dropped_bytes;
/* Console writers never wait. Readers hold this only for bounded memory copies,
 * never over UFS I/O. Writers that collide are deliberately dropped.
 */
static uint32_t _Atomic sweet_log_busy;
#define SWEET_LOG_APPEND_MAX 256u
#define SWEET_LOG_COPY_MAX 4096u
#define SWEET_LOG_BATCH (64u << 10)
/* Exactly one logger thread owns this buffer; it is never a DMA target. */
static char sweet_log_snapshot[SWEET_LOG_BATCH];
static int sweet_log_chan, sweet_log_fair_chan;
static uint32_t sweet_log_secs = SWEET_LOG_SECS;

/* Dali ramoops console region; the ADT carries and validates these ranges. */
#define DALI_RAMOOPS_BASE              0xaec00000ULL
#define DALI_RAMOOPS_SIZE              0x00410000ULL
#define DALI_RAMOOPS_CONSOLE_OFFSET    0x0000f000ULL
#define DALI_RAMOOPS_CONSOLE_SIZE      0x00200000ULL
#define DALI_RAMOOPS_SIGNATURE         0x43474244U
#define DALI_RAMOOPS_REPLAY_MAX        (256U << 10)

struct sweet_ramoops_header {
	volatile uint32_t signature;
	volatile uint32_t start;
	volatile uint32_t size;
};

static volatile struct sweet_ramoops_header *sweet_ramoops_ring;
static volatile uint8_t *sweet_ramoops_data;
static _Atomic bool sweet_ramoops_active;
static _Atomic uint32_t sweet_ramoops_busy;
static uint32_t sweet_ramoops_capacity;

void sweet_ramoops_append(const char *bytes, int size);

/* Called for all console output, any context: never blocks. */
void sweet_log_append(const char *c, int size);

void
sweet_log_append(const char *c, int size)
{
	if (c == NULL || size <= 0) {
		return;
	}
	/* Never spin on a writer interrupted by panic or by this console call.
	 * Each acquisition copies at most 256 bytes, including for early/panic
	 * direct writes that bypass the normal console's 16-byte drain.
	 */
	while (size > 0) {
		uint32_t chunk = (uint32_t)MIN(size, (int)SWEET_LOG_APPEND_MAX);
		bool dropped = true;
		if (os_atomic_cmpxchg(&sweet_log_busy, 0, 1, acquire)) {
			uint64_t at = os_atomic_load(&sweet_log_total, relaxed);
			if ((uint64_t)chunk <= UINT64_MAX - at) {
				for (uint32_t i = 0; i < chunk; i++) {
					sweet_log_ring[(at + i) % SWEET_LOG_SIZE] = c[i];
				}
				/* Publish only after the complete chunk is in the ring. */
				os_atomic_store(&sweet_log_total, at + chunk, release);
				dropped = false;
			}
			os_atomic_store(&sweet_log_busy, 0, release);
		}
		if (dropped) {
			(void)os_atomic_add_orig(&sweet_log_dropped_chunks, 1, relaxed);
			(void)os_atomic_add_orig(&sweet_log_dropped_bytes, chunk, relaxed);
		}
		/* Also bound the ramoops helper's interrupt-masked section. */
		sweet_ramoops_append(c, (int)chunk);
		c += chunk;
		size -= (int)chunk;
	}
}

/*
 * Copy console bytes [*pos, total) (at most max) for live log streaming
 * (sweet_usb.c). A reader that fell more than the ring behind skips ahead.
 * Returns the byte count; *pos advances.
 */
uint32_t sweet_log_copy(uint64_t *pos, char *out, uint32_t max);

uint32_t
sweet_log_copy(uint64_t *pos, char *out, uint32_t max)
{
	if (pos == NULL || out == NULL || max == 0 ||
	    !os_atomic_cmpxchg(&sweet_log_busy, 0, 1, acquire)) {
		return 0;
	}
	uint64_t total = os_atomic_load(&sweet_log_total, acquire);
	if (*pos > total) {
		*pos = total;
	} else if (total - *pos > SWEET_LOG_SIZE) {
		*pos = total - SWEET_LOG_SIZE;
	}
	uint32_t n = (uint32_t)MIN((uint64_t)MIN(max, SWEET_LOG_COPY_MAX), total - *pos);
	for (uint32_t i = 0; i < n; i++) {
		out[i] = sweet_log_ring[(*pos + i) % SWEET_LOG_SIZE];
	}
	*pos += n;
	os_atomic_store(&sweet_log_busy, 0, release);
	return n;
}

uint64_t sweet_log_position(void);

uint64_t
sweet_log_position(void)
{
	return os_atomic_load(&sweet_log_total, acquire);
}

/*
 * Append console bytes in the Linux persistent_ram_buffer format. The map is
 * restricted to the 2 MiB console subregion; pmsg, dump and ftrace are untouched.
 */
void
sweet_ramoops_append(const char *bytes, int size)
{
	if (!os_atomic_load(&sweet_ramoops_active, acquire) || bytes == NULL || size <= 0) {
		return;
	}
	uint64_t daif;
	__asm__ volatile("mrs %0, daif\n\tmsr daifset, #3" : "=r" (daif) :: "memory");
	/* Never block a panic or reentrant console writer; losing this chunk is safe. */
	if (!os_atomic_cmpxchg(&sweet_ramoops_busy, 0, 1, acquire)) {
		__asm__ volatile("msr daif, %0" :: "r" (daif) : "memory");
		return;
	}
	uint32_t start = sweet_ramoops_ring->start;
	uint32_t used = sweet_ramoops_ring->size;
	bool initialize = false;
	if (sweet_ramoops_ring->signature != DALI_RAMOOPS_SIGNATURE ||
	    start >= sweet_ramoops_capacity || used > sweet_ramoops_capacity || start > used) {
		sweet_ramoops_ring->signature = 0;
		__asm__ volatile("dsb sy" ::: "memory");
		start = 0;
		used = 0;
		initialize = true;
	}
	if ((uint32_t)size > sweet_ramoops_capacity) {
		uint32_t skip = (uint32_t)size - sweet_ramoops_capacity;
		bytes += skip;
		size = (int)sweet_ramoops_capacity;
	}
	for (int i = 0; i < size; i++) {
		sweet_ramoops_data[start] = (uint8_t)bytes[i];
		start = (start + 1 == sweet_ramoops_capacity) ? 0 : start + 1;
		if (used < sweet_ramoops_capacity) {
			used++;
		}
	}
	__asm__ volatile("dsb sy" ::: "memory");
	sweet_ramoops_ring->size = used;
	/* Linux rejects start > size; commit size before the write cursor. */
	__asm__ volatile("dsb sy" ::: "memory");
	sweet_ramoops_ring->start = start;
	__asm__ volatile("dsb sy" ::: "memory");
	if (initialize) {
		sweet_ramoops_ring->signature = DALI_RAMOOPS_SIGNATURE;
		__asm__ volatile("dsb sy" ::: "memory");
	}
	os_atomic_store(&sweet_ramoops_busy, 0, release);
	__asm__ volatile("msr daif, %0" :: "r" (daif) : "memory");
}

static bool
sweet_ramoops_start(void)
{
	DTEntry chosen;
	const uint64_t *base_prop = NULL, *size_prop = NULL;
	const uint64_t *offset_prop = NULL, *console_size_prop = NULL;
	unsigned int base_bytes = 0, size_bytes = 0;
	unsigned int offset_bytes = 0, console_size_bytes = 0;
	uint64_t base, total, offset, console_size;
	static const char marker[] = "\nSWEET DALI RAMOOPS: root diagnostics begin\n";

	if (os_atomic_load(&sweet_ramoops_active, acquire)) {
		return true;
	}
	if (SecureDTLookupEntry(NULL, "/chosen", &chosen) != kSuccess ||
	    SecureDTGetProperty(chosen, "dali-ramoops-base", (void const **)&base_prop,
	    &base_bytes) != kSuccess || base_prop == NULL || base_bytes != sizeof(base) ||
	    SecureDTGetProperty(chosen, "dali-ramoops-size", (void const **)&size_prop,
	    &size_bytes) != kSuccess || size_prop == NULL || size_bytes != sizeof(total) ||
	    SecureDTGetProperty(chosen, "dali-ramoops-console-offset", (void const **)&offset_prop,
	    &offset_bytes) != kSuccess || offset_prop == NULL || offset_bytes != sizeof(offset) ||
	    SecureDTGetProperty(chosen, "dali-ramoops-console-size", (void const **)&console_size_prop,
	    &console_size_bytes) != kSuccess || console_size_prop == NULL ||
	    console_size_bytes != sizeof(console_size)) {
		return false;
	}
	memcpy(&base, base_prop, sizeof(base));
	memcpy(&total, size_prop, sizeof(total));
	memcpy(&offset, offset_prop, sizeof(offset));
	memcpy(&console_size, console_size_prop, sizeof(console_size));
	if (base != DALI_RAMOOPS_BASE || total != DALI_RAMOOPS_SIZE ||
	    offset != DALI_RAMOOPS_CONSOLE_OFFSET ||
	    console_size != DALI_RAMOOPS_CONSOLE_SIZE ||
	    offset > total || console_size > total - offset ||
	    console_size <= sizeof(*sweet_ramoops_ring)) {
		return false;
	}
	vm_offset_t mapping = ml_io_map_wcomb((vm_offset_t)(base + offset), (vm_size_t)console_size);
	if (mapping == 0) {
		return false;
	}
	sweet_ramoops_ring = (volatile struct sweet_ramoops_header *)mapping;
	sweet_ramoops_data = (volatile uint8_t *)(mapping + sizeof(*sweet_ramoops_ring));
	sweet_ramoops_capacity = (uint32_t)(console_size - sizeof(*sweet_ramoops_ring));
	__asm__ volatile("dsb sy" ::: "memory");
	os_atomic_store(&sweet_ramoops_active, true, release);

	/* Preserve the most recent pre-root output so early failures are recoverable. */
	uint64_t logged = os_atomic_load(&sweet_log_total, acquire);
	uint64_t pos = logged > DALI_RAMOOPS_REPLAY_MAX ? logged - DALI_RAMOOPS_REPLAY_MAX : 0;
	char chunk[256];
	while (pos < logged) {
		uint32_t count = (uint32_t)MIN((uint64_t)sizeof(chunk), logged - pos);
		count = sweet_log_copy(&pos, chunk, count);
		if (count == 0) break; /* A live or interrupted writer must never block boot. */
		sweet_ramoops_append(chunk, (int)count);
	}
	sweet_ramoops_append(marker, sizeof(marker) - 1);
	return true;
}

struct sweet_log_header {
	char magic[8];
	uint32_t version, size;
	uint64_t total, seq;
	/* Optional v1-compatible G4 extension, CRC covers only header bytes. */
	char identity[8], run_id[32];
	uint64_t dropped_chunks, dropped_bytes;
	uint32_t header_crc;
};
_Static_assert(offsetof(struct sweet_log_header, dropped_chunks) == 72 &&
    offsetof(struct sweet_log_header, header_crc) == 88, "log header layout");

/* The pre-root probes do not use the persistent UFS log writer. */

#if DEVELOPMENT
/* Completed logger publications only: no UFS lock or DMA access in the waiter. */
static os_atomic(uint32_t) sweet_log_fastboot_requested, sweet_log_fastboot_running;
static os_atomic(uint64_t) sweet_log_fastboot_persisted;
static int sweet_log_fastboot_chan;

static void
sweet_log_fastboot_publish(uint64_t total)
{
    if (os_atomic_load(&sweet_log_fastboot_requested, acquire)) {
        os_atomic_store(&sweet_log_fastboot_persisted, total, release);
        wakeup(&sweet_log_fastboot_chan);
    }
}

bool sweet_log_fastboot_drain(uint64_t deadline);
bool
sweet_log_fastboot_drain(uint64_t deadline)
{
    if (!su.dali || !su.log_partition_ready || hz <= 0 || hz > 1000 ||
        !ml_get_interrupts_enabled() || ml_at_interrupt_context() || get_preemption_level() != 0 ||
        !os_atomic_load(&sweet_log_fastboot_running, acquire) || mach_absolute_time() >= deadline)
        return false;
    uint64_t target = os_atomic_load(&sweet_log_total, acquire);
    if (target == 0)
        return false;
    os_atomic_store(&sweet_log_fastboot_requested, 1, release);
    wakeup(&sweet_log_chan);
    for (unsigned int attempts = 0; attempts < (unsigned int)hz * 20; ++attempts) {
        if (mach_absolute_time() >= deadline ||
            !os_atomic_load(&sweet_log_fastboot_running, acquire))
            return false;
        if (os_atomic_load(&sweet_log_fastboot_persisted, acquire) >= target)
            return true;
        wakeup(&sweet_log_chan); /* Retry only the wakeup, never the I/O, after a missed wake. */
        (void)tsleep(&sweet_log_fastboot_chan, PZERO, "fbdrain", 1);
    }
    return false; /* Bounded even if an unexpected clock fails to advance. */
}
#endif

static void
sweet_log_thread(__unused void *arg, __unused wait_result_t wr)
{
	bool separate = su.log_partition_configured;
	uint64_t base = separate ? su.log_first_block :
	    su.first_block + su.nblocks - SWEET_LOG_BLOCKS;
	uint64_t flushed = 0, seq = 0;
	bool backlog = false;
	void *bounce = (void *)(su.dma + DMA_BOUNCE);
	char run_id[33] = {0};
	if (su.hsg4_root && !ufs_dali_run_id(run_id)) {
		printf("SWEET LOG: missing G4 run identity; persistent logging disabled\n");
		return;
	}

	printf("SWEET LOG: console log in %s (%u KiB at block 0x%llx)\n",
	    separate ? su.log_partition_name : "root partition tail",
	    SWEET_LOG_BLOCKS * UFS_BLOCK >> 10, base);
#if DEVELOPMENT
	os_atomic_store(&sweet_log_fastboot_running, 1, release);
#endif
	for (;;) {
		/* Release the UFS mutex and wait one tick between backlog batches;
		 * fastboot wakeups cannot bypass this private fairness opportunity.
		 */
		if (backlog) (void)tsleep(&sweet_log_fair_chan, PZERO, "sweetlog", 1);
		else (void)tsleep(&sweet_log_chan, PZERO, "sweetlog", hz * (int)sweet_log_secs);
		/* A busy console writer is never waited on, including during panic. */
		if (!os_atomic_cmpxchg(&sweet_log_busy, 0, 1, acquire)) {
			backlog = false;
			continue;
		}
		uint64_t total = os_atomic_load(&sweet_log_total, acquire);
		if (total == flushed) {
			backlog = false;
			os_atomic_store(&sweet_log_busy, 0, release);
#if DEVELOPMENT
			sweet_log_fastboot_publish(flushed);
#endif
			continue;
		}
		if (total - flushed > SWEET_LOG_SIZE) {
			/* A v1 header cannot encode a hole after overrunning the RAM ring. */
			os_atomic_store(&sweet_log_busy, 0, release);
#if DEVELOPMENT
			os_atomic_store(&sweet_log_fastboot_running, 0, release);
#endif
			printf("SWEET LOG: RAM ring overrun; stopping without publishing a gap\n");
			return;
		}
		uint64_t pos = flushed;
		uint32_t off = (uint32_t)(pos % SWEET_LOG_SIZE);
		uint32_t blk = off / UFS_BLOCK;
		uint64_t first = pos - off % UFS_BLOCK;
		uint64_t wanted = total - first;
		uint32_t n = (uint32_t)MIN(MIN((wanted + UFS_BLOCK - 1) / UFS_BLOCK,
		    (uint64_t)(SWEET_LOG_SIZE / UFS_BLOCK - blk)),
		    (uint64_t)(SWEET_LOG_BATCH / UFS_BLOCK));
		uint64_t end = first + MIN(wanted, (uint64_t)n * UFS_BLOCK);
		os_atomic_store(&sweet_log_busy, 0, release);
		/* Snapshot at most 64 KiB, one 4 KiB gated copy at a time. If a
		 * writer laps any not-yet-copied byte, abandon without device I/O.
		 * No console gate is ever held across a mutex or UFS command.
		 */
		bool retry = false;
		for (uint32_t i = 0; i < n; i++) {
			if (!os_atomic_cmpxchg(&sweet_log_busy, 0, 1, acquire)) {
				retry = true;
				break;
			}
			uint64_t now = os_atomic_load(&sweet_log_total, acquire);
			/* Only [pos,end) is copied into the disk write. Any prior
			 * block prefix and future suffix come from the disk itself.
			 */
			uint64_t required = MAX(pos, first + (uint64_t)i * UFS_BLOCK);
			if (now < required || now - required > SWEET_LOG_SIZE) {
				retry = true;
			} else {
				memcpy(sweet_log_snapshot + (uint64_t)i * UFS_BLOCK,
				    sweet_log_ring + (uint64_t)(blk + i) * UFS_BLOCK, UFS_BLOCK);
			}
			os_atomic_store(&sweet_log_busy, 0, release);
			if (retry) break;
		}
		if (retry) {
			backlog = false;
			continue;
		}
		int error = 0;

		lck_mtx_lock(&sweet_ufs_lock);
		/* Do not overwrite DMA memory until hardware ownership is known. */
		if (su.dali && su.command_poisoned) {
			error = ETIMEDOUT;
		} else {
			uint32_t skip = (uint32_t)(pos - first);
			uint32_t count = (uint32_t)(end - pos);
			/* Preserve already committed prefix and older ring suffix.
			 * A concurrent producer can change bytes outside [pos,end)
			 * before the RAM block snapshot is taken.
			 */
			if (skip != 0 || skip + count != n * UFS_BLOCK) {
				error = ufs_read_blocks(base + 1 + blk, n);
			}
			if (!error) {
				memcpy((char *)bounce + skip, sweet_log_snapshot + skip, count);
				error = separate ? ufs_write_log_blocks(base + 1 + blk, n) :
				    ufs_write_blocks(base + 1 + blk, n);
			}
		}
		if (!error) {
				/* Persist payload before publishing a header that references it. */
				error = ufs_sync_cache();
				if (error) {
					lck_mtx_unlock(&sweet_ufs_lock);
#if DEVELOPMENT
					os_atomic_store(&sweet_log_fastboot_running, 0, release);
#endif
					printf("SWEET LOG: payload sync failed (%d), stopping\n", error);
					return;
				}
			struct sweet_log_header h = {
				.magic = { 'S', 'W', 'E', 'E', 'T', 'L', 'O', 'G' },
				.version = 1, .size = SWEET_LOG_SIZE, .total = end, .seq = ++seq,
			};
			if (su.hsg4_root) {
				memcpy(h.identity, "SWIDv002", sizeof(h.identity));
				memcpy(h.run_id, run_id, sizeof(h.run_id));
				/* No recursive printf or I/O merely to report a drop. These
				 * independent samples become visible at the next dirty flush.
				 */
				h.dropped_chunks = os_atomic_load(&sweet_log_dropped_chunks, relaxed);
				h.dropped_bytes = os_atomic_load(&sweet_log_dropped_bytes, relaxed);
				h.header_crc = crc32(0, &h, offsetof(struct sweet_log_header, header_crc));
			}
			bzero(bounce, UFS_BLOCK);
			memcpy(bounce, &h, offsetof(struct sweet_log_header, header_crc) + sizeof(h.header_crc));
			error = separate ? ufs_write_log_blocks(base, 1) : ufs_write_blocks(base, 1);
		}
		if (!error) {
			error = ufs_sync_cache();
		}
		lck_mtx_unlock(&sweet_ufs_lock);
		if (error) {
#if DEVELOPMENT
			os_atomic_store(&sweet_log_fastboot_running, 0, release);
#endif
			printf("SWEET LOG: write failed (%d), stopping\n", error);
			return;
		}
		flushed = end;
		backlog = os_atomic_load(&sweet_log_total, acquire) > flushed;
#if DEVELOPMENT
		sweet_log_fastboot_publish(flushed);
#endif
	}
}

static void
sweet_log_start(void)
{
	uint32_t on = 1;
	thread_t thread;

	PE_parse_boot_argn("sweetlog", &on, sizeof(on));
	if (!on) {
		return;
	}
	if (su.log_partition_configured) {
		if (!su.log_partition_ready || su.log_nblocks < SWEET_LOG_BLOCKS) {
			printf("SWEET LOG: dedicated log partition unavailable; persistent logging disabled\n");
			return;
		}
	} else if (su.dali || !su.writable || su.nblocks < 2 * SWEET_LOG_BLOCKS) {
		if (su.dali) {
			printf("SWEET LOG: Dali root-tail fallback disabled; use unixnu_logs\n");
		}
		return;
	}
	PE_parse_boot_argn("ufslogsecs", &sweet_log_secs, sizeof(sweet_log_secs));
	if (sweet_log_secs == 0 || sweet_log_secs > 60) {
		sweet_log_secs = SWEET_LOG_SECS;
	}
	if (kernel_thread_start(sweet_log_thread, NULL, &thread) == KERN_SUCCESS) {
		thread_deallocate(thread);
	}
}

/* Root device for rd=ufsroot0 (IOFindBSDRoot). */
int sweet_ufs_rootdev(dev_t *dev);
const char *sweet_ufs_failure_stage(void);

const char *
sweet_ufs_failure_stage(void)
{
	return su.failure_stage != NULL ? su.failure_stage : "unknown";
}

int
sweet_ufs_rootdev(dev_t *dev)
{
    /* Check the link/init latch before ramoops or any other possible MMIO. */
    int pwm_error = ufs_pwm_reentry_error();
    if (pwm_error) return pwm_error;
    uint32_t pwm_requested = 0;
    (void)PE_parse_boot_argn("ufspwm", &pwm_requested, sizeof(pwm_requested));
    bool two_lanes = ufs_dali_two_lane_requested();
    bool hsg1 = ufs_dali_speed_flag("dali-hs-g1");
    bool speed_probe = ufs_dali_speed_flag("dali-speed-probe");
    bool extended = ufs_dali_speed_flag("dali-speed-extended");
    uint32_t hs_target = 0;
    int target_error = ufs_dali_hs_target(&hs_target);
    if (target_error) return target_error;
    bool hsg4 = hs_target == 4, hsg4_root = false;
    int root_option_error = ufs_dali_hsg4_root_requested(&hsg4_root);
    if (root_option_error) return root_option_error;
    char run_id[33] = {0};
    uint32_t root_rw = 0, root_logger = 0;
    if ((two_lanes && pwm_requested != 0) ||
        (hsg1 && (hsg4 || two_lanes || pwm_requested != 0)) ||
        (hsg4_root && !hsg4) ||
        (hsg4 && ((speed_probe == hsg4_root) || two_lanes || pwm_requested != 0)) ||
        (speed_probe && !hsg1 && !hsg4) ||
        (extended && !speed_probe) ||
        ((extended || (hsg4 && speed_probe)) && !ufs_dali_extended_configuration(run_id)) ||
        (hsg4_root && !ufs_dali_hsg4_root_configuration(run_id, &root_rw, &root_logger))) return EINVAL;
    bool pwm_guard = hsg1 || hsg4 || two_lanes || (pwm_requested == 1 && SWEET_PWM_PLATFORM_CAP > 1);
    pwm_error = ufs_pwm_root_enter(pwm_guard);
    if (pwm_error) return pwm_error;
    su.pwm_two_lanes = two_lanes;
    su.hsg1_requested = hsg1;
    su.hsg4_requested = hsg4;
    su.hsg4_root = hsg4_root;
    su.speed_probe = speed_probe;
    su.speed_extended = extended;
	(void)sweet_ramoops_start();
	if (extended) printf("SWEET UFS EXT: v=1 start=1 run-id=%s auto=0 root-write=0 log=0\n", run_id);
	if (hsg4 && speed_probe)
		printf("SWEET UFS HSG4: v=1 start=1 run-id=%s auto=0 root-write=0 log=0\n", run_id);
	if (hsg4_root)
		printf("SWEET UFS HSG4 ROOT: v=1 start=1 run-id=%s auto=0 root-write=%u log=%u physical-write-stability=unproven\n",
		    run_id, root_rw, root_logger);
	if (!su.ready) {
		su.failure_stage = "root-init";
		int error = sweet_ufs_init();
		if (error) {
            if (error == ECANCELED && su.speed_probe && su.speed_probe_done && !su.ready) {
                /* Keep the root-entry claim forever: a returned SMC may not mount
                 * or restart this probe. No project/UFS lock is held here.
                 */
                dali_fastboot_return_ufs_probe();
                return ECANCELED;
            }
            ufs_pwm_root_leave(pwm_guard, error);
            if (pwm_guard && su.dali && !su.ready)
                dali_fastboot_return_root_failure(); /* No UFS lock/drain, one armed reset. */
			return error;
		}
	}
    ufs_pwm_root_leave(pwm_guard, 0);
	*dev = su.bdev;
	return 0;
}
#endif /* ARM64_BOARD_CONFIG_SWEET */
