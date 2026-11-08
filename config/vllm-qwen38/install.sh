#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Install native runtime, model data or configuration without starting a server.
set -euo pipefail
source_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
config_home=${XDG_CONFIG_HOME:-$HOME/.config}
env_file=$config_home/vllm-qwen38.env
if [[ -f $env_file ]]; then
  settings=$env_file
else
  settings=$source_dir/environment.example
fi
set -a
# The environment file uses shell-compatible assignments, as in the example.
# shellcheck disable=SC1090
source "$settings"
set +a

case ${1:-} in
  runtime|models|config)
    systemctl --user show-environment >/dev/null
    state=$(systemctl --user show vllm-qwen38.service --property=ActiveState --value)
    case $state in
      inactive|failed) ;;
      *) printf 'Refusing to change installation while service state is %s.\n' "$state" >&2; exit 1 ;;
    esac
    ;;
esac

case ${1:-} in
  runtime)
    if [[ ! -x $VLLM_VENV/bin/python ]]; then uv venv --python 3.12 "$VLLM_VENV"; fi
    "$VLLM_VENV/bin/python" -c 'import sys; sys.exit(0 if sys.version_info[:2] == (3,12) else "Python 3.12 required")'
    uv pip install --python "$VLLM_VENV/bin/python" --link-mode copy --no-deps \
      --index-strategy unsafe-best-match \
      --index https://download.pytorch.org/whl/cu130 \
      --index https://pypi.nvidia.com \
      --index https://flashinfer.ai/whl/cu130 \
      --index https://flashinfer.ai/whl \
      --default-index https://pypi.org \
      -r "$source_dir/requirements.lock"
    "$VLLM_VENV/bin/python" "$source_dir/build-wheel.py" "$VLLM_CACHE_DIR/wheels"
    wheel=$("$VLLM_VENV/bin/python" -c \
      'import json,sys,urllib.parse; p=json.load(open(sys.argv[1])); print(urllib.parse.unquote(p["base_wheel_url"].rsplit("/",1)[1]).replace(p["base_version"],p["version"]))' \
      "$source_dir/build-profile.json")
    uv pip install --python "$VLLM_VENV/bin/python" --link-mode copy --no-deps "$VLLM_CACHE_DIR/wheels/$wheel"
    ;;
  models)
    mapfile -t pins < <("$VLLM_VENV/bin/python" -c \
      'import json,sys; p=json.load(open(sys.argv[1])); [print(p[k][v]) for k in ("target","draft") for v in ("repo","revision")]' \
      "$source_dir/models.json")
    [[ ${#pins[@]} == 4 ]] || exit 1
    for name in VLLM_SOURCE_MODEL VLLM_TARGET_MODEL VLLM_DRAFT_MODEL; do
      [[ -n ${!name:-} ]] || { printf 'Missing variable: %s\n' "$name" >&2; exit 1; }
    done
    "$VLLM_VENV/bin/hf" download "${pins[0]}" --revision "${pins[1]}" --local-dir "$VLLM_SOURCE_MODEL"
    "$VLLM_VENV/bin/hf" cache verify "${pins[0]}" --revision "${pins[1]}" --local-dir "$VLLM_SOURCE_MODEL"
    "$VLLM_VENV/bin/hf" download "${pins[2]}" --revision "${pins[3]}" --local-dir "$VLLM_DRAFT_MODEL"
    "$VLLM_VENV/bin/hf" cache verify "${pins[2]}" --revision "${pins[3]}" --local-dir "$VLLM_DRAFT_MODEL"
    CUDA_VISIBLE_DEVICES='' "$VLLM_VENV/bin/python" "$source_dir/prepare-checkpoint.py" \
      "$VLLM_SOURCE_MODEL" "$VLLM_TARGET_MODEL" --pins "$source_dir/models.json"
    ;;
  config)
    CUDA_VISIBLE_DEVICES='' "$VLLM_VENV/bin/python" "$source_dir/check.py" --schemas
    backup_root=${XDG_STATE_HOME:-$HOME/.local/state}/vllm-qwen38
    mkdir -p "$backup_root"
    backup=$(mktemp -d "$backup_root/setup.XXXXXX")
    service=$config_home/systemd/user/vllm-qwen38.service
    installed=$HOME/.local/libexec/vllm-qwen38
    mkdir -p "$backup"
    [[ ! -e $service ]] || cp -a -- "$service" "$backup/vllm-qwen38.service"
    [[ ! -e $env_file ]] || cp -a -- "$env_file" "$backup/environment"
    if [[ -e $installed ]]; then cp -a -- "$installed" "$backup/launcher"; fi
    [[ ! -f $installed ]] || { echo 'Launcher path is a file; configuration preserved.' >&2; exit 1; }
    install -d "$installed"
    if [[ $source_dir != "$installed" ]]; then
      for file in serve.sh check.py checkpoint.py build-wheel.py build-profile.json install.sh prepare-checkpoint.py models.json client-presets.json requirements.lock environment.example vllm-qwen38.service; do
        install -m644 "$source_dir/$file" "$installed/$file"
      done
      install -d "$installed/patches"
      install -m644 "$source_dir/patches/0001-sm12x-nvfp4-and-bf16.patch" "$installed/patches/"
    fi
    chmod 755 "$installed/serve.sh" "$installed/install.sh"
    if [[ ! -e $env_file ]]; then install -Dm600 "$source_dir/environment.example" "$env_file"; fi
    install -Dm644 "$source_dir/vllm-qwen38.service" "$service"
    mkdir -p "$VLLM_CACHE_DIR"/{huggingface,triton,cuda}
    systemctl --user daemon-reload
    systemctl --user disable vllm-qwen38.service
    printf 'Configuration installed; service remains stopped and disabled. Backup: %s\n' "$backup"
    ;;
  check)
    CUDA_VISIBLE_DEVICES='' "$VLLM_VENV/bin/python" "$source_dir/check.py" --schemas
    ;;
  *) printf 'Usage: %s {runtime|models|config|check}\n' "$0" >&2; exit 2 ;;
esac
