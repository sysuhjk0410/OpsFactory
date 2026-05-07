#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
if [ -d "${SCRIPT_DIR}/SRE" ]; then
  ROOT_DIR="${SCRIPT_DIR}"
else
  ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
fi
APP_DIR="${ROOT_DIR}/SRE"
OPS_DIR="${ROOT_DIR}/.opsfactory"
BIN_DIR="${OPS_DIR}/bin"
VENV_DIR="${ROOT_DIR}/.venv"
MODEL_DIR="${ROOT_DIR}/models/Qwen/Qwen3-0.6B"
KIND_CLUSTER_NAME="${KIND_CLUSTER_NAME:-opsfactory}"
KIND_VERSION="${KIND_VERSION:-v0.23.0}"
KUBECTL_VERSION="${KUBECTL_VERSION:-stable}"
INSTALL_K8S_TOOLS=1
CREATE_KIND_CLUSTER=0
BOOTSTRAP_PLATFORMS=0
INSTALL_FULL_STACK=0
PYTHON_BIN="${PYTHON_BIN:-}"

usage() {
  cat <<'EOF'
Usage: bash scripts/setup_opsfactory_env.sh [options]

Options:
  --with-kind             Create a local kind Kubernetes cluster. Requires Docker.
  --bootstrap-platforms   Deploy local Sock-Shop / Online-Shop / Train-Ticket placeholder platforms.
                          Implies --with-kind.
  --full                  Install the heavier unified dependency set for OpsAug/PromCopilot research modules.
  --skip-k8s-tools        Do not install local kubectl/kind binaries.
  -h, --help              Show this help.

Environment overrides:
  PYTHON_BIN              Python executable to use.
  KUBECTL_VERSION         kubectl version, e.g. v1.31.4. Default: stable.
  KIND_VERSION            kind version. Default: v0.23.0.
  KIND_CLUSTER_NAME       kind cluster name. Default: opsfactory.
EOF
}

log() { printf '\033[1;34m[Ops Factory]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[Ops Factory]\033[0m %s\n' "$*" >&2; }
die() { printf '\033[1;31m[Ops Factory]\033[0m %s\n' "$*" >&2; exit 1; }

while [ "$#" -gt 0 ]; do
  case "$1" in
    --with-kind) CREATE_KIND_CLUSTER=1 ;;
    --bootstrap-platforms) CREATE_KIND_CLUSTER=1; BOOTSTRAP_PLATFORMS=1 ;;
    --full) INSTALL_FULL_STACK=1 ;;
    --skip-k8s-tools) INSTALL_K8S_TOOLS=0 ;;
    -h|--help) usage; exit 0 ;;
    *) die "Unknown option: $1" ;;
  esac
  shift
done

find_python() {
  if [ -n "${PYTHON_BIN}" ]; then
    command -v "${PYTHON_BIN}" >/dev/null 2>&1 || die "PYTHON_BIN not found: ${PYTHON_BIN}"
    command -v "${PYTHON_BIN}"
    return
  fi
  if [ -x "${OPS_DIR}/python/bin/python" ]; then
    printf '%s\n' "${OPS_DIR}/python/bin/python"
    return
  fi
  for candidate in python3.12 python3.11 python3.10 python3; do
    if command -v "${candidate}" >/dev/null 2>&1; then
      command -v "${candidate}"
      return
    fi
  done
  bootstrap_python
  printf '%s\n' "${OPS_DIR}/python/bin/python"
}

download_file() {
  local url="$1"
  local out="$2"
  if command -v curl >/dev/null 2>&1; then
    curl -fsSL "$url" -o "$out"
  elif command -v wget >/dev/null 2>&1; then
    wget -q "$url" -O "$out"
  else
    [ -n "${PYTHON_BIN}" ] || die "Need curl, wget, or Python to download ${url}"
    "${PYTHON_BIN}" - "$url" "$out" <<'PY'
import sys
from urllib.request import urlopen
url, out = sys.argv[1], sys.argv[2]
with urlopen(url, timeout=60) as response:
    data = response.read()
with open(out, "wb") as fh:
    fh.write(data)
PY
  fi
}

bootstrap_python() {
  mkdir -p "${OPS_DIR}/downloads"
  local os arch installer url out
  os="$(platform_os)"
  arch="$(platform_arch)"
  case "${os}-${arch}" in
    darwin-arm64) installer="Miniforge3-MacOSX-arm64.sh" ;;
    darwin-amd64) installer="Miniforge3-MacOSX-x86_64.sh" ;;
    linux-arm64) installer="Miniforge3-Linux-aarch64.sh" ;;
    linux-amd64) installer="Miniforge3-Linux-x86_64.sh" ;;
    *) die "No bundled Python installer mapping for ${os}-${arch}" ;;
  esac
  url="https://github.com/conda-forge/miniforge/releases/latest/download/${installer}"
  out="${OPS_DIR}/downloads/${installer}"
  log "No Python 3.10+ found. Installing private Python runtime into ${OPS_DIR}/python"
  download_file "${url}" "${out}"
  bash "${out}" -b -p "${OPS_DIR}/python"
  [ -x "${OPS_DIR}/python/bin/python" ] || die "Python bootstrap failed."
}

platform_os() {
  case "$(uname -s)" in
    Darwin) printf 'darwin' ;;
    Linux) printf 'linux' ;;
    *) die "Unsupported OS: $(uname -s). Use Linux/macOS or WSL." ;;
  esac
}

platform_arch() {
  case "$(uname -m)" in
    x86_64|amd64) printf 'amd64' ;;
    arm64|aarch64) printf 'arm64' ;;
    *) die "Unsupported CPU architecture: $(uname -m)" ;;
  esac
}

resolve_kubectl_version() {
  if [ "${KUBECTL_VERSION}" != "stable" ]; then
    printf '%s' "${KUBECTL_VERSION}"
    return
  fi
  if command -v curl >/dev/null 2>&1; then
    curl -fsSL https://dl.k8s.io/release/stable.txt
  else
    "${PYTHON_BIN}" - <<'PY'
from urllib.request import urlopen
print(urlopen("https://dl.k8s.io/release/stable.txt", timeout=30).read().decode().strip())
PY
  fi
}

install_python_env() {
  PYTHON_BIN="$(find_python)"
  log "Using Python: ${PYTHON_BIN}"
  if ! "${PYTHON_BIN}" - <<'PY'
import sys
raise SystemExit(0 if sys.version_info >= (3, 10) else 1)
PY
  then
    warn "Python is older than 3.10: ${PYTHON_BIN}"
    PYTHON_BIN=""
    bootstrap_python
    PYTHON_BIN="${OPS_DIR}/python/bin/python"
  fi

  if [ ! -d "${VENV_DIR}" ]; then
    log "Creating virtual environment: ${VENV_DIR}"
    "${PYTHON_BIN}" -m venv "${VENV_DIR}" || die "Failed to create venv. On Debian/Ubuntu install python3-venv."
  fi

  # shellcheck disable=SC1091
  source "${VENV_DIR}/bin/activate"
  python -m ensurepip --upgrade >/dev/null 2>&1 || true
  python -m pip install --upgrade pip setuptools wheel

  log "Installing Ops Factory dashboard dependencies"
  python -m pip install -r "${APP_DIR}/requirements.txt"
  if [ "${INSTALL_FULL_STACK}" = "1" ]; then
    log "Installing full unified stack dependencies. This can take a while."
    python -m pip install -r "${ROOT_DIR}/requirements-unified.txt"
  fi
}

ensure_local_model_files() {
  if [ -f "${MODEL_DIR}/model.safetensors" ] && [ -f "${MODEL_DIR}/tokenizer.json" ]; then
    log "Local Qwen-0.6B model already exists: ${MODEL_DIR}"
    return 0
  fi
  # shellcheck disable=SC1091
  source "${VENV_DIR}/bin/activate"
  mkdir -p "${MODEL_DIR}"
  log "Downloading local Qwen-0.6B model into ${MODEL_DIR}"
  if command -v modelscope >/dev/null 2>&1; then
    modelscope download --model Qwen/Qwen3-0.6B --local_dir "${MODEL_DIR}"
  else
    python -m modelscope.cli.cli download --model Qwen/Qwen3-0.6B --local_dir "${MODEL_DIR}"
  fi
}

install_k8s_tools() {
  [ "${INSTALL_K8S_TOOLS}" = "1" ] || return 0
  mkdir -p "${BIN_DIR}"
  local os arch kubever kubectl_url kind_url
  os="$(platform_os)"
  arch="$(platform_arch)"

  if [ ! -x "${BIN_DIR}/kubectl" ]; then
    kubever="$(resolve_kubectl_version)"
    kubectl_url="https://dl.k8s.io/release/${kubever}/bin/${os}/${arch}/kubectl"
    log "Installing kubectl ${kubever} into ${BIN_DIR}"
    download_file "${kubectl_url}" "${BIN_DIR}/kubectl"
    chmod +x "${BIN_DIR}/kubectl"
  else
    log "kubectl already exists: ${BIN_DIR}/kubectl"
  fi

  if [ ! -x "${BIN_DIR}/kind" ]; then
    kind_url="https://kind.sigs.k8s.io/dl/${KIND_VERSION}/kind-${os}-${arch}"
    log "Installing kind ${KIND_VERSION} into ${BIN_DIR}"
    download_file "${kind_url}" "${BIN_DIR}/kind"
    chmod +x "${BIN_DIR}/kind"
  else
    log "kind already exists: ${BIN_DIR}/kind"
  fi

  cat > "${OPS_DIR}/env.sh" <<EOF
export PATH="${BIN_DIR}:\$PATH"
export OPSFACTORY_KUBECTL="${BIN_DIR}/kubectl"
EOF
}

create_env_file() {
  if [ ! -f "${APP_DIR}/.env" ] && [ -f "${APP_DIR}/.env.example" ]; then
    log "Creating ${APP_DIR}/.env from .env.example"
    cp "${APP_DIR}/.env.example" "${APP_DIR}/.env"
  fi
}

create_kind_cluster() {
  [ "${CREATE_KIND_CLUSTER}" = "1" ] || return 0
  export PATH="${BIN_DIR}:$PATH"
  command -v docker >/dev/null 2>&1 || die "Docker is required for kind. Install Docker Desktop/Engine, then rerun this script."
  docker info >/dev/null 2>&1 || die "Docker is installed but not running."
  command -v kind >/dev/null 2>&1 || die "kind not found in PATH."
  command -v kubectl >/dev/null 2>&1 || die "kubectl not found in PATH."

  if kind get clusters 2>/dev/null | grep -qx "${KIND_CLUSTER_NAME}"; then
    log "kind cluster already exists: ${KIND_CLUSTER_NAME}"
  else
    log "Creating kind cluster: ${KIND_CLUSTER_NAME}"
    kind create cluster --name "${KIND_CLUSTER_NAME}"
  fi
  kubectl config use-context "kind-${KIND_CLUSTER_NAME}"
  kubectl cluster-info

  if [ "${BOOTSTRAP_PLATFORMS}" = "1" ]; then
    log "Bootstrapping local dynamic demo platforms"
    bash "${APP_DIR}/deploy/bootstrap_local_platforms.sh"
  fi
}

print_summary() {
  cat <<EOF

Ops Factory environment is ready.

Next:
  source "${OPS_DIR}/env.sh"
  bash scripts/start_opsfactory.sh

Virtualenv:
  ${VENV_DIR}

Local tools:
  ${BIN_DIR}

Dashboard:
  http://127.0.0.1:8080

EOF
}

cd "${ROOT_DIR}"
mkdir -p "${OPS_DIR}" "${BIN_DIR}"
install_python_env
ensure_local_model_files
install_k8s_tools
create_env_file
create_kind_cluster
print_summary
