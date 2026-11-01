#!/usr/bin/env python3
"""Execute final0180 pure clock policy and actual read-only mapping helper.
Synthetic registers only: not cold-XNU register values or a hardware PWM proof.
"""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import test_dali_gui_load_progress as source

HEADER = source.SOURCE['bsd/dev/SweetUFSPWMClock.h']
HELPER = source.function(source.UFS, 'ufs_pwm_clock_cap')

MOCK = r'''
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "SweetUFSPWMClock.h"
typedef uintptr_t vm_offset_t;
static uint32_t top[0x800/4],pll[0x1000/4],saved_top[0x800/4],saved_pll[0x1000/4];
static unsigned maps,fences,fail_map,mutate;
static vm_offset_t ml_io_map(uint64_t address,unsigned size){
 ++maps;
 if(address==0x10000000ull){assert(size==0x800);if(fail_map==1)return 0;return (uintptr_t)top;}
 assert(address==0x10000800ull && size==0x1000);
 if(fail_map==2)return 0;return (uintptr_t)pll;
}
static void clock_fence(unsigned operand){
 assert(operand==0xf);++fences;
 if(fences==1 && mutate){
  switch(mutate){case 1:top[0xd0/4]^=0x100;break;case 2:pll[0x3c/4]^=0x80;break;
   case 3:pll[0x254/4]^=1;break;case 4:pll[0x268/4]^=1;break;default:assert(0);}
 }
}
#define __builtin_arm_dsb(x) clock_fence(x)
static void reset(void){
 memset(top,0,sizeof(top));memset(pll,0,sizeof(pll));
 top[0xd0/4]=3u<<8;pll[0x3c/4]=0xc0;pll[0x254/4]=84u<<14;pll[0x268/4]=96u<<14;
 maps=fences=fail_map=mutate=0;
}
static void save(void){memcpy(saved_top,top,sizeof(top));memcpy(saved_pll,pll,sizeof(pll));}
static void unchanged(void){assert(!memcmp(saved_top,top,sizeof(top)) && !memcmp(saved_pll,pll,sizeof(pll)));}
'''


class ClockAdmissionTest(unittest.TestCase):
    def run_c(self, body, native=False):
        cc = shutil.which('clang') or shutil.which('cc')
        self.assertIsNotNone(cc)
        with tempfile.TemporaryDirectory(prefix='pwm-clock-') as td:
            p = Path(td)
            (p/'SweetUFSPWMClock.h').write_text(HEADER)
            prefix = MOCK + HELPER if native else '#include <assert.h>\n#include "SweetUFSPWMClock.h"\n'
            uses = '(void)&reset;(void)&save;(void)&unchanged;' if native else ''
            (p/'test.c').write_text(prefix+'\nint main(void){'+uses+body+'\nreturn 0;}\n')
            subprocess.run([cc, '-std=gnu11', '-O2', '-Wall', '-Wextra', '-Werror',
                            str(p/'test.c'), '-o', str(p/'test')], check=True)
            r = subprocess.run([str(p/'test')], capture_output=True, text=True, timeout=10)
            self.assertEqual(r.returncode, 0, r.stdout+r.stderr)

    def test_known_min_max_and_parent_rates(self):
        self.run_c(r'''
struct sweet_pwm_clock_snapshot s={.mux=3u<<8,.pll_status=0xc0,.main_con1=84u<<14,.univ_con1=96u<<14};
assert(sweet_pwm_pll_hz(s.main_con1)==2184000000ull);
assert(sweet_pwm_clock_hz(&s,&s)==273000000ull);
s.mux=6u<<8;assert(sweet_pwm_clock_hz(&s,&s)==499200000ull);
s.univ_con1|=1u<<24;assert(!sweet_pwm_clock_hz(&s,&s));
''')

    def test_all_parent_and_gate_combinations(self):
        self.run_c(r'''
for(unsigned parent=0;parent<8;++parent)for(unsigned gate=0;gate<2;++gate){
 struct sweet_pwm_clock_snapshot s={.mux=(parent<<8)|(gate<<15),.pll_status=0xc0,.main_con1=84u<<14,.univ_con1=96u<<14};
 uint64_t rate=sweet_pwm_clock_hz(&s,&s);
 assert((rate!=0)==(!gate && (parent==3 || parent==6)));
}
''')

    def test_status_and_unstable_snapshots_reject(self):
        self.run_c(r'''
struct sweet_pwm_clock_snapshot s={.mux=3u<<8,.pll_status=0xc0,.main_con1=84u<<14,.univ_con1=96u<<14};
for(unsigned field=0;field<4;++field){struct sweet_pwm_clock_snapshot b=s;
 switch(field){case 0:b.mux^=0x100;break;case 1:b.pll_status^=0x80;break;case 2:b.main_con1^=1;break;case 3:b.univ_con1^=1;break;}
 assert(!sweet_pwm_clock_hz(&s,&b));}
s.pll_status=0;assert(!sweet_pwm_clock_hz(&s,&s));
s.pll_status=UINT32_MAX;assert(!sweet_pwm_clock_hz(&s,&s));
s.pll_status=0xc0;s.mux=UINT32_MAX;assert(!sweet_pwm_clock_hz(&s,&s));
assert(!sweet_pwm_clock_hz(0,&s) && !sweet_pwm_clock_hz(&s,0));
''')

    def test_vco_pcw_fraction_and_postdiv_boundaries(self):
        self.run_c(r'''
assert(!sweet_pwm_pll_hz(0) && !sweet_pwm_pll_hz(UINT32_MAX));
assert(!sweet_pwm_pll_hz(1) && !sweet_pwm_pll_hz(0x3fffff));
assert(sweet_pwm_pll_hz((84u<<14)|1)==2184001587ull);
for(unsigned pd=0;pd<8;++pd){uint64_t got=sweet_pwm_pll_hz((84u<<14)|(pd<<24));
 assert(got==(2184000000ull+(1u<<pd)-1)/(1u<<pd));}
''')

    def test_native_exact_map_and_registers_read_only(self):
        self.run_c(r'''
for(unsigned parent=3;parent<=6;parent+=3){reset();top[0xd0/4]=parent<<8;save();
 assert(ufs_pwm_clock_cap()==2 && maps==2 && fences==2);unchanged();}
''', native=True)

    def test_native_missing_mapping_does_not_dereference(self):
        self.run_c(r'''
for(unsigned failed=1;failed<=2;++failed){reset();fail_map=failed;save();
 assert(ufs_pwm_clock_cap()==1 && maps==2 && fences==0);unchanged();}
''', native=True)

    def test_native_unprepared_unknown_gated_and_outside_range(self):
        self.run_c(r'''
for(unsigned bad=0;bad<6;++bad){reset();
 switch(bad){case 0:pll[0x3c/4]=0;break;case 1:top[0xd0/4]=0;break;
 case 2:top[0xd0/4]|=1u<<15;break;case 3:pll[0x254/4]=1;break;
 case 4:pll[0x254/4]|=1u<<24;break;case 5:top[0xd0/4]=UINT32_MAX;break;}
 save();assert(ufs_pwm_clock_cap()==1 && maps==2 && fences==2);unchanged();}
''', native=True)

    def test_native_changed_snapshot_is_not_admitted(self):
        self.run_c(r'''
for(unsigned field=1;field<=4;++field){reset();mutate=field;
 assert(ufs_pwm_clock_cap()==1 && maps==2 && fences==2);
 save();mutate=0;assert(ufs_pwm_clock_cap()==(field<=2?1u:2u));unchanged();}
''', native=True)

    def test_caller_gate_before_uic_and_capacity(self):
        init = source.function(source.UFS, 'sweet_ufs_init')
        self.assertIn('if (pwm_requested == 1 && su.dali && pwm_cap > 1)\n            pwm_cap = ufs_pwm_clock_cap();', init)
        self.assertLess(init.index('ufs_dali_prepare_lun('), init.index('ufs_pwm_clock_cap('))
        self.assertLess(init.index('ufs_pwm_clock_cap('), init.index('ufs_dali_prepare_pwm('))
        self.assertLess(init.index('ufs_dali_prepare_pwm('), init.index('ufs_read_capacity('))
        self.assertNotIn('ufs_wr(', HELPER)
        self.assertNotIn('ufs_mtk_uic(', HELPER)
        self.assertEqual(HELPER.count('*(volatile uint32_t *)'), 8)
        self.assertEqual(HELPER.count('__builtin_arm_dsb(0xf);'), 2)


if __name__ == '__main__':
    unittest.main(verbosity=2)
