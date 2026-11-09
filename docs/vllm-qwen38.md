# Native Qwen3.8-27B with vLLM

This profile serves `unsloth/Qwen3.8-27B-NVFP4` on one RTX Spark N1X using
a native ARM64 Python environment. DFlash2 uses Model Runner V2. Other Qwen
27B checkpoints need a matching drafter, tokenizer, parser and quantization
configuration; replacing the model path alone is not a supported migration.

The service is installed stopped and disabled. Integrity checks read files in
bounded CPU buffers; configuration/schema checks do not construct a model or
create an inference engine. Successful model
startup, output correctness and throughput still require a separately
authorized runtime check.

## Pinned inputs

[`models.json`](../config/vllm-qwen38/models.json) records the target and draft
repositories, immutable revisions and expected architectures.
[`requirements.lock`](../config/vllm-qwen38/requirements.lock) pins the dependencies;
[`build-profile.json`](../config/vllm-qwen38/build-profile.json) pins the vLLM
base wheel, its SHA-256 and the backport patch SHA-256. Together they specify
all 203 installed packages. The base is the immutable ARM64 wheel for commit
`57d8e025e907a0104a9e8a87cd3998256221e48f`.
The runtime is vLLM `0.33.0rc1.dev1011+g57d8e025e.p16.1`, PyTorch
`2.14.0+cu130` and FlashInfer `0.7.3`.

The package installer specifies the PyTorch CUDA 13.0, NVIDIA and FlashInfer
indexes explicitly. It uses exact package pins with `--no-deps`, including
the FlashInfer SM120f and SM121a providers. These are version pins, not a
complete wheel-hash lock. Wheels, model weights and caches stay outside Git.

## Installation without starting the service

Requirements: AArch64 Linux, Python 3.12 supplied through `uv`, the existing
NVIDIA driver, and a CUDA toolkit at `CUDA_HOME`. Root is not needed for these
user-level installation steps. Use the repository's `main` checkout.

Set the paths in a private environment file. Values must be literal absolute
paths or numbers, using shell-compatible assignments; systemd does not expand
shell variables in its environment file.

```sh
config_dir=${XDG_CONFIG_HOME:-$HOME/.config}
install -d "$config_dir"
if [ ! -e "$config_dir/vllm-qwen38.env" ]; then
    install -m600 config/vllm-qwen38/environment.example "$config_dir/vllm-qwen38.env"
fi
```

Review that file before installation. Then run the stages in order:

```sh
bash config/vllm-qwen38/install.sh runtime
bash config/vllm-qwen38/install.sh models
bash config/vllm-qwen38/install.sh check
bash config/vllm-qwen38/install.sh config
```

- All three writing stages (`runtime`, `models`, `config`) require the user
  systemd manager and refuse changes unless the service is inactive or failed.
- `runtime` creates the dedicated environment if absent and installs the pinned
  dependencies. It verifies and patches the base wheel, rebuilds its metadata
  and RECORD, then installs the resulting native wheel. Existing CUDA binaries
  are preserved. An existing environment must use Python 3.12. Building this
  wheel does not compile or execute GPU kernels.
- `models` downloads both pinned checkpoints with `hf`, verifies their Hub
  checksums, and splits the target's large weight file on the CPU. Each output
  tensor is compared byte-for-byte, including auxiliary MTP weights. Completed
  output is renamed into place. A SHA-256 manifest covers every prepared file.
  Reuse requires matching hashes and valid safetensors headers, file lengths
  and tensor inventory. Output predating the manifest is compared against the
  source tensor bytes before a manifest is added. Incomplete, modified or
  differently pinned output is rejected.
- `check` checks package versions, backport provenance, checkpoint integrity,
  runner selection and vLLM's kernel/graph configuration schemas. It also checks
  FlashInfer's declared SM121 backend support. CUDA is hidden; the command
  rejects CUDA initialization. No model is constructed.
- `config` refuses to replace a running service, runs the preflight, backs up
  existing configuration under `$XDG_STATE_HOME/vllm-qwen38` (normally
  `~/.local/state/vllm-qwen38`), installs the versioned files and reloads systemd.
  It disables autostart and never starts, restarts or enables the service.

The installed launcher and support files live in
`~/.local/libexec/vllm-qwen38/`. The unit reads
`$XDG_CONFIG_HOME/vllm-qwen38.env` (normally `~/.config/vllm-qwen38.env`).
It no longer depends on a dated benchmark directory. Re-run the `config`
stage after reviewing repository updates; it preserves the existing environment
file. No stage writes kernel, firmware, boot or GPU power settings.

## Operating profiles

The default uses DFlash2 with seven draft tokens and standard rejection
sampling. Eligible draft linear layers use online FP8 quantization via
`--speculative-config.quantization fp8_per_tensor`; the pinned source checkpoint
remains BF16 on disk. This is draft-only: the Unsloth target retains its mixed
NVFP4/FP8 scheme. Candidate-selector codebooks and layers the model leaves
unquantized retain their original precision. Set `VLLM_DRAFT_QUANTIZATION=bf16`
to compare with the unquantized drafter. The older `fp8` online shorthand is
not used by this profile.

It selects V2 decode graphs for 1, 2, 4 and 8 requests and prefill
graphs for 512, 1024, 2048, 4096 and 8192 tokens. FlashInfer autotuning is enabled.

`environment.example` exposes the context length, sequence count, prefill batch
size, KV-cache allocation, host and port, and startup memory fraction.
The initial values are 64K context, eight sequences, 8K prefill batches and
a 6 GiB FP8 KV cache. This workstation profile shares memory with other
applications. It does not claim the model's full 262K context or multimodal
support. The launcher deliberately uses text-only mode.

`VLLM_GPU_MEMORY_UTILIZATION=0.40` is a memory-budget setting, not a GPU
utilization, clock or power cap. The KV allocation is explicit. Raising a
context or concurrency limit requires checking cache capacity, available
shared memory and graph coverage; the offline check catches incomplete graph
coverage but does not measure runtime memory peaks. Wider graphs consume
additional memory. CPU affinity selects this N1X machine's performance cores.

Set `VLLM_SPEC_METHOD=mtp` for the checkpoint's built-in three-token MTP head.
DFlash2 benefits depend on output predictability, context and concurrency;
it is not assumed faster for every workload. Neither profile is benchmarked here.

[`client-presets.json`](../config/vllm-qwen38/client-presets.json) supplies
explicit HTTP-request settings for direct answers and reasoning. Merge one
preset into a request body. Thinking remains the checkpoint's default unless
the client selects otherwise; speed comparisons must use the same mode,
sampling, prompt/output sizes and concurrency.

After a later explicit start, the intended endpoint is
`http://127.0.0.1:8080/v1`, with model names `p16-local` and
`qwen3.8-27b-nvfp4`.

## Kernel scope

The native wheel backports NVIDIA's dynamic W4A4/W4A16 selection, independent
unquantized-linear selection and the FlashInfer cuDNN BF16 backend. Its source
references and patch hashes are recorded in `build-profile.json`. The initial
policy uses ordinary-linear cutoff 0 and SiLU/down cutoff 16, sharing one
canonical weight representation. These are independent inputs, not measured
optimal N1X cutoffs. W4A4 uses FlashInfer CUTLASS; the mixed
checkpoint's FP8 layers retain CUTLASS. Eligible narrow BF16 projections use
FlashInfer cuDNN, with the existing automatic fallback for other shapes.

The Qwen MLP calls the dynamic SiLU/down-projection path directly, including
Unsloth's compressed-tensors scheme wrapper. This supplies activation fusion
under Breakable CUDA Graphs without relying on the inactive `fuse_act_quant`
compiler pass. Warmup covers every exact M from 1 through the A16 cutoff, a
bounded BF16 bucket and logits buckets limited to sampled rows. Warmup runs
only when the service is explicitly started, never during installation.

The base includes the DFlash2 sliding-window/prefix-cache correction.
`VLLM_KERNEL_CONFIG` and
`VLLM_COMPILATION_CONFIG` can override their JSON configurations; `check`
validates these against the pinned vLLM schemas and native serve CLI before
installation. Kernel overrides must retain dynamic NVFP4, CUTLASS FP8 and
cuDNN BF16 for the qualification runner.

## Qualification on an explicit start

The launcher's no-argument execution first runs `qualify-kernels.py` in a
separate process. Installation, `check` and `serve.sh --print-config` never run
this CUDA program. The service remains stopped until explicitly started.

Qualification uses synthetic packed FP4 weights with the target's gate/up and
down dimensions. It constructs no model, loads no checkpoint tensors and
creates no vLLM engine. An independent E2M1 decoder and scale-layout inverse
produce FP32 references. Eager results and CUDA graph replay must agree within
`atol=0.03, rtol=0.01`, including changed live scales. The fused activation has
an additional normalized quantization-error bound of 0.18. CUTLASS FP8 and
cuDNN BF16 receive their own reference checks. Any failure prevents vLLM from
executing.

For M=1,2,4,8,16 the runner measures A4 and A16 separately for gate/up and
SiLU/down after warming the kernels through the server's FlashInfer managed
autotune cache and token buckets: three rounds of ten CUDA graph replays per
candidate. It chooses
the cutoff minimizing total latency across those equally weighted shapes,
requiring at least a 3% gain over fixed W4A4. M=17 and 32 also exercise the
W4A4 path. This is local component tuning, not an end-to-end workload or TPS
benchmark. Full generation correctness and speculative acceptance still need
a later model run.

The DFlash drafter uses greedy sampling by default, matching the published
Windows N1X code benchmark. `VLLM_DRAFT_SAMPLE_METHOD=probabilistic` selects
probabilistic drafting. This changes the draft proposals, not the target
model's requested sampling distribution.

`$VLLM_CACHE_DIR/kernel-qualification.json` records results, timings, GPU UUID,
selected cutoffs and software fingerprint. A matching successful report is
reused. Changes to the device, package versions, driver, power profile, model
dimensions, requested kernel settings or qualification code invalidate it.
`--print-config` displays the selected policy when a matching report exists;
otherwise it displays the initial policy.

`VLLM_NVFP4_LINEAR_CUTOFF` and `VLLM_NVFP4_SILU_CUTOFF` set independent initial
values in 0..16. `VLLM_NVFP4_AUTOTUNE=0` preserves them while retaining numeric
qualification. None of these settings starts the service.

FlashInfer-b12x is distinct from the optional native `b12x` package.
Selecting the latter requires a separately installed package; preflight rejects
that selection when the package is absent.

The backports derive from [vLLM #54614](https://github.com/vllm-project/vllm/pull/54614),
[#59021](https://github.com/vllm-project/vllm/pull/59021),
[#59025](https://github.com/vllm-project/vllm/pull/59025) and
[#59244](https://github.com/vllm-project/vllm/pull/59244), adapted for this newer
upstream wheel and the Unsloth checkpoint. These are local backports of open
changes, not an upstream release or a measured equivalent of
[NVIDIA's Spark recipe](https://github.com/NVIDIA/local-ai-inference-recipes/blob/main/models/qwen/qwen3.8-27b/dgx-spark/linux/vllm/128gb/nvfp4/recipe.yaml).

## Verification and known package metadata

Run the repository tests without hardware access:

```sh
python3 -m unittest discover -s tests -p test_vllm_configuration.py -v
```

To include the small CPU-only safetensors conversion test, set
`VLLM_TEST_PYTHON` to the installed environment's Python. This converts synthetic
tensors and tests the backport precision dispatch, Unsloth MLP fusion, exact-M
warmup coverage and bounded logits tuning with mocked GPU calls. It does not
load an LLM or execute GPU kernels.

NVIDIA's `nvidia-cusparselt-cu13==0.8.1` wheel contains an ARM64 library but
uses the internal platform tag `manylinux2014_sbsa`. NVIDIA and PyPI publish
the same SHA-256:
`4dca476c50bf4780d46cd0bfbd82e2bc10a08e4fef7950917ce8d7578d22a23f`.
The installed metadata is not edited.

FlashInfer's minimal shim declares providers for additional GPU families.
Consequently, `uv pip check` reports the cuSPARSELt platform tag and six missing
providers: SM80, SM89, SM90a, SM100a, SM103a and SM110a. The selected SM120f and
SM121a packages are installed. These known diagnostics do not establish GPU
execution correctness; any additional dependency error needs investigation.
