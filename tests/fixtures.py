"""Synthetic PE and early-CPIO inputs for boot-verifier tests."""
import struct


def entry(name, body=b''):
    name = name + b'\0'
    fields = [1, 0o100644, 0, 0, 1, 0, len(body), 0, 0, 0, 0, len(name), 0]
    data = b'070701' + b''.join(f'{value:08x}'.encode() for value in fields) + name
    data += bytes(-len(data) % 4)
    data += body
    return data + bytes(-len(data) % 4)


def fixture(command=b'acpi_osi="!Windows 2022" root=/dev/mapper/root'):
    dsdt, ssdt = b'DSDT synthetic fixture', b'SSDT synthetic fixture'
    archive = (entry(b'kernel/firmware/acpi/DSDT.aml', dsdt) + entry(b'kernel/firmware/acpi/SSDT.aml', ssdt)
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
