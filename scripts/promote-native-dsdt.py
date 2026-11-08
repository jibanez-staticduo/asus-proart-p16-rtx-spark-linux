#!/usr/bin/env python3
"""Promote the observed working native-DSDT boot and install update-safe policy."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

ENTRY = 'p16-native-dsdt-test'
STATE = Path('/home/johann/.local/state/hyprland-recovery-20261105')
IMAGE = Path('/boot/EFI/Linux/omarchy_p16-native-dsdt-test.efi')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def efi_entry(name):
    files = list(Path('/sys/firmware/efi/efivars').glob(name + '-*'))
    return files[0].read_bytes()[4:].decode('utf-16-le').rstrip('\0') if files else None


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package', required=True, type=Path)
    args = parser.parse_args()
    require(os.geteuid() == 0, 'Run as root')
    require(efi_entry('LoaderEntrySelected') == ENTRY, 'Boot the native-DSDT test before promoting it')
    cpus = sorted(int(p.name[3:]) for p in Path('/sys/devices/system/cpu').glob('cpu[0-9]*'))
    require(cpus == list(range(20)), 'CPU devices have not recovered')
    require(all(Path(f'/sys/devices/system/cpu/cpu{i}/topology/core_id').is_file() for i in cpus), 'CPU topology is incomplete')
    for device in ('NVDA8000:00', 'NVDA8000:01', 'NVDA8000:02', 'NVDA8001:00', 'NVDA8000:04'):
        require((Path('/sys/bus/acpi/devices') / device / 'status').read_text().strip() == '15', 'USB controller absent: ' + device)
    require(bool(list(Path('/dev').glob('video[0-9]*'))), 'No camera device is present')
    require(Path('/sys/firmware/acpi/tables/DSDT').read_bytes()[24:28] == b'\x04\0\0\0', 'Native firmware DSDT is not loaded')
    report = json.loads((STATE / 'native-dsdt-test.json').read_text())
    require(digest(IMAGE) == report['candidate_sha256'], 'Installed native image differs from the tested manifest')
    package = subprocess.check_output(['pacman', '-Qp', str(args.package)], text=True).strip()
    require(package == 'p16-platform-policy 1.1.4-1', 'Unexpected policy package: ' + package)
    npu = subprocess.run(['systemctl', 'is-enabled', 'n1x-platform-npu.service'], capture_output=True, text=True)
    require(npu.stdout.strip() in ('disabled', 'masked', 'masked-runtime'), 'Keep experimental NPU startup disabled during qualification')
    menu = Path('/boot/limine.conf')
    backup = Path(tempfile.mkdtemp(prefix='native-dsdt-promote-', dir='/var/lib/p16-platform'))
    original = menu.read_text()
    require(len(re.findall(r'^default_entry:.*$', original, re.M)) == 1, 'Ambiguous boot default')
    require(re.search(r'^/' + ENTRY + r'\s*$', original, re.M), 'Test entry is missing from the menu')
    (backup / 'limine.conf').write_text(original)
    before = {str(p): digest(p) for p in IMAGE.parent.glob('*.efi')}
    (backup / 'image-hashes-before.json').write_text(json.dumps(before, indent=2) + '\n')
    managed = []
    for base in Path('/usr/lib/modules').glob('*/pkgbase'):
        if base.read_text().strip() == 'linux-p16-platform':
            image = IMAGE.parent / 'omarchy_linux-p16-platform.efi'
            if image.is_file():
                shutil.copy2(image, backup / image.name)
            managed.append(base.parent)
    print('Preflight passed. Recovery backup:', backup, flush=True)
    subprocess.run(['pacman', '-U', '--noconfirm', str(args.package)], check=True)
    shutil.copy2(args.package, Path('/var/lib/p16-platform/packages') / args.package.name)
    with Path('/run/lock/boot-partition.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        current = menu.read_text()
        require(current == original, 'Boot menu changed during policy installation')
        updated = re.sub(r'^default_entry:.*$', 'default_entry: ' + ENTRY, current, count=1, flags=re.M)
        descriptor, temporary = tempfile.mkstemp(prefix='.native-dsdt-default-', dir=menu.parent)
        try:
            with os.fdopen(descriptor, 'w') as output:
                output.write(updated)
                output.flush()
                os.fsync(output.fileno())
            os.chmod(temporary, menu.stat().st_mode & 0o777)
            os.replace(temporary, menu)
        finally:
            Path(temporary).unlink(missing_ok=True)
    subprocess.run(['/usr/local/sbin/p16-boot-menu-policy'], check=True)
    subprocess.run(['bootctl', 'set-default', ENTRY], check=True)
    if managed:
        targets = ''.join(str(path / 'modules.builtin').removeprefix('/') + '\n' for path in managed)
        subprocess.run(['/usr/local/sbin/p16-mkinitcpio-managed'], input=targets, text=True, check=True)
    subprocess.run(['/usr/local/sbin/p16-boot-menu-policy'], check=True)
    subprocess.run(['/usr/local/sbin/p16-platform-update-check', '--all'], check=True)
    subprocess.run(['pacman', '-Qk', 'p16-platform-policy'], check=True)
    allowed = {str(IMAGE.parent / 'omarchy_linux-p16-platform.efi')} if managed else set()
    for name, previous in before.items():
        require(name in allowed or digest(Path(name)) == previous, 'A preserved boot image changed: ' + name)
    require(efi_entry('LoaderEntryDefault') == ENTRY, 'Persistent EFI default did not stick')
    require(re.search(r'^default_entry: ' + ENTRY + '$', menu.read_text(), re.M), 'Menu default did not stick')
    os.sync()
    summary = dict(report, persistent_default=ENTRY, policy_package=package,
                   recovery_images_preserved=True, managed_images_rebuilt=[p.name for p in managed], backup=str(backup))
    (backup / 'result.json').write_text(json.dumps(summary, indent=2) + '\n')
    (STATE / 'persistent-fix.json').write_text(json.dumps(summary, indent=2) + '\n')
    print('Persistent native-DSDT default and update policy installed; preserved recovery image hashes verified.')


if __name__ == '__main__':
    main()
