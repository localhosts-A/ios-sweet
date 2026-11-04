#!/usr/bin/env python3
"""Exercise isolated preboot/recovery runner with fake transports only."""
from pathlib import Path
import json
import subprocess
import sys
import tempfile
import types
import unittest

D=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(D/'tools'));sys.path.insert(0,str(D/'tests'))
import sweet_preboot_capture as pre
import wait_return_recover as rr
from test_evidence import RID, EMPTY, header


def result(code=0, out=b'', err=b''):
    return types.SimpleNamespace(returncode=code, stdout=out, stderr=err)


class Fake:
    def __init__(self, slots, fail_slot=None, flash_fail=None, pstore='missing'):
        self.slots=slots;self.fail_slot=fail_slot;self.flash_fail=flash_fail;self.pstore=pstore
        self.adb_calls=[];self.fastboot_calls=[];self.states=iter((False,True))
    def adb(self,args,timeout=60):
        self.adb_calls.append((list(args),timeout))
        if args[:3]==['shell','blockdev','--getsize64']:
            return result(out=b'33554432\n')
        if args[:2]==['exec-out','dd']:
            skip=int(next(x for x in args if x.startswith('skip=')).split('=')[1])
            slot=(skip-4096)//1025
            if slot==self.fail_slot:return result(1,b'partial')
            if self.fail_slot=='timeout-'+str(slot):raise subprocess.TimeoutExpired('fake',60,output=b'partial-timeout')
            return result(out=self.slots[slot])
        if args[:3]==['exec-out','sh','-c']:
            if self.pstore=='timeout':raise subprocess.TimeoutExpired('fake',30)
            if self.pstore=='data':return result(out=b'ramoops-data')
            return result(1,b'')
        raise AssertionError(args)
    def fastboot_present(self):return next(self.states)
    def android_ready(self):return True
    def fastboot(self,args,timeout=120):
        self.fastboot_calls.append((list(args),timeout))
        if args[0]=='flash' and args[1]==self.flash_fail:return result(1,b'',b'failed')
        return result(0,b'OKAY')
    def compare_local(self,pre_dir,receipt_sha256,post_dir,run_id):
        return subprocess.run([sys.executable,'-I',str(D/'tools/sweet_log_evidence.py'),'compare',
                               '--preboot-dir',str(pre_dir),'--receipt-sha256',receipt_sha256,
                               '--post-dir',str(post_dir),'--run-id',run_id],capture_output=True,timeout=60)


class TestRunner(unittest.TestCase):
    def inventory(self,root):
        fake=Fake([header(),EMPTY,EMPTY]);code,status=pre.capture_preboot(root/'pre',RID,fake)
        self.assertEqual(code,0,status);self.assertFalse(status['device_write_occurred'])
        self.assertEqual(len([c for c,_ in fake.adb_calls if c[:2]==['exec-out','dd']]),3)
        return root/'pre',status['receipt_sha256']

    def test_preboot_short_reads_are_independent(self):
        with tempfile.TemporaryDirectory(prefix='sweetlog-runner-') as t:
            root=Path(t);fake=Fake([header(),EMPTY,EMPTY],fail_slot=0)
            code,status=pre.capture_preboot(root/'pre',RID,fake)
            self.assertEqual(code,6);self.assertFalse((root/'pre/receipt.json').exists())
            self.assertEqual((root/'pre/slot0.bin').read_bytes(),b'partial')
            self.assertEqual((root/'pre/slot1.bin').stat().st_size,len(EMPTY))
            self.assertEqual((root/'pre/slot2.bin').stat().st_size,len(EMPTY))
            self.assertEqual(len([c for c,_ in fake.adb_calls if c[:2]==['exec-out','dd']]),3)
            self.assertFalse(status['device_write_occurred'])
            with self.assertRaises(FileExistsError):pre.capture_preboot(root/'pre',RID,fake)

    def test_preboot_refuses_reused_or_zero_id(self):
        with tempfile.TemporaryDirectory(prefix='sweetlog-runner-') as t:
            root=Path(t);fake=Fake([header(RID),EMPTY,EMPTY])
            code,status=pre.capture_preboot(root/'pre',RID,fake)
            self.assertEqual(code,6);self.assertIn('already exists',status['error'])
            self.assertFalse((root/'pre/receipt.json').exists());self.assertEqual((root/'pre/slot0.bin').stat().st_size,len(EMPTY))
            untouched=Fake([header(),EMPTY,EMPTY])
            with self.assertRaises(Exception):pre.capture_preboot(root/'zero','0'*32,untouched)
            self.assertFalse((root/'zero').exists());self.assertEqual(untouched.adb_calls,[])

    def test_one_shot_restore_capture_and_unverified_candidate(self):
        with tempfile.TemporaryDirectory(prefix='sweetlog-runner-') as t:
            root=Path(t);pre_dir,digest=self.inventory(root);trial=root/'trial';trial.mkdir()
            fake=Fake([header(),header(RID),EMPTY],pstore='missing')
            state=rr.recover(trial,pre_dir,digest,RID,fake)
            self.assertTrue(state['boot_images_restored']);self.assertTrue(state['android_ready'])
            self.assertTrue(state['capture']['all_slots_complete'])
            self.assertEqual(state['capture']['comparison']['helper_exit'],5)
            self.assertTrue(state['capture']['comparison']['candidate_only'])
            self.assertEqual(state['capture']['boot_identity'],'unverified')
            self.assertEqual(state['capture']['payload_integrity'],'unverified')
            self.assertFalse(state['capture']['pstore']['saved'])
            self.assertEqual([c[:2] for c,_ in fake.fastboot_calls],[['flash','init_boot_b'],['flash','boot_b'],['reboot']])
            self.assertEqual(len([c for c,_ in fake.adb_calls if c[:2]==['exec-out','dd']]),3)
            self.assertEqual(len([c for c,_ in fake.adb_calls if c[:3]==['exec-out','sh','-c']]),1)
            self.assertEqual(json.loads((trial/'slot-comparison.json').read_text())['result'],'candidate-only')
            receipt=json.loads((trial/'return-recovery.json').read_text())
            self.assertFalse(receipt['root_rewritten']);self.assertFalse(receipt['automatic_fastboot_return_verified'])
            self.assertEqual(receipt['capture']['comparison']['helper_exit'],5)
            before=len(fake.fastboot_calls)
            with self.assertRaises(FileExistsError):rr.recover(trial,pre_dir,digest,RID,fake)
            self.assertEqual(len(fake.fastboot_calls),before)

    def test_stock_flash_failure_is_never_retried(self):
        with tempfile.TemporaryDirectory(prefix='sweetlog-runner-') as t:
            root=Path(t);pre_dir,digest=self.inventory(root);trial=root/'trial';trial.mkdir()
            fake=Fake([header(),EMPTY,EMPTY],flash_fail='init_boot_b')
            with self.assertRaises(RuntimeError):rr.recover(trial,pre_dir,digest,RID,fake)
            self.assertEqual([c[:2] for c,_ in fake.fastboot_calls],[['flash','init_boot_b']])
            self.assertIn('error',json.loads((trial/'return-recovery.json').read_text()))
            with self.assertRaises(FileExistsError):rr.recover(trial,pre_dir,digest,RID,fake)
            self.assertEqual(len(fake.fastboot_calls),1)

    def test_second_stock_flash_failure_is_never_retried(self):
        with tempfile.TemporaryDirectory(prefix='sweetlog-runner-') as t:
            root=Path(t);pre_dir,digest=self.inventory(root);trial=root/'trial';trial.mkdir()
            fake=Fake([header(),EMPTY,EMPTY],flash_fail='boot_b')
            with self.assertRaises(RuntimeError):rr.recover(trial,pre_dir,digest,RID,fake)
            self.assertEqual([c[:2] for c,_ in fake.fastboot_calls],[['flash','init_boot_b'],['flash','boot_b']])
            self.assertFalse(json.loads((trial/'return-recovery.json').read_text())['boot_images_restored'])
            with self.assertRaises(FileExistsError):rr.recover(trial,pre_dir,digest,RID,fake)
            self.assertEqual(len(fake.fastboot_calls),2)

    def test_bad_pinned_preboot_hash_cannot_claim_candidate(self):
        with tempfile.TemporaryDirectory(prefix='sweetlog-runner-') as t:
            root=Path(t);pre_dir,_=self.inventory(root);trial=root/'trial';trial.mkdir()
            fake=Fake([header(),header(RID),EMPTY],pstore='missing')
            state=rr.recover(trial,pre_dir,'0'*64,RID,fake)
            self.assertTrue(state['capture']['all_slots_complete'])
            self.assertEqual(state['capture']['comparison']['helper_exit'],6)
            self.assertFalse(state['capture']['comparison']['candidate_only'])
            self.assertEqual(state['capture']['boot_identity'],'unverified')
            for i in range(3):self.assertEqual((trial/f'posttrial-slots/slot{i}.bin').stat().st_size,len(EMPTY))

    def test_slot_failure_does_not_suppress_other_slots_or_pstore(self):
        with tempfile.TemporaryDirectory(prefix='sweetlog-runner-') as t:
            root=Path(t);pre_dir,digest=self.inventory(root);trial=root/'trial';trial.mkdir()
            fake=Fake([header(),header(RID),EMPTY],fail_slot=0,pstore='data')
            state=rr.recover(trial,pre_dir,digest,RID,fake)
            self.assertFalse(state['capture']['all_slots_complete'])
            self.assertIsNone(state['capture']['comparison'])
            self.assertTrue(state['capture']['pstore']['saved'])
            self.assertEqual((trial/'posttrial-slots/slot0.bin').read_bytes(),b'partial')
            self.assertEqual((trial/'posttrial-slots/slot1.bin').stat().st_size,len(EMPTY))
            self.assertEqual((trial/'posttrial-slots/slot2.bin').stat().st_size,len(EMPTY))
            self.assertEqual((trial/'console-ramoops-0.bin').read_bytes(),b'ramoops-data')
            self.assertEqual(len([c for c,_ in fake.adb_calls if c[:2]==['exec-out','dd']]),3)

    def test_slot_timeout_retains_partial_and_continues(self):
        with tempfile.TemporaryDirectory(prefix='sweetlog-runner-') as t:
            root=Path(t);pre_dir,digest=self.inventory(root);trial=root/'trial';trial.mkdir()
            fake=Fake([header(),header(RID),EMPTY],fail_slot='timeout-1',pstore='data')
            state=rr.recover(trial,pre_dir,digest,RID,fake)
            self.assertFalse(state['capture']['all_slots_complete'])
            self.assertEqual((trial/'posttrial-slots/slot1.bin').read_bytes(),b'partial-timeout')
            self.assertEqual((trial/'posttrial-slots/slot2.bin').stat().st_size,len(EMPTY))
            self.assertTrue(state['capture']['pstore']['saved'])

    def test_pstore_timeout_does_not_suppress_slot_capture(self):
        with tempfile.TemporaryDirectory(prefix='sweetlog-runner-') as t:
            root=Path(t);pre_dir,digest=self.inventory(root);trial=root/'trial';trial.mkdir()
            fake=Fake([header(),header(RID),EMPTY],pstore='timeout')
            state=rr.recover(trial,pre_dir,digest,RID,fake)
            self.assertTrue(state['capture']['all_slots_complete'])
            self.assertIn('TimeoutExpired',state['capture']['pstore']['error'])
            self.assertTrue(state['capture']['comparison']['candidate_only'])

if __name__=='__main__':unittest.main(verbosity=2)
