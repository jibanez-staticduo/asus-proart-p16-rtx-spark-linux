"""Check launch configuration and installation without executing vLLM."""
import importlib.util
import json
import hashlib
import base64
import csv
import io
import os
from pathlib import Path
import subprocess
import struct
import sys
import tempfile
import unittest
import zipfile
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / "config/vllm-qwen38"
sys.path.insert(0, str(SOURCE))
spec = importlib.util.spec_from_file_location("vllm_setup_check", SOURCE / "check.py")
CHECK = importlib.util.module_from_spec(spec)
spec.loader.exec_module(CHECK)
from checkpoint import tensor_inventory, verify_manifest, write_provenance
import kernel_policy


def small_checkpoint(path, config=None):
    path.mkdir(exist_ok=True)
    (path / "config.json").write_text(json.dumps(config or {}))
    header = json.dumps({"weight": {"dtype": "U8", "shape": [4], "data_offsets": [0, 4]}}).encode()
    (path / "model.safetensors").write_bytes(struct.pack("<Q", len(header)) + header + b"abcd")


class VllmConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.venv = self.root / "environment with spaces"
        (self.venv / "bin").mkdir(parents=True)
        (self.venv / "bin/python").symlink_to(sys.executable)
        # A real launch must fail these tests, rather than start an installed server.
        sentinel = self.venv / "bin/vllm"
        sentinel.write_text("#!/bin/sh\nexit 99\n")
        sentinel.chmod(0o755)
        self.env = {**os.environ, "VLLM_VENV": str(self.venv),
                    "VLLM_TARGET_MODEL": str(self.root / "target model"),
                    "VLLM_DRAFT_MODEL": str(self.root / "draft model"),
                    "VLLM_CACHE_DIR": str(self.root / "cache"),
                    "VLLM_USE_V2_MODEL_RUNNER": "0"}
        for key in ("VLLM_SPEC_METHOD", "VLLM_KERNEL_CONFIG", "VLLM_COMPILATION_CONFIG",
                    "VLLM_MAX_MODEL_LEN", "VLLM_MAX_NUM_SEQS", "VLLM_MAX_BATCHED_TOKENS",
                    "VLLM_KV_CACHE_BYTES", "VLLM_GPU_MEMORY_UTILIZATION",
                    "VLLM_DRAFT_QUANTIZATION", "VLLM_NVFP4_LINEAR_CUTOFF",
                    "VLLM_NVFP4_SILU_CUTOFF", "VLLM_NVFP4_AUTOTUNE"):
            self.env.pop(key, None)

    def render(self, **changes):
        output = subprocess.check_output(["bash", str(SOURCE / "serve.sh"), "--print-config"],
                                         env={**self.env, **changes}, text=True)
        return json.loads(output)

    def test_dflash2_overrides_inherited_v1_and_keeps_model_paths_intact(self):
        command = self.render()
        values, _, graphs = CHECK.check_command(command)
        self.assertEqual(command["runner"], "1")
        self.assertEqual(command["argv"][2], self.env["VLLM_TARGET_MODEL"])
        self.assertEqual(values["--speculative-config.model"], self.env["VLLM_DRAFT_MODEL"])
        self.assertEqual(values["--speculative-config.num_speculative_tokens"], "7")
        self.assertEqual(max(graphs["decode_cudagraph_capture_request_sizes"]), 8)
        self.assertNotIn("max_cudagraph_capture_size", graphs)
        self.assertNotIn("fuse_act_quant", graphs.get("pass_config", {}))
        self.assertEqual(json.loads(values["--kernel-config"])["linear_backend"], "nvfp4_dynamic")
        self.assertEqual(values["--speculative-config.quantization"], "fp8_per_tensor")
        self.assertEqual(json.loads(values["--kernel-config"])["nvfp4_dynamic_max_tokens"], 0)
        self.assertEqual(json.loads(values["--kernel-config"])["nvfp4_dynamic_silu_max_tokens"], 16)

    def test_v1_and_graphs_too_small_for_concurrency_are_rejected(self):
        command = self.render()
        command["runner"] = "0"
        with self.assertRaisesRegex(ValueError, "Runner V2"):
            CHECK.check_command(command)
        with self.assertRaisesRegex(ValueError, "sequence count"):
            CHECK.check_command(self.render(VLLM_MAX_NUM_SEQS="16"))

    def test_mtp_uses_the_native_head_without_external_draft_arguments(self):
        command = self.render(VLLM_SPEC_METHOD="mtp")
        values, _, _ = CHECK.check_command(command)
        self.assertEqual(values["--speculative-config.num_speculative_tokens"], "3")
        self.assertNotIn("--speculative-config.model", command["argv"])
        self.assertNotIn("--speculative-config.quantization", command["argv"])

    def test_draft_precision_and_separate_cutoffs_can_be_selected(self):
        command = self.render(VLLM_DRAFT_QUANTIZATION="bf16", VLLM_NVFP4_LINEAR_CUTOFF="4",
                              VLLM_NVFP4_SILU_CUTOFF="8")
        values, kernel, _ = CHECK.check_command(command)
        self.assertNotIn("--speculative-config.quantization", values)
        self.assertEqual(kernel["nvfp4_dynamic_max_tokens"], 4)
        self.assertEqual(kernel["nvfp4_dynamic_silu_max_tokens"], 8)
        with self.assertRaisesRegex(ValueError, "cutoff"):
            CHECK.check_command(self.render(VLLM_NVFP4_LINEAR_CUTOFF="17"))

    def test_qualification_failure_prevents_exec_of_vllm(self):
        python = self.venv / "bin/python"
        python.unlink()
        python.write_text('#!/bin/sh\ncase "$1" in */qualify-kernels.py) exit 67;; *) exit 68;; esac\n')
        python.chmod(0o755)
        result = subprocess.run(["bash", str(SOURCE / "serve.sh")], env=self.env, capture_output=True)
        self.assertEqual(result.returncode, 67)

    def test_unknown_mode_cannot_launch_a_server(self):
        result = subprocess.run(["bash", str(SOURCE / "serve.sh"), "--check-and-start"],
                                env=self.env, capture_output=True)
        self.assertEqual(result.returncode, 2)

    def test_incomplete_or_incompatible_checkpoint_is_rejected_without_loading_weights(self):
        target = self.root / "target"
        small_checkpoint(target, {"architectures": ["OtherModel"]})
        pins = json.loads((SOURCE / "models.json").read_text())
        with self.assertRaisesRegex(ValueError, "architecture"):
            CHECK.check_models(target, self.root / "missing draft", pins, "dflash")
        (target / "model.safetensors").unlink()
        with self.assertRaisesRegex(ValueError, "Incomplete checkpoint"):
            CHECK.load_checkpoint(target)

    def test_empty_truncated_and_modified_weights_are_rejected(self):
        target = self.root / "checkpoint"
        small_checkpoint(target)
        write_provenance(target, {"source": "test"})
        provenance = json.loads((target / "reshard-provenance.json").read_text())
        verify_manifest(target, provenance)
        weights = target / "model.safetensors"
        good = weights.read_bytes()
        for broken in (b"", good[:-1], good[:-1] + b"x"):
            weights.write_bytes(broken)
            with self.assertRaises(ValueError):
                verify_manifest(target, provenance)
        weights.write_bytes(good)
        verify_manifest(target, provenance)

    def test_checkpoint_index_cannot_escape_directory(self):
        target = self.root / "checkpoint"
        small_checkpoint(target)
        (target / "model.safetensors.index.json").write_text(json.dumps({"weight_map": {"weight": "../outside"}}))
        with self.assertRaisesRegex(ValueError, "escapes"):
            tensor_inventory(target)

    def test_ineffective_compiler_fusion_is_rejected(self):
        graphs = json.loads(self.render()["argv"][self.render()["argv"].index("--compilation-config") + 1])
        graphs["pass_config"] = {"fuse_act_quant": True}
        with self.assertRaisesRegex(ValueError, "manual activation fusion"):
            CHECK.check_command(self.render(VLLM_COMPILATION_CONFIG=json.dumps(graphs)))

    def installer_environment(self, state):
        home = self.root / "home"
        config = home / ".config"
        config.mkdir(parents=True)
        venv = self.root / "installer-venv"
        (venv / "bin").mkdir(parents=True)
        python = venv / "bin/python"
        python.write_text("#!/bin/sh\nexit 0\n")  # Isolate the separately tested preflight.
        python.chmod(0o755)
        env_file = config / "vllm-qwen38.env"
        env_file.write_text(f"VLLM_VENV={venv}\nVLLM_CACHE_DIR={self.root / 'cache'}\n")
        tools = self.root / "tools"
        tools.mkdir()
        calls = self.root / "systemctl.calls"
        control = tools / "systemctl"
        control.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$CALLS"\n'
                           'case "$*" in *--property=ActiveState*) printf "%s\\n" "$STATE";; esac\n')
        control.chmod(0o755)
        env = {**os.environ, "HOME": str(home), "XDG_CONFIG_HOME": str(config),
               "XDG_STATE_HOME": str(home / ".local/state"), "PATH": f"{tools}:/usr/bin:/bin",
               "CALLS": str(calls), "STATE": state}
        return home, calls, env

    def test_config_install_preserves_environment_and_never_starts_or_enables(self):
        home, calls, env = self.installer_environment("inactive")
        env_file = home / ".config/vllm-qwen38.env"
        before = env_file.read_bytes()
        subprocess.run(["bash", str(SOURCE / "install.sh"), "config"], env=env,
                       check=True, capture_output=True)
        self.assertEqual(env_file.read_bytes(), before)
        launcher = home / ".local/libexec/vllm-qwen38/serve.sh"
        self.assertEqual(launcher.read_bytes(), (SOURCE / "serve.sh").read_bytes())
        operations = calls.read_text().splitlines()
        self.assertIn("--user daemon-reload", operations)
        self.assertIn("--user disable vllm-qwen38.service", operations)
        self.assertFalse(any(word in op.split() for op in operations for word in ["start", "restart", "enable", "stop"]))

    def test_running_service_is_never_replaced(self):
        home, _, env = self.installer_environment("active")
        for mode in ("runtime", "models", "config"):
            result = subprocess.run(["bash", str(SOURCE / "install.sh"), mode], env=env,
                                    capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("service state is active", result.stderr)
        self.assertFalse((home / ".local/libexec").exists())

    def test_unknown_service_state_fails_closed(self):
        _, _, env = self.installer_environment("unknown")
        result = subprocess.run(["bash", str(SOURCE / "install.sh"), "runtime"], env=env,
                                capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("service state is unknown", result.stderr)


@unittest.skipUnless(os.environ.get("VLLM_TEST_PYTHON"), "Set VLLM_TEST_PYTHON for CPU-only safetensors tests")
class CheckpointPreparationTests(unittest.TestCase):
    def test_native_backports_use_expected_dispatch_without_gpu_execution(self):
        subprocess.run([os.environ["VLLM_TEST_PYTHON"], str(Path(__file__).with_name("vllm_kernels.py"))],
                       env={**os.environ, "CUDA_VISIBLE_DEVICES": ""}, check=True,
                       capture_output=True, text=True)

    def test_small_packed_and_mtp_tensors_survive_resharding_on_cpu(self):
        code = r'''
import importlib.util,json,sys,tempfile
from pathlib import Path
import torch
from safetensors.torch import save_file,load_file
p=Path(sys.argv[1]); sys.path.insert(0,str(p)); spec=importlib.util.spec_from_file_location("prepare",p/"prepare-checkpoint.py")
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
pins=json.loads((p/"models.json").read_text())
with tempfile.TemporaryDirectory() as directory:
 root=Path(directory); source=root/"source"; source.mkdir(); target=root/"target"
 cache=source/".cache/huggingface/download";cache.mkdir(parents=True)
 (cache/"config.json.metadata").write_text(pins["target"]["revision"]+"\n")
 tensors={"packed_weight":torch.arange(32,dtype=torch.uint8).reshape(4,8)}
 mtp={"mtp.weight":torch.arange(8,dtype=torch.bfloat16)}
 save_file(tensors,source/"model.safetensors");save_file(mtp,source/"model_mtp.safetensors")
 index={"metadata":{},"weight_map":{"packed_weight":"model.safetensors","mtp.weight":"model_mtp.safetensors"}}
 (source/"model.safetensors.index.json").write_text(json.dumps(index));(source/"config.json").write_text("{}")
 m.prepare(source,target,pins)
 mapping=json.loads((target/"model.safetensors.index.json").read_text())["weight_map"]
 for key,value in {**tensors,**mtp}.items():
  assert torch.equal(value.view(torch.uint8),load_file(target/mapping[key])[key].view(torch.uint8))
 m.prepare(source,target,pins)
 # Reuse checks hashes, including same-size mutations.
 file=target/mapping["packed_weight"]; good=file.read_bytes(); file.write_bytes(good[:-1]+bytes([good[-1]^1]))
 try: m.prepare(source,target,pins)
 except ValueError: pass
 else: raise AssertionError("Modified prepared weights were accepted")
 file.write_bytes(good)
 # Legacy output is accepted only after comparing all bytes with the source.
 provenance=target/"reshard-provenance.json"; old=json.loads(provenance.read_text()); old.pop("files"); provenance.write_text(json.dumps(old))
 m.prepare(source,target,pins)
 assert "files" in json.loads(provenance.read_text())
 assert not torch.cuda.is_initialized()
'''
        subprocess.run([os.environ["VLLM_TEST_PYTHON"], "-c", code, str(SOURCE)],
                       env={**os.environ, "CUDA_VISIBLE_DEVICES": ""}, check=True,
                       capture_output=True, text=True)


class NativeWheelBuildTests(unittest.TestCase):
    def test_build_preserves_binaries_updates_record_and_is_reproducible(self):
        spec = importlib.util.spec_from_file_location("build_vllm_wheel", SOURCE / "build-wheel.py")
        build = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(build)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            build.BASE = root
            artifacts = root / "artifacts"
            artifacts.mkdir()
            name = "vllm-1.0+base-cp38-abi3-manylinux_2_28_aarch64.whl"
            original = artifacts / name
            with zipfile.ZipFile(original, "w") as archive:
                archive.writestr("vllm/_version.py", "__version__ = '1.0+base'\n__version_tuple__ = version_tuple = (1, 0, 'base')\n__commit_id__ = 'base'\n")
                archive.writestr("vllm/example.py", "value = 1\n")
                archive.writestr("vllm/_C.so", b"unchanged-native-binary")
                archive.writestr("vllm-1.0+base.dist-info/METADATA", "Name: vllm\nVersion: 1.0+base\n")
                archive.writestr("vllm-1.0+base.dist-info/RECORD", "")
            patch = root / "runtime.patch"
            patch.write_text("--- a/vllm/example.py\n+++ b/vllm/example.py\n@@ -1 +1 @@\n-value = 1\n+value = 2\n")
            profile = {"base_version": "1.0+base", "version": "1.0+base.p16.1",
                       "base_wheel_url": "https://invalid.example/" + name,
                       "base_wheel_sha256": build.sha256(original),
                       "patches": [{"path": patch.name, "sha256": build.sha256(patch)}]}
            (root / "build-profile.json").write_text(json.dumps(profile))
            output = build.build(artifacts)
            first = build.sha256(output)
            self.assertEqual(first, build.sha256(build.build(artifacts)))
            with zipfile.ZipFile(output) as archive:
                self.assertEqual(archive.read("vllm/_C.so"), b"unchanged-native-binary")
                self.assertEqual(archive.read("vllm/example.py"), b"value = 2\n")
                self.assertIn(b"__commit_id__ = 'base'", archive.read("vllm/_version.py"))
                self.assertIn(b"'base.p16.1'", archive.read("vllm/_version.py"))
                record = next(n for n in archive.namelist() if n.endswith("/RECORD"))
                for name, digest, size in csv.reader(io.StringIO(archive.read(record).decode())):
                    if not digest:
                        continue
                    data = archive.read(name)
                    actual = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode().rstrip("=")
                    self.assertEqual(digest, "sha256=" + actual)
                    self.assertEqual(len(data), int(size))
            patch.write_text(patch.read_text() + "\n")
            with self.assertRaisesRegex(ValueError, "Patch SHA-256"):
                build.build(artifacts)


class KernelPolicyTests(unittest.TestCase):
    def test_cutoff_uses_measured_improvement_and_rejects_noise(self):
        rows = [{"m": 1, "a4_ms": 1, "a16_ms": .8},
                {"m": 2, "a4_ms": 1, "a16_ms": .8},
                {"m": 4, "a4_ms": 1, "a16_ms": 1.5}]
        self.assertEqual(kernel_policy.choose_cutoff(rows), 2)
        self.assertEqual(kernel_policy.choose_cutoff([{"m": 1, "a4_ms": 1, "a16_ms": .99}]), 0)
        with self.assertRaises(ValueError):
            kernel_policy.choose_cutoff([])

    def test_saved_qualification_requires_matching_software_settings_and_mode(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
                "VLLM_CACHE_DIR": directory, "VLLM_NVFP4_AUTOTUNE": "1"}, clear=False):
            requested = {"linear_backend": "nvfp4_dynamic", "nvfp4_dynamic_max_tokens": 0}
            selected = {**requested, "nvfp4_dynamic_silu_max_tokens": 8}
            report = {"passed": True, "requested_kernel": requested, "kernel_config": selected,
                      "software_fingerprint": "current", "autotune": True}
            kernel_policy.report_path().write_text(json.dumps(report))
            with patch.object(kernel_policy, "requested_settings", return_value=requested), \
                    patch.object(kernel_policy, "software_fingerprint", return_value="current"):
                self.assertEqual(kernel_policy.resolved_settings(), selected)
                with patch.dict(os.environ, {"VLLM_NVFP4_AUTOTUNE": "0"}):
                    self.assertEqual(kernel_policy.resolved_settings(), requested)
            with patch.object(kernel_policy, "requested_settings", return_value=requested), \
                    patch.object(kernel_policy, "software_fingerprint", return_value="changed"):
                self.assertEqual(kernel_policy.resolved_settings(), requested)


if __name__ == "__main__":
    unittest.main()
