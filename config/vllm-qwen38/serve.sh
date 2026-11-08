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
export VLLM_USE_V2_MODEL_RUNNER=1
export OMP_NUM_THREADS=${OMP_NUM_THREADS:-8}
export TOKENIZERS_PARALLELISM=false

speculation=${VLLM_SPEC_METHOD:-dflash}
case $speculation in
  dflash) draft_args=(--speculative-config.model "$VLLM_DRAFT_MODEL"
      --speculative-config.num_speculative_tokens 7
      --speculative-config.draft_sample_method probabilistic
      --speculative-config.rejection_sample_method standard) ;;
  mtp) draft_args=(--speculative-config.num_speculative_tokens 3) ;;
  *) printf 'Unsupported speculative method: %s\n' "$speculation" >&2; exit 2 ;;
esac

kernel=${VLLM_KERNEL_CONFIG:-'{"linear_backend":"nvfp4_dynamic","nvfp4_dynamic_max_tokens":16,"unquantized_linear_backend":"flashinfer_cudnn","linear_backend_per_quant":{"fp8_w8a8":"cutlass"},"enable_flashinfer_autotune":true}'}
graphs=${VLLM_COMPILATION_CONFIG:-'{"decode_cudagraph_capture_request_sizes":[1,2,4,8],"prefill_cudagraph_capture_token_sizes":[512,1024,2048,4096,8192]}'}

args=(serve \
  "$VLLM_TARGET_MODEL" \
  --dtype bfloat16 \
  --host "${VLLM_HOST:-127.0.0.1}" --port "${VLLM_PORT:-8080}" \
  --served-model-name p16-local qwen3.8-27b-nvfp4 \
  --max-model-len "${VLLM_MAX_MODEL_LEN:-65536}" --max-num-seqs "${VLLM_MAX_NUM_SEQS:-8}" \
  --max-num-batched-tokens "${VLLM_MAX_BATCHED_TOKENS:-8192}" \
  --kv-cache-memory-bytes "${VLLM_KV_CACHE_BYTES:-6442450944}" --kv-cache-dtype fp8 \
  --gpu-memory-utilization "${VLLM_GPU_MEMORY_UTILIZATION:-0.40}" \
  --async-scheduling \
  --kernel-config "$kernel" \
  --speculative-config.method "$speculation" "${draft_args[@]}" \
  --compilation-config "$graphs" \
  --language-model-only --reasoning-parser qwen3 \
  --tool-call-parser qwen3_xml --enable-auto-tool-choice \
  --safetensors-load-strategy eager)

if [[ ${1:-} == --print-config ]]; then
  "$VLLM_VENV/bin/python" -c 'import json,os,sys; print(json.dumps({"argv":sys.argv[1:],"runner":os.environ["VLLM_USE_V2_MODEL_RUNNER"]},indent=2))' \
    "$VLLM_VENV/bin/vllm" "${args[@]}"
  exit
fi
if [[ $# != 0 ]]; then
  printf 'Usage: %s [--print-config]\n' "$0" >&2
  exit 2
fi
exec "$VLLM_VENV/bin/vllm" "${args[@]}"
