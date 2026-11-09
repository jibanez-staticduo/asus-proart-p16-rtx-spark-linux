"""Check package source contracts and execute battery policy on a temporary root."""
import hashlib
from pathlib import Path
import subprocess
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]


class PackagePolicyTests(unittest.TestCase):
    def test_policy_manifest_and_recipe_verify_the_same_source_bytes(self):
        package = REPO / 'packages/p16-platform-policy'
        files = [REPO / line for line in (package / 'sources').read_text().splitlines() if line]
        self.assertEqual(len({file.name for file in files}), len(files))
        records = subprocess.check_output(['bash', '-c',
            'source PKGBUILD; for ((i=0;i<${#source[@]};i++)); do '
            'printf "%s\\t%s\\n" "${source[i]}" "${sha256sums[i]}"; done'],
            cwd=package, text=True)
        expected = {name: digest for name, digest in (line.split('\t') for line in records.splitlines())}
        self.assertEqual({file.name for file in files}, set(expected))
        for file in files:
            self.assertEqual(hashlib.sha256(file.read_bytes()).hexdigest(), expected[file.name], str(file))

    def test_tmpfiles_updates_present_batteries_without_creating_missing_attributes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            attributes = []
            for name in ('BAT0', 'BAT2'):
                attribute = root / 'sys/class/power_supply' / name / 'charge_control_end_threshold'
                attribute.parent.mkdir(parents=True)
                attribute.write_text('80\n')
                attributes.append(attribute)
            missing = root / 'sys/class/power_supply/BAT3/charge_control_end_threshold'
            missing.parent.mkdir(parents=True)
            config = root / 'usr/lib/tmpfiles.d/p16-charge-limit.conf'
            config.parent.mkdir(parents=True)
            config.write_bytes((REPO / 'config/p16-charge-limit.tmpfiles').read_bytes())
            subprocess.run(['systemd-tmpfiles', '--root=' + directory, '--create',
                            str(config)], check=True, capture_output=True)
            for attribute in attributes:
                self.assertEqual(attribute.read_text(), '100')
            self.assertFalse(missing.exists())

    def test_tmpfiles_with_no_power_supply_is_a_noop(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / 'usr/lib/tmpfiles.d/p16-charge-limit.conf'
            config.parent.mkdir(parents=True)
            config.write_bytes((REPO / 'config/p16-charge-limit.tmpfiles').read_bytes())
            subprocess.run(['systemd-tmpfiles', '--root=' + directory, '--create',
                            str(config)], check=True, capture_output=True)
            self.assertFalse((root / 'sys').exists())
