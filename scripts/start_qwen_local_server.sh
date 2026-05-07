#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
MODEL_DIR="${ROOT_DIR}/models/Qwen/Qwen3-0.6B"
HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8000}"
CACHE_DIR="${ROOT_DIR}/.cache/huggingface"

if [ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ]; then
  cat <<EOF
Usage: bash scripts/start_qwen_local_server.sh

Starts the bundled local Qwen-0.6B service.

Environment overrides:
  HOST        Bind host. Default: 127.0.0.1
  PORT        Bind port. Default: 8000
  PYTHON_BIN  Python executable. Defaults to .venv/bin/python when present.
EOF
  exit 0
fi

if [ -x "${ROOT_DIR}/.venv/bin/python" ]; then
  PYTHON_BIN="${ROOT_DIR}/.venv/bin/python"
else
  PYTHON_BIN="${PYTHON_BIN:-python3}"
fi

if [ ! -d "${MODEL_DIR}" ]; then
  echo "Model directory not found: ${MODEL_DIR}" >&2
  echo "Run scripts/download_qwen3_0_6b.sh first." >&2
  exit 1
fi

mkdir -p "${CACHE_DIR}"

if ! "${PYTHON_BIN}" -c "import transformers, torch, fastapi, uvicorn" >/dev/null 2>&1; then
  echo "Required local server dependencies are missing." >&2
  echo "Please run scripts/setup_env.sh first." >&2
  exit 1
fi

HF_HOME="${CACHE_DIR}" TRANSFORMERS_CACHE="${CACHE_DIR}" "${PYTHON_BIN}" "${ROOT_DIR}/SRE/local_model_server.py" \
  --model-path "${MODEL_DIR}" \
  --model-name "Qwen/Qwen3-0.6B" \
  --host "${HOST}" \
  --port "${PORT}"
