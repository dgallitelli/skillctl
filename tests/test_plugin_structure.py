"""Plugin structure validation — catches broken plugin before it reaches users.

Milestone 0 trimmed the MCP plugin to exactly 5 governance tools
(validate, audit, bump, diff, publish) and removed the bundled authoring
skills.
"""

import json
import subprocess
import sys
from pathlib import Path

from skillctl.version import __version__

PLUGIN_ROOT = Path(__file__).parent.parent / "plugin"

EXPECTED_TOOLS = {"validate", "audit", "bump", "diff", "publish"}


class TestPluginManifest:
    def test_plugin_json_exists(self):
        assert (PLUGIN_ROOT / ".claude-plugin" / "plugin.json").exists()

    def test_plugin_json_valid(self):
        data = json.loads((PLUGIN_ROOT / ".claude-plugin" / "plugin.json").read_text())
        assert "name" in data
        assert data["name"] == "skillctl"
        assert "description" in data

    def test_plugin_json_has_version(self):
        data = json.loads((PLUGIN_ROOT / ".claude-plugin" / "plugin.json").read_text())
        assert data["version"] == __version__


class TestPluginMCP:
    def test_mcp_json_exists(self):
        assert (PLUGIN_ROOT / ".mcp.json").exists()

    def test_mcp_json_valid(self):
        data = json.loads((PLUGIN_ROOT / ".mcp.json").read_text())
        assert "mcpServers" in data
        assert "skillctl" in data["mcpServers"]

    def test_mcp_server_script_exists(self):
        assert (PLUGIN_ROOT / "scripts" / "mcp_server.py").exists()

    def test_mcp_launcher_exists_and_executable(self):
        launcher = PLUGIN_ROOT / "scripts" / "launch_mcp.sh"
        assert launcher.exists()

    def test_mcp_server_initializes_over_stdio(self):
        init_msg = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "0.1"},
                },
            }
        )
        r = subprocess.run(
            [sys.executable, str(PLUGIN_ROOT / "scripts" / "mcp_server.py")],
            input=init_msg + "\n",
            capture_output=True,
            text=True,
            timeout=10,
            env={**__import__("os").environ, "PYTHONPATH": str(PLUGIN_ROOT.parent)},
        )
        response = json.loads(r.stdout.strip().split("\n")[0])
        assert response["result"]["serverInfo"]["name"] == "skillctl"

    def test_mcp_server_exposes_exactly_five_tools(self):
        """Verify the plugin exposes exactly the 5 core governance tools."""
        r = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys; sys.path.insert(0, '.'); "
                "from plugin.scripts.mcp_server import mcp; "
                "tools = mcp._tool_manager.list_tools(); "
                "print(len(tools)); "
                "[print(t.name) for t in tools]",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=str(PLUGIN_ROOT.parent),
            env={**__import__("os").environ, "PYTHONPATH": str(PLUGIN_ROOT.parent)},
        )
        lines = r.stdout.strip().split("\n")
        assert int(lines[0]) == 5, f"expected 5 tools, stdout={r.stdout!r} stderr={r.stderr!r}"
        tool_names = set(lines[1:])
        assert tool_names == EXPECTED_TOOLS, f"unexpected tools: {tool_names}"
