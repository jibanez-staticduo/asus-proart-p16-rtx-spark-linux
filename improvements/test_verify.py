#!/usr/bin/env python3
"""Check the public export integrity gate rejects tampering and stale pins."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


class IntegrityTest(unittest.TestCase):
    def test_manifest_and_inventory_fail_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            shutil.copyfile(Path(__file__).with_name('verify.py'), root/'verify.py')
            (root/'kernel').mkdir()
            (root/'kernel/one.patch').write_text('fixture\n')
            (root/'kernel/series').write_text('one.patch\n')
            digest = hashlib.sha256(b'fixture\n').hexdigest()
            manifest = {'kernel': {'patch_count': 1, 'followups': [],
                                  'local_followups': [{'file': 'kernel/one.patch', 'sha256': digest}]}}
            def seal():
                (root/'sources.json').write_text(json.dumps(manifest))
                files = sorted(p for p in root.rglob('*') if p.is_file() and p.name != 'SHA256SUMS')
                (root/'SHA256SUMS').write_text(''.join(hashlib.sha256(p.read_bytes()).hexdigest()+'  '+str(p.relative_to(root))+'\n' for p in files))
            def run():
                return subprocess.run([sys.executable, str(root/'verify.py')], capture_output=True)
            seal()
            self.assertEqual(run().returncode, 0)
            (root/'kernel/one.patch').write_text('tampered')
            self.assertNotEqual(run().returncode, 0)
            seal()  # Inventory hashes cannot override a stale independent source pin.
            self.assertNotEqual(run().returncode, 0)
            (root/'kernel/one.patch').write_text('fixture\n')
            manifest['kernel']['patch_count'] = 2
            seal()
            self.assertNotEqual(run().returncode, 0)
            manifest['kernel']['patch_count'] = 1
            (root/'payload.bin').write_bytes(b'no binaries')
            seal()
            self.assertNotEqual(run().returncode, 0)


if __name__ == '__main__':
    unittest.main()
