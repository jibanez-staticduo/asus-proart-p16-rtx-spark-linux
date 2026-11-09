"""Validate the upstream ALS backport and its per-channel unit handling."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
ALS = ROOT / 'platform/als'


class ALSScaleTests(unittest.TestCase):
    def test_install_rejects_embedded_als_with_either_module_spelling(self):
        spec = importlib.util.spec_from_file_location('als_installer', ROOT / 'scripts/boot_audit.py')
        installer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(installer)
        from fixtures import fixture
        with tempfile.TemporaryDirectory() as directory:
            boot = Path(directory)
            images = boot / 'EFI/Linux'
            images.mkdir(parents=True)
            data, _, _ = fixture()
            (images / 'omarchy_p16-recovery.efi').write_bytes(data)
            for module in ('hid_sensor_als', 'hid-sensor-als'):
                for suffix in ('', '.zst', '.xz', '.gz'):
                    listing = f'usr/lib/modules/test/{module}.ko{suffix}\n'
                    with patch.object(installer.subprocess, 'check_output', return_value=listing):
                        with self.subTest(module=module, suffix=suffix), self.assertRaisesRegex(RuntimeError, 'ALS is embedded'):
                            installer.late_module_initramfs_audit(('hid_sensor_als', 'hid-sensor-als'), 'ALS', boot)

    def test_actual_parse_and_read_callbacks(self):
        subprocess.run(['python3', str(ALS / 'test_scale.py')], check=True, timeout=60)

    def test_source_differs_only_by_standalone_header_location(self):
        record = json.loads((ALS / 'provenance.json').read_text())
        for spec in record['files']:
            name = Path(spec['path']).name
            data = (ALS / name).read_bytes()
            self.assertEqual(hashlib.sha256(data).hexdigest(), spec['packaged_sha256'])
            if name == 'hid-sensor-als.c':
                self.assertEqual(data.count(b'#include "hid-sensor-trigger.h"'), 1)
                data = data.replace(b'#include "hid-sensor-trigger.h"',
                                    b'#include "../common/hid-sensors/hid-sensor-trigger.h"', 1)
            self.assertEqual(hashlib.sha256(data).hexdigest(), spec['upstream_sha256'])
            self.assertEqual(hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest(),
                             spec['git_blob'])


if __name__ == '__main__':
    unittest.main()
