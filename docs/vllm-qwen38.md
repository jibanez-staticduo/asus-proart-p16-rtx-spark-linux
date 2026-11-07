# Native Qwen3.8-27B with vLLM

Configuration templates and the installed package versions are in
[`config/vllm-qwen38/`](../config/vllm-qwen38/). They configure a native Python
environment with Unsloth NVFP4 and DFlash2. The installed local service remains
stopped and disabled. Model loading, inference correctness and throughput with
this configuration have not been validated.

## Model inputs

| Input | Repository | Revision |
| --- | --- | --- |
| Target | `unsloth/Qwen3.8-27B-NVFP4` | `f0b7c9e722f5565102fff8481c99e4d86ae099c7` |
| Drafter | `z-lab/Qwen3.8-27B-DFlash2` | `50307d4c4cde6860d4eee73e2547cd786fe8e8a4` |

The local target checkpoint was split into 22 main weight files plus the MTP
file, with tensor bytes verified unchanged. All draft files passed Hugging Face
checksum verification. Weights, downloads and caches are not included in Git.
Set the paths to these prepared checkpoints in `environment.example`.

The runtime versions are vLLM `0.33.0rc1.dev1011+g57d8e025e`, PyTorch
`2.14.0+cu130` and FlashInfer `0.7.3`. `requirements.lock` records the complete
installed environment, including the SM120f and SM121a kernel providers.

## Configuration

The launcher selects DFlash2 with seven draft tokens, standard rejection
sampling, the V1 model runner, CUDA graphs and FlashInfer autotuning. NVFP4
linear layers use FlashInfer B12x; the FP8 parts of the mixed checkpoint use
CUTLASS. The context limit is 65,536 tokens, with eight sequences, 8,192-token
prefill batches and a 6 GiB FP8 KV cache.

`VLLM_GPU_MEMORY_UTILIZATION=0.40` controls the startup memory budget, not GPU
utilization, clocks or power. The KV-cache allocation is explicit. The service's
CPU affinity selects this N1X machine's performance cores; adjust it for a
different CPU topology. No driver, firmware or power-profile changes are made
by these templates.

After a later explicit start, the intended endpoint is
`http://127.0.0.1:8080/v1`, with model names `p16-local` and
`qwen3.8-27b-nvfp4`.

## Installing the templates

The Python environment and prepared checkpoints must already exist at the
configured paths. These commands only copy configuration and reload systemd;
they do not enable or start the service:

```sh
install -Dm755 config/vllm-qwen38/serve.sh "$HOME/.local/libexec/vllm-qwen38"
install -Dm644 config/vllm-qwen38/environment.example "$HOME/.config/vllm-qwen38.env"
install -Dm644 config/vllm-qwen38/vllm-qwen38.service "$HOME/.config/systemd/user/vllm-qwen38.service"
systemctl --user daemon-reload
```

Review existing configuration before replacing it. These templates have not
been installed over the stopped local service as part of the Git transfer.

## Package metadata limitations

NVIDIA's `nvidia-cusparselt-cu13==0.8.1` wheel contains an ARM64 library but its
internal platform tag is `manylinux2014_sbsa`. Both NVIDIA and PyPI publish the
same wheel hash:
`4dca476c50bf4780d46cd0bfbd82e2bc10a08e4fef7950917ce8d7578d22a23f`.
This causes a platform warning from `uv pip check`; the metadata was not edited.

FlashInfer's minimal installation uses `--no-deps` for the JIT-cache shim and
the selected GPU providers. The shim also declares providers for SM80, SM89,
SM90a, SM100a, SM103a and SM110a. Those other GPU families are not installed, so
`uv pip check` reports six missing provider dependencies. The installed SM120f
and SM121a providers match the N1X hardware.
