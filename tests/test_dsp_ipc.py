"""Build the DSP packet codec's wire-vector and bounds tests with sanitizers."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class DSPIPCTests(unittest.TestCase):
    def test_wire_vectors_and_truncated_packets(self):
        with tempfile.TemporaryDirectory(prefix='p16-dsp-ipc-') as directory:
            binary = Path(directory) / 'ipc-test'
            subprocess.run(['cc', '-std=gnu11', '-Wall', '-Wextra', '-Werror',
                            '-fsanitize=address,undefined', '-g',
                            str(ROOT / 'platform/dsp/ipc_test.c'), '-o', str(binary)], check=True)
            subprocess.run([str(binary)], check=True)


if __name__ == '__main__':
    unittest.main()
