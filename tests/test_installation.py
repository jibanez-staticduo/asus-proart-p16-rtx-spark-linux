"""Regression tests using temporary roots; no live installation is performed."""
import hashlib
import importlib.util
from pathlib import Path
import subprocess
import unittest

REPO = Path(__file__).resolve().parents[1]


def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, REPO / 'scripts' / filename)
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


boot = module('verify_boot', 'verify-boot.py')


class KernelInputsTests(unittest.TestCase):
    def test_kernel_inputs_are_complete_verified_and_have_no_orphan_patches(self):
        script = r'''source PKGBUILD
for ((i=0; i<${#source[@]}; i++)); do
    printf '%s\t%s\t%s\n' "${source[i]}" "${sha256sums[i]}" "${b2sums[i]}"
done
for ((i=0; i<${#source_aarch64[@]}; i++)); do
    printf '%s\t%s\t%s\n' "${source_aarch64[i]}" "${sha256sums_aarch64[i]}" "${b2sums_aarch64[i]}"
done
'''
        kernel = REPO / 'kernel'
        records = subprocess.check_output(['bash', '-c', script], cwd=kernel, text=True)
        local = set()
        for line in records.splitlines():
            name, sha256, blake2 = line.split('\t')
            if '://' in name:
                continue
            local.add(name)
            data = (kernel / name).read_bytes()
            if sha256 != 'SKIP':
                self.assertEqual(hashlib.sha256(data).hexdigest(), sha256, name)
            if blake2 != 'SKIP':
                self.assertEqual(hashlib.blake2b(data).hexdigest(), blake2, name)
        bundled = {p.name for p in kernel.iterdir() if p.suffix in ('.patch', '.sig')}
        self.assertEqual(bundled - local, set(), 'Unreferenced kernel patch/signature')


if __name__ == '__main__':
    unittest.main()
