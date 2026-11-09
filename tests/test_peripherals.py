"""Verify firmware preparation without installing files or executing the OEM archive."""
import hashlib
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from test_installation import module
peripherals = module('peripherals', 'peripherals.py')


class FirmwarePreparationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.rows = [{'name': name, 'member': 'payload/' + name, 'bytes': len(name),
                      'sha256': hashlib.sha256(name.encode()).hexdigest()}
                     for name in peripherals.FIRMWARE_NAMES]
        archive = b'verified archive'
        (self.directory / 'asus-audio.exe').write_bytes(archive)
        manifest = {'files': self.rows, 'archive': {'bytes': len(archive), 'offset': 3,
                    'sha256': hashlib.sha256(archive).hexdigest(), 'url': 'https://invalid.example'}}
        mock = patch.object(peripherals, 'manifest', return_value=manifest)
        mock.start()
        self.addCleanup(mock.stop)

    def extracted(self, args, **kwargs):
        self.assertEqual(args[:2], ['bsdtar', '-xOf'])
        name = args[-1].split('/')[-1]
        return subprocess.CompletedProcess(args, 0, stdout=name.encode(), stderr=b'')

    def test_explicit_members_are_verified_before_outputs_are_written(self):
        with patch.object(peripherals.subprocess, 'run', side_effect=self.extracted) as run:
            peripherals.prepare(self.directory)
        self.assertEqual(run.call_count, 3)
        for row in self.rows:
            self.assertEqual((self.directory / row['name']).read_bytes(), row['name'].encode())

    def test_corrupt_archive_does_not_extract(self):
        (self.directory / 'asus-audio.exe').write_bytes(b'corrupt')
        with patch.object(peripherals.subprocess, 'run') as run:
            with self.assertRaisesRegex(ValueError, 'SHA-256'):
                peripherals.prepare(self.directory)
            run.assert_not_called()

    def test_corrupt_last_member_does_not_publish_partial_set(self):
        def extract(args, **kwargs):
            result = self.extracted(args, **kwargs)
            if args[-1].endswith(self.rows[-1]['name']):
                result.stdout = b'corrupt'
            return result
        with patch.object(peripherals.subprocess, 'run', side_effect=extract):
            with self.assertRaisesRegex(ValueError, 'SHA-256'):
                peripherals.prepare(self.directory)
        for row in self.rows:
            self.assertFalse((self.directory / row['name']).exists())

    def test_atomic_write_cleans_temporary_file_on_failure(self):
        destination = self.directory / 'output'
        destination.write_bytes(b'original')
        with patch.object(peripherals.os, 'fsync', side_effect=OSError('disk failure')):
            with self.assertRaises(OSError):
                peripherals.atomic_write(destination, b'replacement')
        self.assertEqual(destination.read_bytes(), b'original')
        self.assertEqual(list(self.directory.glob('.p16-*')), [])
