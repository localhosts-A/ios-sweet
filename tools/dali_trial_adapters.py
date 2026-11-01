#!/usr/bin/env python3
"""Thin, approved single-trial recovery stages; no device access on import/help.

Candidate wrappers call main(stage, argv). The coordinator owns the device lock,
process group and permanent claim. Local cleanup never proves remote quiescence.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import time

PREFIX = 17179869184
BASELINE = 'b4ee77882c02dc0074da4dba0a229e682f2ab437cdd23b7f02c6012ebd8683bd'
STAGES = ('stock_pair', 'capture', 'root_restore')
REQUIRED = {'device_pair.py', 'dali-root-capture.py', 'restore-trial-root.py'}


def coordinator():
    path = Path(__file__).with_name('dali_unattended_return.py')
    spec = importlib.util.spec_from_file_location('dali_return_core', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def file_sha(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, 'O_NONBLOCK', 0))
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise ValueError('hash输入不是普通文件')
        h = hashlib.sha256()
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            for piece in iter(lambda: stream.read(8 * 1024**2), b''):
                h.update(piece)
        end = os.fstat(fd)
        if (info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns) != (
                end.st_ino, end.st_size, end.st_mtime_ns, end.st_ctime_ns):
            raise ValueError('hash期间输入变化')
        return h.hexdigest()
    finally:
        os.close(fd)


def gate(pair, stage):
    c = coordinator()
    pair = Path(pair).absolute()
    c.require(stage in STAGES and pair.is_dir() and not pair.is_symlink(), '错误候选/阶段')
    claim = json.loads(c.read_regular(pair / 'unattended-return-claim.json'))
    expected = claim.get('approval_sha256', '')
    c.require(re.fullmatch('[0-9a-f]{64}', expected) and
              c.digest(pair / 'trial-approval.json') == expected, 'claim/批准不符')
    a = json.loads(c.read_regular(pair / 'trial-approval.json'))
    s = json.loads(c.read_regular(pair / 'trial-state.json'))
    config = json.loads(c.read_regular(pair / 'unattended-return.json'))
    c.require(a.get('initial_fastboot_and_flash_confirmed') is True and
              a.get('unattended_return_authorized') is True, '缺少真人该轮批准')
    for field in ('run', 'source_commit', 'serial', 'run_token', 'root_base_sha256', 'original_sha256'):
        c.require(s.get(field) is not None and s[field] == a.get(field), '批准绑定不符: ' + field)
    c.require(re.fullmatch('[0-9a-f]{32}', s['run_token']) and int(s['run_token'], 16) and
              claim.get('run_token') == s['run_token'] and
              s.get('approved_trials') == s.get('trial_attempts') == a.get('approved_trials') == 1 and
              s.get('device_write_authorized') is True and s.get('root_restore_authorized') is True and
              s.get('root_delta_authorized') is False and s.get('root_restore_pending') is True and
              s.get('unattended_failed') is not True, '非当前单次恢复状态')
    c.require(s['root_base_sha256'] == BASELINE and
              s.get('approval_clean_root_sha256') == BASELINE and
              s.get('observation_limit_seconds') == config.get('window_seconds') == 1200 and
              config.get('started_utc') == s.get('trial_started_utc') and
              config.get('approval_sha256') == expected and config.get('run_token') == s['run_token'],
              '固定根authority/窗口绑定不符')
    start = datetime.fromisoformat(s['trial_started_utc'])
    c.require(start.tzinfo is not None, '窗口时间缺时区')
    deps = c.verify_dependencies(pair, s, a)
    c.require(REQUIRED <= set(deps['files']) and
              c.digest(Path(__file__)) == deps['files'].get('dali_trial_adapters.py') and
              c.digest(Path(c.__file__)) == a.get('unattended_coordinator_sha256') ==
              deps['files'].get('dali_unattended_return.py'), 'stage核心/底层依赖未绑定')
    c.require(re.fullmatch(r'[A-Za-z0-9_-]{1,80}', s['serial']), '设备身份格式错误')
    index = STAGES.index(stage)
    for previous in STAGES[:index]:
        done = json.loads(c.read_regular(pair / ('unattended-' + previous + '-complete.json')))
        c.require(done.get('stage') == previous and done.get('run_token') == s['run_token'], '前阶段未完成')
    c.require(s.get('state') == ('trial_rebooted' if index == 0 else 'originals_verified') and
              s.get('stock_restore_authorized') is (index == 0), '阶段/stock权限错误')
    if stage == 'root_restore':
        c.verify_stage(pair, 'capture', a)
    # No transport and no mutation occurred before all the above gates.
    return c, pair, s, a


def prepare_reboot(pair, approval_sha256):
    """New candidate device_pair calls this after flashes, BEFORE its one reboot.

    No transport here. Runtime timestamps/config are single-use and are never
    re-created by the return coordinator. A partial publication blocks reboot.
    """
    c = coordinator()
    pair = Path(pair).absolute()
    c.require(pair.is_dir() and not pair.is_symlink() and
              re.fullmatch('[0-9a-f]{64}', approval_sha256 or '') and
              c.digest(pair / 'trial-approval.json') == approval_sha256, '起点批准不符')
    state = json.loads(c.read_regular(pair / 'trial-state.json'))
    approval = json.loads(c.read_regular(pair / 'trial-approval.json'))
    c.require(state.get('state') == 'candidates_flashed' and
              state.get('trial_attempts') == state.get('approved_trials') == approval.get('approved_trials') == 1 and
              all(state.get(k) is True for k in ('device_write_authorized','stock_restore_authorized',
                                               'root_restore_authorized','root_restore_pending')) and
              state.get('root_delta_authorized') is False and
              approval.get('initial_fastboot_and_flash_confirmed') is True and
              approval.get('unattended_return_authorized') is True, '不是已批准刷完的唯一候选')
    for field in ('run','source_commit','serial','run_token','original_sha256','root_base_sha256'):
        c.require(state.get(field) is not None and state[field] == approval.get(field), '起点身份不符')
    deps = c.verify_dependencies(pair, state, approval)
    c.require(state.get('root_base_sha256') == BASELINE and
              state.get('observation_limit_seconds') == 1200 and
              re.fullmatch('[0-9a-f]{32}', state['run_token']) and int(state['run_token'],16) and
              approval.get('unattended_coordinator_sha256') == deps['files']['dali_unattended_return.py'],
              '起点根/预算/核心不符')
    c.require(not state.get('trial_started_utc') and
              not (pair/'unattended-return.json').exists() and
              not (pair/'trial-started.json').exists(), '起点已消耗/部分发布，不得重建')
    stages = {}
    for stage, name in zip(c.STAGES,c.FILES):
        c.require(approval.get('unattended_helper_sha256',{}).get(name) == deps['files'][name], '起点helper不符')
        stages[stage] = dict(file=name,sha256=deps['files'][name])
    started = datetime.now(timezone.utc).isoformat()
    c.write_exclusive(pair/'trial-started.json',dict(started_utc=started,run_token=state['run_token'],
                      approval_sha256=approval_sha256))
    config = dict(schema=1,approval_sha256=approval_sha256,run_token=state['run_token'],
                  started_utc=started,window_seconds=1200,stages=stages)
    c.write_exclusive(pair/'unattended-return.json',config)
    state['trial_started_utc'] = started
    c.atomic_state(pair,state)
    c.require(json.loads(c.read_regular(pair/'unattended-return.json')) == config and
              json.loads(c.read_regular(pair/'trial-state.json')) == state, '起点持久记录复核失败')
    return started


class Operations:
    """Transport boundary; tests replace this object, not the stage/gate logic."""
    def __init__(self, pair, state):
        self.pair, self.state = pair, state
        self.env = dict(os.environ)
        self.env.pop('DALI_ADB_SERIAL', None)
        self.env.pop('ANDROID_SERIAL', None)

    def run(self, argv, timeout=120):
        result = subprocess.run(argv, capture_output=True, timeout=timeout, env=self.env)
        if result.returncode:
            raise RuntimeError('受控命令失败: ' + argv[0])
        return result.stdout

    def query(self, argv, timeout):
        result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, env=self.env)
        if result.returncode:
            raise RuntimeError('Fastboot查询失败')
        return result.stdout + result.stderr

    def helper(self, name, *args):
        return self.run([sys.executable, '-I', str(self.pair / name), *map(str, args)],
                        timeout=3600 if name != 'device_pair.py' else 300)

    def adb(self, *args):
        return self.run(['adb', '-s', self.state['serial'], *args])

    def wait_android(self):
        until = time.monotonic() + 240
        while time.monotonic() < until:
            remaining = max(0.1, min(10, until - time.monotonic()))
            result = subprocess.run(['adb', '-s', self.state['serial'], 'shell', 'getprop', 'sys.boot_completed'],
                                    capture_output=True, timeout=remaining, env=self.env)
            if result.returncode == 0 and result.stdout.strip() == b'1':
                return
            time.sleep(min(3, max(0, until - time.monotonic())))
        raise TimeoutError('原厂Android240秒内未完成，不追加reboot/重试')


def private_file(path, data):
    with path.open('xb') as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def capture_logs(c, pair, state, ops):
    # Same bounded area and console-only pstore policy as the accepted wrapper.
    for key, value in (('ro.serialno', state['serial']), ('ro.product.device', 'dali'),
                       ('ro.boot.slot_suffix', '_b'), ('sys.boot_completed', '1')):
        c.require(ops.adb('shell', 'getprop', key).decode().strip() == value, '日志设备身份变化')
    c.require(ops.adb('shell', 'id', '-u').strip() == b'0', '日志读取不是root')
    c.require(ops.adb('shell', 'blockdev', '--getsize64', '/dev/block/by-name/unixnu_logs').strip() == b'33554432',
              '日志分区尺寸变化')
    area = ops.adb('exec-out', 'dd', 'if=/dev/block/by-name/unixnu_logs',
                   'bs=4096', 'skip=4096', 'count=1025', 'status=none')
    c.require(len(area) == 4198400, 'SWEET窗口短读/超长')
    names = ops.adb('shell', 'ls', '/sys/fs/pstore').decode().split()
    console = [n for n in names if re.fullmatch(r'console-ramoops(?:-\d+)?', n)]
    c.require(len(console) <= 1, 'pstore console不唯一')
    ram = ops.adb('exec-out', 'cat', '/sys/fs/pstore/' + console[0]) if console else b''
    c.require(len(ram) <= 2097152, 'pstore console超限')
    trial = pair / 'trial' / 'attempt1'
    for name, filename, data in (('posttrial-ufslog', 'area.bin', area), ('posttrial-pstore', 'console.bin', ram)):
        directory = trial / name
        directory.mkdir(mode=0o700)
        private_file(directory / filename, data)
    c.write_exclusive(trial / 'posttrial-log-capture.json', dict(
        log_offset=16777216, log_bytes=len(area), log_sha256=hashlib.sha256(area).hexdigest(),
        pstore_console_source=console, pstore_console_bytes=len(ram),
        pstore_console_sha256=hashlib.sha256(ram).hexdigest(), device_write_occurred=False))


def execute(stage, pair, operations=Operations):
    c, pair, s, approval = gate(pair, stage)
    c.write_exclusive(pair / ('unattended-' + stage + '-started.json'),
                      dict(run_token=s['run_token'], stage=stage, started_utc=datetime.now(timezone.utc).isoformat()))
    ops = operations(pair, s)
    if stage == 'stock_pair':
        c.require(c.verify_fastboot(s['serial'], ops.query), '目标Fastboot消失')
        ops.helper('device_pair.py', 'recover', pair)
        ops.wait_android()
        ops.helper('device_pair.py', 'verify', pair)
        current = json.loads(c.read_regular(pair / 'trial-state.json'))
        c.require(current.get('state') == 'originals_verified', '原厂物理对未验收')
        current.update(stock_restore_authorized=False, root_delta_authorized=False, root_restore_pending=True)
        c.atomic_state(pair, current)
    elif stage == 'capture':
        raw = pair / 'trial/attempt1/posttrial-root/root.raw'
        c.require(s.get('root_capture_output') == str(raw) and
                  s.get('root_capture_directory') == str(raw.parent), '完整capture目录不符')
        c.require(not raw.parent.exists() and not raw.parent.is_symlink() and
                  raw.parent.parent.is_dir() and not raw.parent.parent.is_symlink(), 'capture目录已存在/不安全')
        c.require(shutil.disk_usage(pair).free >= PREFIX + 2*1024**3, '完整现场空间不足')
        ops.helper('device_pair.py', 'verify', pair)
        capture_logs(c, pair, s, ops)
        raw.parent.mkdir(mode=0o700)
        ops.helper('dali-root-capture.py', '--serial', s['serial'], '--output', raw, '--capture')
        meta = json.loads(c.read_regular(raw.with_name('root.raw.json')))
        c.require(not raw.is_symlink() and raw.is_file() and raw.stat().st_size == PREFIX and
                  meta.get('status') == 'verified' and meta.get('prefix_bytes') == PREFIX and
                  meta.get('serial') == s['serial'] and
                  meta.get('sha256') == meta.get('remote_before_sha256') == meta.get('remote_after_sha256') and
                  re.fullmatch('[0-9a-f]{64}', meta.get('sha256', '')) and file_sha(raw) == meta['sha256'],
                  '完整16GiB现场三hash/本地复核失败')
        c.write_exclusive(pair / 'actual-capture-verified.json', meta)
        current = json.loads(c.read_regular(pair / 'trial-state.json'))
        current.update(root_capture_verified_sha256=meta['sha256'], root_restore_pending=True)
        c.atomic_state(pair, current)
    else:
        raw = pair / 'trial/attempt1/posttrial-root/root.raw'
        c.require(s.get('root_capture_output') == str(raw) and
                  not (pair / 'trial/attempt1/root-restore-progress.json').exists(), '恢复目录/单次门禁失败')
        ops.helper('restore-trial-root.py', pair, raw, '--write')
        current = json.loads(c.read_regular(pair / 'trial-state.json'))
        c.require(current.get('root_restore_verified') is True and
                  current.get('root_restored_sha256') == BASELINE and
                  all(current.get(k) is False for k in c.FLAGS), 'pinned根/物理对验收未闭合')
        current.update(state='trial_closed_stock_and_new_b4ee_baseline_verified',
                       root_restore_pending=False, desktop_verified=False,
                       trial_closed_utc=datetime.now(timezone.utc).isoformat())
        c.atomic_state(pair, current)
    return dict(stage=stage, desktop_verified=False)


def main(stage, argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('candidate', type=Path)
    args = parser.parse_args(argv)
    if not sys.flags.isolated:
        parser.error('请使用python3 -I')
    try:
        print(json.dumps(execute(stage, args.candidate), indent=2))
    except (ValueError, OSError, KeyError, subprocess.SubprocessError) as error:
        print('恢复阶段停止，不自动重试: ' + str(error), file=sys.stderr)
        return 1
    return 0
