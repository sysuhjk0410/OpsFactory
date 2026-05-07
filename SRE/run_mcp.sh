#!/bin/bash
# SRE MCP Server Launcher
set -e

cd "$(dirname "$0")"
export PYTHONPATH="$(pwd):$PYTHONPATH"

echo "🔌 Starting SRE MCP Server (stdio)..."
python mcp_server.py
