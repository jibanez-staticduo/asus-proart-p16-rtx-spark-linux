#!/usr/bin/env python3
"""Build an isolated UKI test using the firmware DSDT and existing ASUS SSDT.

The original image is never changed. Only the early CPIO DSDT pathname
changes; the kernel, command line, modules and compressed initramfs stay
byte-identical. --install appends a test entry without changing the default.
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import struct
import subprocess
import sys
import tempfile

OLD_NAME = b'kernel/firmware/acpi/DSDT.aml'
NEW_NAME = b'kernel/firmware/DSDT.skip.aml'
DSDT_SHA256 = '80e0ad4a051704003d09029cb600a9814e1cd370ce774b9749dc09d2232b5aaf'
SSDT_SHA256 = '9b28ffc14c19f8b7ea2278de88ff9ec070f5f70401107d81fef067a916aa9695'
ENTRY = 'p16-native-dsdt-test'
IMAGE = '/EFI/Linux/omarchy_p16-native-dsdt-test.efi'


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sections(data):
    require(data[:2] == b'MZ' and len(data) >= 64, 'Not a PE image')
    pe = struct.unpack_from('<I', data, 60)[0]
    require(pe + 24 <= len(data) and data[pe:pe + 4] == b'PE\0\0', 'Invalid PE header')
    machine, count = struct.unpack_from('<HH', data, pe + 4)
    require(machine == 0xaa64, 'Expected ARM64 UKI')
    optional_size = struct.unpack_from('<H', data, pe + 20)[0]
    optional = pe + 24
    require(optional + optional_size + 40 * count <= len(data), 'Truncated PE sections')
    require(struct.unpack_from('<H', data, optional)[0] == 0x20b, 'Expected PE32+')
    # A modified image cannot retain a valid Authenticode or PCR signature.
    if optional_size >= 152:
        require(struct.unpack_from('<I', data, optional + 148)[0] == 0, 'Signed UKI: refuse modification')
    result = {}
    for index in range(count):
        header = optional + optional_size + index * 40
        name = data[header:header + 8].rstrip(b'\0').decode('ascii')
        virtual_size, va, raw_size, offset = struct.unpack_from('<IIII', data, header + 8)
        require(offset + raw_size <= len(data), 'Section outside image: ' + name)
        require(name not in result, 'Duplicate section: ' + name)
        result[name] = dict(header=header, virtual_size=virtual_size, va=va,
                            raw_size=raw_size, offset=offset,
                            data=data[offset:offset + raw_size])
    require('.pcrsig' not in result, 'PCR-signed UKI: refuse modification')
    return result, optional


def cpio_entries(data):
    offset = 0
    while True:
        require(offset + 110 <= len(data), 'Truncated early CPIO header')
        require(data[offset:offset + 6] == b'070701', 'Expected newc early CPIO')
        fields = [int(data[offset + 6 + i * 8:offset + 14 + i * 8], 16) for i in range(13)]
        size, name_size = fields[6], fields[11]
        name_start = offset + 110
        require(name_size > 0 and name_start + name_size <= len(data), 'Truncated CPIO name')
        require(data[name_start + name_size - 1] == 0, 'Unterminated CPIO name')
        name = data[name_start:name_start + name_size - 1]
        body = (name_start + name_size + 3) & ~3
        require(body + size <= len(data), 'Truncated CPIO payload')
        yield name, name_start, data[body:body + size]
        offset = (body + size + 3) & ~3
        if name == b'TRAILER!!!':
            return


def checksum(data, optional):
    data[optional + 64:optional + 68] = bytes(4)
    require(sys.byteorder == 'little', 'Expected little-endian build host')
    total = sum(memoryview(data)[:len(data) & ~1].cast('H'))
    if len(data) % 2:
        total += data[-1]
    while total >> 16:
        total = (total & 0xffff) + (total >> 16)
    struct.pack_into('<I', data, optional + 64, total + len(data))


def build_candidate(source):
    sec, optional = sections(source)
    for name in ('.linux', '.initrd', '.cmdline', '.uname'):
        require(name in sec, 'Missing UKI section: ' + name)
    command = sec['.cmdline']['data'].split(b'\0', 1)[0].decode()
    arguments = shlex.split(command)
    osi = [a for a in arguments if a.startswith('acpi_osi=')]
    require(osi == ['acpi_osi=!Windows 2022'], 'Expected one negative Windows-2022 OSI argument')
    require(not any(a in ('acpi=off', 'acpi=noirq') for a in arguments), 'ACPI is disabled')
    entries = list(cpio_entries(sec['.initrd']['data']))
    dsdt = [(start, body) for name, start, body in entries if name.removeprefix(b'./') == OLD_NAME]
    ssdt = [body for name, _, body in entries if name.removeprefix(b'./') == b'kernel/firmware/acpi/SSDT.aml']
    require(len(dsdt) == 1 and len(ssdt) == 1, 'Expected exactly one pinned DSDT and ASUS SSDT')
    require(hashlib.sha256(dsdt[0][1]).hexdigest() == DSDT_SHA256, 'Unexpected source DSDT')
    require(hashlib.sha256(ssdt[0]).hexdigest() == SSDT_SHA256, 'Unexpected source ASUS SSDT')
    modified = bytearray(source)
    name_start = dsdt[0][0]
    if sec['.initrd']['data'][name_start:name_start + 2] == b'./':
        name_start += 2
    absolute = sec['.initrd']['offset'] + name_start
    require(len(OLD_NAME) == len(NEW_NAME), 'CPIO pathname sizes differ')
    modified[absolute:absolute + len(OLD_NAME)] = NEW_NAME
    checksum(modified, optional)
    candidate = bytes(modified)
    check, _ = sections(candidate)
    for name in sec:
        if name != '.initrd':
            require(sec[name]['data'] == check[name]['data'], 'Unexpected changed section: ' + name)
    restored = bytearray(check['.initrd']['data'])
    restored[name_start:name_start + len(NEW_NAME)] = OLD_NAME
    require(bytes(restored) == sec['.initrd']['data'], 'Changed main initramfs or CPIO payload')
    active = [name.removeprefix(b'./') for name, _, _ in cpio_entries(check['.initrd']['data'])]
    require(OLD_NAME not in active and NEW_NAME in active, 'DSDT override still active')
    report = {'kernel': check['.uname']['data'].rstrip(b'\0\n').decode(),
              'source_sha256': hashlib.sha256(source).hexdigest(),
              'candidate_sha256': hashlib.sha256(candidate).hexdigest(),
              'candidate_blake2b': hashlib.blake2b(candidate).hexdigest(),
              'command_line': command, 'active_acpi_tables': [n.decode() for n in active if n.startswith(b'kernel/firmware/acpi/')],
              'kernel_and_main_initramfs_unchanged': True}
    return candidate, report


def install(source_path, candidate, report, select_once):
    require(os.geteuid() == 0, '--install requires root')
    require(Path('/sys/class/dmi/id/product_name').read_text().strip() == 'ProArt P16 H7607BA', 'Wrong model')
    require(Path('/sys/class/dmi/id/bios_version').read_text().strip() == 'H7607BA.300', 'Wrong BIOS')
    npu = subprocess.run(['systemctl', 'is-enabled', 'n1x-platform-npu.service'],
                         text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    require(npu.stdout.strip() in ('disabled', 'masked', 'masked-runtime'),
            'NPU startup must already be disabled for this qualification')
    for secure in Path('/sys/firmware/efi/efivars').glob('SecureBoot-*'):
        require(secure.read_bytes()[4:] == b'\0', 'Secure Boot is enabled or unrecognized')
    require(source_path.resolve().parent == Path('/boot/EFI/Linux'), 'Source must be an existing boot UKI')
    menu = Path('/boot/limine.conf')
    with Path('/run/lock/boot-partition.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        original = menu.read_text()
        current_source = source_path.read_bytes()
        require(hashlib.sha256(current_source).hexdigest() == report['source_sha256'], 'Source image changed during staging')
        source_digest = hashlib.blake2b(current_source).hexdigest()
        require(f'boot():/EFI/Linux/{source_path.name}#{source_digest}' in original, 'Source does not match hashed boot menu')
        destination = Path('/boot' + IMAGE)
        require(not destination.exists() and not re.search(r'^/' + ENTRY + r'\s*$', original, re.M), 'Test entry already exists')
        backup = Path(tempfile.mkdtemp(prefix='native-dsdt-', dir='/var/lib/p16-platform'))
        (backup / 'limine.conf').write_text(original)
        (backup / 'manifest.json').write_text(json.dumps(report, indent=2) + '\n')
        block = (f'\n/{ENTRY}\n    comment: Test native firmware DSDT; original kernel and command line\n'
                 f'    protocol: efi\n    path: boot():{IMAGE}#{report["candidate_blake2b"]}\n')
        updated = original.rstrip() + '\n' + block
        temporary = destination.with_suffix('.efi.tmp')
        try:
            temporary.write_bytes(candidate)
            with temporary.open('rb') as image:
                os.fsync(image.fileno())
            require(hashlib.sha256(temporary.read_bytes()).hexdigest() == report['candidate_sha256'], 'Staged image hash mismatch')
            os.replace(temporary, destination)
            descriptor, name = tempfile.mkstemp(prefix='.native-dsdt-', dir=menu.parent)
            try:
                with os.fdopen(descriptor, 'w') as file:
                    file.write(updated)
                    file.flush()
                    os.fsync(file.fileno())
                require(menu.read_text() == original, 'Boot menu changed concurrently')
                os.chmod(name, menu.stat().st_mode & 0o777)
                os.replace(name, menu)
            finally:
                Path(name).unlink(missing_ok=True)
            require(menu.read_text().startswith(original.rstrip() + '\n'), 'Existing boot entries changed')
            require(hashlib.blake2b(destination.read_bytes()).hexdigest() == report['candidate_blake2b'], 'Installed image hash mismatch')
            os.sync()
            if select_once:
                subprocess.run(['bootctl', 'set-oneshot', ENTRY], check=True)
            print('Installed isolated test entry:', ENTRY)
            print('Existing default and images preserved. Menu backup:', backup)
        except BaseException:
            if menu.read_text() == updated:
                menu.write_text(original)
            temporary.unlink(missing_ok=True)
            destination.unlink(missing_ok=True)
            os.sync()
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--install', action='store_true')
    parser.add_argument('--select-once', action='store_true')
    args = parser.parse_args()
    require(not args.select_once or args.install, '--select-once requires --install')
    require(args.output.resolve() != args.source.resolve(), 'Cannot overwrite source image')
    require(not args.output.exists(), 'Output already exists')
    candidate, report = build_candidate(args.source.read_bytes())
    args.output.write_bytes(candidate)
    args.output.with_suffix('.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))
    if args.install:
        install(args.source, candidate, report, args.select_once)


if __name__ == '__main__':
    main()
