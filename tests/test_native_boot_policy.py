import hashlib
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch


SOURCE = Path(__file__).resolve().parents[1] / 'scripts/p16-boot-menu-policy'
SPEC = importlib.util.spec_from_file_location('boot_verify', SOURCE.with_name('verify-boot.py'))
BOOT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BOOT)


class NativeBootPolicyTests(unittest.TestCase):
    def prepare(self, root):
        boot = root / 'boot'
        images = boot / 'EFI/Linux'
        images.mkdir(parents=True)
        old = images / 'omarchy_linux-rtx-spark-npu.efi'
        native = images / 'omarchy_p16-native-dsdt-test.efi'
        old.write_bytes(b'preserved recovery image')
        native.write_bytes(b'validated native DSDT image')
        menu = (f'default_entry: p16-native-dsdt-test\ntimeout: 5\nquiet: no\n/Omarchy\n'
                f'  //linux-rtx-spark-npu\n    protocol: efi\n'
                f'    path: boot():/EFI/Linux/{old.name}#{hashlib.blake2b(old.read_bytes()).hexdigest()}\n'
                f'/p16-native-dsdt-test\n    protocol: efi\n'
                f'    path: boot():/EFI/Linux/{native.name}#{hashlib.blake2b(native.read_bytes()).hexdigest()}\n')
        config = boot / 'limine.conf'
        config.write_text(menu)
        # Run the real policy against an isolated filesystem fixture, without root.
        script = SOURCE.read_text().replace('[[ $EUID == 0 ]] || exit 1', 'true')
        script = script.replace('/run/lock/boot-partition.lock', str(root / 'lock'))
        script = script.replace('/boot', str(boot))
        return config, native, old, script

    def run_policy(self, script):
        return subprocess.run(['bash', '-c', script], text=True, capture_output=True)

    def prepare_clean(self, root):
        config, native, old, script = self.prepare(root)
        primary = native.with_name('omarchy_p16.efi')
        recovery = native.with_name('omarchy_p16-recovery.efi')
        for image in (primary, recovery):
            image.write_bytes(native.read_bytes())
        text = config.read_text().replace('default_entry: p16-native-dsdt-test', 'default_entry: Omarchy-P16')
        text = text.replace('/Omarchy\n', '/+Omarchy\n')
        text += '\n/EFI fallback\n path: boot():/EFI/BOOT/BOOTAA64.EFI\n/Windows\n path: boot():/EFI/Microsoft/Boot/bootmgfw.efi\n'
        for title, image in (('Omarchy-P16', primary), ('Omarchy-P16-Recovery', recovery)):
            text += f'\n/{title}\n protocol: efi\n path: boot():/EFI/Linux/{image.name}#{hashlib.blake2b(image.read_bytes()).hexdigest()}\n'
        config.write_text(text)
        return config, primary, recovery, script

    def test_native_default_survives_menu_policy(self):
        with tempfile.TemporaryDirectory() as directory:
            config, native, old, script = self.prepare(Path(directory))
            original = config.read_bytes()
            result = self.run_policy(script)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(config.read_bytes(), original)
            self.assertEqual(old.read_bytes(), b'preserved recovery image')

    def test_missing_native_entry_is_rejected_without_changing_menu(self):
        with tempfile.TemporaryDirectory() as directory:
            config, native, old, script = self.prepare(Path(directory))
            config.write_text(config.read_text().replace('/p16-native-dsdt-test\n', '/other-entry\n'))
            original = config.read_bytes()
            result = self.run_policy(script)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(config.read_bytes(), original)

    def test_unrelated_bad_image_does_not_displace_verified_native_default(self):
        with tempfile.TemporaryDirectory() as directory:
            config, native, old, script = self.prepare(Path(directory))
            old.write_bytes(b'corrupt unrelated recovery')
            result = self.run_policy(script)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('default_entry: p16-native-dsdt-test\n', config.read_text())

    def test_corrupt_native_image_falls_back_to_verified_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            config, native, old, script = self.prepare(Path(directory))
            native.write_bytes(b'corrupt native image')
            result = self.run_policy(script)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('default_entry: Omarchy/linux-rtx-spark-npu\n', config.read_text())

    def test_boot_verifier_validates_the_promoted_image(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config, native, old, script = self.prepare(root)
            baseline = native.with_name('omarchy_linux-p16-asus-23.efi')
            baseline.write_bytes(b'original baseline recovery')
            with config.open('a') as out:
                out.write(f'\n/baseline\n path: boot():/EFI/Linux/{baseline.name}#{hashlib.blake2b(baseline.read_bytes()).hexdigest()}\n')
            with patch.object(BOOT, 'verify_image') as verify:
                BOOT.verify_menu(config, root / 'boot', installed_npu=True)
                verify.assert_called_once_with(native)

    def test_clean_menu_prunes_regenerated_entries_and_preserves_other_os(self):
        with tempfile.TemporaryDirectory() as directory:
            config, primary, recovery, script = self.prepare_clean(Path(directory))
            result = self.run_policy(script)
            self.assertEqual(result.returncode, 0, result.stderr)
            text = config.read_text()
            self.assertNotIn('/+Omarchy\n', text)
            self.assertNotIn('/p16-native-dsdt-test\n', text)
            self.assertNotIn('/EFI fallback\n', text)
            self.assertIn('/Windows\n path: boot():/EFI/Microsoft/Boot/bootmgfw.efi\n', text)
            self.assertIn('/Omarchy-P16\n', text)
            self.assertIn('/Omarchy-P16-Recovery\n', text)

    def test_clean_menu_falls_back_to_qualified_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            config, primary, recovery, script = self.prepare_clean(Path(directory))
            primary.write_bytes(b'corrupt primary')
            result = self.run_policy(script)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('default_entry: Omarchy-P16-Recovery\n', config.read_text())

    def test_clean_menu_verifier_no_longer_requires_obsolete_kernels(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config, primary, recovery, script = self.prepare_clean(root)
            self.assertEqual(self.run_policy(script).returncode, 0)
            with patch.object(BOOT, 'verify_image') as verify:
                BOOT.verify_menu(config, root / 'boot', installed_npu=True)
                self.assertEqual(verify.call_args_list, [((primary,),), ((recovery,),)])


if __name__ == '__main__':
    unittest.main()
