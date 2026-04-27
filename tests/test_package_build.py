"""Package build tests — verify pip install produces a working CLI."""

import subprocess
import sys


class TestPackageBuild:
    def test_package_installs_cleanly(self, tmp_path):
        r = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--dry-run", "-e", "."],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert r.returncode == 0

    def test_entry_point_resolves(self):
        r = subprocess.run(
            [sys.executable, "-c", "from skillctl.cli import main; print('ok')"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert r.returncode == 0
        assert "ok" in r.stdout

    def test_all_core_modules_importable(self):
        modules = [
            "skillctl.cli",
            "skillctl.config",
            "skillctl.diff",
            "skillctl.errors",
            "skillctl.manifest",
            "skillctl.store",
            "skillctl.utils",
            "skillctl.validator",
            "skillctl.version",
            "skillctl.eval.cli",
            "skillctl.eval.schemas",
        ]
        for mod in modules:
            r = subprocess.run(
                [sys.executable, "-c", f"import {mod}"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            assert r.returncode == 0, f"Failed to import {mod}: {r.stderr}"

    def test_optional_module_mcp_server(self):
        r = subprocess.run(
            [sys.executable, "-c", "from plugin.scripts.mcp_server import mcp; print('ok')"],
            capture_output=True,
            text=True,
            timeout=10,
            env={**__import__("os").environ, "PYTHONPATH": "."},
        )
        assert r.returncode == 0
