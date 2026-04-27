#!/usr/bin/env bash
# Launcher for the skillctl MCP server.
# Finds the right Python: venv in the repo (development) or system python3 (installed).
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

if [ -x "$REPO_ROOT/.venv/bin/python" ]; then
    exec "$REPO_ROOT/.venv/bin/python" "$SCRIPT_DIR/mcp_server.py" "$@"
else
    exec python3 "$SCRIPT_DIR/mcp_server.py" "$@"
fi
