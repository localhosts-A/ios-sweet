#!/usr/bin/env python3
"""Exercise actual finite watchdog policy/poll code with no hardware access."""
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / 'xnu/patches/0167-dali-configurable-finite-boot-watchdog-lease.patch'


def final_file(path):
    for block in PATCH.read_text().split('diff --git ')[1:]:
        if f'b/{path}\n' in block:
            return ''.join(line[1:] for line in block.splitlines(keepends=True)
                           if line.startswith(('+', ' ')) and not line.startswith('+++'))
    raise AssertionError(path)


class WatchdogLeaseTest(unittest.TestCase):
    def run_c(self, body):
        compiler = shutil.which('clang') or shutil.which('cc')
        self.assertIsNotNone(compiler)
        source = '''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
''' + final_file('pexpert/arm/DaliWatchdogPolicy.h') + body
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'test.c').write_text(source)
            subprocess.run([compiler, '-std=c11', '-O2', '-Wall', '-Wextra', '-Werror',
                            str(root / 'test.c'), '-o', str(root / 'test')], check=True)
            result = subprocess.run([str(root / 'test')], capture_output=True, text=True,
                                    timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), 'PASS')

    def test_policy_defaults_boundaries_clock_overflow_and_rate_limit(self):
        self.run_c('''
int main(void) {
    assert(DALI_WDT_DEFAULT_SECONDS==120 && DALI_WDT_MAX_SECONDS==600);
    for(unsigned int n=0;n<1000;++n)
        assert(dali_wdt_select_lease(n)==((n>=120 && n<=600)?n:120));
    assert(dali_wdt_select_lease(UINT32_MAX)==120);
    const uint64_t hz=13000000,start=1000*hz;
    for(unsigned int sec=120;sec<=600;++sec) {
        assert(dali_wdt_reload_due(start,0,start,hz,sec));
        assert(dali_wdt_reload_due(start,0,start+sec*hz-1,hz,sec));
        assert(!dali_wdt_reload_due(start,0,start+sec*hz,hz,sec));
        assert(!dali_wdt_reload_due(start,0,start+(sec+1)*hz,hz,sec));
    }
    assert(!dali_wdt_reload_due(start,0,start,0,120));
    assert(!dali_wdt_reload_due(start,0,start,UINT64_MAX,600));
    assert(!dali_wdt_reload_due(start,0,start-1,hz,120));
    assert(!dali_wdt_reload_due(start,start+hz,start,hz,120));
    assert(!dali_wdt_reload_due(start,start,start+hz-1,hz,120));
    assert(dali_wdt_reload_due(start,start,start+hz,hz,120));
    assert(!dali_wdt_reload_due(start,0,start,hz,119));
    assert(!dali_wdt_reload_due(start,0,start,hz,601));
    puts("PASS");return 0;
}
''')

    def test_actual_poll_preserves_registers_disabled_state_cpu_and_expiry(self):
        source = final_file('pexpert/arm/pe_dali_watchdog.c')
        start = source.index('void\ndali_watchdog_poll(void)')
        end = source.index('\nvoid\ndali_watchdog_init(void)', start)
        poll = source[start:end]
        substitutions = {
            '__asm__ volatile("mrs %0, mpidr_el1" : "=r"(cpu));': 'cpu = test_cpu;',
            '__asm__ volatile("mrs %0, cntpct_el0" : "=r"(now));': 'now = test_now;',
            '__asm__ volatile("dsb sy" ::: "memory");': '++barriers;',
        }
        for old, new in substitutions.items():
            self.assertEqual(poll.count(old), 1)
            poll = poll.replace(old, new)
        self.run_c('''
#define DALI_WDT_RELOAD 0x1971u
static volatile uint32_t *dali_wdt;
static uint64_t dali_wdt_hz,dali_wdt_started;
static volatile uint64_t dali_wdt_last;
static unsigned int dali_wdt_boot_seconds=120,barriers;
static uint64_t test_now,test_cpu;
''' + poll + '''
int main(void) {
    volatile uint32_t regs[6]={0x17d,0xf000,0xfeed,0x11223344,0x55667788,0xaabbccdd};
    dali_watchdog_poll();assert(!barriers);
    dali_wdt=regs;dali_watchdog_poll();assert(!barriers && regs[2]==0xfeed);
    dali_wdt_hz=13000000;dali_wdt_started=1000*dali_wdt_hz;test_now=dali_wdt_started;
    test_cpu=1;dali_watchdog_poll();assert(!barriers && regs[2]==0xfeed);
    test_cpu=0;regs[0]=0x17c;dali_watchdog_poll();assert(!barriers && regs[2]==0xfeed);
    regs[0]=0x17d;dali_watchdog_poll();assert(barriers==1 && regs[2]==0x1971 && dali_wdt_last==test_now);
    regs[2]=0xfeed;test_now+=dali_wdt_hz-1;dali_watchdog_poll();assert(barriers==1 && regs[2]==0xfeed);
    ++test_now;dali_watchdog_poll();assert(barriers==2 && regs[2]==0x1971);
    test_now=dali_wdt_started+120*dali_wdt_hz;regs[2]=0xfeed;
    dali_watchdog_poll();assert(barriers==2 && regs[2]==0xfeed);
    dali_wdt_boot_seconds=600;dali_watchdog_poll();assert(barriers==3 && regs[2]==0x1971);
    test_now=dali_wdt_started+600*dali_wdt_hz;regs[2]=0xfeed;
    dali_watchdog_poll();assert(barriers==3 && regs[2]==0xfeed);
    assert(regs[0]==0x17d && regs[1]==0xf000 && regs[3]==0x11223344 && regs[4]==0x55667788 && regs[5]==0xaabbccdd);
    assert(dali_wdt_select_lease(0)==120);
    puts("PASS");return 0;
}
''')

    def test_exact_dt_gate_default_and_mmio_write_scope_remain(self):
        source = final_file('pexpert/arm/pe_dali_watchdog.c')
        self.assertIn('#define DALI_WDT_BOOT_SECONDS 120u', source)
        self.assertIn('PE_parse_boot_argn("daliwdtlease"', source)
        self.assertIn('dali_wdt_boot_seconds = dali_wdt_select_lease(requested)', source)
        self.assertIn('reg[0] != DALI_WDT_PA || reg[1] != DALI_WDT_SIZE', source)
        self.assertIn('"mediatek,mt6991-wdt"', source)
        self.assertIn('cpu & 0xff00ffffffULL', source)
        self.assertIn('if (!(dali_wdt[0] & 1u))', source)
        self.assertEqual(re.findall(r'\bdali_wdt\[(\d+)\]\s*=', source), ['2'])
        self.assertIn('dali_wdt[2] = DALI_WDT_RELOAD;', source)
        self.assertIn('printf("DALI WDT: mode=0x%x length=0x%x boot lease=%us', source)
        poll = source[source.index('void\ndali_watchdog_poll(void)'):
                      source.index('void\ndali_watchdog_init(void)')]
        self.assertNotIn('printf(', poll)
        self.assertNotIn('PE_parse_boot_argn', poll)


if __name__ == '__main__':
    unittest.main()
