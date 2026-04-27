"""Plugin structure validation — catches broken plugin before it reaches users."""

import json
import subprocess
import sys
from pathlib import Path

import yaml

PLUGIN_ROOT = Path(__file__).parent.parent / "plugin"


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
        assert "version" in data


class TestPluginSkills:
    def _skill_dirs(self):
        skills_dir = PLUGIN_ROOT / "skills"
        return [d for d in skills_dir.iterdir() if d.is_dir()]

    def test_skills_directory_exists(self):
        assert (PLUGIN_ROOT / "skills").is_dir()

    def test_each_skill_has_skill_md(self):
        for skill_dir in self._skill_dirs():
            assert (skill_dir / "SKILL.md").exists(), f"{skill_dir.name} missing SKILL.md"

    def test_each_skill_has_frontmatter(self):
        for skill_dir in self._skill_dirs():
            content = (skill_dir / "SKILL.md").read_text()
            assert content.startswith("---"), f"{skill_dir.name} SKILL.md missing frontmatter"
            end = content.index("---", 3)
            fm = yaml.safe_load(content[3:end])
            assert "description" in fm, f"{skill_dir.name} frontmatter missing description"

    def test_skill_count(self):
        assert len(self._skill_dirs()) >= 3


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

    def test_mcp_server_lists_all_tools(self):
        messages = "\n".join(
            [
                json.dumps(
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
                ),
                json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
                json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}),
            ]
        )
        r = subprocess.run(
            [sys.executable, str(PLUGIN_ROOT / "scripts" / "mcp_server.py")],
            input=messages + "\n",
            capture_output=True,
            text=True,
            timeout=10,
            env={**__import__("os").environ, "PYTHONPATH": str(PLUGIN_ROOT.parent)},
        )
        for line in r.stdout.strip().split("\n"):
            msg = json.loads(line)
            if msg.get("id") == 2:
                tool_names = [t["name"] for t in msg["result"]["tools"]]
                assert len(tool_names) == 14
                assert "skillctl_validate" in tool_names
                assert "skillctl_optimize" in tool_names
                break
        else:
            raise AssertionError("tools/list response not found")
