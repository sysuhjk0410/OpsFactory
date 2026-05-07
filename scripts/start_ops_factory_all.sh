#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
MODEL_DIR="${ROOT_DIR}/models/Qwen/Qwen3-0.6B"
APP_DIR="${ROOT_DIR}/SRE"
MODEL_SESSION="${MODEL_SESSION:-ops_factory-model}"
APP_SESSION="${APP_SESSION:-ops_factory-app}"
MODEL_HOST="${MODEL_HOST:-127.0.0.1}"
MODEL_PORT="${MODEL_PORT:-8000}"
APP_PORT="${APP_PORT:-8080}"
WAIT_FOR_MODEL="${WAIT_FOR_MODEL:-1}"
if [ -x "${ROOT_DIR}/.venv/bin/python" ]; then
  PYTHON_BIN="${ROOT_DIR}/.venv/bin/python"
else
  PYTHON_BIN="${PYTHON_BIN:-python3}"
fi

need_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing required command: $1" >&2
    exit 1
  fi
}

check_model_ready() {
  [ -f "${MODEL_DIR}/config.json" ] \
    && [ -f "${MODEL_DIR}/tokenizer.json" ] \
    && [ -f "${MODEL_DIR}/generation_config.json" ] \
    && [ -f "${MODEL_DIR}/model.safetensors" ]
}

print_status() {
  echo "Ops Factory root: ${ROOT_DIR}"
  echo "Model dir: ${MODEL_DIR}"
  echo "Model endpoint: http://${MODEL_HOST}:${MODEL_PORT}/v1"
  echo "Dashboard: http://127.0.0.1:${APP_PORT}"
}

need_cmd tmux
need_cmd "${PYTHON_BIN}"

if [ ! -d "${MODEL_DIR}" ]; then
  echo "Model directory not found: ${MODEL_DIR}" >&2
  echo "Run ${ROOT_DIR}/scripts/download_qwen3_0_6b.sh first." >&2
  exit 1
fi

if ! "${PYTHON_BIN}" -c "import transformers, torch, fastapi, uvicorn" >/dev/null 2>&1; then
  echo "Local model server dependencies are missing." >&2
  echo "Please run scripts/setup_env.sh first, then rerun this script." >&2
  exit 1
fi

if ! check_model_ready; then
  if [ "${WAIT_FOR_MODEL}" = "1" ]; then
    echo "Model is still downloading. Waiting for model.safetensors to finish..."
    while ! check_model_ready; do
      sleep 10
    done
  else
    echo "Model files are incomplete in ${MODEL_DIR}" >&2
    exit 1
  fi
fi

if tmux has-session -t "${MODEL_SESSION}" 2>/dev/null; then
  tmux kill-session -t "${MODEL_SESSION}"
fi

if tmux has-session -t "${APP_SESSION}" 2>/dev/null; then
  tmux kill-session -t "${APP_SESSION}"
fi

tmux new-session -d -s "${MODEL_SESSION}" -c "${ROOT_DIR}"
tmux send-keys -t "${MODEL_SESSION}" "cd ${ROOT_DIR} && HOST=${MODEL_HOST} PORT=${MODEL_PORT} bash scripts/start_qwen_local_server.sh" C-m

echo "Waiting for local model server to open port ${MODEL_PORT}..."
for _ in $(seq 1 90); do
  if "${PYTHON_BIN}" - <<PY >/dev/null 2>&1
import socket
s = socket.socket()
s.settimeout(1)
try:
    s.connect(("${MODEL_HOST}", int("${MODEL_PORT}")))
    ok = True
except Exception:
    ok = False
finally:
    s.close()
raise SystemExit(0 if ok else 1)
PY
  then
    break
  fi
  sleep 2
done

tmux new-session -d -s "${APP_SESSION}" -c "${APP_DIR}"
tmux send-keys -t "${APP_SESSION}" "cd ${APP_DIR} && LLM_BASE_URL=http://${MODEL_HOST}:${MODEL_PORT}/v1 LLM_MODEL=Qwen/Qwen3-0.6B ${PYTHON_BIN} main.py web --port ${APP_PORT}" C-m

print_status
echo
echo "tmux sessions:"
echo "  model: ${MODEL_SESSION}"
echo "  app:   ${APP_SESSION}"
echo
echo "Useful commands:"
echo "  tmux attach -t ${MODEL_SESSION}"
echo "  tmux attach -t ${APP_SESSION}"
