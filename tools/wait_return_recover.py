#!/usr/bin/env python3
"""Isolated one-shot stock recovery and independent raw log capture.

No root rewrite, no retry of a write, and no automatic-return claim. A matching
image header is candidate-only; neither boot identity nor payload is verified.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import sweet_log_evidence as evidence
import sweet_preboot_capture as preboot

STOCK_IMAGES = ('init_boot_b.img', 'boot_b.img')
PSTORE_COMMAND = 'test -r /sys/fs/pstore/console-ramoops-0 && exec cat /sys/fs/pstore/console-ramoops-0'


def utc():
    return datetime.now(timezone.utc).isoformat()


class Transport(preboot.Transport):
    def fastboot(self, args, timeout=120):
        prefix = ['fastboot'] + (['-s', self.serial] if self.serial else [])
        return subprocess.run(prefix + list(args), capture_output=True, timeout=timeout)

    def fastboot_present(self):
        result = self.fastboot(['devices'], timeout=8)
        if result.returncode:
            raise RuntimeError('fastboot enumeration command failed')
        return bool(result.stdout.strip())

    def android_ready(self):
        try:
            result = self.adb(['shell', 'getprop', 'sys.boot_completed'], timeout=8)
            return result.returncode == 0 and result.stdout.strip() == b'1'
        except subprocess.TimeoutExpired:
            return False

    def compare_local(self, pre_dir, receipt_sha256, post_dir, run_id):
        return subprocess.run([sys.executable, '-I', str(HERE / 'sweet_log_evidence.py'), 'compare',
                               '--preboot-dir', str(pre_dir), '--receipt-sha256', receipt_sha256,
                               '--post-dir', str(post_dir), '--run-id', run_id],
                              capture_output=True, timeout=60)


def wait_for(predicate, seconds):
    deadline = time.monotonic() + seconds
    while True:
        if predicate():
            return
        if time.monotonic() >= deadline:
            raise TimeoutError('timed out waiting for the requested transport state')
        time.sleep(2)


def run_logged(transport, arguments, output):
    # Reserve this one-shot log before issuing the only write attempt.
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW
    fd = os.open(Path(output), flags, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        result = transport.fastboot(arguments, timeout=120)
        stream.write(result.stdout + result.stderr)
        stream.flush()
        os.fsync(stream.fileno())
    if result.returncode:
        raise RuntimeError('command failed; no write will be retried: ' + ' '.join(arguments[:2]))
    return result


def validate_stock_images(stock_image_dir):
    """Resolve both required recovery images before any transport activity."""
    directory = Path(stock_image_dir).expanduser()
    try:
        directory = directory.resolve(strict=True)
    except OSError as error:
        raise ValueError('stock image directory is unavailable') from error
    if not directory.is_dir():
        raise ValueError('stock image path must be a directory')

    images = {}
    for name in STOCK_IMAGES:
        image = directory / name
        try:
            image = image.resolve(strict=True)
        except OSError as error:
            raise ValueError('required stock image is unavailable: ' + name) from error
        if not image.is_file() or not os.access(image, os.R_OK):
            raise ValueError('required stock image is not a readable file: ' + name)
        images[name] = image
    return images


def capture_pstore(directory, transport):
    try:
        result = transport.adb(['exec-out', 'sh', '-c', PSTORE_COMMAND], timeout=30)
        data = result.stdout
        report = {'exit': result.returncode, 'bytes': len(data), 'saved': False}
        if (result.returncode == 0 and data and len(data) <= 2097152 and
                not data.startswith((b'cat: ', b'/system/bin/sh: ', b'sh: '))):
            evidence.write_private_new(Path(directory) / 'console-ramoops-0.bin', data)
            report.update(saved=True, sha256=hashlib.sha256(data).hexdigest())
        else:
            report['reason'] = 'missing, empty, oversize, or unreadable; not evidence of absence of events'
        return report
    except Exception as error:
        return {'saved': False, 'error': type(error).__name__ + ': ' + str(error)}


def capture_after_return(trial_dir, pre_dir, pre_sha256, run_id, transport):
    """Attempt three raw slots and pstore independently, then compare offline."""
    result = {'slots': [], 'all_slots_complete': False, 'comparison': None,
              'pstore': None, 'boot_identity': 'unverified', 'payload_integrity': 'unverified'}
    post_dir = Path(trial_dir) / 'posttrial-slots'
    try:
        post_dir.mkdir(mode=0o700)  # refuse reuse or replacement
        geometry = transport.adb(['shell', 'blockdev', '--getsize64', preboot.PARTITION], timeout=15)
        result['geometry'] = {'exit': geometry.returncode, 'raw': geometry.stdout.decode('ascii', 'replace').strip()}
        if geometry.returncode or geometry.stdout.strip() != str(preboot.PARTITION_BYTES).encode():
            raise evidence.EvidenceError('unixnu_logs geometry is unavailable or not 32 MiB')
        result['slots'] = preboot.capture_slots(post_dir, transport)
        result['all_slots_complete'] = all(item['complete'] for item in result['slots'])
    except Exception as error:
        result['slot_capture_error'] = type(error).__name__ + ': ' + str(error)
    # Optional pstore failure cannot suppress any slot attempt or invalidate raw capture.
    result['pstore'] = capture_pstore(trial_dir, transport)
    if result['all_slots_complete']:
        try:
            checked = transport.compare_local(pre_dir, pre_sha256, post_dir, run_id)
            evidence.write_private_new(Path(trial_dir) / 'slot-comparison.json', checked.stdout)
            try:
                detail = json.loads(checked.stdout)
            except (ValueError, UnicodeDecodeError):
                detail = {'result': 'invalid-comparison-output'}
            result['comparison'] = {'helper_exit': checked.returncode, 'detail': detail,
                                    'boot_identity': 'unverified', 'payload_integrity': 'unverified'}
            # Exit 5 is successful candidate-only comparison, not proof of boot.
            result['comparison']['candidate_only'] = (
                checked.returncode == 5 and detail.get('result') == 'candidate-only')
        except Exception as error:
            result['comparison'] = {'error': type(error).__name__ + ': ' + str(error),
                                    'boot_identity': 'unverified', 'payload_integrity': 'unverified'}
    return result


def recover(trial_dir, pre_dir, pre_sha256, run_id, transport, stock_image_dir,
            return_timeout=2550, reboot_trial=False):
    stock_images = validate_stock_images(stock_image_dir)
    trial_dir = Path(trial_dir)
    if not trial_dir.is_dir():
        raise ValueError('trial_dir must already exist')
    if not evidence.valid_id(run_id):
        raise ValueError('run ID must be nonzero lowercase hex')
    if len(pre_sha256) != 64 or any(c not in '0123456789abcdef' for c in pre_sha256):
        raise ValueError('preboot receipt hash must be 64 lowercase hex')
    receipt_path = trial_dir / 'return-recovery.json'
    state = {'started_utc': utc(), 'automatic_fastboot_return_verified': False,
             'fastboot_enumeration_interpretation': 'mechanism unknown until independent evidence',
             'root_rewritten': False, 'full_root_captured': False, 'boot_images_restored': False,
             'android_ready': False, 'boot_identity': 'unverified', 'payload_integrity': 'unverified',
             'preboot_receipt_sha256': pre_sha256, 'image_run_id': run_id}
    flags = os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW
    fd = os.open(receipt_path, flags, 0o600)
    with os.fdopen(fd, 'w+', encoding='utf-8') as receipt:
        def save():
            receipt.seek(0)
            receipt.truncate()
            receipt.write(json.dumps(state, indent=2, sort_keys=True) + '\n')
            receipt.flush()
            os.fsync(receipt.fileno())
        save()
        try:
            if reboot_trial:
                state['in_progress'] = 'request_trial_reboot'
                save()
                run_logged(transport, ['reboot'], trial_dir / 'trial-reboot.log')
                state['trial_reboot_exit_zero'] = True
                save()
            wait_for(lambda: not transport.fastboot_present(), 30)
            state['fastboot_disappearance_observed_utc'] = utc()
            save()
            wait_for(transport.fastboot_present, return_timeout)
            state['fastboot_reappearance_observed_utc'] = utc()
            save()
            # Exactly one attempt each; an error or timeout stops all writes.
            for partition, image in (('init_boot_b', 'init_boot_b.img'), ('boot_b', 'boot_b.img')):
                state['in_progress'] = 'restore_' + partition
                save()
                run_logged(transport, ['flash', partition, str(stock_images[image])],
                           trial_dir / ('restore-' + partition + '.log'))
                state[partition + '_stock_flash_exit_zero'] = True
                save()
            state['boot_images_restored'] = True
            state['in_progress'] = 'reboot_stock'
            save()
            run_logged(transport, ['reboot'], trial_dir / 'restore-reboot.log')
            wait_for(transport.android_ready, 360)
            state['android_ready'] = True
            state['android_ready_utc'] = utc()
            state['in_progress'] = 'capture_independent_raw_slots_and_pstore'
            save()
            state['capture'] = capture_after_return(trial_dir, pre_dir, pre_sha256, run_id, transport)
            state['in_progress'] = None
            state['finished_utc'] = utc()
            save()
            return state
        except Exception as error:
            state['error'] = type(error).__name__ + ': ' + str(error)
            state['stopped_utc'] = utc()
            save()
            raise


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('trial_dir', type=Path)
    p.add_argument('--preboot-dir', type=Path, required=True)
    p.add_argument('--preboot-receipt-sha256', required=True)
    p.add_argument('--run-id', required=True)
    p.add_argument('--serial')
    p.add_argument('--stock-image-dir', type=Path, required=True,
                   help='directory containing init_boot_b.img and boot_b.img')
    p.add_argument('--return-timeout', type=int, default=2550)
    p.add_argument('--reboot-trial', action='store_true')
    args = p.parse_args()
    state = recover(args.trial_dir, args.preboot_dir, args.preboot_receipt_sha256,
                    args.run_id, Transport(args.serial), args.stock_image_dir,
                    args.return_timeout, args.reboot_trial)
    print(json.dumps({'boot_images_restored': state['boot_images_restored'],
                      'all_slots_complete': state['capture']['all_slots_complete'],
                      'comparison': state['capture']['comparison'],
                      'boot_identity': 'unverified', 'payload_integrity': 'unverified'}, sort_keys=True))


if __name__ == '__main__':
    main()
