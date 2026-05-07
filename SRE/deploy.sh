#!/bin/bash
# ═══════════════════════════════════════════
# SRE — Deploy to K8S Cluster Node
# Uploads code, installs deps, starts web dashboard
# ═══════════════════════════════════════════
set -e

# ── Config ──
JUMP_HOST="openstack@222.200.180.102"
TARGET="ubuntu@10.10.3.110"
REMOTE_DIR="/home/ubuntu/SRE"
SSH_CMD="ssh -J ${JUMP_HOST} ${TARGET}"
SCP_CMD="scp -o ProxyJump=${JUMP_HOST}"
LOCAL_PORT=8080
REMOTE_PORT=8080

echo "🚀 SRE Cluster Deployment"
echo "   Target: ${TARGET} (via ${JUMP_HOST})"
echo ""

# ── Step 1: Create remote directory ──
echo "📁 Step 1: Preparing remote directory..."
${SSH_CMD} "mkdir -p ${REMOTE_DIR}"

# ── Step 2: Upload code ──
echo "📦 Step 2: Uploading code to cluster..."
# Use rsync if available, fallback to tar+scp
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Create a tar excluding unnecessary files
cd "${SCRIPT_DIR}"
tar czf /tmp/sre.tar.gz \
    --exclude='.venv' \
    --exclude='__pycache__' \
    --exclude='.git' \
    --exclude='data' \
    --exclude='*.pyc' \
    --exclude='.env' \
    .

${SCP_CMD} /tmp/sre.tar.gz ${TARGET}:/tmp/sre.tar.gz
${SSH_CMD} "cd ${REMOTE_DIR} && tar xzf /tmp/sre.tar.gz && rm /tmp/sre.tar.gz"
rm /tmp/sre.tar.gz
echo "   ✅ Code uploaded"

# ── Step 3: Use cluster config ──
echo "🔧 Step 3: Applying cluster configuration..."
${SSH_CMD} "cd ${REMOTE_DIR} && cp configs/config_cluster.yaml configs/config.yaml"

# ── Step 4: Install dependencies ──
echo "📥 Step 4: Installing Python dependencies..."
${SSH_CMD} "cd ${REMOTE_DIR} && python3 -m venv .venv 2>/dev/null || true && source .venv/bin/activate && pip install --upgrade pip -q && pip install pyyaml python-dotenv pydantic fastapi uvicorn jinja2 aiofiles numpy requests 2>&1 | tail -5"
echo "   ✅ Dependencies installed"

# ── Step 5: Optional runtime overrides ──
echo "🔑 Step 5: Setting up optional environment overrides..."
if [ -f "${SCRIPT_DIR}/.env" ]; then
    ${SCP_CMD} "${SCRIPT_DIR}/.env" ${TARGET}:${REMOTE_DIR}/.env
    echo "   ✅ .env file uploaded"
else
    echo "   ✅ No .env uploaded; default local Qwen-0.6B settings will be used"
fi

# ── Step 6: Create data directories ──
${SSH_CMD} "mkdir -p ${REMOTE_DIR}/data/memory ${REMOTE_DIR}/logs"

# ── Step 7: Start web app ──
echo "🌐 Step 6: Starting web dashboard on cluster..."
${SSH_CMD} "cd ${REMOTE_DIR} && source .venv/bin/activate && \
    export PYTHONPATH=${REMOTE_DIR}:\$PYTHONPATH && \
    pkill -f 'uvicorn web_app.app:app' 2>/dev/null || true && \
    sleep 1 && \
    nohup python -m uvicorn web_app.app:app --host 0.0.0.0 --port ${REMOTE_PORT} \
    > logs/web.log 2>&1 &"

echo "   ✅ Web dashboard started on cluster port ${REMOTE_PORT}"

# ── Step 8: Setup SSH tunnel ──
echo ""
echo "═══════════════════════════════════════════"
echo "✅ Deployment complete!"
echo ""
echo "📡 Starting SSH tunnel: localhost:${LOCAL_PORT} → cluster:${REMOTE_PORT}"
echo "   Access dashboard at: http://localhost:${LOCAL_PORT}"
echo "   Press Ctrl+C to stop the tunnel"
echo "═══════════════════════════════════════════"
echo ""

# SSH tunnel (foreground, Ctrl+C to stop)
ssh -J ${JUMP_HOST} -N -L ${LOCAL_PORT}:localhost:${REMOTE_PORT} ${TARGET}
