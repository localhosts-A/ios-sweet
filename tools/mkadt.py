#!/usr/bin/env python3
"""Build the minimal Apple Device Tree that the SWEET xnu needs to boot.

Only what the early boot path actually reads (see NOTES.md); every MMIO
address comes from the selected board's live FDT. Usage: mkadt.py OUT.adt
[--board sweet|dali] [--dump] [--qemu] [--highmem]
"""
import hashlib, os, struct, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import adt  # noqa: E402

# Kernel-managed RAM: above every Qualcomm XPU carve-out (see NOTES.md)
PHYS_BASE = 0xb0000000
MEM_SIZE = 0x4d000000
# --highmem (phone only): the whole upper DDR bank. The live FDT memory node is
# <0x80000000 2G>, <0x1_00000000 0xfcc00000>; every fixed reserved-memory
# carve-out is in the low bank and the dynamic pools are limited below 4G.
HIGH_PHYS_BASE = 0x100000000
HIGH_MEM_SIZE = 0xfcc00000
# /arm-io children are relative to this; it must not be 0 (serial_init bails)
SOC_BASE = 0x01000000

GICD, GICD_SIZE = 0x17a00000, 0x10000
GICR, GICR_SIZE = 0x17a60000, 0x100000
FB, FB_SIZE = 0x9c000000, 0x1700000
SWEET_ECID = 0x5357454554000001  # 'SWEET' + 1, synthetic
FB_W, FB_H, FB_STRIDE = 1080, 2400, 1080 * 4
# Internal UFS host (ufshc@1d84000 "ufs_mem"); QEMU virt has none
UFS, UFS_SIZE = 0x01d84000, 0x3000
TIMEBASE = 19200000
VTIMER_INTID = 19
# QEMU virt (-M virt,gic-version=3 -cpu cortex-a76): GICD/GICR and the generic timer's CNTFRQ
QEMU_GICD, QEMU_GICR, QEMU_TIMEBASE = 0x08000000, 0x080a0000, 62500000
QEMU_VTIMER_INTID = 27

# Redmi K80 Ultra / Dali (MT6991). The memory range is the largest continuous
# interval left after excluding the explicit reserved ranges in the live
# /proc/iomem map; keep the shim and bootloader in low memory.
DALI_PHYS_BASE, DALI_MEM_SIZE = 0x200000000, 0x13fe00000
DALI_GICD, DALI_GICD_SIZE = 0x0c400000, 0x40000
DALI_GICR, DALI_GICR_SIZE = 0x0c440000, 0x200000
DALI_FB, DALI_FB_SIZE = 0xfc990000, 0x3660000
DALI_FB_W, DALI_FB_H = 1280, 2772
DALI_UFS, DALI_UFS_SIZE = 0x16810000, 0x2a00
DALI_UFS_AO, DALI_UFS_AO_SIZE = 0x168a0000, 0x1000
# Preserve the existing DALIUFSLOG1 area in unixnu_logs' first 16 MiB.
DALI_UFS_LOG_OFFSET = 16 * 1024 * 1024
DALI_TIMEBASE, DALI_VTIMER_INTID = 13000000, 27

BOOT_MANIFEST_HASH = hashlib.sha384(b'sweet boot manifest').digest()

# The iOS userland is the iPhone X (iPhone10,3 / D22AP) IPSW. MobileGestalt
# derives product type, device class, home-button-less UI, artwork etc. from
# the root identity and /product of the real D22AP device tree. That tree is
# an Apple artifact (kept out of git): extract it from the IPSW with
#   tools/remotezip.py URL DeviceTree.d22ap.im4p DIR
#   ipsw img4 im4p extract DIR/Firmware/all_flash/DeviceTree.d22ap.im4p -o d22ap.adt
D22AP_ADT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '../research/ipsw-x/d22ap.adt')


def d22ap_product(path=D22AP_ADT):
    """/product properties of the real D22AP tree, without the syscfg
    placeholders iBoot would fill in (flag bit set) and the node bookkeeping."""
    if not os.path.exists(path):
        print(f'warning: {path} missing, no /product node', file=sys.stderr)
        return None
    root, _ = adt.parse(open(path, 'rb').read())
    for c in root.children:
        if dict((k, v) for k, f, v in c.props).get('name') == b'product\0':
            return [(k, v) for k, f, v in c.props if not f and k not in ('name', 'AAPL,phandle')]
    raise SystemExit(f'{path}: no /product node')

u32 = lambda *v: struct.pack('<%dI' % len(v), *v)
u64 = lambda *v: struct.pack('<%dQ' % len(v), *v)
cstr = lambda s: s.encode() + b'\0'


def node(name, props=(), children=()):
    n = adt.Node()
    n.props = [('name', 0, cstr(name))] + [(k, 0, v) for k, v in props]
    n.children = list(children)
    return n


def build(seed=None, qemu=False, highmem=False, dali=False):
    """Build a SWEET or Dali ADT; qemu=True selects QEMU virt's GIC and timer."""
    if dali:
        if qemu or highmem:
            raise ValueError('--board dali cannot be combined with --qemu or --highmem')
        phys_base, mem_size = DALI_PHYS_BASE, DALI_MEM_SIZE
        gicd, gicd_size = DALI_GICD, DALI_GICD_SIZE
        gicr, gicr_size = DALI_GICR, DALI_GICR_SIZE
        timebase, vtimer_intid = DALI_TIMEBASE, DALI_VTIMER_INTID
        fb, fb_size = DALI_FB, DALI_FB_SIZE
        fb_w, fb_h, fb_stride = DALI_FB_W, DALI_FB_H, DALI_FB_W * 4
        ufs, ufs_size, ufs_lun = DALI_UFS, DALI_UFS_SIZE, 2
        ufs_compatible = cstr('mediatek,mt6991-ufshc') + cstr('qcom,ufshc')
        ufs_root_partition, ufs_log_partition = 'unixnu_data', 'unixnu_logs'
        cpu_count = 1  # first boot remains on the firmware-started CPU
        cpu_compat = cstr('arm,armv8')
        board_compat = cstr('xiaomi,sweet') + cstr('xiaomi,dali') + cstr('mediatek,mt6991')
        firmware_name = 'dali-shim'
    else:
        phys_base, mem_size = (HIGH_PHYS_BASE, HIGH_MEM_SIZE) if highmem else (PHYS_BASE, MEM_SIZE)
        gicd, gicr, timebase = (QEMU_GICD, QEMU_GICR, QEMU_TIMEBASE) if qemu else (GICD, GICR, TIMEBASE)
        gicd_size, gicr_size = GICD_SIZE, GICR_SIZE
        vtimer_intid = QEMU_VTIMER_INTID if qemu else VTIMER_INTID
        fb, fb_size, fb_w, fb_h, fb_stride = FB, FB_SIZE, FB_W, FB_H, FB_STRIDE
        ufs, ufs_size, ufs_lun = UFS, UFS_SIZE, 0
        ufs_compatible = cstr('qcom,ufshc')
        ufs_root_partition = ufs_log_partition = None
        cpu_count = 1 if qemu else 8
        cpu_compat = cstr('qcom,kryo470') + cstr('ARM,v8')
        board_compat = cstr('xiaomi,sweet') + cstr('qcom,sm7150')
        firmware_name = 'sweet-shim'
    seed = seed if seed is not None else os.urandom(64)
    phandle = iter(range(1, 1000))
    ph = lambda: ('AAPL,phandle', u32(next(phandle)))

    chosen = node('chosen', [
        ph(),
        ('dram-base', u64(phys_base)),
        ('dram-size', u64(mem_size)),
        ('random-seed', seed),
        ('debug-enabled', u32(1)),
        ('firmware-version', cstr(firmware_name)),
        ('system-firmware-version', cstr(firmware_name)),
        # iBoot's SHA-384 of the boot manifest; /sbin/mount -P 1 exits unless
        # it exists (bind mounts /private/preboot/<hex>/usr/standalone/firmware,
        # tolerating a missing directory). Fixed stand-in value.
        ('boot-manifest-hash', BOOT_MANIFEST_HASH),
        # libimg4 (cryptexd) aborts "failed to get crypto algorithm" without
        # it; real A11+ device trees say sha2-384.
        ('crypto-hash-method', cstr('sha2-384')),
        # MobileGestalt's screen class: display-scale (u32, default 0) comes
        # from /chosen, the pixel size from IOMobileFramebuffer. Scale 0 makes
        # SpringBoard's FBSDisplayMode "infxinf@0x" invalid. 1080x2400 @3x =
        # 360x800 points (like iPhone X class devices).
        ('display-scale', u32(3)),
        # XNU derives IOPlatformUUID (gethostuuid) from these on arm; without
        # them CFPreferences cannot use kCFPreferencesCurrentHost (no ByHost
        # files), so e.g. DataMigration never records LastSystemVersion and
        # migrates on every boot. Synthetic ECID (not the phone's); chip-id
        # of the A11 that the iPhone X userland expects.
        ('unique-chip-id', struct.pack('<Q', SWEET_ECID)),
        ('chip-id', u32(0x8015)),
        # Security status as on a dev-fused, undemoted board (tuple 0,0,1,1):
        # MobileGestalt reads these from /chosen, and mobileactivationd's
        # DeviceType then takes its native "Hactivation" branch (state
        # Activated without an activation record; no SEP, no baseband here).
        ('certificate-production-status', u32(0)),
        ('effective-production-status-ap', u32(0)),
        ('certificate-security-mode', u32(1)),
        ('effective-security-mode-sep', u32(1)),
    ], [node('memory-map', [ph()]),
        # Local boot policy (kern_csr.c): SIP relaxed like `csrutil disable`
        # (lp-sip0: untrusted kexts, unrestricted FS, task_for_pid, kernel
        # debugger, dtrace, nvram), lp-sip1: unauthenticated (unsealed) root.
        # The iOS shared cache is in no trust cache, so dyld can only map it
        # with CSR_ALLOW_UNRESTRICTED_FS (vm_unix.c shared region checks).
        node('asmb', [ph(), ('lp-sip0', u64(0x6f)), ('lp-sip1', u32(1))])])

    # SWEET SMP: all eight cores in physical-core order (0-5 Cortex-A55,
    # 6-7 Cortex-A76), one homogeneous cluster. The kernel defaults to one
    # and uses cpus=N to select an initial prefix (N <= 8).
    # QEMU retains its original single-CPU tree: its MPIDRs differ from sweet.
    cpu_nodes = []
    for core in range(cpu_count):
        cpu_props = [
            ph(),
            ('device_type', cstr('cpu')),
            ('compatible', cpu_compat),
            ('reg', u32(core << 8)),  # actual MPIDR Aff1:Aff0, NOT logical ID
            ('cpu-id', u32(core)),   # descriptive: XNU assigns IDs by DT order
            ('cluster-id', u32(0)),  # descriptive; see ml_parse_cpu_topology
            ('cluster-type', u32(0)),  # homogeneous CLUSTER_TYPE_SMP, not AMP
            ('die-cluster-id', u32(0)),
            ('cluster-core-id', u32(core)),
            ('l2-cache-id', u32(core)),  # distinct per-core L2 nodes in live FDT
            ('l3-cache-id', u32(0)),     # shared L3
            ('state', cstr('running' if core == 0 else 'stopped')),
            ('timebase-frequency', u32(timebase)),
        ]
        if dali:
            # Local driver routing marker; retain the real generic ARM identity.
            cpu_props.append(('dali-boot-cpu', u32(1)))
        else:
            cpu_props += [
                ('clock-frequency', u32(2304000000 if core >= 6 else 1804800000)),
                ('bus-frequency', u32(100000000)),
                ('memory-frequency', u32(1866000000)),
                ('peripheral-frequency', u32(timebase)),
                ('fixed-frequency', u32(timebase)),
            ]
        cpu_nodes.append(node(f'cpu{core}', cpu_props))
    cpus = node('cpus', [ph(), ('#address-cells', u32(1)),
                         ('#size-cells', u32(0))], cpu_nodes)

    gic = node('gic', [
        ph(),
        ('compatible', cstr('arm,gic-v3')),
        # XNU's pe_arm_map_interrupt_controller searches this string property
        # for the master controller; an empty DT boolean is not equivalent.
        ('interrupt-controller', cstr('master')),
        ('virtual-timer-intid', u32(vtimer_intid)),
        ('reg', u64(gicd - SOC_BASE, gicd_size, gicr - SOC_BASE, gicr_size)),
    ])
    arm_io = node('arm-io', [
        ph(),
        ('device_type', cstr('sweet-io')),
        ('compatible', cstr('sweet-io') + cstr('arm-io')),
        ('#address-cells', u32(2)),
        ('#size-cells', u32(2)),
        ('ranges', u64(0, SOC_BASE, 0x100000000 - SOC_BASE)),
    ], [gic,
        # No hardware: watchdogd checks IODeviceTree:/arm-io/wdt for no-pmu and
        # otherwise crashes trying to set the PMU boot stage (customer builds
        # ignore the -wdt_no_pmu boot-arg).
        node('wdt', [ph(), ('no-pmu', u32(1))]),
        # Main display: IOMobileFramebuffer.framework counts /arm-io children
        # with this device_type and reads the canvas size from the node
        # (the presented iPhone X size, see SweetFramebuffer.cpp);
        # the kernel's AppleCLCD matches the compatible string.
        node('disp0', [
            ph(),
            ('device_type', cstr('display-subsystem')),
            ('compatible', cstr('sweet,display')),
            ('canvas-width', u32(1125)),
            ('canvas-height', u32(2436)),
        ])])

    fbcon = node('sweet-fbcon', [
        ph(),
            ('reg', u64(fb, fb_size)),
            ('width', u32(fb_w)),
            ('height', u32(fb_h)),
            ('stride', u32(fb_stride)),
    ])

    # bsd/dev/sweet_ufs.c: the UFS host ABL leaves running (absolute physical reg).
    ufs_props = [
        ph(),
        ('compatible', ufs_compatible),
        ('reg', u64(ufs, ufs_size)),
        ('lun', u32(ufs_lun)),
    ]
    if ufs_root_partition:
        ufs_props.extend([
            ('ao-reg', u64(DALI_UFS_AO, DALI_UFS_AO_SIZE)),
            ('root-partition', cstr(ufs_root_partition)),
            ('log-partition', cstr(ufs_log_partition)),
            ('log-offset', u64(DALI_UFS_LOG_OFFSET)),
        ])
    ufs = node('sweet-ufs', ufs_props)

    # pmap_compute_max_asids requires the node even when no overrides are set.
    # An absent pmap-max-asids property selects XNU's own MAX_ASIDS default.
    # No effaceable storage: keybagd then reads /private/var/keybags/systembag.kb
    # as a plain plist instead of decrypting it with the effaceable key.
    defaults = node('defaults', [ph(), ('no-effaceable-storage', u32(1))])

    # MobileGestalt HomeButtonType = IODeviceTree:/buttons home-button-type
    # (D22AP: 2, no Home button; absent = 0 = Home-button device).
    buttons = node('buttons', [
        ph(),
        ('button-names', cstr('volup') + cstr('voldown') + cstr('hold')),
        ('home-button-type', u32(2)),
        ('opposed-power-vol-buttons', u32(1)),
    ])

    product_props = d22ap_product()
    product = [node('product', [ph()] + product_props)] if product_props else []

    # Identity of the iPhone X whose userland runs here: compatible[0] is
    # hw.model/hw.target (D22AP), model is hw.machine/hw.product
    # (iPhone10,3). 'xiaomi,sweet' stays for SweetPlatformExpert matching.
    dali_rtc = [node('dali-rtc', [
        ph(),
        ('compatible', cstr('mediatek,mt6685-rtc')),
        # SID9 uses the main PMIF path (live spmi-dev-mask=0x85c0).
        ('reg', u64(0x1c01a000, 0x900)),
    ])] if dali else []

    # Shared main PMIF, SID14 MT6379. The first native USB stage only reads
    # Type-C/charger/repeater state when explicitly enabled with daliusb=1.
    dali_usb_pmic = [node('dali-usb-pmic', [
        ph(),
        ('compatible', cstr('mediatek,dali-usb-pmic')),
        ('reg', u64(0x1c01a000, 0x900)),
        ('cp-i2c-reg', u64(0x120a0000, 0x20000)),
        # 5V, USB-default500mA, observed SC8585. No external boost GPIO.
        ('otg-source-config', u32(5000000, 500000, 0x8585)),
        # Guarded MTU3 v2 host bring-up, separately enabled by daliusbhost=1.
        ('host-observe-reg', u64(0x16700000, 0x1000, 0x16703e00, 0x100,
                                 0x16730000, 0x400, 0x16790000, 0x100)),
        ('host-hwrscs-version', u32(2)),
        ('host-vusb33-always-on', u32(1)),
        # Exact USB0 UTMI width, fixed ref clock, XSphy LPM timings/refclk-sel.
        ('host-u2-native-config', u32(16, 26000000, 0x19, 0x1e, 0x1e, 1)),
        # MT6379 eUSB2 SID/base, observed chip74, basic-power policy version.
        ('host-repeater-config', u32(14, 0xd00, 0x74, 1)),
    ])] if dali else []

    dali_watchdog = [node('dali-watchdog', [
        ph(),
        ('compatible', cstr('mediatek,mt6991-wdt') + cstr('mediatek,mt6589-wdt')),
        ('reg', u64(0x1c010000, 0x1000)),
    ])] if dali else []

    return node('device-tree', [
        ph(),
        ('compatible', cstr('D22AP') + cstr('iPhone10,3') + board_compat + cstr('AppleARM')),
        ('model', cstr('iPhone10,3')),
        ('target-type', cstr('D22')),
        ('target-sub-type', cstr('D22AP')),
        ('manufacturer', cstr('Apple Inc.')),
        ('#address-cells', u32(2)),
        ('#size-cells', u32(2)),
    ], [chosen, defaults, cpus, arm_io, fbcon, buttons] + product + dali_rtc + dali_usb_pmic + dali_watchdog + ([] if qemu else [ufs]))


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('output')
    ap.add_argument('--board', choices=('sweet', 'dali'), default='sweet')
    ap.add_argument('--dump', action='store_true')
    ap.add_argument('--qemu', action='store_true')
    ap.add_argument('--highmem', action='store_true')
    args = ap.parse_args()
    root = build(qemu=args.qemu, highmem=args.highmem, dali=args.board == 'dali')
    blob = adt.serialize(root)
    open(args.output, 'wb').write(blob)
    if args.dump:
        adt.dump(adt.parse(blob)[0])
    print(f'{args.output}: {len(blob)} bytes', file=sys.stderr)
