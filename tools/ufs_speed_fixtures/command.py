#!/usr/bin/env python3
"""Execute exact final SCSI body with real descriptor/cache/completion fixture.
Only platform callbacks are replaced to inject active-HS loss and success.
The separate startup suite executes its existing default-off dynamic cases.
"""
import pathlib
import subprocess
import sys
import tempfile

repo,view=map(pathlib.Path,sys.argv[1:3])
sys.path.insert(0,str(repo/'tools'))
import test_dali_ufs_startup as old
text=(view/'bsd/dev/sweet_ufs.c').read_text(encoding='latin-1')
before=old.fixture()
new_fn=old.source.function(text,'ufs_scsi_on_lun_timeout')
assert old.source.function(old.UFS,'ufs_scsi_on_lun_timeout')==new_fn
assert before.count(new_fn)==1
changes={
 'static int ufs_pwm_reentry_error(void){++hs_reentry_calls;return EIO;}':
 'static int floor_error,link_error;static unsigned floor_checks;\nstatic int ufs_pwm_reentry_error(void){++hs_reentry_calls;return link_error;}',
 'static int ufs_dali_hs_platform_validate(void){++hs_validate_calls;return EIO;}':
 'static int ufs_dali_hs_platform_validate(void){++hs_validate_calls;++floor_checks;return floor_error;}',
 'static int ufs_pwm_failure(int error){++hs_failure_calls;return error;}':
 'static int ufs_pwm_failure(int error){++hs_failure_calls;link_error=error;return error;}',
}
for original,replacement in changes.items():
    assert before.count(original)==1
    before=before.replace(original,replacement)
checks=r'''
static void active_reset(void){hs_reentry_calls=hs_validate_calls=hs_failure_calls=0;reset();}
int main(void){
 (void)unchanged_after_poison;
 active_reset();su.hsg1_active=true;floor_error=EIO;memcpy(saved_dma,dma,sizeof(dma));
 assert(command(100)==EIO && floor_checks==1 && commands==0 && polls==0 && cache_calls==0);
 assert(!memcmp(saved_dma,dma,sizeof(dma)) && link_error==EIO);
 floor_error=0;assert(command(100)==EIO && floor_checks==1 && !commands && !polls && !cache_calls);
 active_reset();link_error=0;floor_checks=0;su.hsg1_active=true;
 assert(command(100)==0 && floor_checks==1 && commands==1);
 active_reset();su.hsg1_active=false;link_error=EIO;floor_error=EIO;floor_checks=0;
 assert(command(100)==0 && !floor_checks && commands==1);check_counters();
 puts("PASS full actual SCSI command: lost floor locks out before DMA/doorbell, default remains unchanged");
 return 0;}
'''
with tempfile.TemporaryDirectory(prefix='ufs-speed-command-') as tmp:
    p=pathlib.Path(tmp)
    (p/'test.c').write_text(before+checks)
    subprocess.run(['clang','-std=gnu11','-O1','-Wall','-Wextra','-Werror','-fsanitize=address,undefined',str(p/'test.c'),'-o',str(p/'test')],check=True)
    subprocess.run([str(p/'test')],check=True)
