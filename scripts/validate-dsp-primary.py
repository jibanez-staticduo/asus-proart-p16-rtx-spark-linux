#!/usr/bin/env python3
"""Validate primary DSP buffers or OPEN/HWFREE/CLOSE on already booted cores.

No stream is opened, no driver is replaced, and no uncertain request is retried.
All published DSP memory remains owned by the DSP driver until reboot.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
SYS = Path('/sys/bus/platform/devices')
CLIENT_NAMES = {'primary': 'n1x_adsp_primary_check', 'task': 'n1x_adsp_task_check'}


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / filename)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def run(args, timeout=30):
    result = subprocess.run([str(x) for x in args], capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError('Command failed: ' + ' '.join(map(str, args)) + '\n' +
                           (result.stdout + result.stderr)[-4000:])
    return result.stdout.strip()


def fields(text):
    return dict(item.split('=', 1) for item in text.split() if '=' in item)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--client-module', type=Path, required=True)
    parser.add_argument('--mode', choices=tuple(CLIENT_NAMES), default='primary')
    parser.add_argument('--sha256', required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    client_name = CLIENT_NAMES[args.mode]
    if os.geteuid():
        parser.error('Run as root via sudo or pkexec')
    if args.report.exists():
        parser.error('Choose a new report path')
    live = load('primary_live', 'validate-platform-live.py')
    late = load('primary_boot_hashes', 'boot_audit.py')
    provider = load('primary_provider', 'validate-sroot-provider.py')
    live.check_target()
    client = live.check_module(args.client_module)
    actual = hashlib.sha256(client.read_bytes()).hexdigest()
    if actual != args.sha256 or run(['modinfo', '-F', 'name', client]) != client_name or \
            run(['modinfo', '-F', 'depends', client]) != 'n1x_adsp':
        parser.error('Validation module identity or hash does not match')
    client_sysfs = Path('/sys/module') / client_name
    if client_sysfs.exists():
        parser.error('Validation module already exists; no replacement attempted')
    loaded = Path('/sys/module/n1x_adsp/srcversion').read_text().strip()
    if loaded != run(['modinfo', '-F', 'srcversion', 'n1x_adsp']):
        parser.error('Loaded DSP source differs from the installed module')
    before = {}
    for name in ('NVDA8301:00', 'NVDA8302:00', 'NVDA8303:00'):
        device = SYS / name
        if (device / 'driver').resolve().name != 'n1x-adsp':
            parser.error('DSP binding changed: ' + name)
        before[name] = fields((device / 'status').read_text())
        if any(before[name].get(key) != '1' for key in
               ('published', 'loader_accepted', 'ready')) or \
                any(before[name].get(key) != '0' for key in
                    ('error', 'power_error', 'ipc_error', 'ipc_pending')):
            parser.error('DSP boot/transport state is not healthy: ' + name)
    primary = SYS / 'NVDA8303:00' / 'status'
    primary_before = before['NVDA8303:00']
    if args.mode == 'primary' and primary_before.get('primary_attempted') != '0':
        parser.error('Primary registration was already attempted; no replay')
    if args.mode == 'task' and (primary_before.get('primary_ready') != '1' or
                               primary_before.get('primary_completed') != '4' or
                               primary_before.get('ipc_token') != '4' or
                               any(primary_before.get(key) != '0' for key in
                                   ('primary_task_active', 'primary_task_error', 'primary_client'))):
        parser.error('Expected a fresh completed primary registration before the task trial')
    npu_before = provider.snapshot()
    if npu_before.get('status', {}).get('queue_poisoned') != '0' or \
            npu_before.get('status', {}).get('task_verified') != '1':
        parser.error('NPU is not healthy; no shared-platform trial attempted')
    messages, hashes = live.kernel_messages(), late.boot_hashes()
    report = {'kernel': os.uname().release,
              'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
              'driver_srcversion': loaded, 'client_sha256': actual,
              'mode': args.mode,
              'before': before, 'npu_before': npu_before,
              'registration_verified': False, 'stream_started': False,
              'task_lifecycle_verified': False,
              'dma_release_verified': False, 'needs_reboot_for_cleanup': True}
    failure = None
    submitted = False
    try:
        submitted = True
        run(['insmod', client], timeout=90)
        status = fields(primary.read_text())
        deadline = time.monotonic() + 5
        while status.get('power_queued') != status.get('power_completed') and \
                status.get('power_error') == '0' and time.monotonic() < deadline:
            time.sleep(0.01)
            status = fields(primary.read_text())
        report['primary_after'] = status
        if any(status.get(key) != '1' for key in ('primary_attempted', 'primary_ready')) or \
                status.get('primary_completed') != '4' or \
                any(status.get(key) != '0' for key in
                    ('primary_error', 'ipc_error', 'ipc_pending', 'power_error', 'error')):
            raise RuntimeError('Primary registrations were not all acknowledged; no retry')
        if status.get('power_queued') != status.get('power_completed'):
            raise RuntimeError('Firmware power worker did not finish its queued requests')
        report['registration_verified'] = True
        if args.mode == 'task':
            if any(status.get(key) != '0' for key in
                   ('primary_task_active', 'primary_task_error', 'primary_client')):
                raise RuntimeError('Primary task or client did not close cleanly')
            if status.get('ipc_token') != '7':
                raise RuntimeError('Task commands did not consume exactly three transactions')
            report['task_lifecycle_verified'] = True
    except Exception as error:
        report['error'] = str(error)
        failure = error
    finally:
        # A timed-out insmod can still be executing in the kernel. Only remove
        # a fully initialized validation client; the DSP driver remains pinned.
        if client_sysfs.exists() and (client_sysfs / 'initstate').read_text().strip() == 'live':
            try:
                run(['rmmod', client_name])
            except Exception as error:
                report['client_cleanup_error'] = str(error)
                failure = failure or error
        report['client_submitted'] = submitted
        report['client_still_present'] = client_sysfs.exists()
        report['primary_after'] = fields(primary.read_text())
        report['npu_after'] = provider.snapshot()
        report['kernel_messages'] = live.new_kernel_messages(messages)
        report['boot_artifacts_unchanged'] = hashes == late.boot_hashes()
        try:
            live.check_kernel_messages(report['kernel_messages'])
            if any(re.search(r'Unhandled fault|translation fault|SError|smmu.*(?:fault|event)|NVRM: Xid',
                             line, re.I) for line in report['kernel_messages']):
                raise RuntimeError('New kernel/DMA fault during registration')
            if not report['boot_artifacts_unchanged']:
                raise RuntimeError('Boot artifacts changed unexpectedly')
            if report['npu_after'].get('status', {}).get('queue_poisoned') != '0':
                raise RuntimeError('NPU queue failed during registration')
        except Exception as error:
            report['postflight_error'] = str(error)
            failure = failure or error
        verified = report['registration_verified'] if args.mode == 'primary' else report['task_lifecycle_verified']
        report['passed'] = failure is None and verified and \
            not report['client_still_present']
        args.report.parent.mkdir(parents=True, exist_ok=True)
        with args.report.open('x') as stream:
            json.dump(report, stream, indent=2)
            stream.write('\n')
        print(args.report, flush=True)
    if failure:
        raise SystemExit(str(failure))


if __name__ == '__main__':
    main()
