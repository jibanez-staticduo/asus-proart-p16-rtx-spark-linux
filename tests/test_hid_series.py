"""Reapply the focused HID fixes and compare them to the packaged sources."""
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('hid_series', ROOT / 'scripts/prepare-hid-series.py')
hid_series = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(hid_series)


class HIDSeriesTests(unittest.TestCase):
    def test_reply_race_reproduces_on_prerequisite_and_is_fixed(self):
        with tempfile.TemporaryDirectory() as temporary:
            tree = Path(temporary)
            hid_series.reconstruct(tree)
            subprocess.run(['python3', str(ROOT / 'platform/sensorhub/test_request_race.py'),
                            '--baseline-source', str(tree / hid_series.PREFIX / 'spi-hid-core.c')],
                           check=True, timeout=60)

    def test_concurrent_report_requests(self):
        subprocess.run(['python3', str(ROOT / 'platform/sensorhub/test_requests.py')],
                       check=True, timeout=60)

    def test_series_reapplies_and_reproduces_packaged_sources(self):
        with tempfile.TemporaryDirectory() as temporary:
            output, tree = Path(temporary) / 'output', Path(temporary) / 'tree'
            tree.mkdir()
            result = hid_series.prepare(output)
            recorded = json.loads((ROOT / 'upstream/spi-hid/baseline.json').read_text())
            self.assertEqual(result, recorded)
            hid_series.reconstruct(tree)
            for name in result['patches']:
                self.assertEqual((output / name).read_bytes(),
                                 (ROOT / 'upstream/spi-hid' / name).read_bytes())
                subprocess.run(['git', 'apply', '--check', str(output / name)],
                               cwd=tree, check=True, capture_output=True)
                subprocess.run(['git', 'apply', str(output / name)],
                               cwd=tree, check=True, capture_output=True)
            for name in hid_series.FILES:
                self.assertEqual((tree / hid_series.PREFIX / name).read_bytes(),
                                 (ROOT / 'platform/sensorhub' / name).read_bytes())

    def test_existing_output_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            existing = output / 'series'
            existing.write_text('existing review work\n')
            with self.assertRaisesRegex(RuntimeError, 'empty output'):
                hid_series.prepare(output)
            self.assertEqual(existing.read_text(), 'existing review work\n')


if __name__ == '__main__':
    unittest.main()
