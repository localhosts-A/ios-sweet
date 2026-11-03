#!/usr/bin/env python3
"""执行0178实际C策略、logger函数、SMC代码生成与raw FDT/ADT负控；零设备动作。"""
from pathlib import Path
import hashlib
import importlib.util
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / 'xnu/patches/0178-sweet-dali-fastboot-return.patch'
SAMPLER_OVERLAY = ROOT / 'xnu/patches/0179-sweet-gui-rw-wait-provenance.patch'
_SPEC = importlib.util.spec_from_file_location('recovery_adt', ROOT / 'tools/dali_recovery_adt.py')
recovery = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(recovery)
_SPEC = importlib.util.spec_from_file_location('unattended', ROOT / 'tools/dali_unattended_return.py')
unattended = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(unattended)


def fragment(path, patch=PATCH):
    blocks = [block for block in patch.read_text().split('diff --git ')[1:]
              if ('b/' + path + '\n') in block]
    if len(blocks) != 1:
        raise AssertionError(path)
    return ''.join(line[1:] for line in blocks[0].splitlines(True)
                   if line.startswith(('+', ' ')) and not line.startswith('+++'))


def apply_exact_hunks(text, path, patch):
    """Replay every hunk at its declared line, with no offset or fuzz."""
    patch_text = patch.read_text()
    blocks = patch_text.split('diff --git ')[1:]
    matches = [b for b in blocks if b.startswith(f'a/{path} b/{path}\n')]
    if len(matches) == 1:
        lines = matches[0].splitlines(keepends=True)
    elif not blocks:
        marker = f'--- a/{path}\n'
        start = patch_text.find(marker)
        if start < 0:
            raise AssertionError(path)
        ends = [i for i in (patch_text.find('\n--- a/', start + len(marker)),
                            patch_text.find('\n--- /dev/null', start + len(marker))) if i >= 0]
        end = min(ends) + 1 if ends else len(patch_text)
        lines = patch_text[start:end].splitlines(keepends=True)
    else:
        raise AssertionError(path)
    original = text.splitlines(keepends=True)
    result, cursor, index = [], 0, 0
    while index < len(lines) and not lines[index].startswith('@@ '):
        index += 1
    while index < len(lines):
        match = re.match(r'^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@', lines[index])
        if not match:
            raise AssertionError(f'malformed hunk for {path}')
        old_start, old_count, new_start, new_count = (int(match.group(1)), int(match.group(2) or 1),
                                                      int(match.group(3)), int(match.group(4) or 1))
        target = old_start - 1 if old_count else old_start
        if target < cursor or new_start != len(result) + (target - cursor) + 1:
            raise AssertionError(f'hunk position mismatch for {path}')
        result.extend(original[cursor:target]); cursor = target
        index += 1; removed = added = 0
        while index < len(lines) and not lines[index].startswith('@@ '):
            line = lines[index]; index += 1
            if not line or line[0] not in ' +-':
                raise AssertionError(f'unexpected patch line for {path}')
            if line[0] in ' -':
                if cursor >= len(original) or original[cursor] != line[1:]:
                    actual = original[cursor] if cursor < len(original) else '<eof>'
                    raise AssertionError(
                        f'hunk context mismatch for {path} at line {cursor + 1}: '
                        f'expected {line[1:]!r}, got {actual!r}')
                cursor += 1; removed += 1
            if line[0] in ' +':
                result.append(line[1:]); added += 1
        if removed != old_count or added != new_count:
            raise AssertionError(f'hunk count mismatch for {path}')
    result.extend(original[cursor:])
    return ''.join(result)


def function(text, name):
    match = re.search(r'(?:static (?:inline )?)?(?:void|bool|uint32_t|uint64_t)\n' + name + r'\(', text)
    if match is None:
        raise AssertionError(name)
    begin = text.index('{', match.start())
    end, depth = begin + 1, 1
    while depth:
        depth += (text[end] == '{') - (text[end] == '}')
        end += 1
    return text[match.start():end] + '\n'


def fdt_fixture(offset=0x24, mask=15, bootloader=3, hz=13000000, method=b'smc\0'):
    def cells(*values):
        return struct.pack('>' + 'I' * len(values), *values)
    nodes = [('', {'compatible': b'mediatek,MT6991\0', '#address-cells': cells(2), '#size-cells': cells(2)}, [
        ('watchdog@1c010000', {'compatible': b'mediatek,mt6991-wdt\0mediatek,mt6589-wdt\0',
                                'reg': cells(0, 0x1c010000, 0, 0x1000)}, [
            ('reboot-mode', {'compatible': b'syscon-reboot-mode\0', 'offset': cells(offset),
                             'mask': cells(mask), 'mode-bootloader': cells(bootloader)}, [])]),
        ('psci', {'compatible': b'arm,psci-1.0\0', 'method': method}, []),
        ('timer', {'clock-frequency': cells(hz)}, [])])]
    strings, name_offsets = bytearray(), {}
    structure = bytearray()
    def emit(node):
        name, props, children = node
        structure.extend(cells(1) + name.encode() + b'\0')
        structure.extend(b'\0' * (-len(structure) % 4))
        for key, value in props.items():
            if key not in name_offsets:
                name_offsets[key] = len(strings)
                strings.extend(key.encode() + b'\0')
            structure.extend(cells(3, len(value), name_offsets[key]) + value)
            structure.extend(b'\0' * (-len(structure) % 4))
        for child in children:
            emit(child)
        structure.extend(cells(2))
    emit(nodes[0])
    structure.extend(cells(9))
    offset_struct = 56
    offset_strings = offset_struct + len(structure)
    header = cells(0xd00dfeed, offset_strings + len(strings), offset_struct,
                   offset_strings, 40, 17, 16, 0, len(strings), len(structure))
    return header + b'\0' * 16 + structure + strings


def adt_fixture():
    root = recovery.adt.Node()
    root.props = [('name', 0, b'device-tree\0'), ('keep', 0, b'untouched')]
    child = recovery.adt.Node()
    child.props = [('name', 0, b'dali-watchdog\0'),
                   ('compatible', 0, b'mediatek,mt6991-wdt\0mediatek,mt6589-wdt\0'),
                   ('reg', 0, struct.pack('<QQ', 0x1c010000, 0x1000))]
    root.children = [child]
    return recovery.adt.serialize(root)


LIMIT_PATCH = ROOT / 'xnu/patches/0187-dali-extend-bounded-return-lease.patch'
INTERVAL_PATCH = ROOT / 'xnu/patches/0188-dali-fastboot-supported-sample-intervals.patch'
UFS_CAP_PATCH = ROOT / 'xnu/patches/0190-sweet-ufs-io-capabilities.patch'
UFS_POLL_PATCH = ROOT / 'xnu/patches/0193-sweet-ufs-bounded-poll-adt.patch'
BASE_PATCH = ROOT / 'xnu/patches/0180-sweet-dali-pwm-link-policy.patch'
POLICY_PATH = 'pexpert/arm/DaliFastbootReturnPolicy.h'
NATIVE_PATH = 'pexpert/arm/pe_dali_watchdog.c'
POLICY = apply_exact_hunks(fragment(POLICY_PATH, BASE_PATCH), POLICY_PATH, LIMIT_PATCH)
NATIVE_BASE = fragment(NATIVE_PATH, BASE_PATCH)
NATIVE = apply_exact_hunks(NATIVE_BASE, NATIVE_PATH, INTERVAL_PATCH)
MOCK = r'''
#include <assert.h>
#include <stdio.h>
#include <string.h>
static uint32_t registers[10],writes,resets,drains,records,fences;
static bool wrong_marker,fail_drain,stale_mode,expire_drain;
static uint64_t ticks;
static uint32_t rd(void *c,uint32_t off){(void)c;return registers[off/4];}
static void wr(void *c,uint32_t off,uint32_t value){(void)c;assert(off==0x24);++writes;registers[off/4]=wrong_marker?value^1:value;}
static void fence(void *c){(void)c;++fences;}
static void record(void *c,const char *s,uint32_t m,uint32_t mode){(void)c;(void)m;(void)mode;assert(s);++records;}
static uint64_t now(void *c){(void)c;return ticks;}
static bool drain(void *c,uint64_t deadline){(void)c;++drains;assert(deadline>ticks);if(stale_mode)registers[0]^=8;if(expire_drain)ticks=deadline;return !fail_drain;}
static uint32_t reset(void *c){(void)c;++resets;return (uint32_t)-1;}
static const struct dali_fb_ops ops={0,rd,wr,fence,record,now,drain,reset};
static struct dali_fb_state state;
static void initial(void){memset(&state,0,sizeof(state));memset(registers,0,sizeof(registers));registers[0]=0x17d;registers[1]=0xf000;registers[9]=0x40002000;writes=resets=drains=records=fences=0;wrong_marker=fail_drain=stale_mode=expire_drain=false;ticks=1000;}
static bool arm(void){return dali_fb_arm(&state,&ops,true,true,1000,13000000,1080,1140);}
'''

# Compile the actual patched prepare function and actual policy together; only the
# boot-argument, device-tree, clock, atomic and register interfaces are faked.
PREPARE_MOCK = r'''
#include <assert.h>
#include <stdio.h>
#include <string.h>
typedef void *DTEntry;
#define kSuccess 0
#define DALI_WDT_PA 0x1c010000ULL
#define DALI_WDT_SIZE 0x1000ULL
#define os_atomic_store(p,v,order) (*(p)=(v))
enum { ENABLED=1, HEARTBEAT=2, DELAY=4, INTERVAL=8, NAMES=16 };
static struct {
    unsigned int present, enabled, heartbeat, delay, interval;
    char names[8];
    bool contract_present, contract_null, policy_present, policy_null;
    unsigned int contract_size, policy_size;
} args;
static uint32_t contract[3], registers[10];
static uint8_t policy[20];
static uint64_t ticks, dali_wdt_hz;
static unsigned int dali_wdt_boot_seconds, reads, writes, fences, records, drains, resets;
static unsigned int contract_calls, policy_calls, config_refused, contract_refused, budget_refused;
static struct dali_fb_state dali_fastboot_state;
static uint32_t dali_fastboot_ready;
static char dali_fastboot_token[33];

static void set_after(uint32_t after) { memcpy(policy, &after, sizeof(after)); }
static void fixture_reset(void) {
    memset(&args, 0, sizeof(args));
    args.present=ENABLED|HEARTBEAT|DELAY|INTERVAL|NAMES;
    args.enabled=1; args.heartbeat=1; args.delay=30; args.interval=60;
    memcpy(args.names, "-", 2);
    args.contract_present=args.policy_present=true;
    args.contract_size=sizeof(contract); args.policy_size=sizeof(policy);
    contract[0]=0x24; contract[1]=15; contract[2]=3;
    memset(policy, 0, sizeof(policy)); set_after(2280);
    for (unsigned int i=0; i<16; ++i) policy[4+i]=(uint8_t)(i+1);
    memset(registers, 0, sizeof(registers));
    registers[0]=0x17d; registers[1]=0xf000; registers[9]=0x40002000;
    memset(&dali_fastboot_state, 0, sizeof(dali_fastboot_state));
    memset(dali_fastboot_token, 0, sizeof(dali_fastboot_token));
    dali_fastboot_ready=0; dali_wdt_hz=13000000; dali_wdt_boot_seconds=2400; ticks=1000;
    reads=writes=fences=records=drains=resets=contract_calls=policy_calls=0;
    config_refused=contract_refused=budget_refused=0;
}
static int PE_parse_boot_argn(const char *name, void *out, int size) {
    unsigned int bit, value=0;
    if (!strcmp(name, "dalifastboot")) { bit=ENABLED; value=args.enabled; }
    else if (!strcmp(name, "sweetheartbeat")) { bit=HEARTBEAT; value=args.heartbeat; }
    else if (!strcmp(name, "sweetsampledelay")) { bit=DELAY; value=args.delay; }
    else if (!strcmp(name, "sweetsampleinterval")) { bit=INTERVAL; value=args.interval; }
    else { assert(!strcmp(name, "sweetsample")); bit=NAMES; }
    if (!(args.present & bit)) return 0;
    if (bit==NAMES) { assert(size==(int)sizeof(args.names)); memcpy(out,args.names,sizeof(args.names)); }
    else { assert(size==(int)sizeof(value)); memcpy(out,&value,sizeof(value)); }
    return 1;
}
static int SecureDTGetProperty(DTEntry entry, const char *name, const void **out, unsigned int *size) {
    assert(entry==&args);
    if (!strcmp(name,"fastboot-return-contract")) {
        ++contract_calls; if (!args.contract_present) return -1;
        *out=args.contract_null ? NULL : contract; *size=args.contract_size; return kSuccess;
    }
    assert(!strcmp(name,"fastboot-return-policy")); ++policy_calls;
    if (!args.policy_present) return -1;
    *out=args.policy_null ? NULL : policy; *size=args.policy_size; return kSuccess;
}
static uint64_t mach_absolute_time(void) { return ticks; }
static uint32_t rd(void *c,uint32_t off) { (void)c; ++reads; assert(off<sizeof(registers) && off%4==0); return registers[off/4]; }
static void wr(void *c,uint32_t off,uint32_t value) { (void)c; assert(off==0x24); ++writes; registers[off/4]=value; }
static void fence(void *c) { (void)c; ++fences; }
static void record(void *c,const char *stage,uint32_t marker,uint32_t mode) { (void)c; (void)marker; (void)mode; assert(stage); ++records; }
static uint64_t now(void *c) { (void)c; return ticks; }
static bool drain(void *c,uint64_t deadline) { (void)c; assert(deadline>ticks); ++drains; return true; }
static uint32_t reset(void *c) { (void)c; ++resets; return 0; }
static const struct dali_fb_ops dali_fastboot_ops={0,rd,wr,fence,record,now,drain,reset};
static int fixture_printf(const char *format, ...) {
    if (!strcmp(format,"SWEET FASTBOOT: v=1 stage=config-refused\n")) ++config_refused;
    else if (!strcmp(format,"SWEET FASTBOOT: v=1 stage=contract-refused\n")) ++contract_refused;
    else { assert(!strcmp(format,"SWEET FASTBOOT: v=1 stage=budget-refused\n")); ++budget_refused; }
    return 0;
}
#define printf fixture_printf
#define ASSERT_UNTOUCHED() assert(!dali_fastboot_ready && !dali_fastboot_state.phase && !reads && !writes && !fences && !records && !drains && !resets)
#define ASSERT_CONFIG_REFUSED() do { ASSERT_UNTOUCHED(); assert(config_refused==1 && !contract_refused && !budget_refused); } while (0)
#define ASSERT_CONTRACT_REFUSED() do { ASSERT_UNTOUCHED(); assert(!config_refused && contract_refused==1 && !budget_refused); } while (0)
#define ASSERT_BUDGET_REFUSED() do { ASSERT_UNTOUCHED(); assert(!config_refused && !contract_refused && budget_refused==1); } while (0)
'''


class FastbootReturnTests(unittest.TestCase):
    def run_c(self, body, prefix=POLICY + MOCK):
        compiler = shutil.which('clang') or shutil.which('cc')
        self.assertIsNotNone(compiler)
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            source = directory / 'test.c'
            binary = directory / 'test'
            if prefix == POLICY + MOCK:
                body = body.replace('int main(void){',
                    'int main(void){(void)&arm;(void)&dali_fb_contract_valid;'
                    '(void)&dali_fb_step;(void)&dali_fb_root_failure;'
                    '(void)writes;(void)records;(void)fences;', 1)
            source.write_text(prefix + '\n' + body)
            compiled = subprocess.run([compiler, '-std=c11', '-O2', '-Wall', '-Wextra', '-Werror',
                                       str(source), '-o', str(binary)], capture_output=True, text=True)
            self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
            result = subprocess.run([str(binary)], text=True, capture_output=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(result.stdout.strip(), 'PASS')

    def run_prepare_c(self, body, native=NATIVE):
        body = body.replace('int main(void) {',
                            'int main(void) { (void)&dali_fb_step; (void)&dali_fb_root_failure;', 1)
        self.run_c(body, POLICY + PREPARE_MOCK + function(native, 'dali_fastboot_prepare'))

    def test_actual_prepare_supported_intervals_and_old_source_negative_control(self):
        # Execute the pre-0188 function too: the real 60-second trial must fail there.
        self.run_prepare_c(r'''
int main(void) {
    fixture_reset(); dali_fastboot_prepare(&args); ASSERT_CONFIG_REFUSED();
    fixture_reset(); args.interval=30; set_after(1080); dali_wdt_boot_seconds=1140;
    dali_fastboot_prepare(&args);
    assert(dali_fastboot_ready==1 && dali_fastboot_state.phase==1 && writes==1);
    puts("PASS");
}
''', NATIVE_BASE)
        self.run_prepare_c(r'''
int main(void) {
    for (unsigned int interval=0; interval<=3600; ++interval) {
        if (interval==30 || interval==60) continue;
        fixture_reset(); args.interval=interval; dali_fastboot_prepare(&args);
        ASSERT_CONFIG_REFUSED(); assert(!contract_calls && !policy_calls);
    }
    fixture_reset(); args.interval=UINT32_MAX; dali_fastboot_prepare(&args);
    ASSERT_CONFIG_REFUSED();
    for (unsigned int interval=30; interval<=60; interval+=30) {
        fixture_reset(); args.interval=interval;
        if (interval==30) { set_after(1080); dali_wdt_boot_seconds=1140; }
        dali_fastboot_prepare(&args);
        unsigned int after=interval==30 ? 1080 : 2280;
        unsigned int lease=interval==30 ? 1140 : 2400;
        assert(!config_refused && !contract_refused && !budget_refused);
        assert(contract_calls==1 && policy_calls==1 && dali_fastboot_ready==1);
        assert(dali_fastboot_state.phase==1 && dali_fastboot_state.after==after);
        assert(dali_fastboot_state.lease==lease && dali_fastboot_state.frequency==13000000);
        assert(!strcmp(dali_fastboot_token,"0102030405060708090a0b0c0d0e0f10"));
        assert(writes==1 && fences==1 && records==1 && registers[9]==0x40002003);
        assert(registers[0]==0x17d && registers[1]==0xf000 && !drains && !resets);
        ticks=1000+(uint64_t)(after-1)*13000000;
        assert(!dali_fb_step(&dali_fastboot_state,&dali_fastboot_ops) && !drains && !resets);
        /* Synthetic sampler alignment: 30s delay, then 30s/60s intervals. */
        ticks=1000+(uint64_t)(interval==30 ? 1080 : 2310)*13000000;
        assert(!dali_fb_step(&dali_fastboot_state,&dali_fastboot_ops));
        assert(drains==1 && resets==1 && dali_fastboot_state.phase==3);
        assert(registers[9]==0x40002000 && registers[0]==0x17d && registers[1]==0xf000);
    }
    /* A late poll at the unchanged lease cutoff must not attempt a reset. */
    fixture_reset(); set_after(120); dali_wdt_boot_seconds=180;
    dali_fastboot_prepare(&args); assert(dali_fastboot_ready==1);
    ticks=1000+170ULL*13000000;
    assert(!dali_fb_step(&dali_fastboot_state,&dali_fastboot_ops));
    assert(dali_fastboot_state.phase==5 && !drains && !resets && registers[9]==0x40002003);
    puts("PASS");
}
''')

    def test_actual_prepare_preserves_opt_in_contract_and_budget_guards(self):
        self.run_prepare_c(r'''
int main(void) {
    const unsigned int bits[]={ENABLED,HEARTBEAT,DELAY,INTERVAL,NAMES};
    const unsigned int bad_heartbeat[]={0,2,UINT32_MAX};
    const unsigned int bad_delay[]={0,29,31,60,UINT32_MAX};
    const char *bad_names[]={"", "--", "-x", "SpringB", "--------"};
    for (unsigned int interval=30; interval<=60; interval+=30) {
        for (unsigned int i=0; i<sizeof(bits)/sizeof(bits[0]); ++i) {
            fixture_reset(); args.interval=interval; args.present &= ~bits[i];
            dali_fastboot_prepare(&args);
            if (bits[i]==ENABLED) { ASSERT_UNTOUCHED(); assert(!config_refused); }
            else ASSERT_CONFIG_REFUSED();
            assert(!contract_calls && !policy_calls);
        }
        for (unsigned int enabled=0; enabled<=2; enabled+=2) {
            fixture_reset(); args.interval=interval; args.enabled=enabled;
            dali_fastboot_prepare(&args); ASSERT_UNTOUCHED();
            assert(!config_refused && !contract_calls && !policy_calls);
        }
        for (unsigned int i=0; i<sizeof(bad_heartbeat)/sizeof(bad_heartbeat[0]); ++i) {
            fixture_reset(); args.interval=interval; args.heartbeat=bad_heartbeat[i];
            dali_fastboot_prepare(&args); ASSERT_CONFIG_REFUSED(); assert(!contract_calls);
        }
        for (unsigned int i=0; i<sizeof(bad_delay)/sizeof(bad_delay[0]); ++i) {
            fixture_reset(); args.interval=interval; args.delay=bad_delay[i];
            dali_fastboot_prepare(&args); ASSERT_CONFIG_REFUSED(); assert(!contract_calls);
        }
        for (unsigned int i=0; i<sizeof(bad_names)/sizeof(bad_names[0]); ++i) {
            fixture_reset(); args.interval=interval; memset(args.names,0,sizeof(args.names));
            size_t n=strlen(bad_names[i]); if (n>sizeof(args.names)) n=sizeof(args.names);
            memcpy(args.names,bad_names[i],n);
            dali_fastboot_prepare(&args); ASSERT_CONFIG_REFUSED(); assert(!contract_calls);
        }
        for (unsigned int i=0; i<4; ++i) {
            fixture_reset(); args.interval=interval;
            if (i==0) args.contract_present=false;
            if (i==1) args.contract_null=true;
            if (i==2) --args.contract_size;
            if (i==3) ++args.contract_size;
            dali_fastboot_prepare(&args); ASSERT_CONFIG_REFUSED(); assert(contract_calls==1 && !policy_calls);
        }
        for (unsigned int i=0; i<3; ++i) {
            fixture_reset(); args.interval=interval; ++contract[i];
            dali_fastboot_prepare(&args); ASSERT_CONTRACT_REFUSED(); assert(contract_calls==1 && !policy_calls);
        }
        for (unsigned int i=0; i<4; ++i) {
            fixture_reset(); args.interval=interval;
            if (i==0) args.policy_present=false;
            if (i==1) args.policy_null=true;
            if (i==2) --args.policy_size;
            if (i==3) ++args.policy_size;
            dali_fastboot_prepare(&args); ASSERT_CONTRACT_REFUSED(); assert(contract_calls==1 && policy_calls==1);
        }
        const uint32_t bad_after[]={0,119,2281,UINT32_MAX};
        for (unsigned int i=0; i<sizeof(bad_after)/sizeof(bad_after[0]); ++i) {
            fixture_reset(); args.interval=interval; set_after(bad_after[i]);
            dali_fastboot_prepare(&args); ASSERT_BUDGET_REFUSED();
        }
        for (unsigned int i=0; i<5; ++i) {
            fixture_reset(); args.interval=interval;
            if (i==0) memset(policy+4,0,16);
            if (i==1) dali_wdt_hz=0;
            if (i==2) --dali_wdt_hz;
            if (i==3) dali_wdt_boot_seconds=2339;
            if (i==4) dali_wdt_boot_seconds=2401;
            dali_fastboot_prepare(&args); ASSERT_BUDGET_REFUSED();
        }
        const uint32_t valid[][2]={{120,180},{2280,2340},{2280,2400}};
        for (unsigned int i=0; i<sizeof(valid)/sizeof(valid[0]); ++i) {
            fixture_reset(); args.interval=interval; set_after(valid[i][0]);
            dali_wdt_boot_seconds=valid[i][1]; dali_fastboot_prepare(&args);
            assert(dali_fastboot_ready==1 && dali_fastboot_state.phase==1 && writes==1);
        }
        fixture_reset(); args.interval=interval; ticks=UINT64_MAX;
        dali_fastboot_prepare(&args); ASSERT_UNTOUCHED();
        assert(!config_refused && !contract_refused && !budget_refused);
        fixture_reset(); args.interval=interval; registers[0]=0x17c;
        dali_fastboot_prepare(&args);
        assert(!dali_fastboot_ready && !dali_fastboot_state.phase && reads==3 && !writes);
    }
    puts("PASS");
}
''')

    def test_default_off_and_exact_contract_budget(self):
        self.run_c(r'''
int main(void){initial();
assert(dali_fb_contract_valid(0x1c010000,0x1000,0x24,15,3));
assert(!dali_fb_contract_valid(0x1c010001,0x1000,0x24,15,3));
assert(!dali_fb_contract_valid(0x1c010000,0x1000,0x20,15,3));
assert(!dali_fb_contract_valid(0x1c010000,0x1000,0x24,255,3));
assert(DALI_FB_MAX_AFTER==2280 && DALI_FB_MAX_LEASE==2400);
for(unsigned a=0;a<=2281;++a){bool valid=a>=120&&a<=2280;assert(dali_fb_parameters_valid(13000000,a,2400)==valid);}
assert(dali_fb_parameters_valid(13000000,1080,1140));
assert(dali_fb_parameters_valid(13000000,2280,2340));
assert(dali_fb_parameters_valid(13000000,2280,2400));
assert(!dali_fb_parameters_valid(13000000,2280,2339));
assert(!dali_fb_parameters_valid(13000000,2280,2401));
assert(!dali_fb_parameters_valid(13000000,2281,2400));
assert(!dali_fb_parameters_valid(13000000,UINT32_MAX,2400));
assert(!dali_fb_parameters_valid(0,1080,1140));assert(!dali_fb_parameters_valid(13000000,1080,1139));
assert(!dali_fb_arm(&state,&ops,false,true,1000,13000000,1080,1140));
assert(!dali_fb_arm(&state,&ops,true,false,1000,13000000,1080,1140));
assert(!dali_fb_arm(&state,&ops,true,true,UINT64_MAX-1,13000000,1080,1140));
assert(!writes&&!records&&!drains&&!resets);assert(!dali_fb_step(&state,&ops));puts("PASS");}
''')

    def test_marker_preserves_other_bits_mode_length_and_single_arm(self):
        self.run_c(r'''
int main(void){initial();assert(arm());assert(registers[9]==0x40002003);assert(registers[0]==0x17d&&registers[1]==0xf000);
assert(writes==1&&state.phase==1);assert(!arm()&&writes==1);assert(!dali_fb_step(&state,&ops));
assert(!resets&&!drains);puts("PASS");}
''')

    def test_arm_errors_readback_and_rollback_never_touch_watchdog(self):
        self.run_c(r'''
int main(void){initial();registers[9]=UINT32_MAX;assert(!arm()&&!writes);
initial();registers[0]=0x17c;assert(!arm()&&!writes);
initial();registers[9]=0x40002003;assert(!arm()&&!writes);
initial();wrong_marker=true;assert(!arm());assert(writes==2&&state.phase==4&&!resets);
assert(registers[0]==0x17d&&registers[1]==0xf000);assert(!arm()&&writes==2);puts("PASS");}
''')

    def test_deadline_drain_order_reset_return_and_no_retry(self):
        self.run_c(r'''
int main(void){initial();assert(arm());ticks=999;assert(!dali_fb_step(&state,&ops));
ticks=1000+1080ULL*13000000-1;assert(!dali_fb_step(&state,&ops));assert(!drains);
++ticks;assert(!dali_fb_step(&state,&ops));assert(drains==1&&resets==1&&state.phase==3);
assert(registers[9]==0x40002000&&registers[0]==0x17d&&registers[1]==0xf000);
assert(!dali_fb_step(&state,&ops)&&resets==1);puts("PASS");}
''')

    def test_extended_deadline_still_requires_drain_and_unchanged_registers(self):
        self.run_c(r'''
int main(void){initial();
assert(dali_fb_arm(&state,&ops,true,true,1000,13000000,2280,2400));
ticks=1000+2280ULL*13000000-1;assert(!dali_fb_step(&state,&ops));assert(!drains&&!resets);
++ticks;assert(!dali_fb_step(&state,&ops));
assert(drains==1&&resets==1&&state.phase==3);
assert(registers[9]==0x40002000&&registers[0]==0x17d&&registers[1]==0xf000);
initial();assert(dali_fb_arm(&state,&ops,true,true,1000,13000000,2280,2400));
ticks=1000+2340ULL*13000000;
assert(!dali_fb_step(&state,&ops));assert(drains==1&&resets==1&&state.phase==3);
initial();assert(dali_fb_arm(&state,&ops,true,true,1000,13000000,2280,2400));
fail_drain=true;ticks=1000+2280ULL*13000000;
assert(!dali_fb_step(&state,&ops));assert(state.phase==5&&!resets&&registers[9]==0x40002003);
initial();assert(dali_fb_arm(&state,&ops,true,true,1000,13000000,2280,2400));
ticks=1000+2390ULL*13000000;
assert(!dali_fb_step(&state,&ops));assert(state.phase==5&&!drains&&!resets&&registers[9]==0x40002003);
puts("PASS");}
''')

    def test_unconfirmed_drain_state_clock_keep_marker_no_cold_fallback(self):
        self.run_c(r'''
int main(void){initial();assert(arm());fail_drain=true;ticks=1000+1080ULL*13000000;
assert(!dali_fb_step(&state,&ops));assert(state.phase==5&&!resets&&registers[9]==0x40002003);
initial();assert(arm());stale_mode=true;ticks=1000+1080ULL*13000000;assert(!dali_fb_step(&state,&ops)&&!resets&&state.phase==5);
initial();assert(arm());expire_drain=true;ticks=1000+1080ULL*13000000;assert(!dali_fb_step(&state,&ops)&&!resets);
initial();assert(arm());ticks=1000+1130ULL*13000000;assert(!dali_fb_step(&state,&ops)&&!drains&&!resets);
puts("PASS");}
''')

    def test_actual_logger_drain_waiter_never_takes_ufs_lock(self):
        ufs = fragment('bsd/dev/sweet_ufs.c')
        functions = function(ufs, 'sweet_log_fastboot_publish') + function(ufs, 'sweet_log_fastboot_drain')
        for forbidden in ('lck_mtx_', 'memcpy', 'dma_', 'ufs_scsi', 'su.command_poisoned'):
            self.assertNotIn(forbidden, functions)
        prefix = r'''
#include <stdbool.h>
#include <stdint.h>
#include <assert.h>
#include <stdio.h>
#define os_atomic(x) x
#define os_atomic_load(p,o) (*(p))
#define os_atomic_store(p,v,o) (*(p)=(v))
#define PZERO 0
static struct {bool dali,log_partition_ready;} su;
static uint32_t sweet_log_fastboot_requested,sweet_log_fastboot_running;
static uint64_t sweet_log_fastboot_persisted,sweet_log_total,ticks;
static int sweet_log_fastboot_chan,sweet_log_chan,hz=100;
static bool irq=true,in_irq,stall_clock,complete;static int preempt,sleeps,wakes;
static bool ml_get_interrupts_enabled(void){return irq;}
static bool ml_at_interrupt_context(void){return in_irq;}
static int get_preemption_level(void){return preempt;}
static uint64_t mach_absolute_time(void){return ticks;}
static void wakeup(void *p){assert(p==&sweet_log_fastboot_chan||p==&sweet_log_chan);++wakes;}
static void sweet_log_fastboot_publish(uint64_t);
static int tsleep(void *p,int pri,const char *name,int count){(void)name;assert(p==&sweet_log_fastboot_chan&&pri==PZERO&&count==1);++sleeps;if(!stall_clock)++ticks;if(complete)sweet_log_fastboot_publish(sweet_log_total);return 0;}
''' + functions
        self.run_c(r'''
int main(void){su.dali=true;su.log_partition_ready=true;sweet_log_fastboot_running=1;sweet_log_total=99;
complete=true;assert(sweet_log_fastboot_drain(100));assert(sleeps==1&&sweet_log_fastboot_persisted==99&&wakes>=2);
sweet_log_fastboot_persisted=0;complete=false;ticks=0;sleeps=0;assert(!sweet_log_fastboot_drain(4)&&sleeps==4);
ticks=0;sleeps=0;stall_clock=true;assert(!sweet_log_fastboot_drain(100)&&sleeps==2000);
irq=false;sleeps=0;assert(!sweet_log_fastboot_drain(100)&&!sleeps);irq=true;in_irq=true;assert(!sweet_log_fastboot_drain(100));
in_irq=false;preempt=1;assert(!sweet_log_fastboot_drain(100));puts("PASS");}
''', prefix)

    def test_smc_real_arm64_object_has_cold_id_zero_arguments_single_instruction(self):
        compiler = shutil.which('clang')
        disassembler = shutil.which('llvm-objdump') or shutil.which('objdump')
        self.assertIsNotNone(compiler)
        self.assertIsNotNone(disassembler)
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            source = directory / 'cold.c'
            source.write_text(fragment('pexpert/arm/DaliFastbootReturnSMC.h') +
                              '\nuint32_t invoke(void){return dali_fb_psci_cold();}\n')
            target = 'arm64-apple-macos14.0' if sys.platform == 'darwin' else 'aarch64-none-elf'
            obj = directory / 'cold.o'
            subprocess.run([compiler, '-target', target, '-ffreestanding', '-O2', '-Wall',
                            '-Wextra', '-Werror', '-c', str(source), '-o', str(obj)],
                           check=True, capture_output=True, text=True)
            result = subprocess.run([disassembler, '-d', str(obj)], check=True,
                                    text=True, capture_output=True)
            text = result.stdout.lower()
            self.assertEqual(len(re.findall(r'\bsmc\s+', text)), 1, text)
            self.assertIn('dsb', text)
            self.assertIn('daif', text)
            self.assertRegex(text, r'(?:#0x8400|#33792)')
            for register in range(1, 8):
                self.assertRegex(text, rf'\b(?:x|w){register},\s*(?:[xw]zr|#0(?:x0)?\b)')
            self.assertNotIn('c4000012', text.lower())

    def test_fdt_adt_only_two_properties_roundtrip_and_cli(self):
        original = adt_fixture()
        output, record = recovery.prepare(fdt_fixture(), original, '1' * 32, 1080, 1140)
        root = recovery.parse_raw_adt(output)
        target = root.children[0]
        self.assertEqual(target.prop('fastboot-return-contract'), struct.pack('<III', 0x24, 15, 3))
        self.assertEqual(target.prop('fastboot-return-policy'), struct.pack('<I16s', 1080, b'\x11' * 16))
        target.props = target.props[:-2]
        self.assertEqual(recovery.adt.serialize(root), original)
        self.assertFalse(record['device_accessed'])
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            (directory / 'fdt').write_bytes(fdt_fixture())
            (directory / 'adt').write_bytes(original)
            result = subprocess.run([sys.executable, '-I', str(ROOT / 'tools/dali_recovery_adt.py'),
                                     '--fdt', str(directory / 'fdt'), '--adt', str(directory / 'adt'),
                                     '--output', str(directory / 'new.adt'), '--run-token', '1' * 32],
                                    capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((directory / 'new.adt').read_bytes(), output)
            self.assertEqual(json.loads((directory / 'new.adt.json').read_text()), record)
            self.assertEqual((directory / 'adt').read_bytes(), original)

    def test_fdt_wrong_modes_timer_psci_and_duplicate_adt_refused(self):
        for options in ({'offset': 0x20}, {'mask': 255}, {'bootloader': 4},
                        {'hz': 24000000}, {'method': b'hvc\0'}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                recovery.prepare(fdt_fixture(**options), adt_fixture(), '1' * 32, 1080, 1140)
        output, _ = recovery.prepare(fdt_fixture(), adt_fixture(), '1' * 32, 1080, 1140)
        with self.assertRaisesRegex(ValueError, '已存在'):
            recovery.prepare(fdt_fixture(), output, '2' * 32, 1080, 1140)
        for token in ('0' * 32, 'A' * 32, 'x', ''):
            with self.subTest(token=token), self.assertRaises(ValueError):
                recovery.prepare(fdt_fixture(), adt_fixture(), token, 1080, 1140)
        for after, lease in ((119, 1140), (1081, 1140), (1080, 1139), (1080, 1200)):
            with self.subTest(after=after, lease=lease), self.assertRaises(ValueError):
                recovery.prepare(fdt_fixture(), adt_fixture(), '1' * 32, after, lease)

    def test_adt_corruption_and_existing_output_do_not_overwrite(self):
        for damaged in (adt_fixture()[:-1], adt_fixture() + b'junk', b'\xff' * 64):
            with self.subTest(length=len(damaged)), self.assertRaises(ValueError):
                recovery.prepare(fdt_fixture(), damaged, '1' * 32, 1080, 1140)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'keep'
            path.write_bytes(b'keep')
            with self.assertRaises(ValueError):
                recovery.publish(path, b'changed', {})
            self.assertEqual(path.read_bytes(), b'keep')

    def test_final_sources_default_off_and_no_unrelated_transaction_changes(self):
        old_policy = fragment(POLICY_PATH, ROOT / 'xnu/patches/0180-sweet-dali-pwm-link-policy.patch')
        self.assertEqual(old_policy.count('#define DALI_FB_MAX_AFTER 1080u'), 1)
        self.assertEqual(old_policy.count('#define DALI_FB_MAX_LEASE 1140u'), 1)
        self.assertEqual(POLICY, old_policy.replace('#define DALI_FB_MAX_AFTER 1080u',
                                                    '#define DALI_FB_MAX_AFTER 2280u').replace(
            '#define DALI_FB_MAX_LEASE 1140u', '#define DALI_FB_MAX_LEASE 2400u'))
        self.assertEqual(hashlib.sha256(POLICY.encode()).hexdigest(),
                         'e293a1a0b051ba7724cfcbaa7fce15189f2ac4e54df417fd24a1184663c26297')
        old_guard = '    if (heartbeat != 1 || delay_seconds != 30 || interval != 30 ||\n'
        new_guard = ('    if (heartbeat != 1 || delay_seconds != 30 ||\n'
                     '        (interval != 30 && interval != 60) ||\n')
        self.assertEqual(NATIVE_BASE.count(old_guard), 1)
        self.assertEqual(NATIVE, NATIVE_BASE.replace(old_guard, new_guard))
        native = NATIVE
        sampler = fragment('bsd/dev/sweet_sample.c')
        self.assertIn('"dalifastboot"', native)
        self.assertIn('enabled != 1', native)
        write = function(native, 'dali_fastboot_write')
        self.assertIn('offset == DALI_FB_MARKER_OFFSET', write)
        self.assertNotIn('0x22000000', native)
        self.assertEqual(sampler.count('dali_fastboot_return_poll();'), 1)
        self.assertEqual(PATCH.read_text().count('diff --git '), 6)
        source_dir = os.environ.get('SWEET_LOAD_SOURCE_DIR')
        if source_dir:
            for path in ('pexpert/arm/pe_dali_watchdog.c', 'pexpert/arm/DaliFastbootReturnPolicy.h',
                         'pexpert/arm/DaliFastbootReturnSMC.h', 'osfmk/arm64/dali_watchdog.h',
                         'bsd/dev/sweet_sample.c', 'bsd/dev/sweet_ufs.c'):
                if path == 'bsd/dev/sweet_sample.c':
                    expected = (Path(source_dir) / path).read_text()
                    self.assertEqual(hashlib.sha256(expected.encode()).hexdigest(),
                                     '021bf0b0c785abd81cbc358722fa3a72965a5ca1206b36eb04500cf52aae3b16')
                elif path == POLICY_PATH:
                    expected = POLICY
                elif path == NATIVE_PATH:
                    expected = NATIVE
                elif path == 'bsd/dev/sweet_ufs.c':
                    expected = apply_exact_hunks(fragment(path, BASE_PATCH), path, UFS_CAP_PATCH)
                    expected = apply_exact_hunks(expected, path, UFS_POLL_PATCH)
                elif path == 'osfmk/arm64/dali_watchdog.h':
                    expected = fragment(path, BASE_PATCH)
                else:
                    expected = fragment(path)
                self.assertEqual((Path(source_dir) / path).read_text(), expected, path)
                if path == 'bsd/dev/sweet_sample.c':
                    thread = function(expected, 'sweet_sample_thread')
                    for addition in ('    sweet_bbctx_window_begin();\n',
                                     '        sweet_bbctx_dump((unsigned int)i);\n'):
                        self.assertEqual(thread.count(addition), 1)
                        thread = thread.replace(addition, '')
                    self.assertEqual(thread, function(sampler, 'sweet_sample_thread'))
                    self.assertEqual(expected.count('dali_fastboot_return_poll();'), 1)


class UnattendedReturnTests(unittest.TestCase):
    def make_contract(self, pair):
        started = datetime.now(timezone.utc).isoformat()
        helpers = {}
        for filename in unattended.FILES:
            (pair / filename).write_text('raise SystemExit("fixture helper must not execute")\n')
            helpers[filename] = unattended.digest(pair / filename)
        state = dict(state='trial_rebooted', trial_attempts=1, approved_trials=1,
                     device_write_authorized=True, stock_restore_authorized=True,
                     root_restore_authorized=True, root_delta_authorized=False,
                     root_restore_pending=True, run=123, source_commit='c' * 40,
                     candidate_sha256={'boot': 'b' * 64}, root_base_sha256='a' * 64,
                     original_sha256={'boot_b.img': 'd' * 64}, serial='synthetic-dali',
                     observation_limit_seconds=1200, trial_started_utc=started, run_token='1' * 32)
        # A real static dependency graph, separate from mutable state/config.
        (pair / 'boot').write_bytes(b'synthetic boot')
        state['candidate_sha256'] = {'boot': unattended.digest(pair / 'boot')}
        for name in ('dali_trial_adapters.py', 'dali_unattended_return.py'):
            source = ROOT / 'tools' / name
            (pair / name).write_bytes(source.read_bytes())
        dependencies = {name: unattended.digest(pair / name) for name in
                        (*unattended.FILES, 'dali_trial_adapters.py', 'dali_unattended_return.py')}
        (pair / 'trial-dependencies.json').write_text(json.dumps(dict(schema=1, files=dependencies)))
        state.update(dependency_manifest_sha256=unattended.digest(pair / 'trial-dependencies.json'),
                     root_candidate_sha256='f' * 64, root_restore_target_sha256='a' * 64,
                     root_clean_image='/synthetic/root.img', restore_source='/synthetic/root.img')
        approval = {key: state[key] for key in ('run', 'source_commit', 'candidate_sha256',
                    'root_base_sha256', 'original_sha256', 'serial', 'run_token', 'approved_trials',
                    'dependency_manifest_sha256', 'root_candidate_sha256', 'root_restore_target_sha256',
                    'restore_source')}
        approval.update(initial_fastboot_and_flash_confirmed=True, unattended_return_authorized=True,
                        unattended_helper_sha256=helpers,
                        unattended_coordinator_sha256=unattended.digest(Path(unattended.__file__)))
        (pair / 'trial-approval.json').write_text(json.dumps(approval))
        digest = unattended.digest(pair / 'trial-approval.json')
        config = dict(schema=1, approval_sha256=digest, run_token='1' * 32,
                      window_seconds=1200, started_utc=started,
                      stages={stage: dict(file=name, sha256=helpers[name])
                              for stage, name in zip(unattended.STAGES, unattended.FILES)})
        (pair / 'trial-state.json').write_text(json.dumps(state))
        (pair / 'unattended-return.json').write_text(json.dumps(config))
        return digest, state, approval

    def test_dry_validation_zero_transport_and_inactive_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            pair = Path(temp)
            digest, state, _ = self.make_contract(pair)
            with mock.patch.object(unattended.subprocess, 'run', side_effect=AssertionError('transport')):
                self.assertEqual(unattended.recover(pair, digest)['status'], 'validated-no-transport')
                state['state'] = 'trial_closed_stock_and_new_b4ee_baseline_verified'
                (pair / 'trial-state.json').write_text(json.dumps(state))
                with self.assertRaisesRegex(ValueError, '不是当前'):
                    unattended.recover(pair, digest, execute=True)
            self.assertFalse((pair / 'unattended-return-claim.json').exists())

    def test_wrong_or_changed_helper_and_missing_human_approval_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            pair = Path(temp)
            digest, _, approval = self.make_contract(pair)
            with mock.patch.object(unattended.subprocess, 'run', side_effect=AssertionError('transport')):
                (pair / unattended.FILES[0]).write_text('changed')
                with self.assertRaisesRegex(ValueError, 'helper'):
                    unattended.recover(pair, digest, execute=True)
                approval['initial_fastboot_and_flash_confirmed'] = False
                (pair / 'trial-approval.json').write_text(json.dumps(approval))
                digest = unattended.digest(pair / 'trial-approval.json')
                config = json.loads((pair / 'unattended-return.json').read_text())
                config['approval_sha256'] = digest
                (pair / 'unattended-return.json').write_text(json.dumps(config))
                with self.assertRaisesRegex(ValueError, '明确批准'):
                    unattended.recover(pair, digest, execute=True)
            self.assertFalse((pair / 'unattended-return-claim.json').exists())

    def test_exact_fastboot_identity_slot_and_multiple_device_rejection(self):
        good = ['synthetic-dali\tfastboot\n', '(bootloader) product: dali\nFinished.\n',
                'current-slot: b\nFinished.\n']
        calls = []
        def runner(argv, timeout):
            calls.append(argv)
            return good[len(calls) - 1]
        self.assertTrue(unattended.verify_fastboot('synthetic-dali', runner))
        self.assertEqual(calls[0], ['fastboot', 'devices'])
        for output in ('other fastboot\n', 'synthetic-dali fastboot\nother fastboot\n',
                       'synthetic-dali device\n'):
            with self.subTest(output=output), self.assertRaises(ValueError):
                unattended.verify_fastboot('synthetic-dali', lambda *args: output)
        answers = iter(['synthetic-dali fastboot\n', 'product: other\n'])
        with self.assertRaises(ValueError):
            unattended.verify_fastboot('synthetic-dali', lambda *args: next(answers))

    def test_fixed_window_never_extends_or_accepts_late_query(self):
        clock = [1000.0]
        def sleeper(seconds):
            clock[0] += seconds
        self.assertFalse(unattended.wait_return('synthetic-dali', -199,
                         wall=lambda: clock[0], monotonic=lambda: clock[0],
                         sleeper=sleeper, runner=lambda *args: ''))
        self.assertEqual(clock[0], 1001)
        with self.assertRaises(ValueError):
            unattended.wait_return('synthetic-dali', -200, wall=lambda: 1000,
                                   monotonic=lambda: 1000, runner=lambda *args: '')

    def test_claim_is_single_use_and_timeout_never_runs_helper(self):
        with tempfile.TemporaryDirectory() as temp:
            pair = Path(temp)
            digest, _, _ = self.make_contract(pair)
            with mock.patch.object(unattended, 'wait_return', return_value=False), \
                 mock.patch.object(unattended.subprocess, 'run', side_effect=AssertionError('helper')):
                with self.assertRaisesRegex(ValueError, '窗口结束'):
                    unattended.recover(pair, digest, execute=True)
                with self.assertRaises(FileExistsError):
                    unattended.recover(pair, digest, execute=True)
            self.assertTrue((pair / 'unattended-return-claim.json').exists())

    def test_capture_and_closed_state_gates_before_following_stage(self):
        with tempfile.TemporaryDirectory() as temp:
            pair = Path(temp)
            _, state, approval = self.make_contract(pair)
            with self.assertRaises(ValueError):
                unattended.verify_stage(pair, 'stock_pair', approval)
            state.update(state='originals_verified', stock_restore_authorized=False)
            (pair / 'trial-state.json').write_text(json.dumps(state))
            unattended.verify_stage(pair, 'stock_pair', approval)
            capture = dict(status='verified', prefix_bytes=17179869184, serial=approval['serial'],
                           sha256='e' * 64, remote_before_sha256='e' * 64, remote_after_sha256='f' * 64)
            (pair / 'actual-capture-verified.json').write_text(json.dumps(capture))
            with self.assertRaises(ValueError):
                unattended.verify_stage(pair, 'capture', approval)
            capture['remote_after_sha256'] = 'e' * 64
            state['root_capture_verified_sha256'] = 'e' * 64
            (pair / 'trial-state.json').write_text(json.dumps(state))
            (pair / 'actual-capture-verified.json').write_text(json.dumps(capture))
            unattended.verify_stage(pair, 'capture', approval)
            with self.assertRaises(ValueError):
                unattended.verify_stage(pair, 'root_restore', approval)


if __name__ == '__main__':
    unittest.main()
