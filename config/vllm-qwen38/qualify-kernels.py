#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Qualify synthetic CUDA operations and tune projection cutoffs before serving."""
from datetime import datetime, timezone
from contextlib import nullcontext
import json
import os
from pathlib import Path
import statistics
import tempfile

from kernel_policy import (choose_cutoff, report_path, requested_settings,
                           software_fingerprint, autotune_enabled, validate_settings)


def kernel_tuning_context(kernel):
    """Use the server's managed cache and token buckets before timing kernels."""
    if not kernel.get("enable_flashinfer_autotune", False):
        return nullcontext()
    from flashinfer import autotune_v2
    from vllm.model_executor.warmup.flashinfer_autotune_cache import (
        resolve_flashinfer_autotune_v2_root,
    )
    from vllm.utils.flashinfer import flashinfer_get_hybrid_num_tokens_buckets

    max_tokens = int(os.environ.get("VLLM_MAX_BATCHED_TOKENS", "8192"))
    return autotune_v2(
        mode="tune", cache_root=resolve_flashinfer_autotune_v2_root(),
        tuning_buckets=tuple(flashinfer_get_hybrid_num_tokens_buckets(max_tokens)),
        round_up=True,
    )


def dequantize_fp4(packed, scales):
    """Independent E2M1 decoding; scales are in logical row/block order."""
    import torch
    table = torch.tensor([0, .5, 1, 1.5, 2, 3, 4, 6], device=packed.device)
    output = torch.empty((packed.shape[0], packed.shape[1] * 2), device=packed.device)
    # Bound temporary integer indices and expanded scales for full projection
    # dimensions; only the FP32 reference matrix remains resident.
    for first in range(0, packed.shape[0], 256):
        chunk = packed[first:first + 256]
        codes = torch.stack((chunk & 15, chunk >> 4), dim=-1).flatten(-2)
        values = table[(codes & 7).long()] * torch.where(codes < 8, 1, -1)
        output[first:first + 256] = values * scales[first:first + 256].float().repeat_interleave(16, dim=-1)
    return output


def unswizzle_scales(scales, rows, blocks):
    import torch
    padded_rows = (rows + 127) // 128 * 128
    padded_blocks = (blocks + 3) // 4 * 4
    logical = scales.view(torch.float8_e4m3fn).reshape(
        padded_rows // 128, padded_blocks // 4, 32, 4, 4)
    return logical.permute(0, 3, 2, 1, 4).contiguous().reshape(
        padded_rows, padded_blocks)[:rows, :blocks]


def check_and_time(operation, reference, scale_tensors=()):
    """Check eager and graph results, changed scales, then time graph replay."""
    import torch
    with torch.inference_mode():
        for _ in range(3):
            result = operation()
        torch.testing.assert_close(result, reference, atol=.03, rtol=.01)
        stream = torch.cuda.Stream()
        stream.wait_stream(torch.cuda.current_stream())
        with torch.cuda.stream(stream):
            operation()
        stream.synchronize()
        graph = torch.cuda.CUDAGraph()
        with torch.cuda.graph(graph, stream=stream):
            captured = operation()
        graph.replay()
        torch.testing.assert_close(captured, reference, atol=.03, rtol=.01)
        for scale in scale_tensors:
            scale.fill_(.5)
        if scale_tensors:
            graph.replay()
            torch.testing.assert_close(captured, reference * .5, atol=.03, rtol=.01)
        for scale in scale_tensors:
            scale.fill_(1)
        samples = []
        for _ in range(3):
            start, end = torch.cuda.Event(enable_timing=True), torch.cuda.Event(enable_timing=True)
            start.record()
            for _ in range(10):
                graph.replay()
            end.record()
            end.synchronize()
            samples.append(start.elapsed_time(end) / 10)
        return statistics.median(samples)


def qualify_projection(name, n, k, fused, kernel):
    import torch
    import flashinfer
    from flashinfer.quantization.fp4_quantization import silu_and_mul_nvfp4_quantize
    from vllm.model_executor.layers.quantization.utils.nvfp4_utils import swizzle_blockscale

    print(f"Qualifying {name}: N={n}, K={k}", flush=True)
    packed = torch.randint(0, 256, (n, k // 2), device="cuda", dtype=torch.uint8)
    logical_scales = torch.full((n, k // 16), 1 / 32, device="cuda").to(torch.float8_e4m3fn)
    weights = dequantize_fp4(packed, logical_scales)
    scales = swizzle_blockscale(logical_scales)
    weight_global = torch.ones(1, device="cuda")
    input_inv = torch.ones(1, device="cuda")
    alpha = torch.ones(1, device="cuda")
    a4_backend = kernel.get("nvfp4_dynamic_w4a4_backend", "flashinfer_cutlass")
    a16_backend = kernel.get("nvfp4_dynamic_w4a16_backend", "flashinfer_cutedsl_native")
    timings = []
    for m in (1, 2, 4, 8, 16, 17, 32):
        x = torch.randn((m, k * (2 if fused else 1)), device="cuda", dtype=torch.bfloat16) * .25
        if fused:
            gate, up = x.chunk(2, dim=-1)
            activated = torch.nn.functional.silu(gate) * up
            q, sf = silu_and_mul_nvfp4_quantize(x, input_inv, enable_pdl=True)
        else:
            activated = x
            q, sf = flashinfer.nvfp4_quantize(x, input_inv, backend="cute-dsl", enable_pdl=True)
        decoded = dequantize_fp4(q, unswizzle_scales(sf, m, k // 16))
        # Check that the fused quantizer represents the independently computed
        # activation within the format's quantization error, not arbitrary data.
        relative_error = (decoded - activated.float()).norm() / activated.float().norm().clamp_min(1e-8)
        if relative_error.item() > .18:
            raise ValueError(f"{name} M={m}: FP4 activation error exceeds bound")
        references = {4: (decoded @ weights.T).to(torch.bfloat16)}
        if m <= 16:
            references[16] = (activated.float() @ weights.T).to(torch.bfloat16)
        row = {"m": m}
        for bits, reference in references.items():
            cutoff = 16 if bits == 16 else 0

            def operation():
                return torch.ops.vllm.dynamic_nvfp4(
                    x, packed, scales, weight_global, input_inv, alpha,
                    cutoff, fused, a4_backend, a16_backend)

            row[f"a{bits}_ms"] = check_and_time(operation, reference, (weight_global, alpha))
        if m <= 16:
            timings.append(row)
    return timings


def qualify_other_backends(hidden):
    import torch
    from vllm import _custom_ops as ops
    from vllm.model_executor.layers.utils import cuda_flashinfer_bf16_gemm_impl

    x = torch.randn((8, hidden), device="cuda", dtype=torch.bfloat16) * .1
    weight = torch.randn((128, hidden), device="cuda", dtype=torch.bfloat16) * .1
    reference = (x.float() @ weight.float().T).to(torch.bfloat16)
    check_and_time(lambda: cuda_flashinfer_bf16_gemm_impl(
        x, weight, None, True, "flashinfer_cudnn"), reference)
    a, b = x.to(torch.float8_e4m3fn), weight.to(torch.float8_e4m3fn).T
    scale = torch.ones((1,), device="cuda")
    reference = (a.float() @ b.float()).to(torch.bfloat16)
    check_and_time(lambda: ops.cutlass_scaled_mm(a, b, scale, scale, torch.bfloat16), reference)


def run():
    import torch
    # Import registers the same operator used by the native Qwen MLP.
    from vllm.model_executor.kernels.linear.nvfp4 import dynamic  # noqa: F401

    requested = requested_settings()
    validate_settings(requested)
    if not torch.cuda.is_available() or torch.cuda.get_device_capability() not in ((12, 0), (12, 1)):
        raise ValueError("Kernel qualification requires the local SM12x CUDA device")
    properties = torch.cuda.get_device_properties(0)
    fingerprint = software_fingerprint()
    device = str(properties.uuid)
    destination = report_path()
    try:
        previous = json.loads(destination.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        previous = {}
    tune = autotune_enabled()
    if (previous.get("passed") is True and previous.get("device_uuid") == device
            and previous.get("software_fingerprint") == fingerprint
            and previous.get("requested_kernel") == requested
            and previous.get("autotune") == tune):
        print("Reusing matching local kernel qualification.", flush=True)
        return
    config = json.loads((Path(os.environ["VLLM_TARGET_MODEL"]) / "config.json").read_text())
    text = config.get("text_config", config)
    hidden, intermediate = text["hidden_size"], text["intermediate_size"]
    torch.manual_seed(271828)
    torch.backends.cuda.matmul.allow_tf32 = False
    torch.set_float32_matmul_precision("highest")
    torch.cuda.reset_peak_memory_stats()
    with torch.inference_mode(), kernel_tuning_context(requested):
        timings = {
            "linear": qualify_projection("gate/up", 2 * intermediate, hidden, False, requested),
            "silu": qualify_projection("SiLU/down", hidden, intermediate, True, requested),
        }
        qualify_other_backends(hidden)
    selected = dict(requested)
    if tune:
        selected["nvfp4_dynamic_max_tokens"] = choose_cutoff(timings["linear"])
        selected["nvfp4_dynamic_silu_max_tokens"] = choose_cutoff(timings["silu"])
    report = {
        "passed": True, "qualified_at": datetime.now(timezone.utc).isoformat(),
        "software_fingerprint": fingerprint, "device_uuid": device,
        "gpu": properties.name, "requested_kernel": requested, "kernel_config": selected,
        "autotune": tune, "timings": timings, "timing_unit": "milliseconds",
        "kernel_autotune": requested.get("enable_flashinfer_autotune", False),
        "method": "median of three rounds of ten CUDA graph replays after serving-kernel autotune",
        "peak_allocated_bytes": torch.cuda.max_memory_allocated(),
        "checked": ["FP4 decoding", "A4/A16 FP32 reference", "SiLU activation",
                    "graph replay with changed scales", "cuDNN BF16", "CUTLASS FP8"],
        "model_loaded": False, "engine_created": False, "inference_tested": False,
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", dir=destination.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            json.dump(report, stream, indent=2)
            stream.write("\n")
            stream.close()
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
    print("Kernel qualification passed; selected independent cutoffs:",
          selected["nvfp4_dynamic_max_tokens"], selected["nvfp4_dynamic_silu_max_tokens"], flush=True)


if __name__ == "__main__":
    run()
