#!/usr/bin/env python3
"""Replace legacy P16 boot choices with verified primary and frozen recovery copies."""
import argparse
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

STATE = Path('/home/johann/.local/state/hyprland-recovery-20261105')
BOOT = Path('/boot')
IMAGES = BOOT / 'EFI/Linux'
PRIMARY = 'Omarchy-P16'
RECOVERY = 'Omarchy-P16-Recovery'
LEGACY = {'Omarchy', 'p16-native-dsdt-test', 'EFI fallback'}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def efi_entry(name):
    files = list(Path('/sys/firmware/efi/efivars').glob(name + '-*'))
    return files[0].read_bytes()[4:].decode('utf-16-le').rstrip('\0') if files else None


def clean_menu(original, blake):
    prefix, blocks, current = [], [], None
    for line in original.splitlines(keepends=True):
        match = re.match(r'^\s*/(?!/)([+]?)([^\n]+?)\s*$', line)
        if match:
            current = [match.group(2), [line]]
            blocks.append(current)
        elif current is None:
            prefix.append(line)
        else:
            current[1].append(line)
    header = ''.join(prefix)
    require(len(re.findall(r'^default_entry:.*$', header, re.M)) == 1, 'Ambiguous boot default')
    header = re.sub(r'^default_entry:.*$', 'default_entry: ' + PRIMARY, header, count=1, flags=re.M)
    stable = (f'\n/{PRIMARY}\n    comment: Verified P16 desktop with native firmware ACPI\n'
              f'    protocol: efi\n    path: boot():/EFI/Linux/omarchy_p16.efi#{blake}\n'
              f'\n/{RECOVERY}\n    comment: Frozen copy of the verified working boot\n'
              f'    protocol: efi\n    path: boot():/EFI/Linux/omarchy_p16-recovery.efi#{blake}\n')
    others = ''.join(''.join(lines) for name, lines in blocks if name not in LEGACY)
    require(not any(name in (PRIMARY, RECOVERY) for name, _ in blocks), 'Clean entries already exist')
    return header.rstrip() + '\n' + stable + ('\n' + others if others else '')


def atomic_menu(text):
    path = BOOT / 'limine.conf'
    descriptor, name = tempfile.mkstemp(prefix='.p16-cleanup-', dir=BOOT)
    try:
        with os.fdopen(descriptor, 'w') as output:
            output.write(text)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(name, path.stat().st_mode & 0o777)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package', type=Path, required=True)
    args = parser.parse_args()
    require(os.geteuid() == 0, 'Run as root')
    require(efi_entry('LoaderEntrySelected') == 'p16-native-dsdt-test', 'Current boot must be the verified native-DSDT entry')
    require(Path('/proc/sys/kernel/osrelease').read_text().strip() == '7.2.8-23-rtx-spark-npu', 'Unexpected running kernel')
    source = IMAGES / 'omarchy_p16-native-dsdt-test.efi'
    manifest = json.loads((STATE / 'native-dsdt-test.json').read_text())
    require(sha(source) == manifest['candidate_sha256'], 'Verified source image changed')
    primary, recovery = IMAGES / 'omarchy_p16.efi', IMAGES / 'omarchy_p16-recovery.efi'
    require(not primary.exists() and not recovery.exists(), 'Destination image already exists')
    require(shutil.disk_usage(BOOT).free > 2 * source.stat().st_size + 16*1024*1024, 'Insufficient ESP staging space')
    inventory = json.loads((STATE / 'boot-cleanup-inventory.json').read_text())
    expected = {row['path']: row['sha256'] for row in inventory['images'] if row['path'].endswith('.efi')}
    actual = {str(path): sha(path) for path in IMAGES.glob('*.efi')}
    require(actual == expected, 'Boot images changed since inventory; inspect again')
    menu = BOOT / 'limine.conf'
    original = menu.read_text()
    require(original == inventory['menu'], 'Boot menu changed since inventory')
    require(subprocess.check_output(['pacman', '-Qp', str(args.package)], text=True).strip() == 'p16-platform-policy 1.1.5-1', 'Unexpected policy package')
    backup = Path(tempfile.mkdtemp(prefix='boot-cleanup-', dir='/var/lib/p16-platform'))
    (backup / 'limine.conf').write_text(original)
    (backup / 'inventory.json').write_text(json.dumps(inventory, indent=2) + '\n')
    old_default = efi_entry('LoaderEntryDefault')
    (backup / 'efi-default.txt').write_text((old_default or '') + '\n')
    # Keep the managed build artifact for package-update checks, outside the menu.
    obsolete = [p for p in IMAGES.glob('*.efi') if p.name != 'omarchy_linux-p16-platform.efi']
    archive = backup / 'EFI/Linux'
    archive.mkdir(parents=True)
    for path in obsolete:
        target = archive / path.name
        shutil.copy2(path, target)
        require(sha(target) == expected[str(path)], 'Archive verification failed: ' + path.name)
    loader_hashes = {str(p): sha(p) for p in (BOOT / 'EFI').rglob('*.efi') if p.parent != IMAGES}
    print('Verified archive:', backup, flush=True)
    print('Installing clean-menu update policy.', flush=True)
    subprocess.run(['pacman', '-U', '--noconfirm', str(args.package)], check=True)
    shutil.copy2(args.package, Path('/var/lib/p16-platform/packages') / args.package.name)
    created, removed, menu_changed = [], [], False
    try:
        for path in (primary, recovery):
            shutil.copy2(source, path)
            created.append(path)
            require(sha(path) == manifest['candidate_sha256'], 'Qualified copy hash mismatch')
        updated = clean_menu(original, manifest['candidate_blake2b'])
        with Path('/run/lock/boot-partition.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            require(menu.read_text() == original, 'Boot menu changed during package install')
            atomic_menu(updated)
            menu_changed = True
        subprocess.run(['bootctl', 'set-default', PRIMARY], check=True)
        subprocess.run(['/usr/local/sbin/p16-boot-menu-policy'], check=True)
        spec = importlib.util.spec_from_file_location('verify_boot', Path(__file__).with_name('verify-boot.py'))
        verifier = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(verifier)
        verifier.verify_menu()
        for path in obsolete:
            require(sha(path) == expected[str(path)], 'Obsolete image changed before removal')
            path.unlink()
            removed.append(path)
        subprocess.run(['/usr/local/sbin/p16-boot-menu-policy'], check=True)
        subprocess.run(['/usr/local/sbin/p16-platform-update-check', '--all'], check=True)
        subprocess.run(['pacman', '-Qk', 'p16-platform-policy'], check=True)
        for name, previous in loader_hashes.items():
            require(sha(Path(name)) == previous, 'An unrelated EFI loader changed: ' + name)
        require(efi_entry('LoaderEntryDefault') == PRIMARY, 'Persistent boot default mismatch')
        require(sha(primary) == sha(recovery) == manifest['candidate_sha256'], 'Final image hashes differ')
        os.sync()
        summary = {'default': PRIMARY, 'recovery': RECOVERY, 'verified_image_sha256': manifest['candidate_sha256'],
                   'archived_images': [p.name for p in removed], 'archive': str(backup),
                   'bytes_reclaimed_on_esp': sum(row['size'] for row in inventory['images'] if Path(row['path']) in removed) - 2*primary.stat().st_size,
                   'menu': menu.read_text()}
        (backup / 'result.json').write_text(json.dumps(summary, indent=2) + '\n')
        (STATE / 'boot-cleanup-result.json').write_text(json.dumps(summary, indent=2) + '\n')
        print(json.dumps(summary, indent=2))
    except BaseException:
        for path in removed:
            shutil.copy2(archive / path.name, path)
        if menu_changed:
            with Path('/run/lock/boot-partition.lock').open('a') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                atomic_menu(original)
            subprocess.run(['bootctl', 'set-default', old_default or ''], check=False)
        for path in created:
            path.unlink(missing_ok=True)
        os.sync()
        print('Original boot menu and images restored. Verified archive:', backup, flush=True)
        raise


if __name__ == '__main__':
    main()
