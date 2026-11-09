"""Check PCBI query wire vectors, response bounds, and ioctl buffer handling."""
import ctypes
import errno
import importlib.util
from pathlib import Path
import struct
import subprocess
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('pluton_info', ROOT / 'platform/pluton/pluton-info.py')
pcbi = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pcbi)


def reply(command, body, result=pcbi.PCBI_SUCCESS):
    return struct.pack('<BBHBBHII', 1, 1, len(body), 1, 2, 0, command, result) + body


class PlutonServiceTests(unittest.TestCase):
    def test_driver_query_ioctl_faults_and_concurrency(self):
        subprocess.run(['python3', str(ROOT / 'platform/pluton/test_query.py')], check=True)

    def test_versions_and_services(self):
        self.assertEqual(pcbi.decode_query(reply(7, bytes([2, 1, 2])), 7), [1, 2])
        services = struct.pack('<I', 2) + bytes([1, 1, 2, 3])
        self.assertEqual(pcbi.decode_query(reply(5, services), 5),
                         [{'id': 1, 'version': 1}, {'id': 2, 'version': 3}])
        self.assertEqual(pcbi.decode_query(reply(5, bytes(4)), 5), [])
        self.assertEqual(pcbi.decode_query(reply(7, bytes(1)), 7), [])
        # PCBI success is a nonzero protocol constant, distinct from PFI status.
        with self.assertRaisesRegex(ValueError, 'query failed'):
            pcbi.decode_query(reply(7, b'', result=0), 7)

    def test_device_info_records(self):
        record = b'PACKAGE1' + bytes(range(24)) + struct.pack('<BBH', 2, 0xf, 3) + b'abc'
        body = bytes.fromhex('0401aabbccddeeff') + record
        self.assertEqual(pcbi.decode_query(reply(4, body), 4),
                         {'platform_id': 4, 'header_data_hex': 'aabbccddeeff',
                          'packages': [{'name_hex': b'PACKAGE1'.hex(),
                                        'metadata_hex': bytes(range(24)).hex(),
                                        'type': 2, 'flags': 15, 'custom_data_hex': '616263'}]})
        self.assertEqual(pcbi.decode_query(reply(4, bytes([4]) + bytes(7)), 4)['packages'], [])
        for size in range(len(body)):
            with self.subTest(size=size), self.assertRaises(ValueError):
                pcbi.decode_query(reply(4, body[:size]), 4)
        # Variable-size records must not be treated as a fixed array.
        second = b'PACKAGE2' + bytes(24) + struct.pack('<BBH', 1, 0, 64) + bytes(range(64))
        two = bytes([4, 2]) + bytes(6) + record + second
        parsed = pcbi.decode_query(reply(4, two), 4)['packages']
        self.assertEqual(len(parsed), 2)
        self.assertEqual(parsed[1]['custom_data_hex'], bytes(range(64)).hex())

    def test_invalid_device_info_records(self):
        valid = bytes([4, 1]) + bytes(6) + b'PACKAGE1' + bytes(24) + struct.pack('<BBH', 1, 0, 0)
        faults = []
        for offset, value in ((0, 0), (1, 0), (1, 2), (40, 0), (41, 0x10)):
            body = bytearray(valid)
            body[offset] = value
            faults.append(body)
        faults.append(valid[:8] + bytes(8) + valid[16:])
        faults.append(valid + bytes(1))
        faults.append(valid[:42] + struct.pack('<H', 65) + bytes(65))
        faults.append(valid[:42] + struct.pack('<H', 1))
        for body in faults:
            with self.subTest(body=body.hex()), self.assertRaises(ValueError):
                pcbi.decode_query(reply(4, body), 4)
        with self.assertRaises(ValueError):
            pcbi.decode_query(bytes(4097), 4)

    def test_truncations_and_unmatched_headers(self):
        complete = reply(7, bytes([2, 1, 2]))
        for size in range(len(complete)):
            with self.subTest(size=size), self.assertRaises(ValueError):
                pcbi.decode_query(complete[:size], 7)
        for offset in (0, 1, 2, 4, 5, 8, 12, 16):
            changed = bytearray(complete)
            changed[offset] ^= 0x80
            with self.subTest(offset=offset), self.assertRaises(ValueError):
                pcbi.decode_query(changed, 7)
        with self.assertRaises(ValueError):
            pcbi.decode_query(complete + bytes(1), 7)
        with self.assertRaises(ValueError):
            pcbi.decode_query(bytes(1025), 7)
        for count in (1, 0x80000000, 0xffffffff):
            with self.subTest(count=count), self.assertRaises(ValueError):
                pcbi.decode_query(reply(5, struct.pack('<I', count)), 5)
        with self.assertRaisesRegex(ValueError, 'error reply'):
            pcbi.decode_query(reply(7, b'\x00', result=0), 7)

    def test_typed_query_ioctl(self):
        expected = reply(7, bytes([1, 1]))

        def ioctl(fd, number, query, mutate):
            self.assertEqual((fd, number, mutate), (123, 0xc0205003, True))
            command, capacity, actual, result, pointer, r1, r2 = struct.unpack('<4IQ2I', query)
            self.assertEqual((command, capacity, actual, result, r1, r2), (7, 1024, 0, 0, 0, 0))
            ctypes.memmove(pointer, expected, len(expected))
            struct.pack_into('<II', query, 8, len(expected), pcbi.PCBI_SUCCESS)

        with patch.object(pcbi.fcntl, 'ioctl', side_effect=ioctl):
            self.assertEqual(pcbi.query_ioctl(123, 7), [1])

    def test_query_errors_never_resubmit_a_request(self):
        for error in (errno.ENOTTY, errno.EOPNOTSUPP, errno.EPROTO, errno.EREMOTEIO):
            with self.subTest(error=error), \
                    patch.object(pcbi.fcntl, 'ioctl', side_effect=OSError(error, 'query')) as call:
                with self.assertRaises(OSError):
                    pcbi.query_ioctl(123, 7)
                self.assertEqual(call.call_count, 1)

    def test_typed_query_result_validation(self):
        expected = reply(7, bytes([1, 1]))
        for offset, value in ((0, 4), (4, 4096), (8, 1025), (12, 0), (24, 1), (28, 1)):
            def ioctl(fd, number, query, mutate):
                pointer = struct.unpack_from('<Q', query, 16)[0]
                ctypes.memmove(pointer, expected, len(expected))
                struct.pack_into('<II', query, 8, len(expected), pcbi.PCBI_SUCCESS)
                struct.pack_into('<I', query, offset, value)
            with self.subTest(offset=offset), patch.object(pcbi.fcntl, 'ioctl', side_effect=ioctl):
                with self.assertRaises(ValueError):
                    pcbi.query_ioctl(123, 7)


if __name__ == '__main__':
    unittest.main()
