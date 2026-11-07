#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
set -euo pipefail

for name in VLLM_VENV VLLM_TARGET_MODEL VLLM_DRAFT_MODEL VLLM_CACHE_DIR; do
  if [[ -z ${!name:-} ]]; then
    printf 'Missing environment variable: %s\n' "$name" >&2
    exit 2
  fi
done

export CUDA_HOME=${CUDA_HOME:-/opt/cuda}
export PATH="$VLLM_VENV/bin:$CUDA_HOME/bin:/usr/bin:/bin"
export HF_HOME="$VLLM_CACHE_DIR/huggingface"
export XDG_CACHE_HOME="$VLLM_CACHE_DIR"
export TRITON_CACHE_DIR="$VLLM_CACHE_DIR/triton"
export CUDA_CACHE_PATH="$VLLM_CACHE_DIR/cuda"
export VLLM_NO_USAGE_STATS=1
export VLLM_USE_V2_MODEL_RUNNER=0
export OMP_NUM_THREADS=${OMP_NUM_THREADS:-8}
export TOKENIZERS_PARALLELISM=false

exec "$VLLM_VENV/bin/vllm" serve \
  "$VLLM_TARGET_MODEL" \
  --host 127.0.0.1 --port 8080 \
  --served-model-name p16-local qwen3.8-27b-nvfp4 \
  --max-model-len 65536 --max-num-seqs 8 \
  --max-num-batched-tokens 8192 \
  --kv-cache-memory-bytes 6442450944 --kv-cache-dtype fp8 \
  --gpu-memory-utilization "${VLLM_GPU_MEMORY_UTILIZATION:-0.40}" \
  --async-scheduling \
  --kernel-config '{"linear_backend_per_quant":{"nvfp4_w4a4":"flashinfer_b12x","fp8_w8a8":"cutlass"},"enable_flashinfer_autotune":true}' \
  --speculative-config.method dflash \
  --speculative-config.model "$VLLM_DRAFT_MODEL" \
  --speculative-config.num_speculative_tokens 7 \
  --speculative-config.draft_sample_method probabilistic \
  --speculative-config.rejection_sample_method standard \
  --compilation-config '{"pass_config":{"fuse_act_quant":true},"max_cudagraph_capture_size":64}' \
  --language-model-only --reasoning-parser qwen3 \
  --tool-call-parser qwen3_xml --enable-auto-tool-choice \
  --safetensors-load-strategy eager
