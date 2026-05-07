#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
if [ -d "${SCRIPT_DIR}/SRE" ]; then
  ROOT_DIR="${SCRIPT_DIR}"
else
  ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
fi
APP_DIR="${ROOT_DIR}/SRE"
RUN_DIR="${ROOT_DIR}/.opsfactory/run"
LOG_DIR="${APP_DIR}/logs"
HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8080}"
BACKGROUND=0
TMUX_MODE=0
RESTART=0
STOP_ONLY=0
PYTHON_BIN="${PYTHON_BIN:-}"
TMUX_SESSION_WAS_SET=0
if [ -n "${TMUX_SESSION:-}" ]; then
  TMUX_SESSION_WAS_SET=1
fi
TMUX_SESSION="${TMUX_SESSION:-}"

usage() {
  cat <<'EOF'
Usage: bash scripts/start_opsfactory.sh [options]

Options:
  --host HOST       Bind host. Default: 0.0.0.0
  --port PORT       Bind port. Default: 8080
  --background      Start in background and write logs to SRE/logs/
  --tmux            Start in a detached tmux session; useful when background
                    processes are reaped by the desktop sandbox.
  --restart         Stop the recorded process and stale Ops Factory listener on the port before starting.
  --stop            Stop the background process recorded in the pid file.
  -h, --help        Show this help.

Examples:
  bash scripts/start_opsfactory.sh
  bash scripts/start_opsfactory.sh --background
  bash scripts/start_opsfactory.sh --tmux --restart --port 8080
  bash scripts/start_opsfactory.sh --restart --port 8080
  bash scripts/start_opsfactory.sh --stop --port 8080
EOF
}

log() { printf '\033[1;34m[Ops Factory]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[Ops Factory]\033[0m %s\n' "$*" >&2; }
die() { printf '\033[1;31m[Ops Factory]\033[0m %s\n' "$*" >&2; exit 1; }

while [ "$#" -gt 0 ]; do
  case "$1" in
    --host)
      [ "$#" -ge 2 ] || die "--host requires a value"
      HOST="$2"
      shift
      ;;
    --port)
      [ "$#" -ge 2 ] || die "--port requires a value"
      PORT="$2"
      if [ "${TMUX_SESSION_WAS_SET}" = "0" ]; then
        TMUX_SESSION="opsfactory-${PORT}"
      fi
      shift
      ;;
    --background) BACKGROUND=1 ;;
    --tmux) TMUX_MODE=1 ;;
    --restart) RESTART=1 ;;
    --stop) STOP_ONLY=1 ;;
    -h|--help) usage; exit 0 ;;
    *) die "Unknown option: $1" ;;
  esac
  shift
done

PID_FILE="${RUN_DIR}/opsfactory-${PORT}.pid"
LOG_FILE="${LOG_DIR}/opsfactory-${PORT}.log"
TMUX_SESSION="${TMUX_SESSION:-opsfactory-${PORT}}"

find_python() {
  if [ -n "${PYTHON_BIN}" ]; then
    command -v "${PYTHON_BIN}" >/dev/null 2>&1 || die "PYTHON_BIN not found: ${PYTHON_BIN}"
    command -v "${PYTHON_BIN}"
    return
  fi
  if [ -x "${ROOT_DIR}/.venv/bin/python" ]; then
    printf '%s\n' "${ROOT_DIR}/.venv/bin/python"
    return
  fi
  for candidate in python3.12 python3.11 python3.10 python3 python; do
    if command -v "${candidate}" >/dev/null 2>&1; then
      command -v "${candidate}"
      return
    fi
  done
  die "No usable Python found. Run: bash scripts/setup_opsfactory_env.sh"
}

is_running() {
  local pid="$1"
  [ -n "${pid}" ] && kill -0 "${pid}" >/dev/null 2>&1
}

stop_existing() {
  if [ ! -f "${PID_FILE}" ]; then
    warn "No pid file found for port ${PORT}: ${PID_FILE}"
    return 0
  fi
  local pid
  pid="$(cat "${PID_FILE}")"
  if is_running "${pid}"; then
    log "Stopping Ops Factory process ${pid}"
    kill "${pid}"
    local waited=0
    while is_running "${pid}" && [ "${waited}" -lt 15 ]; do
      sleep 1
      waited=$((waited + 1))
    done
    if is_running "${pid}"; then
      warn "Process ${pid} did not exit gracefully; sending SIGKILL"
      kill -9 "${pid}" || true
    fi
  else
    warn "Recorded process is not running: ${pid}"
  fi
  rm -f "${PID_FILE}"
}

port_listener_pids() {
  command -v lsof >/dev/null 2>&1 || return 0
  lsof -nP -tiTCP:"${PORT}" -sTCP:LISTEN 2>/dev/null || true
}

pid_cwd() {
  local pid="$1"
  command -v lsof >/dev/null 2>&1 || return 0
  lsof -a -p "${pid}" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' | head -n 1
}

stop_port_listeners() {
  local pids pid cwd
  pids="$(port_listener_pids)"
  [ -n "${pids}" ] || return 0
  for pid in ${pids}; do
    cwd="$(pid_cwd "${pid}")"
    case "${cwd}" in
      "${ROOT_DIR}"*|"${APP_DIR}"*)
        log "Stopping stale Ops Factory listener ${pid} on port ${PORT}"
        kill "${pid}" || true
        local waited=0
        while is_running "${pid}" && [ "${waited}" -lt 15 ]; do
          sleep 1
          waited=$((waited + 1))
        done
        if is_running "${pid}"; then
          warn "Listener ${pid} did not exit gracefully; sending SIGKILL"
          kill -9 "${pid}" || true
        fi
        ;;
      "")
        warn "Port ${PORT} is in use by process ${pid}, but its working directory could not be read."
        ;;
      *)
        warn "Port ${PORT} is in use by process ${pid} from ${cwd}; leaving it untouched."
        ;;
    esac
  done
}

validate_python_deps() {
  "${PYTHON_BIN}" - <<'PY'
missing = []
for module in ("fastapi", "uvicorn", "torch", "transformers"):
    try:
        __import__(module)
    except Exception:
        missing.append(module)
if missing:
    print("missing:" + ",".join(missing))
    raise SystemExit(1)
PY
}

prepare_env() {
  mkdir -p "${RUN_DIR}" "${LOG_DIR}"
  if [ -f "${ROOT_DIR}/.opsfactory/env.sh" ]; then
    # shellcheck disable=SC1091
    source "${ROOT_DIR}/.opsfactory/env.sh"
  fi
  if [ -x "${ROOT_DIR}/.opsfactory/bin/kubectl" ]; then
    export PATH="${ROOT_DIR}/.opsfactory/bin:${PATH}"
    export OPSFACTORY_KUBECTL="${ROOT_DIR}/.opsfactory/bin/kubectl"
  fi
  export PYTHONPATH="${APP_DIR}:${PYTHONPATH:-}"
}

print_start_summary() {
  cat <<EOF

Ops Factory is starting.

Dashboard:
  http://127.0.0.1:${PORT}

Remote access on another machine:
  http://<server-ip>:${PORT}

Kubernetes access:
  KUBECONFIG=${KUBECONFIG:-<not set>}
  OPSFACTORY_KUBECTL=${OPSFACTORY_KUBECTL:-<system kubectl>}

EOF
}

cd "${APP_DIR}"
prepare_env

if [ "${STOP_ONLY}" = "1" ]; then
  if [ "${TMUX_MODE}" = "1" ] && command -v tmux >/dev/null 2>&1; then
    if tmux has-session -t "${TMUX_SESSION}" >/dev/null 2>&1; then
      log "Stopping tmux session ${TMUX_SESSION}"
      tmux kill-session -t "${TMUX_SESSION}"
    fi
  fi
  stop_existing
  exit 0
fi

if [ ! -x "${ROOT_DIR}/.venv/bin/python" ] && [ -x "${ROOT_DIR}/setup_opsfactory_env.sh" ]; then
  log "Runtime environment is missing. Running one-click setup first."
  bash "${ROOT_DIR}/setup_opsfactory_env.sh"
fi

PYTHON_BIN="$(find_python)"

if [ "${TMUX_MODE}" = "1" ] && [ "${OPSFACTORY_IN_TMUX:-0}" != "1" ]; then
  command -v tmux >/dev/null 2>&1 || die "tmux is required for --tmux mode"
  if [ "${RESTART}" = "1" ] && tmux has-session -t "${TMUX_SESSION}" >/dev/null 2>&1; then
    log "Stopping existing tmux session ${TMUX_SESSION}"
    tmux kill-session -t "${TMUX_SESSION}"
  fi
  if tmux has-session -t "${TMUX_SESSION}" >/dev/null 2>&1; then
    log "tmux session ${TMUX_SESSION} is already running."
    log "Dashboard: http://127.0.0.1:${PORT}"
    exit 0
  fi
  tmux_args=(env OPSFACTORY_IN_TMUX=1 bash start_opsfactory.sh --host "${HOST}" --port "${PORT}")
  printf -v tmux_cmd '%q ' "${tmux_args[@]}"
  log "Starting tmux session ${TMUX_SESSION}. Attach with: tmux attach -t ${TMUX_SESSION}"
  tmux new-session -d -s "${TMUX_SESSION}" -c "${ROOT_DIR}" "${tmux_cmd}"
  log "Dashboard: http://127.0.0.1:${PORT}"
  exit 0
fi

if [ "${RESTART}" = "1" ] && [ -f "${PID_FILE}" ]; then
  stop_existing
fi

if [ "${RESTART}" = "1" ]; then
  stop_port_listeners
elif [ -n "$(port_listener_pids)" ]; then
  die "Port ${PORT} is already in use. Rerun with --restart to replace a stale Ops Factory process."
fi

if ! validate_python_deps >/tmp/opsfactory-deps-check.txt 2>&1; then
  cat /tmp/opsfactory-deps-check.txt >&2
  if [ -x "${ROOT_DIR}/setup_opsfactory_env.sh" ]; then
    log "Missing Python dependencies. Running one-click setup."
    bash "${ROOT_DIR}/setup_opsfactory_env.sh"
    PYTHON_BIN="$(find_python)"
    validate_python_deps >/tmp/opsfactory-deps-check.txt 2>&1 || {
      cat /tmp/opsfactory-deps-check.txt >&2
      die "Dependencies are still missing after setup."
    }
  else
    die "Missing Python dependencies. Run: bash scripts/setup_opsfactory_env.sh"
  fi
fi

print_start_summary
CMD=("${PYTHON_BIN}" -m uvicorn web_app.app:app --host "${HOST}" --port "${PORT}")

if [ "${BACKGROUND}" = "1" ]; then
  log "Starting in background. Logs: ${LOG_FILE}"
  nohup "${CMD[@]}" >"${LOG_FILE}" 2>&1 &
  printf '%s\n' "$!" > "${PID_FILE}"
  log "Started process $(cat "${PID_FILE}")"
else
  if [ "${OPSFACTORY_IN_TMUX:-0}" = "1" ]; then
    printf '%s\n' "$$" > "${PID_FILE}"
  fi
  exec "${CMD[@]}"
fi
