#!/usr/bin/env python3
"""Real no-device process trees and real recovery-stage code with transport substitutes."""
from pathlib import Path
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parent

def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

c = load('return_safety', ROOT / 'dali_unattended_return.py')
a = load('adapter_safety', ROOT / 'dali_trial_adapters.py')


def save(pair, name, value):
    (pair / name).write_text(json.dumps(value))


def contract(pair):
    for name in ('dali_unattended_return.py', 'dali_trial_adapters.py'):
        (pair / name).write_bytes((ROOT / name).read_bytes())
    for name in (*c.FILES, *a.REQUIRED):
        (pair / name).write_text('raise SystemExit("fixture dependency must never execute")\n')
    (pair / 'boot.bin').write_bytes(b'synthetic boot')
    deps = {p.name: c.digest(p) for p in pair.iterdir() if p.is_file() and p.name != 'boot.bin'}
    save(pair, 'trial-dependencies.json', dict(schema=1, files=deps))
    state = dict(state='trial_rebooted', run=123, source_commit='c'*40, serial='synthetic-dali',
                 trial_attempts=1, approved_trials=1, run_token='1'*32,
                 device_write_authorized=True, root_restore_authorized=True,
                 stock_restore_authorized=True, root_delta_authorized=False, root_restore_pending=True,
                 root_base_sha256=a.BASELINE, root_candidate_sha256='f'*64,
                 approval_clean_root_sha256=a.BASELINE, root_restore_target_sha256=a.BASELINE,
                 root_clean_image='/synthetic/root.img', restore_source='/synthetic/root.img',
                 candidate_sha256={'boot.bin':c.digest(pair/'boot.bin')},
                 original_sha256={'boot_b.img':'d'*64,'init_boot_b.img':'e'*64},
                 dependency_manifest_sha256=c.digest(pair/'trial-dependencies.json'),
                 observation_limit_seconds=1200, trial_started_utc=datetime.now(timezone.utc).isoformat(),
                 root_capture_output=str(pair/'trial/attempt1/posttrial-root/root.raw'),
                 root_capture_directory=str(pair/'trial/attempt1/posttrial-root'))
    fields = ('run','source_commit','serial','approved_trials','run_token','candidate_sha256',
              'original_sha256','root_base_sha256','root_candidate_sha256','root_restore_target_sha256',
              'restore_source','dependency_manifest_sha256')
    approval = {k:state[k] for k in fields}
    approval.update(initial_fastboot_and_flash_confirmed=True, unattended_return_authorized=True,
                    unattended_coordinator_sha256=deps['dali_unattended_return.py'],
                    unattended_helper_sha256={n:deps[n] for n in c.FILES})
    save(pair, 'trial-state.json', state)
    save(pair, 'trial-approval.json', approval)
    digest = c.digest(pair/'trial-approval.json')
    config = dict(schema=1, approval_sha256=digest, run_token=state['run_token'],
                  started_utc=state['trial_started_utc'], window_seconds=1200,
                  stages={stage:dict(file=name,sha256=deps[name]) for stage,name in zip(c.STAGES,c.FILES)})
    save(pair, 'unattended-return.json', config)
    (pair/'trial/attempt1').mkdir(parents=True)
    return digest, state, approval


def claim(pair, digest):
    save(pair, 'unattended-return-claim.json', dict(approval_sha256=digest,run_token='1'*32))


class Simulated(a.Operations):
    calls = []
    failure = None

    def query(self, argv, timeout):
        self.calls.append(('query',argv))
        return {'devices':'synthetic-dali fastboot\n','product':'product: dali\n',
                'current-slot':'current-slot: b\n'}[argv[-1]]

    def wait_android(self):
        self.calls.append(('wait',))
        if self.failure == 'android':
            raise TimeoutError('simulated Android timeout')

    def helper(self, name, *args):
        self.calls.append(('helper', name))
        state=json.loads((self.pair/'trial-state.json').read_text())
        if name == 'device_pair.py' and args[0]=='verify':
            state['state']='originals_verified'
            save(self.pair,'trial-state.json',state)
        elif name == 'dali-root-capture.py':
            raw=Path(args[args.index('--output')+1])
            with raw.open('xb') as stream:
                stream.truncate(a.PREFIX if self.failure != 'short' else 4096)
            h=a.file_sha(raw)
            save(raw.parent,'root.raw.json',dict(status='verified',prefix_bytes=a.PREFIX,
                 serial=state['serial'],sha256=h,remote_before_sha256=h,
                 remote_after_sha256=h if self.failure != 'hash' else '0'*64))
        elif name == 'restore-trial-root.py':
            if self.failure == 'restore':
                raise RuntimeError('simulated remote write uncertainty')
            state.update(root_restore_verified=True,root_restored_sha256=a.BASELINE,
                         **{k:False for k in c.FLAGS})
            save(self.pair,'trial-state.json',state)

    def adb(self, *argv):
        self.calls.append(('adb',argv))
        if argv[:2]==('shell','getprop'):
            return {'ro.serialno':b'synthetic-dali','ro.product.device':b'dali',
                    'ro.boot.slot_suffix':b'_b','sys.boot_completed':b'1'}[argv[2]]
        if argv[:2]==('shell','id'): return b'0'
        if argv[:2]==('shell','blockdev'): return b'33554432'
        if argv[:2]==('shell','ls'): return b'pmsg-ramoops-0 ftrace-ramoops-0'
        if argv[:2]==('exec-out','dd'): return bytes(4198400)
        raise AssertionError('unexpected transport request: '+str(argv))


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.pair=Path(self.temp.name)
        self.digest,self.state,self.approval=contract(self.pair)
        Simulated.calls=[]; Simulated.failure=None

    def start(self): claim(self.pair,self.digest)

    def capture_ready(self):
        self.start()
        a.execute('stock_pair',self.pair,Simulated)
        save(self.pair,'unattended-stock_pair-complete.json',dict(stage='stock_pair',run_token='1'*32))

    def test_missing_claim_zero_transport_and_no_state_mutation(self):
        before=(self.pair/'trial-state.json').read_bytes()
        for stage in c.STAGES:
            with self.assertRaises(OSError): a.execute(stage,self.pair,Simulated)
        self.assertEqual(Simulated.calls,[])
        self.assertEqual(before,(self.pair/'trial-state.json').read_bytes())

    def test_inactive_spent_and_false_authority_zero_transport(self):
        self.start()
        for changes in ({'device_write_authorized':False},{'trial_attempts':2},
                        {'state':'trial_closed_stock_and_new_b4ee_baseline_verified'},
                        {'unattended_failed':True},{'root_base_sha256':'0'*64}):
            save(self.pair,'trial-state.json',dict(self.state,**changes))
            before=(self.pair/'trial-state.json').read_bytes()
            with self.assertRaises(ValueError): a.execute('stock_pair',self.pair,Simulated)
            self.assertEqual(before,(self.pair/'trial-state.json').read_bytes())
        self.assertEqual(Simulated.calls,[])

    def test_changed_transitive_dependency_zero_transport(self):
        self.start()
        (self.pair/'restore-trial-root.py').write_text('changed')
        with self.assertRaisesRegex(ValueError,'依赖'): a.execute('stock_pair',self.pair,Simulated)
        self.assertEqual(Simulated.calls,[])

    def test_approval_hash_or_root_authority_mismatch_zero_transport(self):
        self.start()
        self.approval['restore_source']='/another/root'
        save(self.pair,'trial-approval.json',self.approval)
        with self.assertRaises(ValueError): a.execute('stock_pair',self.pair,Simulated)
        self.assertEqual(Simulated.calls,[])

    def test_reboot_timestamp_is_single_use_durable_and_no_transport(self):
        self.state.update(state='candidates_flashed')
        self.state.pop('trial_started_utc')
        save(self.pair,'trial-state.json',self.state)
        (self.pair/'unattended-return.json').unlink()  # synthetic fixture, not an actual trial
        with mock.patch.object(a.subprocess,'run',side_effect=AssertionError('transport')):
            started=a.prepare_reboot(self.pair,self.digest)
            state=json.loads((self.pair/'trial-state.json').read_text())
            config=json.loads((self.pair/'unattended-return.json').read_text())
            self.assertEqual(started,state['trial_started_utc'])
            self.assertEqual(started,config['started_utc'])
            with self.assertRaisesRegex(ValueError,'起点已消耗'):
                a.prepare_reboot(self.pair,self.digest)
        self.assertTrue(state['root_restore_pending'])

    def test_reboot_unauthorized_refusal_does_not_publish_or_mutate(self):
        before=(self.pair/'trial-state.json').read_bytes()
        with mock.patch.object(a.subprocess,'run',side_effect=AssertionError('transport')):
            with self.assertRaises(ValueError): a.prepare_reboot(self.pair,self.digest)
        self.assertEqual(before,(self.pair/'trial-state.json').read_bytes())
        self.assertFalse((self.pair/'trial-started.json').exists())

    def test_hash_real_boot_sized_file_and_symlink_rejected(self):
        path=self.pair/'large.bin'
        with path.open('wb') as stream: stream.truncate(67108864)
        self.assertEqual(c.digest(path),a.file_sha(path))
        link=self.pair/'link.bin';link.symlink_to(path)
        with self.assertRaises(OSError): c.digest(link)

    def test_capture_hash_mismatch_cannot_release_root_stage(self):
        self.capture_ready()
        # Exercise real capture length/metadata checks; replace only expensive
        # local bytes for this negative. The positive hashes all 16GiB above.
        Simulated.failure='hash'
        with mock.patch.object(a,'file_sha',return_value='a'*64), \
             mock.patch.object(a.shutil,'disk_usage',return_value=shutil_usage()):
            with self.assertRaisesRegex(ValueError,'三hash'): a.execute('capture',self.pair,Simulated)
        self.assertFalse((self.pair/'actual-capture-verified.json').exists())
        self.assertTrue(json.loads((self.pair/'trial-state.json').read_text())['root_restore_pending'])

    def test_stock_state_and_single_use(self):
        self.start()
        a.execute('stock_pair',self.pair,Simulated)
        state=json.loads((self.pair/'trial-state.json').read_text())
        self.assertEqual(state['state'],'originals_verified')
        self.assertFalse(state['stock_restore_authorized'])
        count=len(Simulated.calls)
        with self.assertRaises(ValueError): a.execute('stock_pair',self.pair,Simulated)
        self.assertEqual(count,len(Simulated.calls))

    def test_android_timeout_never_calls_verify_or_capture(self):
        self.start(); Simulated.failure='android'
        with self.assertRaises(TimeoutError): a.execute('stock_pair',self.pair,Simulated)
        self.assertEqual([x for x in Simulated.calls if x[0]=='helper'],[('helper','device_pair.py')])
        self.assertFalse((self.pair/'actual-capture-verified.json').exists())

    def test_full_chain_real_stage_code_and_complete_16gib_local_hash(self):
        def stage_runner(argv, timeout, evidence):
            stage=c.STAGES[c.FILES.index(Path(argv[-2]).name)]
            return a.execute(stage,self.pair,Simulated)
        with mock.patch.object(a.shutil,'disk_usage',return_value=shutil_usage()), \
             mock.patch.object(c,'wait_return',return_value=True), \
             mock.patch.object(c,'run_stage',side_effect=stage_runner):
            self.assertEqual(c.recover(self.pair,self.digest,True)['status'],'closed-verified')
        meta=json.loads((self.pair/'actual-capture-verified.json').read_text())
        self.assertEqual(meta['prefix_bytes'],17179869184)
        self.assertEqual(meta['sha256'],meta['remote_after_sha256'])
        c.verify_stage(self.pair,'root_restore',self.approval)
        state=json.loads((self.pair/'trial-state.json').read_text())
        self.assertFalse(state['root_restore_pending'])
        self.assertTrue(all(state[k] is False for k in c.FLAGS))
        self.assertFalse(any('pmsg' in str(x) or 'ftrace' in str(x) for x in Simulated.calls))

    def test_short_capture_preserved_without_success_marker(self):
        self.capture_ready(); Simulated.failure='short'
        with mock.patch.object(a.shutil,'disk_usage',return_value=shutil_usage()):
            with self.assertRaisesRegex(ValueError,'三hash'): a.execute('capture',self.pair,Simulated)
        self.assertTrue(Path(self.state['root_capture_output']).exists())
        self.assertFalse((self.pair/'actual-capture-verified.json').exists())
        self.assertFalse(any(x==('helper','restore-trial-root.py') for x in Simulated.calls))

    def test_capture_space_failure_never_runs_transport(self):
        self.capture_ready(); Simulated.calls=[]
        with mock.patch.object(a.shutil,'disk_usage',return_value=shutil_usage(0)):
            with self.assertRaisesRegex(ValueError,'空间'): a.execute('capture',self.pair,Simulated)
        self.assertEqual(Simulated.calls,[])

    def test_existing_capture_or_missing_preceding_stage_refused(self):
        self.start()
        self.state.update(state='originals_verified',stock_restore_authorized=False)
        save(self.pair,'trial-state.json',self.state)
        with self.assertRaises(OSError): a.execute('capture',self.pair,Simulated)
        self.assertEqual(Simulated.calls,[])

    def test_coordinator_failure_blocks_next_write_even_if_host_group_gone(self):
        ran=[]
        def failed(*args):
            ran.append(args[0]); raise TimeoutError('host gone, simulated remote dd still running')
        with mock.patch.object(c,'wait_return',return_value=True), mock.patch.object(c,'run_stage',side_effect=failed):
            with self.assertRaises(TimeoutError): c.recover(self.pair,self.digest,True)
        state=json.loads((self.pair/'trial-state.json').read_text())
        self.assertTrue(state['root_restore_pending'])
        self.assertFalse(state['remote_quiescence_proven'])
        self.assertEqual(len(ran),1)
        with self.assertRaises(FileExistsError): c.recover(self.pair,self.digest,True)
        self.assertFalse((self.pair/'unattended-root_restore-complete.json').exists())


def shutil_usage(free=64*1024**3):
    from collections import namedtuple
    return namedtuple('usage','total used free')(128*1024**3,64*1024**3,free)


@unittest.skipUnless(os.name=='posix','POSIX process group contract')
class ProcessTreeTests(unittest.TestCase):
    def test_normal_helper_is_reaped_without_extra_group(self):
        with tempfile.TemporaryDirectory() as t:
            path=Path(t)/'process.json'
            c.run_stage([sys.executable,'-I','-c','pass'],5,path)
            pid=json.loads(path.read_text())['pgid']
            self.assertFalse(c.group_exists(pid))

    def test_timeout_terminates_child_tree_and_retains_intent_record(self):
        # The parent reaps its TERM-ignoring child in its TERM handler, so the
        # test does not rely on the host PID1's orphan/zombie reaping policy.
        script='''import os,signal,subprocess,sys,time
child=subprocess.Popen([sys.executable,'-I','-c','import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(60)'])
def stop(s,f):
 child.kill();child.wait();raise SystemExit(2)
signal.signal(signal.SIGTERM,stop)
print('ready',flush=True)
time.sleep(60)
'''
        with tempfile.TemporaryDirectory() as t:
            path=Path(t)/'process.json'
            with self.assertRaises(subprocess.TimeoutExpired):
                c.run_stage([sys.executable,'-I','-c',script],1,path)
            evidence=json.loads(path.read_text())
            self.assertFalse(evidence['remote_quiescence_proven'])
            self.assertFalse(c.group_exists(evidence['pgid']))

    def test_termination_signal_stops_local_group_and_restores_handler(self):
        import threading
        old=signal.getsignal(signal.SIGTERM)
        timer=threading.Timer(0.5,lambda:os.kill(os.getpid(),signal.SIGTERM))
        with tempfile.TemporaryDirectory() as t:
            evidence=Path(t)/'process.json'
            timer.start()
            try:
                with self.assertRaises(InterruptedError):
                    c.run_stage([sys.executable,'-I','-c','import time;time.sleep(60)'],10,evidence)
            finally:
                timer.cancel();timer.join()
            self.assertFalse(c.group_exists(json.loads(evidence.read_text())['pgid']))
        self.assertEqual(signal.getsignal(signal.SIGTERM),old)

    def test_nonzero_helper_never_counts_as_success(self):
        with tempfile.TemporaryDirectory() as t:
            with self.assertRaisesRegex(ValueError,'helper失败'):
                c.run_stage([sys.executable,'-I','-c','raise SystemExit(7)'],5,Path(t)/'process.json')

    def test_successful_parent_with_live_child_is_not_stage_success(self):
        script='''import os,signal,subprocess,sys,time
child=subprocess.Popen([sys.executable,'-I','-c','import time;time.sleep(60)'])
# Parent success is premature; coordinator must detect the remaining group.
raise SystemExit(0)
'''
        with tempfile.TemporaryDirectory() as t:
            with self.assertRaisesRegex(ValueError,'子进程'):
                c.run_stage([sys.executable,'-I','-c',script],5,Path(t)/'process.json')


if __name__=='__main__':
    unittest.main()
