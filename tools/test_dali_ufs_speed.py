#!/usr/bin/env python3
"""Execute actual 0200 UFS/HS/AP-floor/short-return functions on host mocks.
Optional positional MATERIALIZED_XNU must match the exact formal patch replay.
This is not a firmware, electrical, throughput, or desktop success claim.
"""
from pathlib import Path
import re
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
FIXTURES=ROOT/'tools/ufs_speed_fixtures'
PATCH=ROOT/'xnu/patches/0200-dali-ufs-hsg1-short-speed-trial.patch'
sys.path.insert(0,str(ROOT/'tools'))
import test_dali_gui_load_progress as sources

PATHS=('bsd/dev/sweet_ufs.c','pexpert/arm/pe_dali_watchdog.c',
       'bsd/dev/SweetUFSHSG1.h','bsd/dev/SweetUFSSpeedRead.h',
       'bsd/dev/SweetUFSSpeedLog.h','bsd/dev/SweetUFSHSFloor.h',
       'bsd/dev/SweetUFSHSPlatform.h','pexpert/arm/DaliUFSSpeedReturn.h')


def function(text,name):
    match=re.search(r'(?:static (?:inline )?)?(?:void|bool|int|uint32_t|uint64_t)\n'+name+r'\(',text)
    if match is None:raise AssertionError(name)
    begin=text.index('{',match.start());end=begin+1;depth=1
    while depth:
        depth+=(text[end]=='{')-(text[end]=='}');end+=1
    return text[match.start():end]+'\n'


def run(args):
    result=subprocess.run(list(map(str,args)),capture_output=True,text=True,timeout=120)
    if result.returncode:
        sys.stdout.write(result.stdout);sys.stderr.write(result.stderr)
        raise AssertionError('Command failed: '+str(args[0]))
    return result.stdout+result.stderr


def compile_fixture(source,include,folder,name):
    exe=folder/name
    run(['clang','-std=c11','-O1','-Wall','-Wextra','-Werror',
         '-fsanitize=address,undefined','-I'+str(include),source,'-o',exe])
    result=run([exe])
    if 'PASS' not in result:raise AssertionError(name+' did not report success')


def main():
    expected={path:sources.SOURCE[path] for path in PATHS}
    # The source fixture must reconstruct, not read unchecked mutable bytes.
    assert sources.apply_exact_hunks(sources.UFS_PRE_SPEED,'bsd/dev/sweet_ufs.c',PATCH)==expected[PATHS[0]]
    assert sources.apply_exact_hunks(sources.PE_PRE_SPEED,'pexpert/arm/pe_dali_watchdog.c',PATCH)==expected[PATHS[1]]
    for previous_map,patch,next_map in (
        (sources.HS_PRE_LIVE_OPP,sources.UFS_OPP_PATCH,sources.HS_PRE_FW_INIT),
        (sources.HS_PRE_FW_INIT,sources.UFS_FW_PATCH,sources.HS_PRE_REQUEST_ORDER),
        (sources.HS_PRE_REQUEST_ORDER,sources.UFS_REQUEST_PATCH,sources.HS_PRE_RESOURCE_RESULTS),
        (sources.HS_PRE_RESOURCE_RESULTS,sources.UFS_RESOURCE_PATCH,expected),
    ):
        for path,previous in previous_map.items():
            assert sources.apply_exact_hunks(previous,path,patch)==next_map[path]
            try:
                sources.apply_exact_hunks('\n'+previous,path,patch)
            except AssertionError:
                pass
            else:
                raise AssertionError('Offset accepted in floor/firmware patch: '+path)
    with tempfile.TemporaryDirectory(prefix='dali-ufs-speed-') as name:
        folder=Path(name)
        if len(sys.argv)>1:
            view=Path(sys.argv[1])
            for path,text in expected.items():
                assert (view/path).read_text(encoding='latin-1')==text,path
        else:
            view=folder/'xnu'
            for path,text in expected.items():
                dest=view/path;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_text(text)
        # Include only explicit actual headers, not the entire BSD include tree.
        include=folder/'include';include.mkdir()
        for header in ('SweetUFSHSFloor.h','SweetUFSPWMClock.h'):
            text=sources.SOURCE['bsd/dev/'+header]
            (include/header).write_text(text)
        compile_fixture(FIXTURES/'floor.c',include,folder,'floor')
        adapter=expected['bsd/dev/SweetUFSHSPlatform.h']
        # Only the firmware assembly boundary is mocked; every adapter map,
        # register access, clock check and minimum-request callback stays actual.
        native_smc=function(adapter,'ufs_hs_smc')
        assert '0xc2000506u' in native_smc and 'smc #0' in native_smc
        # Retain the production command/index/NULL guard; replace only the
        # ARM register/assembly and firmware-result boundary below that guard.
        prefix=native_smc[:native_smc.index('    register uint64_t x0')]
        mock_smc=prefix+r'''
 ++smcs;if(smcs==fail_smc)return EIO;*a0=*a1=0;
 if(command==0){
  assert(index==0 && !inits && !acquires && !releases);++inits;*a0=init_fw_status;
  if(!*a0 && init_populates){dvfs[0]=86u<<20;dvfs[0xfc/4]=5;if(init_target_busy)dvfs[0x5f0/4]=1u<<16;}
  if(init_clock_change)top[0xd0/4]^=1;
 }
 else if(command==4){const uint32_t table[]={575000,600000,650000,725000,825000,875000},observed[]={570000,590000,620000,705000,765000,875000};assert(index<6);*a1=android_calibration?observed[index]:table[index];}
 else if(command==0x40){assert(index==0);++acquires;if(minus_one_resource)*a0=UINT64_MAX;}
 else{assert(command==0x41 && index==0);++releases;if(minus_one_resource)*a0=UINT64_MAX;}
 return 0;
}
'''
        fixture=(FIXTURES/'platform.c').read_text()
        assert fixture.count('/* ADAPTER */')==1
        fixture=fixture.replace('/* ADAPTER */',adapter.replace(native_smc,mock_smc))
        platform=folder/'platform.c';platform.write_text(fixture)
        compile_fixture(platform,include,folder,'platform')
        bench=folder/'bench.inc'
        bench.write_text(expected['bsd/dev/SweetUFSSpeedRead.h']+expected['bsd/dev/SweetUFSSpeedLog.h'])
        probe=folder/'probe.inc'
        probe.write_text(function(expected['pexpert/arm/DaliUFSSpeedReturn.h'],'dali_fb_ufs_probe_return'))
        policy=folder/'DaliFastbootReturnPolicy.h'
        policy.write_text(sources.SOURCE['pexpert/arm/DaliFastbootReturnPolicy.h'])
        commands=[
            ('bench.py',[bench]),
            ('hsg1.py',[ROOT,view/'bsd/dev/SweetUFSHSG1.h',view]),
            ('short_return.py',[policy,probe]),
            ('root_entry.py',[ROOT,view]),
            ('init_flow.py',[ROOT,view]),
            ('command.py',[ROOT,view]),
        ]
        for script,args in commands:
            output=run([sys.executable,'-I',FIXTURES/script,*args])
            if 'PASS' not in output:raise AssertionError(script)
            print('PASS '+script)
        # Intentional changes are bounded to the declared formal patch. Default
        # header absence/flag zero cannot admit HS or a pre-root benchmark.
        ufs=expected['bsd/dev/sweet_ufs.c']
        entry=function(ufs,'sweet_ufs_rootdev')
        assert '"dali-hs-g1"' in entry and '"dali-speed-probe"' in entry
        assert 'speed_probe && !hsg1' in entry
        assert 'dali_fastboot_return_ufs_probe();' in entry
        assert 'return ECANCELED;' in entry
        hs=expected['bsd/dev/SweetUFSHSG1.h']
        assert 'ufs_pwm_pmc(0x44, until)' in hs and 'local_max > 5' in hs
        assert 'ufs_pwm_get(false, 0x1587' in hs and 'ufs_pwm_get(true, 0x1587' in hs
        assert 'ops.smc(context, 0x40, 0' in expected['bsd/dev/SweetUFSHSFloor.h']
        assert 'ops.smc(context, 0x41, 0' in expected['bsd/dev/SweetUFSHSFloor.h']
    print('PASS exact 0200 replay and actual functions: AP vote, platform adapter, HS-G1, read-only probe, root entry and single return; host mocks only')


if __name__=='__main__':main()
