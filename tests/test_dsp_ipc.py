"""Build the DSP packet codec's wire-vector and bounds tests with sanitizers."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class DSPIPCTests(unittest.TestCase):
    def test_stream_start_stop_and_terminal_failures(self):
        subprocess.run(['python3', str(ROOT / 'platform/dsp/test_trigger.py')],
                       check=True, timeout=40)

    def test_producer_publication_and_irq_serialization(self):
        subprocess.run(['python3', str(ROOT / 'platform/dsp/test_producer.py')],
                       check=True, timeout=40)

    def test_period_delivery_and_listener_lifetime(self):
        subprocess.run(['python3', str(ROOT / 'platform/dsp/test_period.py')],
                       check=True, timeout=40)

    def test_pcm_service_ownership_and_terminal_failures(self):
        subprocess.run(['python3', str(ROOT / 'platform/dsp/test_pcm_service.py')],
                       check=True, timeout=40)

    def test_pcm_descriptor_vectors_and_overflow(self):
        with tempfile.TemporaryDirectory(prefix='p16-dsp-pcm-') as directory:
            binary = Path(directory) / 'pcm-test'
            subprocess.run(['cc', '-std=gnu11', '-Wall', '-Wextra', '-Werror',
                            '-fsanitize=address,undefined', '-g',
                            str(ROOT / 'platform/dsp/pcm_test.c'), '-o', str(binary)], check=True)
            subprocess.run([str(binary)], check=True)

    def test_exclusive_primary_lifecycle_and_terminal_failures(self):
        subprocess.run(['python3', str(ROOT / 'platform/dsp/test_lifecycle.py')],
                       check=True, timeout=40)

    def test_task_client_stops_after_an_uncertain_request(self):
        subprocess.run(['python3', str(ROOT / 'platform/dsp/test_task_client.py')],
                       check=True, timeout=40)

    def test_primary_memory_registration_and_firmware_route(self):
        subprocess.run(['python3', str(ROOT / 'platform/dsp/test_primary.py')],
                       check=True, timeout=40)

    def test_request_transport_and_concurrent_clients(self):
        subprocess.run(['python3', str(ROOT / 'platform/dsp/test_transport.py')],
                       check=True, timeout=40)

    def test_wire_vectors_and_truncated_packets(self):
        with tempfile.TemporaryDirectory(prefix='p16-dsp-ipc-') as directory:
            binary = Path(directory) / 'ipc-test'
            subprocess.run(['cc', '-std=gnu11', '-Wall', '-Wextra', '-Werror',
                            '-fsanitize=address,undefined', '-g',
                            str(ROOT / 'platform/dsp/ipc_test.c'), '-o', str(binary)], check=True)
            subprocess.run([str(binary)], check=True)


if __name__ == '__main__':
    unittest.main()
