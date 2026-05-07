#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
MODEL_DIR="${ROOT_DIR}/models/Qwen/Qwen3-0.6B"

mkdir -p "${MODEL_DIR}"

if command -v modelscope >/dev/null 2>&1; then
  modelscope download --model Qwen/Qwen3-0.6B --local_dir "${MODEL_DIR}"
elif python3 -c "import modelscope" >/dev/null 2>&1; then
  python3 -m modelscope.cli.cli download --model Qwen/Qwen3-0.6B --local_dir "${MODEL_DIR}"
else
  python3 -m pip install modelscope
  python3 -m modelscope.cli.cli download --model Qwen/Qwen3-0.6B --local_dir "${MODEL_DIR}"
fi

echo "Model downloaded to: ${MODEL_DIR}"
