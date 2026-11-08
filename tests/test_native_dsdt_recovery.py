import hashlib
import importlib.util
from pathlib import Path
import struct
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location(
    'native_dsdt', Path(__file__).resolve().parents[1] / 'scripts/stage-native-dsdt.py')
native = importlib.util.module_from_spec(spec)
spec.loader.exec_module(native)


def entry(name, body=b''):
    name = name + b'\0'
    fields = [1, 0o100644, 0, 0, 1, 0, len(body), 0, 0, 0, 0, len(name), 0]
    data = b'070701' + b''.join(f'{value:08x}'.encode() for value in fields) + name
    data += bytes(-len(data) % 4)
    data += body
    return data + bytes(-len(data) % 4)


def fixture(command=b'acpi_osi="!Windows 2022" root=/dev/mapper/root'):
    dsdt, ssdt = b'DSDT synthetic fixture', b'SSDT synthetic fixture'
    archive = (entry(native.OLD_NAME, dsdt) + entry(b'kernel/firmware/acpi/SSDT.aml', ssdt)
               + entry(b'TRAILER!!!') + b'\x1f\x8bMAIN_COMPRESSED_INITRAMFS_MUST_NOT_CHANGE')
    payloads = {'.linux': bytes(range(256)) * 4, '.initrd': archive,
                '.cmdline': command + b'\0', '.uname': b'7.2.8-test\0'}
    data = bytearray(1024 + len(payloads) * 4096)
    data[:2] = b'MZ'
    struct.pack_into('<I', data, 60, 128)
    data[128:132] = b'PE\0\0'
    struct.pack_into('<HH', data, 132, 0xaa64, len(payloads))
    struct.pack_into('<H', data, 148, 240)
    struct.pack_into('<H', data, 152, 0x20b)
    for index, (name, body) in enumerate(payloads.items()):
        header = 152 + 240 + index * 40
        offset = 1024 + index * 4096
        data[header:header + 8] = name.encode().ljust(8, b'\0')
        struct.pack_into('<IIII', data, header + 8, len(body), 0x2000 * (index + 1), 4096, offset)
        data[offset:offset + len(body)] = body
    return bytes(data), dsdt, ssdt


class NativeDsdtRecoveryTests(unittest.TestCase):
    def build(self, source, dsdt, ssdt):
        with patch.object(native, 'DSDT_SHA256', hashlib.sha256(dsdt).hexdigest()), \
             patch.object(native, 'SSDT_SHA256', hashlib.sha256(ssdt).hexdigest()):
            return native.build_candidate(source)

    def test_removes_only_override_path_and_preserves_kernel_and_main_initramfs(self):
        source, dsdt, ssdt = fixture()
        result, report = self.build(source, dsdt, ssdt)
        original, _ = native.sections(source)
        changed, _ = native.sections(result)
        self.assertEqual(len(source), len(result))
        self.assertEqual(original['.linux']['data'], changed['.linux']['data'])
        original_entries = list(native.cpio_entries(original['.initrd']['data']))
        changed_entries = list(native.cpio_entries(changed['.initrd']['data']))
        self.assertEqual([body for _, _, body in original_entries],
                         [body for _, _, body in changed_entries])
        self.assertNotIn(native.OLD_NAME, [name for name, _, _ in changed_entries])
        self.assertIn(native.NEW_NAME, [name for name, _, _ in changed_entries])
        self.assertEqual(original['.initrd']['data'].split(b'\x1f\x8b', 1)[1],
                         changed['.initrd']['data'].split(b'\x1f\x8b', 1)[1])
        self.assertEqual(original['.cmdline']['data'], changed['.cmdline']['data'])
        self.assertEqual(report['active_acpi_tables'], ['kernel/firmware/acpi/SSDT.aml'])

    def test_rejects_missing_or_overridden_osi_setting(self):
        for command in (b'root=x', b'acpi_osi="!Windows 2022" acpi_osi="Windows 2022"'):
            source, dsdt, ssdt = fixture(command)
            with self.assertRaisesRegex(ValueError, 'OSI'):
                self.build(source, dsdt, ssdt)

    def test_rejects_unrecognized_override_instead_of_silently_disabling_it(self):
        source, dsdt, ssdt = fixture()
        with self.assertRaisesRegex(ValueError, 'DSDT'):
            native.build_candidate(source)

    def test_refuses_signed_image(self):
        source, dsdt, ssdt = fixture()
        modified = bytearray(source)
        struct.pack_into('<I', modified, 152 + 148, 128)
        with self.assertRaisesRegex(ValueError, 'Signed'):
            self.build(bytes(modified), dsdt, ssdt)

    def test_rejects_truncated_archive(self):
        with self.assertRaisesRegex(ValueError, 'Truncated'):
            list(native.cpio_entries(entry(native.OLD_NAME, b'abc')[:-6]))

    def test_preserves_tightly_packed_command_line(self):
        source, dsdt, ssdt = fixture()
        modified = bytearray(source)
        sec, _ = native.sections(source)
        size = len(sec['.cmdline']['data'].split(b'\0', 1)[0]) + 1
        struct.pack_into('<I', modified, sec['.cmdline']['header'] + 16, size)
        result, _ = self.build(bytes(modified), dsdt, ssdt)
        changed, _ = native.sections(result)
        self.assertEqual(changed['.cmdline']['raw_size'], size)
        self.assertEqual(changed['.cmdline']['data'], bytes(modified)[sec['.cmdline']['offset']:sec['.cmdline']['offset'] + size])


if __name__ == '__main__':
    unittest.main()
