"""The service may unload only after the kernel confirms checked shutdown."""
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from test_native_dsdt_recovery import fixture

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('late_install', ROOT / 'scripts/install-scp.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class NPUServiceTests(unittest.TestCase):
    def prepare(self, root):
        dmi = root / 'sys/class/dmi/id'
        dmi.mkdir(parents=True)
        for name, value in {'sys_vendor': 'ASUS', 'product_name': 'ProArt P16 H7607BA',
                            'bios_version': 'H7607BA.300'}.items():
            (dmi / name).write_text(value + '\n')
        (root / 'sys/module/n1x_npu_bootstrap').mkdir(parents=True)
        device = root / 'sys/bus/platform/devices/NVDA200A:00'
        device.mkdir(parents=True)
        (device / 'shutdown').touch()
        debug = root / 'sys/kernel/debug/n1x-npu-bootstrap'
        debug.mkdir(parents=True)
        status = debug / 'shutdown_status'
        status.write_text('error=0 hardware_off=1 released=1 hfrp_held=0 upload_held=0\n')
        binary = root / 'bin'
        binary.mkdir()
        modprobe = binary / 'modprobe'
        modprobe.write_text('#!/bin/sh\nprintf "unload: %s\\n" "$*"\n')
        modprobe.chmod(0o755)
        script = (ROOT / 'platform/dsp/provider/n1x-platform-npu-start').read_text()
        script = script.replace('/sys/', str(root / 'sys') + '/')
        script = script.replace('export PATH=/usr/bin:/bin', f'export PATH={binary}:/usr/bin:/bin')
        return script, device / 'shutdown', status

    def run_stop(self, script):
        return subprocess.run(['bash', '-c', script, 'npu-start', '--stop'],
                              capture_output=True, text=True)

    def test_checked_stop_precedes_unload(self):
        with tempfile.TemporaryDirectory() as directory:
            script, control, status = self.prepare(Path(directory))
            result = self.run_stop(script)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(control.read_text(), 'stop\n')
            self.assertEqual(result.stdout, 'unload: -r n1x_npu_bootstrap\n')

    def test_unconfirmed_or_failed_shutdown_never_unloads(self):
        for value in ('error=-5 hardware_off=1 released=1 hfrp_held=0 upload_held=0',
                      'error=0 hardware_off=0 released=0 hfrp_held=1 upload_held=1', ''):
            with self.subTest(value=value), tempfile.TemporaryDirectory() as directory:
                script, control, status = self.prepare(Path(directory))
                status.write_text(value)
                result = self.run_stop(script)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn('unload:', result.stdout)

    def test_missing_shutdown_interface_never_unloads(self):
        with tempfile.TemporaryDirectory() as directory:
            script, control, status = self.prepare(Path(directory))
            control.unlink()
            result = self.run_stop(script)
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn('unload:', result.stdout)
            self.assertFalse(control.exists())


class ProviderImageAuditTests(unittest.TestCase):
    def test_audits_renamed_primary_recovery_and_managed_images(self):
        with tempfile.TemporaryDirectory() as directory:
            boot = Path(directory)
            images = boot / 'EFI/Linux'
            images.mkdir(parents=True)
            data, _, _ = fixture()
            for name in ('omarchy_p16.efi', 'omarchy_p16-recovery.efi', 'omarchy_linux-p16-platform.efi'):
                (images / name).write_bytes(data)
            with patch.object(installer.subprocess, 'check_output', return_value='usr/lib/modules/test/driver.ko\n') as listing:
                checked = installer.npu_initramfs_audit(boot)
                self.assertEqual(set(checked), {str(p) for p in images.iterdir()})
                self.assertEqual(listing.call_count, 3)

    def test_rejects_embedded_npu_in_any_image(self):
        with tempfile.TemporaryDirectory() as directory:
            boot = Path(directory)
            images = boot / 'EFI/Linux'
            images.mkdir(parents=True)
            data, _, _ = fixture()
            (images / 'omarchy_p16-recovery.efi').write_bytes(data)
            with patch.object(installer.subprocess, 'check_output',
                              return_value='usr/lib/modules/test/n1x_npu_bootstrap.ko.zst\n'):
                with self.assertRaisesRegex(RuntimeError, 'embedded'):
                    installer.npu_initramfs_audit(boot)

    def test_rejects_missing_images(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(RuntimeError, 'No Linux UKIs'):
                installer.npu_initramfs_audit(Path(directory))


if __name__ == '__main__':
    unittest.main()
