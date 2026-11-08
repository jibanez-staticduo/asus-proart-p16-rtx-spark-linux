#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Check native backport dispatch with synthetic CPU tensors and mocked GPU calls."""
import ast
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import os

if os.environ.get("CUDA_VISIBLE_DEVICES") != "":
    raise RuntimeError("Run with CUDA_VISIBLE_DEVICES='' to exclude GPU execution")

import torch
import flashinfer
import vllm
from vllm.model_executor.kernels.linear.nvfp4 import dynamic
from vllm.model_executor.kernels.linear.nvfp4.dynamic_backends import get_dynamic_backend


def fail_cuda(*args, **kwargs):
    raise AssertionError("CUDA initialization is forbidden in offline checks")


torch.cuda._lazy_init = fail_cuda
torch.cuda.init = fail_cuda
ROOT = Path(vllm.__file__).parent


def source_object(relative, name, namespace):
    source = ast.parse((ROOT / relative).read_text())
    node, = [node for node in source.body if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name == name]
    exec(compile(ast.Module(body=[node], type_ignores=[]), relative, "exec"), namespace)
    return namespace[name]


def check_precision_dispatch():
    calls = []

    def backend(name, bits):
        get_dynamic_backend(name, bits)
        return SimpleNamespace(run=lambda *args: calls.append((bits, args[0].shape[0])) or torch.empty((args[0].shape[0], 128)))

    def quant(x, *args, **kwargs):
        calls.append(("quant", x.shape[0]))
        return x, torch.ones(1)

    def silu(x, **kwargs):
        calls.append(("silu", x.shape[0]))
        return x[:, :x.shape[1] // 2]

    with (patch.object(dynamic, "get_dynamic_backend", backend),
          patch.object(flashinfer, "nvfp4_quantize", quant),
          patch("flashinfer.quantization.fp4_quantization.silu_and_mul_nvfp4_quantize", quant),
          patch("vllm.model_executor.kernels.linear.nvfp4.cutedsl_silu.run", silu)):
        for fused in (False, True):
            for rows in (1, 8, 16, 17, 64):
                calls.clear()
                x = torch.zeros((rows, 32), dtype=torch.bfloat16)
                out = dynamic.dynamic_nvfp4(x, torch.zeros((128, 8), dtype=torch.uint8),
                                           torch.ones(1), torch.ones(1), torch.ones(1),
                                           torch.ones(1), 16, fused)
                expected_bits = 16 if rows <= 16 else 4
                assert calls[-1] == (expected_bits, rows), calls
                assert any(c[0] == "quant" for c in calls) == (rows > 16), calls
                assert any(c[0] == "silu" for c in calls) == (fused and rows <= 16), calls
                assert out.shape == (rows, 128)


def check_qwen_mlp_fusion():
    class Kernel:
        def apply_silu_or_linear(self, layer, x, fused):
            assert fused
            gate, up = x.chunk(2, dim=-1)
            return torch.nn.functional.linear(torch.nn.functional.silu(gate) * up, layer.weight)

    kernel = Kernel()

    class Linear(torch.nn.Module):
        def __init__(self, inp, out, **kwargs):
            super().__init__()
            out = sum(out) if isinstance(out, list) else out
            self.weight = torch.nn.Parameter(torch.ones((out, inp)))
            self.quant_method = SimpleNamespace()
            self.scheme = SimpleNamespace(kernel=kernel)

        def forward(self, x):
            return torch.nn.functional.linear(x, self.weight), None

    class SiluAndMul(torch.nn.Module):
        def forward(self, x):
            gate, up = x.chunk(2, dim=-1)
            return torch.nn.functional.silu(gate) * up

    namespace = {"nn": torch.nn, "torch": torch, "F": torch.nn.functional,
                 "QuantizationConfig": object, "MergedColumnParallelLinear": Linear,
                 "RowParallelLinear": Linear, "SiluAndMul": SiluAndMul,
                 "maybe_fused_act_quant": lambda act, x, layer: act(x)}
    mlp = source_object("model_executor/models/qwen2_moe.py", "Qwen2MoeMLP", namespace)
    with patch.object(dynamic, "DynamicNvFp4LinearKernel", Kernel):
        model = mlp(4, 8, "silu")
    assert model._dynamic_nvfp4_kernel is kernel
    x = torch.ones((2, 4))
    fused = model(x)
    model._dynamic_nvfp4_kernel = None
    assert torch.equal(fused, model(x))


def check_warmup_coverage():
    namespace = {"GPUModelRunner": object,
                 "_flashinfer_deferred_moe_token_counts": lambda runner: (),
                 "_FLASHINFER_BF16_AUTOTUNE_MAX_TOKENS": 32,
                 "_FLASHINFER_BF16_LINEAR_BACKENDS": {"flashinfer_cutedsl", "flashinfer_cudnn"}}
    source_object("model_executor/warmup/kernel_warmup.py", "_flashinfer_bf16_linear_backend_active", namespace)
    counts = source_object("model_executor/warmup/kernel_warmup.py", "_flashinfer_autotune_token_counts", namespace)
    config = SimpleNamespace(linear_backend="nvfp4_dynamic", unquantized_linear_backend="flashinfer_cudnn",
                             nvfp4_dynamic_max_tokens=16, nvfp4_dynamic_silu_max_tokens=None)
    runner = SimpleNamespace(scheduler_config=SimpleNamespace(max_num_batched_tokens=8192),
                             vllm_config=SimpleNamespace(kernel_config=config))
    assert counts(runner) == (8192, *range(1, 17), 32)
    runner.scheduler_config.max_num_batched_tokens = 8
    assert counts(runner) == (8, *range(1, 8))


def check_logits_warmup_is_bounded():
    import vllm.utils.flashinfer as fi_utils
    import vllm.models.deepseek_v41.attention as indexer
    buckets = []
    observed = {}

    @contextmanager
    def autotune(*, tuning_buckets, **kwargs):
        buckets.append(tuning_buckets)
        try:
            yield
        finally:
            buckets.pop()

    def logits(x):
        observed["rows"] = x.shape[0]
        observed["buckets"] = buckets[-1]

    runner = SimpleNamespace(
        speculator=None,
        vllm_config=SimpleNamespace(kernel_config=SimpleNamespace(linear_backend="nvfp4_dynamic")),
        _dummy_run=lambda **kwargs: (None, torch.zeros((8, 16))),
        get_model=lambda: SimpleNamespace(compute_logits=logits))
    run = source_object("model_executor/warmup/kernel_warmup.py", "_run_flashinfer_autotune_dummy_runs",
                        {"GPUModelRunner": object, "torch": torch,
                         "logger": SimpleNamespace(info=lambda *args: None),
                         "_flashinfer_autotune_token_counts": lambda runner: (8192,)})
    with (patch.object(fi_utils, "autotune", autotune),
          patch.object(indexer, "autotune_indexer_wq_b", lambda *args: None)):
        run(runner)
    assert observed["rows"] == 8
    assert max(observed["buckets"]) == 8
    assert not buckets


check_precision_dispatch()
check_qwen_mlp_fusion()
check_warmup_coverage()
check_logits_warmup_is_bounded()
if torch.cuda.is_initialized():
    raise AssertionError("Offline checks initialized CUDA")
print("Passed: precision dispatch, Unsloth MLP fusion and complete small-M warmup coverage; no GPU, model or engine.")
