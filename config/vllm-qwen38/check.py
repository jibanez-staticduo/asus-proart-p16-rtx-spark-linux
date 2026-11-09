#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Check installed packages, model metadata and configuration without an engine."""
import argparse
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import re
import subprocess
from checkpoint import tensor_inventory, verify_manifest
from kernel_policy import report_path, validate_settings, autotune_enabled

BASE = Path(__file__).resolve().parent


def load_checkpoint(path):
    config = json.loads((path / "config.json").read_text())
    tensor_inventory(path)
    return config


def check_models(target_path, draft_path, pins, method):
    target = load_checkpoint(target_path)
    if pins["target"]["architecture"] not in target.get("architectures", []):
        raise ValueError("Target architecture differs from the pinned Qwen3.8 profile")
    provenance = json.loads((target_path / "reshard-provenance.json").read_text())
    if (provenance.get("source") != pins["target"]["repo"] or
            provenance.get("revision") != pins["target"]["revision"] or
            provenance.get("all_tensor_bytes_verified_identical") is not True):
        raise ValueError("Prepared target provenance does not match the pinned model")
    verify_manifest(target_path, provenance)
    text = target.get("text_config", target)
    if method == "dflash":
        draft = load_checkpoint(draft_path)
        if pins["draft"]["architecture"] not in draft.get("architectures", []):
            raise ValueError("Expected a DFlash2 draft checkpoint")
        revision = (draft_path / ".cache/huggingface/download/config.json.metadata").read_text().splitlines()[0]
        if revision != pins["draft"]["revision"]:
            raise ValueError("Draft revision differs from the pinned model")
        with (draft_path / "model.safetensors").open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != pins["draft"]["weight_sha256"]:
                raise ValueError("Draft weight SHA-256 differs from pinned checkpoint")
        if (any(text[k] != draft[k] for k in ("hidden_size", "vocab_size")) or
                text["num_hidden_layers"] != draft["num_target_layers"] or
                draft["dflash_config"]["block_size"] != 8 or
                any(not 0 <= i < text["num_hidden_layers"] for i in draft["dflash_config"]["target_layer_ids"])):
            raise ValueError("Draft and target metadata are incompatible")
    elif method == "mtp":
        if text.get("mtp_num_hidden_layers", target.get("mtp_num_hidden_layers", 0)) < 1:
            raise ValueError("MTP requires the checkpoint's native MTP head")
        mapping = json.loads((target_path / "model.safetensors.index.json").read_text())["weight_map"]
        if not any("mtp" in key.split(".") for key in mapping):
            raise ValueError("MTP head weights are missing from the checkpoint index")
    else:
        raise ValueError(f"Unsupported method: {method}")


def check_command(command):
    if command["runner"] != "1":
        raise ValueError("DFlash2 configuration requires Model Runner V2")
    argv = command["argv"]
    values = {}
    for i, arg in enumerate(argv):
        if arg.startswith("--") and i + 1 < len(argv) and not argv[i + 1].startswith("--"):
            values[arg] = argv[i + 1]
    method = values["--speculative-config.method"]
    if method == "dflash" and int(values["--speculative-config.num_speculative_tokens"]) != 7:
        raise ValueError("This DFlash2 checkpoint requires seven draft tokens")
    if values.get("--speculative-config.quantization") not in (None, "fp8_per_tensor"):
        raise ValueError("Draft online FP8 uses fp8_per_tensor in this runtime")
    for flag in ("--max-model-len", "--max-num-seqs", "--max-num-batched-tokens", "--kv-cache-memory-bytes"):
        if int(values[flag]) < 1:
            raise ValueError(f"Expected positive integer: {flag}")
    if not 0 < float(values["--gpu-memory-utilization"]) <= 1:
        raise ValueError("Invalid GPU memory fraction")
    kernel = json.loads(values["--kernel-config"])
    validate_settings(kernel)
    autotune_enabled()
    for name in ("nvfp4_dynamic_max_tokens", "nvfp4_dynamic_silu_max_tokens"):
        if name in kernel and (type(kernel[name]) is not int or not 0 <= kernel[name] <= 16):
            raise ValueError(f"Invalid NVFP4 cutoff: {name}")
    backends = set(kernel.get("linear_backend_per_quant", {}).values())
    backends.add(kernel.get("linear_backend", "auto"))
    if "b12x" in backends:
        metadata.version("b12x")  # The native backend is separate from FlashInfer-b12x.
    graphs = json.loads(values["--compilation-config"])
    if graphs.get("pass_config", {}).get("fuse_act_quant"):
        raise ValueError("This profile uses manual activation fusion, not the inactive compiler pass")
    if "max_cudagraph_capture_size" in graphs:
        raise ValueError("Use the V2 prefill-token/decode-request graph settings")
    requests = graphs.get("decode_cudagraph_capture_request_sizes", [])
    if not requests or max(requests) < int(values["--max-num-seqs"]):
        raise ValueError("Decode graphs do not cover the configured sequence count")
    tokens = graphs.get("prefill_cudagraph_capture_token_sizes", [])
    if not tokens or max(tokens) > int(values["--max-num-batched-tokens"]):
        raise ValueError("Prefill graph sizes exceed the configured token batch")
    return values, kernel, graphs


def check_packages(pins):
    expected = dict(pins["runtime"])
    for line in (BASE / "requirements.lock").read_text().splitlines():
        match = re.fullmatch(r"([A-Za-z0-9_.-]+)==([^\s]+)", line)
        if match:
            expected[match[1]] = match[2]
    for name, wanted in expected.items():
        actual = metadata.version(name)
        if actual != wanted:
            raise ValueError(f"Package version mismatch: {name}: {actual} != {wanted}")
    distribution = metadata.distribution("vllm")
    built = json.loads(Path(distribution.locate_file("vllm/_p16_build.json")).read_text())
    if built != json.loads((BASE / "build-profile.json").read_text()):
        raise ValueError("Installed vLLM backports differ from the pinned build")
    return len(expected)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--schemas", action="store_true", help="Also validate with the installed vLLM configuration classes; CUDA must be hidden")
    args = parser.parse_args()
    pins = json.loads((BASE / "models.json").read_text())
    command = json.loads(subprocess.check_output(["bash", str(BASE / "serve.sh"), "--print-config"], text=True))
    values, kernel, graphs = check_command(command)
    count = check_packages(pins)
    check_models(Path(os.environ["VLLM_TARGET_MODEL"]), Path(os.environ["VLLM_DRAFT_MODEL"]), pins, values["--speculative-config.method"])
    if args.schemas:
        if os.environ.get("CUDA_VISIBLE_DEVICES") != "":
            raise ValueError("Schema checks must run with CUDA_VISIBLE_DEVICES='' (no GPU execution)")
        import torch
        from vllm.config import CompilationConfig, KernelConfig
        KernelConfig(**kernel)
        CompilationConfig(**graphs)
        from vllm.entrypoints.common.cli_args import make_arg_parser, validate_parsed_serve_args
        from vllm.utils.argparse_utils import FlexibleArgumentParser
        from vllm.engine.arg_utils import AsyncEngineArgs
        native_args = make_arg_parser(FlexibleArgumentParser()).parse_args(command["argv"][2:])
        validate_parsed_serve_args(native_args)
        AsyncEngineArgs.from_cli_args(native_args)
        if values.get("--speculative-config.quantization") == "fp8_per_tensor":
            from vllm.config.quantization import resolve_quantization_config
            from vllm.model_executor.layers.quantization.online.base import OnlineQuantizationConfig
            OnlineQuantizationConfig(resolve_quantization_config("fp8_per_tensor", None))
        if kernel.get("linear_backend") == "nvfp4_dynamic":
            import flashinfer
            from vllm.model_executor.kernels.linear.nvfp4.dynamic_backends import get_dynamic_backend
            for name, bits in ((kernel.get("nvfp4_dynamic_w4a4_backend", "flashinfer_cutlass"), 4),
                               (kernel.get("nvfp4_dynamic_w4a16_backend", "flashinfer_cutedsl_native"), 16)):
                if not get_dynamic_backend(name, bits).is_supported(121):
                    raise ValueError(f"FlashInfer API does not advertise SM121 support for {name}")
            if not flashinfer.mm_bf16.is_backend_supported("cudnn", 121):
                raise ValueError("FlashInfer cuDNN BF16 API does not advertise SM121 support")
        if torch.cuda.is_initialized():
            raise RuntimeError("Unexpected CUDA initialization")
    print(json.dumps({"model_metadata": "passed", "package_versions": count,
                      "vllm_schemas": args.schemas, "runner": "V2",
                      "weights_loaded": False, "engine_created": False,
                      "draft_quantization": values.get("--speculative-config.quantization", "bf16"),
                      "kernel_qualification_report": str(report_path()),
                      "kernel_qualification_run_by_check": False,
                      "inference_tested": False, "performance_measured": False}, indent=2))


if __name__ == "__main__":
    main()
