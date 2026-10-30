#!/usr/bin/env python3
"""Exercise the actual Dali UPIU completion policy on malformed/short fixtures."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / 'xnu/patches/0166-sweet-dali-validate-scsi-completions.patch'


def header():
    for block in PATCH.read_text().split('diff --git ')[1:]:
        if 'b/bsd/dev/SweetUFSCompletion.h\n' in block:
            return ''.join(line[1:] for line in block.splitlines(keepends=True)
                           if line.startswith('+') and not line.startswith('+++'))
    raise AssertionError('missing actual helper header')


class UFSCompletionTest(unittest.TestCase):
    def run_c(self, body):
        fixture = '''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
''' + header() + body
        compiler = shutil.which('clang') or shutil.which('cc')
        self.assertIsNotNone(compiler)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'policy.c').write_text(fixture)
            subprocess.run([compiler, '-std=c11', '-O2', '-Wall', '-Wextra', '-Werror',
                            str(root / 'policy.c'), '-o', str(root / 'policy')], check=True)
            result = subprocess.run([str(root / 'policy')], capture_output=True, text=True,
                                    timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), 'PASS')

    def test_identity_and_bounds_including_tag_wrap(self):
        self.run_c('''
int main(void) {
    uint8_t r[17]={0x21,0,2,255};r[16]=0xa5;
    for(unsigned int n=0;n<16;++n)
        assert(sweet_ufs_check_response(r,n,2,255,0x28)==SWEET_UFS_RESPONSE_INVALID);
    assert(sweet_ufs_check_response(NULL,16,2,255,0x28)==SWEET_UFS_RESPONSE_INVALID);
    for(unsigned int lun=0;lun<256;++lun) {
        for(unsigned int tag=0;tag<256;++tag) {
            r[2]=(uint8_t)lun;r[3]=(uint8_t)tag;
            uint8_t before[17];memcpy(before,r,sizeof(r));
            assert(sweet_ufs_check_response(r,16,(uint8_t)lun,(uint8_t)tag,0x28)==SWEET_UFS_RESPONSE_OK);
            assert(sweet_ufs_check_response(r,16,(uint8_t)(lun+1),(uint8_t)tag,0x28)==SWEET_UFS_RESPONSE_IDENTITY);
            assert(sweet_ufs_check_response(r,16,(uint8_t)lun,(uint8_t)(tag+1),0x28)==SWEET_UFS_RESPONSE_IDENTITY);
            assert(!memcmp(before,r,sizeof(r)));
        }
    }
    r[0]=0x36;
    assert(sweet_ufs_check_response(r,16,r[2],r[3],0x28)==SWEET_UFS_RESPONSE_INVALID);
    assert(sweet_ufs_response_desynchronized(SWEET_UFS_RESPONSE_IDENTITY));
    assert(sweet_ufs_response_desynchronized(SWEET_UFS_RESPONSE_INVALID));
    assert(!sweet_ufs_response_desynchronized(SWEET_UFS_RESPONSE_INCOMPLETE));
    assert(!sweet_ufs_response_desynchronized(SWEET_UFS_RESPONSE_OK));
    puts("PASS");return 0;
}
''')

    def test_fixed_io_rejects_residual_but_preserves_scsi_errors_and_inquiry(self):
        self.run_c('''
int main(void) {
    uint8_t r[16]={0x21,0,2,3};uint8_t fixed[]={0x28,0x2a,0x25};
    for(unsigned int j=0;j<sizeof(fixed);++j) {
        for(unsigned int flags=0;flags<256;++flags) {
            r[1]=(uint8_t)flags;
            enum sweet_ufs_response_result expected=(flags&0x60) ? SWEET_UFS_RESPONSE_INCOMPLETE : SWEET_UFS_RESPONSE_OK;
            assert(sweet_ufs_check_response(r,16,2,3,fixed[j])==expected);
        }
        r[1]=0;
        for(unsigned int pos=12;pos<16;++pos) {
            r[pos]=1;
            assert(sweet_ufs_check_response(r,16,2,3,fixed[j])==SWEET_UFS_RESPONSE_INCOMPLETE);
            r[pos]=0;
        }
    }
    r[1]=0x20;r[15]=60;
    assert(sweet_ufs_check_response(r,16,2,3,0x12)==SWEET_UFS_RESPONSE_OK);
    r[7]=2;
    for(unsigned int j=0;j<sizeof(fixed);++j)
        assert(sweet_ufs_check_response(r,16,2,3,fixed[j])==SWEET_UFS_RESPONSE_OK);
    r[7]=0;r[6]=1;
    assert(sweet_ufs_check_response(r,16,2,3,0x28)==SWEET_UFS_RESPONSE_OK);
    r[2]=7;
    assert(sweet_ufs_check_response(r,16,2,3,0x28)==SWEET_UFS_RESPONSE_IDENTITY);
    assert(sweet_ufs_response_desynchronized(SWEET_UFS_RESPONSE_IDENTITY));
    puts("PASS");return 0;
}
''')

    def test_caller_is_dali_only_after_dma_and_preserves_existing_sense_path(self):
        text = PATCH.read_text()
        self.assertIn('if (su.dali && ocs == 0)', text)
        self.assertIn('ucd->resp, sizeof(ucd->resp), lun, ucd->cmd[3], cdb[0]', text)
        self.assertIn('su.command_poisoned = true', text)
        self.assertIn('return EIO;', text)
        self.assertIn('SWEET UFS: rejected completion', text)
        self.assertIn('if (ocs || ucd->resp[0] != 0x21 || ucd->resp[6] || ucd->resp[7])', text)
        # The policy must not disable DMA synchronization or rewrite request identity.
        for line in text.splitlines():
            if line.startswith('-') and not line.startswith('---'):
                self.fail('completion fix must only add validation, not remove existing behavior')


if __name__ == '__main__':
    unittest.main()
