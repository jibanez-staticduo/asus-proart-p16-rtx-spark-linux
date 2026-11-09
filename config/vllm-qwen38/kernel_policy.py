# SPDX-License-Identifier: MIT
"""Resolve requested kernel settings and versioned local qualification results."""
import hashlib
from importlib import metadata
import json
import math
import os
from pathlib import Path

BASE = Path(__file__).resolve().parent


def requested_settings():
    override = os.environ.get("VLLM_KERNEL_CONFIG")
    if override:
        return json.loads(override)
    return {
        "linear_backend": "nvfp4_dynamic",
        "nvfp4_dynamic_max_tokens": int(os.environ.get("VLLM_NVFP4_LINEAR_CUTOFF", "0")),
        "nvfp4_dynamic_silu_max_tokens": int(os.environ.get("VLLM_NVFP4_SILU_CUTOFF", "16")),
        "unquantized_linear_backend": "flashinfer_cudnn",
        "linear_backend_per_quant": {"fp8_w8a8": "cutlass"},
        "enable_flashinfer_autotune": True,
    }


def software_fingerprint():
    factors = {name: metadata.version(name) for name in ("vllm", "torch", "flashinfer-python")}
    for path in (BASE / "build-profile.json", BASE / "qualify-kernels.py",
                 BASE / "kernel_policy.py", Path(os.environ["VLLM_TARGET_MODEL"]) / "config.json",
                 Path("/proc/driver/nvidia/version"), Path("/sys/firmware/acpi/platform_profile")):
        if path.is_file():
            factors[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
    return hashlib.sha256(json.dumps(factors, sort_keys=True).encode()).hexdigest()


def report_path():
    return Path(os.environ["VLLM_CACHE_DIR"]) / "kernel-qualification.json"


def autotune_enabled():
    value = os.environ.get("VLLM_NVFP4_AUTOTUNE", "1")
    if value not in ("0", "1"):
        raise ValueError("VLLM_NVFP4_AUTOTUNE must be 0 or 1")
    return value == "1"


def validate_settings(kernel):
    if kernel.get("linear_backend") != "nvfp4_dynamic":
        raise ValueError("Qualification requires the pinned dynamic NVFP4 profile")
    if (kernel.get("unquantized_linear_backend") != "flashinfer_cudnn"
            or kernel.get("linear_backend_per_quant", {}).get("fp8_w8a8") != "cutlass"
            or kernel.get("linear_backend_per_quant", {}).get("nvfp4_w4a4", "nvfp4_dynamic") != "nvfp4_dynamic"):
        raise ValueError("Qualification requires cuDNN BF16, CUTLASS FP8 and dynamic NVFP4 dispatch")


def resolved_settings():
    requested = requested_settings()
    try:
        report = json.loads(report_path().read_text())
    except FileNotFoundError:
        return requested
    if (report.get("software_fingerprint") == software_fingerprint()
            and report.get("requested_kernel") == requested
            and report.get("autotune") == autotune_enabled()
            and report.get("passed") is True):
        return report["kernel_config"]
    return requested


def choose_cutoff(timings, margin=0.03):
    """Minimize total measured latency; require a >=3% win over fixed W4A4."""
    baseline = sum(row["a4_ms"] for row in timings)
    if not timings or any(not math.isfinite(row[key]) or row[key] <= 0
                          for row in timings for key in ("a4_ms", "a16_ms")):
        raise ValueError("Expected positive kernel timing samples")
    options = [(baseline, 0)]
    for cutoff in sorted({row["m"] for row in timings}):
        score = sum(row["a16_ms"] if row["m"] <= cutoff else row["a4_ms"] for row in timings)
        options.append((score, cutoff))
    best, cutoff = min(options)
    return cutoff if best <= baseline * (1 - margin) else 0


if __name__ == "__main__":
    print(json.dumps(resolved_settings(), separators=(",", ":")))
