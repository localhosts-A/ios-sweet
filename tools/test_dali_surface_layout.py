#!/usr/bin/env python3
"""Exercise the 0182 Surface fix using the materialized, patch-applied XNU source.

Pre-build invocations intentionally skip: the real XNU source only exists after
patch replay in dxb. The post-build workflow sets SWEET_SURFACE_SOURCE_DIR.
"""
from pathlib import Path
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / 'xnu/patches/0182-sweet-iosurface-default-plane-size-overflow.patch'
RELATIVE_SOURCE = Path('iokit/Kernel/arm/SweetSurface.cpp')
BASELINE_SHA256 = '94f76068e255cf7e9a4ce2e16f8bbf1a36b816c05f3988d6956066333e82e3f8'


def _function(text, name, result_types):
    result = '|'.join(re.escape(x) for x in result_types)
    match = re.search(r'(?m)^static\s+(?:inline\s+)?(?:' + result + r')\s*\n\s*' +
                      re.escape(name) + r'\s*\(', text)
    if not match:
        raise AssertionError(f'missing production function {name}')
    brace = text.index('{', match.start())
    depth = 0
    for end in range(brace, len(text)):
        if text[end] == '{':
            depth += 1
        elif text[end] == '}':
            depth -= 1
            if depth == 0:
                return text[match.start():end + 1]
    raise AssertionError(f'unterminated function {name}')


def _real_source_root():
    value = os.environ.get('SWEET_SURFACE_SOURCE_DIR')
    required = os.environ.get('SWEET_SURFACE_SOURCE_REQUIRED', '0')
    if required not in ('0', '1'):
        raise AssertionError('SWEET_SURFACE_SOURCE_REQUIRED must be 0 or 1')
    if not value:
        if required == '1':
            raise AssertionError('SWEET_SURFACE_SOURCE_REQUIRED=1 but SWEET_SURFACE_SOURCE_DIR is unset or empty')
        return None
    root = Path(value).absolute()
    if root.is_symlink() or not root.is_dir():
        raise AssertionError(f'SWEET_SURFACE_SOURCE_DIR must be a real XNU directory: {root}')
    path = root / RELATIVE_SOURCE
    cursor = root
    for part in RELATIVE_SOURCE.parts:
        cursor = cursor / part
        st = cursor.lstat()
        if cursor.is_symlink():
            raise AssertionError(f'symlink in source path: {cursor}')
    st = path.stat()
    if not path.is_file() or st.st_nlink != 1:
        raise AssertionError(f'Surface source must be an independent regular file: {path}')
    return root, path


CPP_PREFIX = r'''#include <cassert>
#include <cstdint>
#include <cstring>
#include <map>
#include <string>
#include <vector>
#include <cstdio>
using uint32_t=::uint32_t; using uint64_t=::uint64_t; using uint8_t=::uint8_t; using uint16_t=::uint16_t;
using IOReturn=int;
static constexpr int kIOReturnBadArgument=-1, kIOReturnNoMemory=-2, kIOReturnSuccess=0;
#define SWEET_SURFACE_ROW_ALIGN 64
#define SWEET_SURFACE_MAX_DIM 16384
#define SWEET_SURFACE_MAX_ALLOC (256u << 20)
#define SWEET_SURFACE_MAX_PLANES 20
#define SWEET_SURFACE_RESULT_SIZE 0xa68
#define SWEET_SURFACE_TIMESTAMPS 0x800
static const uint64_t test_page_size=16384;
#define round_page(x) (((uint64_t)(x)+(test_page_size-1))&~(test_page_size-1))
#define OSDynamicCast(T,o) dynamic_cast<T*>(o)
struct OSObject { virtual ~OSObject()=default; };
struct OSNumber: OSObject { uint64_t value; unsigned bits; OSNumber(uint64_t v,unsigned b=32):value(v),bits(b){} uint32_t unsigned32BitValue()const{return (uint32_t)value;} };
struct OSDictionary:OSObject{std::map<std::string,OSObject*>m;~OSDictionary(){for(auto&e:m)if(dynamic_cast<OSNumber*>(e.second))delete e.second;} OSObject*getObject(const char*k){auto i=m.find(k);return i==m.end()?nullptr:i->second;} void set(const char*k,OSObject*v){m[k]=v;}};
struct OSArray:OSObject{std::vector<OSObject*>a;unsigned getCount()const{return (unsigned)a.size();} OSObject*getObject(unsigned i){return i<a.size()?a[i]:nullptr;}};
struct sweet_surface_plane{uint32_t width,height,bytesPerRow,offset,size;uint16_t bytesPerElement;uint8_t elementWidth,elementHeight;};
struct SweetSurface{uint32_t id,width,height,bytesPerRow,offset,pixelFormat,allocSize;uint16_t bytesPerElement;uint8_t elementWidth,elementHeight;uint32_t cacheMode,planeCount,parentID;sweet_surface_plane planes[SWEET_SURFACE_MAX_PLANES];};
struct Mapping{uint64_t a;uint64_t getAddress()const{return a;}};
struct SweetSurfaceRef{Mapping*data,*state;};
'''


class SurfaceLayoutPatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        materialized = _real_source_root()
        if materialized is None:
            cls.source_root = cls.source_path = None
            return
        cls.source_root, cls.source_path = materialized
        cls.candidate_text = cls.source_path.read_text()
        cls.patch_text = PATCH.read_text()
        cls.temp = tempfile.TemporaryDirectory(prefix='sweet-surface-layout-')
        cls.temp_root = Path(cls.temp.name)
        if cls.temp_root.is_symlink():
            raise AssertionError('temporary workspace must not be a symlink')
        cls.xnu = cls.temp_root / 'xnu'
        cls.baseline_path = cls.xnu / RELATIVE_SOURCE
        cls.baseline_path.parent.mkdir(parents=True)
        shutil.copyfile(cls.source_path, cls.baseline_path)
        if cls.baseline_path.is_symlink() or cls.baseline_path.stat().st_nlink != 1:
            raise AssertionError('temporary source copy must be an independent regular file')
        subprocess.run(['git', 'apply', '--reverse', '--check', str(PATCH)], cwd=cls.xnu, check=True)
        subprocess.run(['git', 'apply', '--reverse', str(PATCH)], cwd=cls.xnu, check=True)
        cls.baseline_text = cls.baseline_path.read_text()
        digest = hashlib.sha256(cls.baseline_path.read_bytes()).hexdigest()
        if digest != BASELINE_SHA256:
            raise AssertionError(f'wrong CI baseline after reverse patch: {digest}')
        subprocess.run(['git', 'apply', '--check', str(PATCH)], cwd=cls.xnu, check=True)
        subprocess.run(['git', 'apply', str(PATCH)], cwd=cls.xnu, check=True)
        if cls.baseline_path.read_text() != cls.candidate_text:
            raise AssertionError('0182 forward replay does not exactly recreate materialized source')
        cls.base_layout = _function(cls.baseline_text, 'sweet_surface_layout', ('IOReturn',))
        cls.new_layout = _function(cls.candidate_text, 'sweet_surface_layout', ('IOReturn',))
        cls.helpers = '\n'.join(_function(cls.candidate_text, name, returns) for name, returns in (
            ('sweet_dict_u32', ('uint32_t',)), ('sweet_div_up', ('uint32_t',)),
            ('sweet_row_bytes', ('uint32_t',)), ('put32', ('void',)),
            ('put64', ('void',)), ('sweet_surface_fill_result', ('void',))))

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, 'temp'):
            cls.temp.cleanup()

    def test_required_source_missing_fails_closed(self):
        env = os.environ.copy()
        env.pop('SWEET_SURFACE_SOURCE_DIR', None)
        env['SWEET_SURFACE_SOURCE_REQUIRED'] = '1'
        result = subprocess.run([sys.executable, '-I', str(Path(__file__).resolve())],
                                env=env, capture_output=True, text=True, timeout=30)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn('SWEET_SURFACE_SOURCE_REQUIRED=1', result.stderr)

    def test_real_source_roundtrip_and_layout_regressions(self):
        if self.source_root is None:
            self.skipTest('pre-build: patch-applied XNU source is not materialized')
        baseline = self.base_layout.replace('sweet_surface_layout', 'baseline_surface_layout')
        candidate = self.new_layout.replace('sweet_surface_layout', 'candidate_surface_layout')
        program = CPP_PREFIX + '\n' + self.helpers + '\n' + baseline + '\n' + candidate + r'''
static void n(OSDictionary&d,const char*k,uint64_t v,unsigned bits=32){d.set(k,new OSNumber(v,bits));}
static OSObject wrongObject;
static void wrong(OSDictionary&d,const char*k){d.set(k,&wrongObject);}
int main(){SweetSurface oldS{},newS{};
 // Reported wrap: 0x40000000 * 4 => 2^32 with implicit default size.
 OSDictionary wrap;n(wrap,"IOSurfaceWidth",1);n(wrap,"IOSurfaceHeight",1);n(wrap,"IOSurfaceBytesPerElement",4);OSArray wa;OSDictionary wp;n(wp,"IOSurfacePlaneHeight",4);n(wp,"IOSurfacePlaneBytesPerRow",0x40000000u);wa.a.push_back(&wp);wrap.set("IOSurfacePlaneInfo",&wa);
 assert(baseline_surface_layout(&oldS,&wrap)==kIOReturnSuccess&&oldS.planes[0].size==0);
 assert(candidate_surface_layout(&newS,&wrap)==kIOReturnNoMemory);
 // The same overflowing geometry with explicit zero or one remains on the explicit-value path.
 OSDictionary overflowZero; n(overflowZero,"IOSurfaceWidth",1);n(overflowZero,"IOSurfaceHeight",1);n(overflowZero,"IOSurfaceBytesPerElement",4);OSArray oza;OSDictionary ozp;n(ozp,"IOSurfacePlaneHeight",4);n(ozp,"IOSurfacePlaneBytesPerRow",0x40000000u);n(ozp,"IOSurfacePlaneSize",0);oza.a.push_back(&ozp);overflowZero.set("IOSurfacePlaneInfo",&oza);
 assert(baseline_surface_layout(&oldS,&overflowZero)==kIOReturnSuccess&&candidate_surface_layout(&newS,&overflowZero)==kIOReturnSuccess&&oldS.planes[0].size==0&&newS.planes[0].size==0);
 OSDictionary overflowOne; n(overflowOne,"IOSurfaceWidth",1);n(overflowOne,"IOSurfaceHeight",1);n(overflowOne,"IOSurfaceBytesPerElement",4);OSArray ooa;OSDictionary oop;n(oop,"IOSurfacePlaneHeight",4);n(oop,"IOSurfacePlaneBytesPerRow",0x40000000u);n(oop,"IOSurfacePlaneSize",1);ooa.a.push_back(&oop);overflowOne.set("IOSurfacePlaneInfo",&ooa);
 assert(baseline_surface_layout(&oldS,&overflowOne)==kIOReturnSuccess&&candidate_surface_layout(&newS,&overflowOne)==kIOReturnSuccess&&oldS.planes[0].size==1&&newS.planes[0].size==1);
 // Wrong-type PlaneSize gets the old default; that default overflow is checked.
 OSDictionary overflowWrong; n(overflowWrong,"IOSurfaceWidth",1);n(overflowWrong,"IOSurfaceHeight",1);n(overflowWrong,"IOSurfaceBytesPerElement",4);OSArray owa;OSDictionary owp;n(owp,"IOSurfacePlaneHeight",4);n(owp,"IOSurfacePlaneBytesPerRow",0x40000000u);wrong(owp,"IOSurfacePlaneSize");owa.a.push_back(&owp);overflowWrong.set("IOSurfacePlaneInfo",&owa);
 assert(baseline_surface_layout(&oldS,&overflowWrong)==kIOReturnSuccess&&candidate_surface_layout(&newS,&overflowWrong)==kIOReturnNoMemory);
 // Products UINT32_MAX-1 and UINT32_MAX+1 both preserve a later bad-plane error; this does not expose the overflow flag.
 OSDictionary belowBoundary; n(belowBoundary,"IOSurfaceWidth",1);n(belowBoundary,"IOSurfaceHeight",1);OSArray bba;OSDictionary bbp,bbbad;n(bbp,"IOSurfacePlaneHeight",2);n(bbp,"IOSurfacePlaneBytesPerRow",0x7fffffffu);n(bbbad,"IOSurfacePlaneElementWidth",0);bba.a.push_back(&bbp);bba.a.push_back(&bbbad);belowBoundary.set("IOSurfacePlaneInfo",&bba);
 assert(baseline_surface_layout(&oldS,&belowBoundary)==kIOReturnBadArgument&&candidate_surface_layout(&newS,&belowBoundary)==kIOReturnBadArgument);
 OSDictionary aboveBoundary; n(aboveBoundary,"IOSurfaceWidth",1);n(aboveBoundary,"IOSurfaceHeight",1);OSArray aba;OSDictionary abp,abbad;n(abp,"IOSurfacePlaneHeight",2);n(abp,"IOSurfacePlaneBytesPerRow",0x80000000u);n(abbad,"IOSurfacePlaneElementWidth",0);aba.a.push_back(&abp);aba.a.push_back(&abbad);aboveBoundary.set("IOSurfacePlaneInfo",&aba);
 assert(baseline_surface_layout(&oldS,&aboveBoundary)==kIOReturnBadArgument&&candidate_surface_layout(&newS,&aboveBoundary)==kIOReturnBadArgument);
 // Overflow flag is call-local: later plane processing continues and next independent call succeeds.
 OSDictionary overflowThenExplicit; n(overflowThenExplicit,"IOSurfaceWidth",1);n(overflowThenExplicit,"IOSurfaceHeight",1);n(overflowThenExplicit,"IOSurfaceBytesPerElement",4);OSArray otep;OSDictionary ote0,ote1;n(ote0,"IOSurfacePlaneHeight",2);n(ote0,"IOSurfacePlaneBytesPerRow",0x80000020u);n(ote1,"IOSurfacePlaneSize",1);otep.a.push_back(&ote0);otep.a.push_back(&ote1);overflowThenExplicit.set("IOSurfacePlaneInfo",&otep);
 assert(baseline_surface_layout(&oldS,&overflowThenExplicit)==kIOReturnSuccess&&candidate_surface_layout(&newS,&overflowThenExplicit)==kIOReturnNoMemory&&newS.planeCount==2);
 OSDictionary afterOverflow; n(afterOverflow,"IOSurfaceWidth",64);n(afterOverflow,"IOSurfaceHeight",32);OSArray ao;OSDictionary aop;n(aop,"IOSurfacePlaneWidth",64);n(aop,"IOSurfacePlaneHeight",32);ao.a.push_back(&aop);afterOverflow.set("IOSurfacePlaneInfo",&ao);
 assert(baseline_surface_layout(&oldS,&afterOverflow)==kIOReturnSuccess&&candidate_surface_layout(&newS,&afterOverflow)==kIOReturnSuccess&&newS.planes[0].size==2048);
 // Error priority: overflowing plane followed by malformed plane remains BadArgument in both.
 OSDictionary badAfter; n(badAfter,"IOSurfaceWidth",1);n(badAfter,"IOSurfaceHeight",1);n(badAfter,"IOSurfaceBytesPerElement",4);OSArray ba;OSDictionary bp;n(bp,"IOSurfacePlaneHeight",2);n(bp,"IOSurfacePlaneBytesPerRow",0x80000020u);ba.a.push_back(&bp);ba.a.push_back(&wrongObject);badAfter.set("IOSurfacePlaneInfo",&ba);
 assert(baseline_surface_layout(&oldS,&badAfter)==kIOReturnBadArgument&&candidate_surface_layout(&newS,&badAfter)==kIOReturnBadArgument);
 // Audited non-wrapping >256MiB case, then malformed plane: preserve BadArgument priority.
 OSDictionary nonwrapBad;n(nonwrapBad,"IOSurfaceWidth",1);n(nonwrapBad,"IOSurfaceHeight",1);OSArray nwa;OSDictionary nwp,nwb;n(nwp,"IOSurfacePlaneHeight",1);n(nwp,"IOSurfacePlaneBytesPerRow",0x10000001u);n(nwb,"IOSurfacePlaneElementWidth",0);nwa.a.push_back(&nwp);nwa.a.push_back(&nwb);nonwrapBad.set("IOSurfacePlaneInfo",&nwa);
 assert(baseline_surface_layout(&oldS,&nonwrapBad)==kIOReturnBadArgument&&candidate_surface_layout(&newS,&nonwrapBad)==kIOReturnBadArgument);
 // Same non-wrapping size, no later error: original final cap check remains NoMemory.
 OSDictionary nonwrapAlloc;n(nonwrapAlloc,"IOSurfaceWidth",1);n(nonwrapAlloc,"IOSurfaceHeight",1);OSArray na;OSDictionary np;n(np,"IOSurfacePlaneHeight",1);n(np,"IOSurfacePlaneBytesPerRow",0x10000001u);na.a.push_back(&np);nonwrapAlloc.set("IOSurfacePlaneInfo",&na);
 assert(baseline_surface_layout(&oldS,&nonwrapAlloc)==kIOReturnNoMemory&&candidate_surface_layout(&newS,&nonwrapAlloc)==kIOReturnNoMemory);
 // A preceding overflow with a later invalid element also preserves existing order.
 OSDictionary badElement;n(badElement,"IOSurfaceWidth",1);n(badElement,"IOSurfaceHeight",1);OSArray be;OSDictionary bep,bei;n(bep,"IOSurfacePlaneHeight",2);n(bep,"IOSurfacePlaneBytesPerRow",0x80000020u);n(bei,"IOSurfacePlaneElementWidth",0);be.a.push_back(&bep);be.a.push_back(&bei);badElement.set("IOSurfacePlaneInfo",&be);
 assert(baseline_surface_layout(&oldS,&badElement)==kIOReturnBadArgument&&candidate_surface_layout(&newS,&badElement)==kIOReturnBadArgument);
 // Explicit 0, undersized nonzero, wrong-type default, and explicit u64 narrowing remain legacy.
 OSDictionary explicitZero;n(explicitZero,"IOSurfaceWidth",1);n(explicitZero,"IOSurfaceHeight",1);OSArray ez;OSDictionary ezp;n(ezp,"IOSurfacePlaneSize",0);ez.a.push_back(&ezp);explicitZero.set("IOSurfacePlaneInfo",&ez);
 assert(baseline_surface_layout(&oldS,&explicitZero)==kIOReturnSuccess&&candidate_surface_layout(&newS,&explicitZero)==kIOReturnSuccess&&oldS.planes[0].size==newS.planes[0].size);
 OSDictionary explicitSmall;n(explicitSmall,"IOSurfaceWidth",1);n(explicitSmall,"IOSurfaceHeight",1);OSArray es;OSDictionary esp;n(esp,"IOSurfacePlaneBytesPerRow",64);n(esp,"IOSurfacePlaneSize",1);es.a.push_back(&esp);explicitSmall.set("IOSurfacePlaneInfo",&es);
 assert(baseline_surface_layout(&oldS,&explicitSmall)==kIOReturnSuccess&&candidate_surface_layout(&newS,&explicitSmall)==kIOReturnSuccess&&oldS.planes[0].size==1&&newS.planes[0].size==1);
 OSDictionary wrongSize;n(wrongSize,"IOSurfaceWidth",64);n(wrongSize,"IOSurfaceHeight",32);OSArray ws;OSDictionary wsp;n(wsp,"IOSurfacePlaneWidth",64);n(wsp,"IOSurfacePlaneHeight",32);wrong(wsp,"IOSurfacePlaneSize");ws.a.push_back(&wsp);wrongSize.set("IOSurfacePlaneInfo",&ws);
 assert(baseline_surface_layout(&oldS,&wrongSize)==kIOReturnSuccess&&candidate_surface_layout(&newS,&wrongSize)==kIOReturnSuccess&&oldS.planes[0].size==2048&&newS.planes[0].size==2048);
 OSDictionary explicitWide;n(explicitWide,"IOSurfaceWidth",1);n(explicitWide,"IOSurfaceHeight",1);OSArray ew;OSDictionary ewp;n(ewp,"IOSurfacePlaneSize",(1ull<<32)+3,64);ew.a.push_back(&ewp);explicitWide.set("IOSurfacePlaneInfo",&ew);
 assert(baseline_surface_layout(&oldS,&explicitWide)==kIOReturnSuccess&&candidate_surface_layout(&newS,&explicitWide)==kIOReturnSuccess&&oldS.planes[0].size==3&&newS.planes[0].size==3);
 // Exact existing 256MiB cap, 16KiB rounding, and plane count/result ABI.
 OSDictionary cap;n(cap,"IOSurfaceWidth",1);n(cap,"IOSurfaceHeight",1);OSArray cp;OSDictionary cpp;n(cpp,"IOSurfacePlaneHeight",16);n(cpp,"IOSurfacePlaneBytesPerRow",1u<<24);cp.a.push_back(&cpp);cap.set("IOSurfacePlaneInfo",&cp);
 assert(baseline_surface_layout(&oldS,&cap)==kIOReturnSuccess&&candidate_surface_layout(&newS,&cap)==kIOReturnSuccess&&newS.allocSize==(256u<<20));
 OSDictionary cross;n(cross,"IOSurfaceWidth",1);n(cross,"IOSurfaceHeight",1);OSArray ca;OSDictionary capl;n(capl,"IOSurfacePlaneOffset",16370);ca.a.push_back(&capl);cross.set("IOSurfacePlaneInfo",&ca);
 assert(baseline_surface_layout(&oldS,&cross)==kIOReturnSuccess&&candidate_surface_layout(&newS,&cross)==kIOReturnSuccess&&newS.allocSize==32768);
 OSDictionary many;n(many,"IOSurfaceWidth",1);n(many,"IOSurfaceHeight",1);OSArray ma;OSDictionary mps[21];for(unsigned i=0;i<20;i++)ma.a.push_back(&mps[i]);many.set("IOSurfacePlaneInfo",&ma);
 assert(baseline_surface_layout(&oldS,&many)==kIOReturnSuccess&&candidate_surface_layout(&newS,&many)==kIOReturnSuccess&&newS.planeCount==20);
 Mapping data{0x100000},state{0x200000};SweetSurfaceRef ref{&data,&state};uint8_t a[SWEET_SURFACE_RESULT_SIZE+16],b[SWEET_SURFACE_RESULT_SIZE+16];memset(a,0xa5,sizeof(a));memset(b,0xa5,sizeof(b));sweet_surface_fill_result(&oldS,&ref,a);sweet_surface_fill_result(&newS,&ref,b);assert(memcmp(a,b,SWEET_SURFACE_RESULT_SIZE)==0);for(unsigned i=SWEET_SURFACE_RESULT_SIZE;i<sizeof(a);i++)assert(a[i]==0xa5&&b[i]==0xa5);
 ma.a.push_back(&mps[20]);assert(baseline_surface_layout(&oldS,&many)==kIOReturnBadArgument&&candidate_surface_layout(&newS,&many)==kIOReturnBadArgument);
 puts("PASS: actual materialized baseline/candidate layouts, priority, explicit compatibility, 16KiB and 20-plane cases");return 0;}
'''
        with tempfile.TemporaryDirectory(prefix='sweet-surface-layout-host-') as td:
            temp = Path(td)
            source = temp / 'fixture.cpp'
            binary = temp / 'fixture'
            source.write_text(program)
            compiler = shutil.which('clang++') or shutil.which('c++')
            self.assertIsNotNone(compiler)
            subprocess.run([compiler, '-std=c++17', '-fsanitize=address,undefined',
                            '-fno-omit-frame-pointer', str(source), '-o', str(binary)],
                           check=True, timeout=60)
            result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('PASS:', result.stdout)

if __name__ == '__main__':
    unittest.main(verbosity=2)
