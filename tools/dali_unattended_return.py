#!/usr/bin/env python3
"""仅接管已明确批准且已启动的一轮Fastboot返回/取证/恢复；没有进入Fastboot或刷候选入口。"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time

STAGES = ('stock_pair', 'capture', 'root_restore')
FILES = ('restore-stock-once.py', 'capture-actual-once.py', 'restore-root-once.py')
FLAGS = ('device_write_authorized', 'root_delta_authorized', 'stock_restore_authorized',
         'root_restore_authorized')


def require(value, message):
    if not value:
        raise ValueError(message)


def read_regular(path, limit=8 * 1024**2):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, 'O_NONBLOCK', 0))
    try:
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and 0 <= info.st_size <= limit, '不是有界普通文件')
        data = os.pread(fd, info.st_size, 0)
        require(len(data) == info.st_size, '输入短读')
        return data
    finally:
        os.close(fd)


def digest(path):
    return hashlib.sha256(read_regular(path)).hexdigest()


def load_contract(candidate, approval_sha256):
    pair = Path(candidate).absolute()
    require(not pair.is_symlink() and pair.is_dir(), '候选必须为实际目录')
    require(re.fullmatch('[0-9a-f]{64}', approval_sha256 or '') is not None, '缺少刷前批准清单完整SHA')
    approval_path = pair / 'trial-approval.json'
    require(digest(approval_path) == approval_sha256, '刷前批准清单SHA不符')
    approval = json.loads(read_regular(approval_path))
    state = json.loads(read_regular(pair / 'trial-state.json'))
    config = json.loads(read_regular(pair / 'unattended-return.json'))
    require(config.get('schema') == 1 and config.get('approval_sha256') == approval_sha256,
            '自动恢复清单版本/批准绑定不符')
    require(approval.get('initial_fastboot_and_flash_confirmed') is True and
            approval.get('unattended_return_authorized') is True,
            '缺少该轮进入Fastboot/刷入及无人值守恢复明确批准')
    require(state.get('state') == 'trial_rebooted' and state.get('trial_attempts') == 1 and
            state.get('approved_trials') == approval.get('approved_trials') == 1 and
            state.get('device_write_authorized') is True and
            state.get('stock_restore_authorized') is True and
            state.get('root_restore_authorized') is True and
            state.get('root_delta_authorized') is False and state.get('root_restore_pending') is True,
            '不是当前已批准且尚未恢复的唯一试验')
    for field in ('run', 'source_commit', 'candidate_sha256', 'root_base_sha256', 'original_sha256', 'serial'):
        require(state.get(field) is not None and state.get(field) == approval.get(field),
                '试验/批准绑定不符: ' + field)
    token = config.get('run_token')
    require(re.fullmatch('[0-9a-f]{32}', token or '') is not None and int(token, 16) != 0 and
            token == approval.get('run_token') == state.get('run_token'), 'run-token不匹配')
    require(state.get('observation_limit_seconds') == config.get('window_seconds') == 1200,
            '只能使用已批准1200秒窗口')
    require(config.get('started_utc') == state.get('trial_started_utc'), '固定窗口起点不匹配')
    start = datetime.fromisoformat(config['started_utc'])
    require(start.tzinfo is not None, '窗口时间必须含时区')
    start = start.timestamp()
    require(re.fullmatch(r'[A-Za-z0-9_-]{1,80}', state['serial']) is not None, '设备身份格式错误')
    require(set(config.get('stages', {})) == set(STAGES), '恢复阶段必须固定且完整')
    for stage, filename in zip(STAGES, FILES):
        row = config['stages'][stage]
        require(set(row) == {'file', 'sha256'} and row['file'] == filename and
                re.fullmatch('[0-9a-f]{64}', row['sha256'] or '') is not None and
                approval.get('unattended_helper_sha256', {}).get(filename) == row['sha256'] and
                digest(pair / filename) == row['sha256'], '恢复helper精确批准/hash不符: ' + stage)
    require(approval.get('unattended_coordinator_sha256') == digest(Path(__file__)),
            '协调器字节未包含在该轮刷前批准中')
    return pair, state, config, start


def write_exclusive(path, value):
    with Path(path).open('x') as stream:
        os.fchmod(stream.fileno(), 0o600)
        json.dump(value, stream, indent=2)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())


def query(argv, timeout):
    result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    require(result.returncode == 0, 'transport失败: ' + ' '.join(argv[:2]))
    return result.stdout + result.stderr


def verify_fastboot(serial, runner=query):
    # 不把部分串匹配、其他设备或Android枚举当作目标Fastboot。
    devices = runner(['fastboot', 'devices'], 5)
    rows = [line.split() for line in devices.splitlines() if line.strip()]
    if not rows:
        return False
    require(rows == [[serial, 'fastboot']], '出现其他/多个/格式未知的Fastboot设备，停止不恢复')
    for key, expected in (('product', 'dali'), ('current-slot', 'b')):
        output = runner(['fastboot', '-s', serial, 'getvar', key], 5)
        values = re.findall(r'^(?:\(bootloader\)\s*)?' + re.escape(key) + r':\s*(\S+)\s*$',
                            output, re.M)
        require(values == [expected] and 'FAILED' not in output, 'Fastboot产品/槽不匹配: ' + key)
    return True


def wait_return(serial, start, wall=time.time, monotonic=time.monotonic, sleeper=time.sleep, runner=query):
    remaining = start + 1200 - wall()
    require(0 < remaining <= 1200, '已超批准窗口或开始时间在未来，不延长/重试')
    until = monotonic() + remaining
    while wall() < start + 1200 and monotonic() < until:
        if verify_fastboot(serial, runner):
            return wall() < start + 1200 and monotonic() < until
        sleeper(min(1, max(0, min(start + 1200 - wall(), until - monotonic()))))
    return False


def verify_stage(pair, stage, approval):
    state = json.loads(read_regular(pair / 'trial-state.json'))
    if stage == 'stock_pair':
        require(state.get('state') == 'originals_verified' and
                state.get('stock_restore_authorized') is False and state.get('root_restore_pending') is True,
                '原厂物理对/Android恢复尚未验收')
    elif stage == 'capture':
        capture = json.loads(read_regular(pair / 'actual-capture-verified.json'))
        require(capture.get('status') == 'verified' and capture.get('prefix_bytes') == 17179869184 and
                capture.get('serial') == approval['serial'] and
                capture.get('sha256') == capture.get('remote_before_sha256') == capture.get('remote_after_sha256') and
                re.fullmatch('[0-9a-f]{64}', capture.get('sha256', '')) is not None and
                state.get('root_capture_verified_sha256') == capture['sha256'],
                '必须先有完整16GiB现场三hash再恢复根')
    else:
        require(state.get('state') == 'trial_closed_stock_and_new_b4ee_baseline_verified' and
                state.get('root_restore_verified') is True and state.get('root_restore_pending') is False and
                state.get('root_restored_sha256') == approval['root_base_sha256'] and
                all(state.get(key) is False for key in FLAGS), '完整根/物理对闭合未通过')
    return state


def recover(candidate, approval_sha256, execute=False):
    pair, state, config, start = load_contract(candidate, approval_sha256)
    approval = json.loads(read_regular(pair / 'trial-approval.json'))
    if not execute:
        return dict(status='validated-no-transport', stages=list(STAGES), device_accessed=False)
    # 独占claim且没有resume/retry开关；失败保留现状，交给原有restore-only入口。
    write_exclusive(pair / 'unattended-return-claim.json',
                    dict(run_token=config['run_token'], approval_sha256=approval_sha256,
                         claimed_utc=datetime.now(timezone.utc).isoformat(), retry_permitted=False))
    require(wait_return(state['serial'], start), '窗口结束未观测目标Fastboot；不刷、不延长，恢复仍pending')
    for stage, filename in zip(STAGES, FILES):
        row = config['stages'][stage]
        require(digest(pair / filename) == row['sha256'] and
                digest(pair / 'trial-approval.json') == approval_sha256, '执行前helper/批准已变化')
        # 各helper各自核对物理hash/完整capture/pinned恢复FD；协调器不发flash命令。
        subprocess.run([sys.executable, '-I', str(pair / filename), str(pair)],
                       check=True, timeout={'stock_pair': 600, 'capture': 3600, 'root_restore': 3600}[stage])
        verify_stage(pair, stage, approval)
        write_exclusive(pair / ('unattended-' + stage + '-complete.json'),
                        dict(run_token=config['run_token'], stage=stage,
                             completed_utc=datetime.now(timezone.utc).isoformat()))
    return dict(status='closed-verified', stages=list(STAGES), desktop_verified=False)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate', type=Path, required=True)
    parser.add_argument('--approval-sha256', required=True)
    parser.add_argument('--recover', action='store_true', help='只在刷前明确批准/已启动该轮后接管恢复')
    args = parser.parse_args(argv)
    if not sys.flags.isolated:
        parser.error('请使用python3 -I')
    try:
        result = recover(args.candidate, args.approval_sha256, args.recover)
    except (ValueError, OSError, KeyError, subprocess.SubprocessError) as error:
        print('无人值守接管停止：' + str(error) + '；保留pending状态，不自动重试。', file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
