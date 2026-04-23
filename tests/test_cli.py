"""Tests for skillctl CLI command handlers."""

from __future__ import annotations

import json
import types
from pathlib import Path

import pytest

from skillctl.errors import SkillctlError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

VALID_SKILL_YAML = """\
apiVersion: skillctl.io/v1
kind: Skill

metadata:
  name: test-org/test-skill
  version: 1.0.0
  description: "A test skill"

spec:
  content:
    path: ./SKILL.md
  capabilities:
    - read_file
"""

VALID_SKILL_MD = """\
# Test Skill

## Instructions

Do something useful.
"""

INVALID_SKILL_YAML = """\
apiVersion: skillctl.io/v1
kind: Skill

metadata:
  name: BAD NAME
  version: not-semver
  description: ""

spec:
  content:
    path: ./SKILL.md
"""

SECRET_SKILL_MD = """\
# Dangerous Skill

## Instructions

Use this API key: AKIA1234567890ABCDEF
"""


def _make_skill_dir(tmp_path: Path, yaml_content: str = VALID_SKILL_YAML,
                    md_content: str = VALID_SKILL_MD) -> Path:
    """Create a skill directory with skill.yaml + SKILL.md."""
    (tmp_path / "skill.yaml").write_text(yaml_content)
    (tmp_path / "SKILL.md").write_text(md_content)
    return tmp_path


def _make_args(**kwargs) -> types.SimpleNamespace:
    """Build a fake argparse namespace."""
    defaults = {
        "path": ".",
        "file": None,
        "dry_run": False,
        "local": True,
        "registry_url": None,
        "token": None,
        "json": False,
        "strict": False,
    }
    defaults.update(kwargs)
    return types.SimpleNamespace(**defaults)


# ---------------------------------------------------------------------------
# cmd_create_skill
# ---------------------------------------------------------------------------

class TestCmdCreateSkill:

    def test_scaffolds_files(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        from skillctl.cli import cmd_create_skill
        args = _make_args(name="my-org/my-skill")
        cmd_create_skill(args)

        assert (tmp_path / "skill.yaml").exists()
        assert (tmp_path / "SKILL.md").exists()
        content = (tmp_path / "skill.yaml").read_text()
        assert "my-org/my-skill" in content
        assert "0.1.0" in content

    def test_refuses_overwrite(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "skill.yaml").write_text("existing")
        from skillctl.cli import cmd_create_skill
        args = _make_args(name="my-org/my-skill")
        with pytest.raises(SkillctlError) as exc_info:
            cmd_create_skill(args)
        assert exc_info.value.code == "E_FILE_EXISTS"


# ---------------------------------------------------------------------------
# cmd_validate
# ---------------------------------------------------------------------------

class TestCmdValidate:

    def test_valid_manifest_exits_0(self, tmp_path):
        _make_skill_dir(tmp_path)
        from skillctl.cli import cmd_validate
        args = _make_args(path=str(tmp_path))
        with pytest.raises(SystemExit) as exc_info:
            cmd_validate(args)
        assert exc_info.value.code == 0

    def test_invalid_manifest_exits_1(self, tmp_path):
        _make_skill_dir(tmp_path, yaml_content=INVALID_SKILL_YAML)
        from skillctl.cli import cmd_validate
        args = _make_args(path=str(tmp_path))
        with pytest.raises(SystemExit) as exc_info:
            cmd_validate(args)
        assert exc_info.value.code == 1

    def test_json_output(self, tmp_path, capsys):
        _make_skill_dir(tmp_path)
        from skillctl.cli import cmd_validate
        args = _make_args(path=str(tmp_path), json=True)
        with pytest.raises(SystemExit) as exc_info:
            cmd_validate(args)
        assert exc_info.value.code == 0
        output = json.loads(capsys.readouterr().out)
        assert output["valid"] is True
        assert output["errors"] == []

    def test_strict_mode_exits_1_on_warnings(self, tmp_path):
        yaml_with_unknown_cap = VALID_SKILL_YAML.replace(
            "    - read_file", "    - read_file\n    - bogus_cap"
        )
        _make_skill_dir(tmp_path, yaml_content=yaml_with_unknown_cap)
        from skillctl.cli import cmd_validate
        args = _make_args(path=str(tmp_path), strict=True)
        with pytest.raises(SystemExit) as exc_info:
            cmd_validate(args)
        assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# cmd_apply
# ---------------------------------------------------------------------------

class TestCmdApply:

    def test_apply_local_push(self, tmp_path, monkeypatch):
        _make_skill_dir(tmp_path)
        store_root = tmp_path / "store-root"
        monkeypatch.setattr("skillctl.cli.ContentStore",
                            lambda: __import__("skillctl.store", fromlist=["ContentStore"]).ContentStore(store_root))

        from skillctl.cli import cmd_apply
        args = _make_args(path=str(tmp_path), local=True)
        cmd_apply(args)

        assert (store_root / "index.json").exists()
        index = json.loads((store_root / "index.json").read_text())
        assert len(index) == 1
        assert index[0]["name"] == "test-org/test-skill"

    def test_apply_dry_run(self, tmp_path, monkeypatch, capsys):
        _make_skill_dir(tmp_path)
        store_root = tmp_path / "store-root"
        monkeypatch.setattr("skillctl.cli.ContentStore",
                            lambda: __import__("skillctl.store", fromlist=["ContentStore"]).ContentStore(store_root))

        from skillctl.cli import cmd_apply
        args = _make_args(path=str(tmp_path), local=True, dry_run=True)
        cmd_apply(args)

        assert not (store_root / "index.json").exists()
        output = capsys.readouterr().out
        assert "Dry run" in output

    def test_apply_invalid_manifest_exits_1(self, tmp_path):
        _make_skill_dir(tmp_path, yaml_content=INVALID_SKILL_YAML)
        from skillctl.cli import cmd_apply
        args = _make_args(path=str(tmp_path), local=True)
        with pytest.raises(SystemExit) as exc_info:
            cmd_apply(args)
        assert exc_info.value.code == 1

    def test_apply_duplicate_push_shows_unchanged(self, tmp_path, monkeypatch, capsys):
        _make_skill_dir(tmp_path)
        store_root = tmp_path / "store-root"
        monkeypatch.setattr("skillctl.cli.ContentStore",
                            lambda: __import__("skillctl.store", fromlist=["ContentStore"]).ContentStore(store_root))

        from skillctl.cli import cmd_apply
        args = _make_args(path=str(tmp_path), local=True)
        cmd_apply(args)
        capsys.readouterr()

        cmd_apply(args)
        output = capsys.readouterr().out
        assert "unchanged" in output

    def test_security_gate_blocks_publish_on_critical(self, tmp_path, monkeypatch, capsys):
        """When publishing remotely, CRITICAL findings block the publish."""
        _make_skill_dir(tmp_path, md_content=SECRET_SKILL_MD)
        store_root = tmp_path / "store-root"
        monkeypatch.setattr("skillctl.cli.ContentStore",
                            lambda: __import__("skillctl.store", fromlist=["ContentStore"]).ContentStore(store_root))
        monkeypatch.setattr("skillctl.cli._get_registry_url",
                            lambda args: "http://fake-registry:8080")

        from skillctl.cli import cmd_apply
        args = _make_args(path=str(tmp_path), local=False, registry_url="http://fake-registry:8080")
        cmd_apply(args)

        stderr = capsys.readouterr().err
        assert "Security gate" in stderr
        assert "CRITICAL" in stderr
        assert "blocked" in stderr.lower() or "publish blocked" in stderr.lower()
