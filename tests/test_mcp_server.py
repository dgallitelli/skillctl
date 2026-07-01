"""Tests for the 5 core skillctl MCP governance tools.

Milestone 0 trimmed the server to: validate, audit, bump, diff, publish.
"""

import json
from pathlib import Path

from plugin.scripts.mcp_server import (
    validate,
    audit,
    bump,
    diff,
    publish,
)


FIXTURES = Path(__file__).parent / "fixtures"


def _parse(result: str) -> dict:
    return json.loads(result)


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------


class TestValidate:
    def test_valid_manifest(self):
        result = _parse(validate(str(FIXTURES / "valid_skill.yaml")))
        assert result["valid"] is True
        assert result["exit_code"] == 0
        assert result["errors"] == []
        assert result["skill_name"] == "test-org/valid-skill"
        assert result["version"] == "1.2.3"

    def test_invalid_manifest(self):
        result = _parse(validate(str(FIXTURES / "invalid_skills" / "bad_semver.yaml")))
        assert result.get("valid") is False or "code" in result

    def test_nonexistent_path(self):
        result = _parse(validate("/nonexistent/path/skill.yaml"))
        assert "code" in result or "error" in result

    def test_plain_markdown(self):
        result = _parse(validate(str(FIXTURES / "plain_skill.md")))
        assert "skill_name" in result or "code" in result

    def test_returns_json(self):
        json.loads(validate("/nonexistent"))


# ---------------------------------------------------------------------------
# audit
# ---------------------------------------------------------------------------


class TestAudit:
    def test_audit_returns_structured_report(self, tmp_path):
        skill_dir = tmp_path / "audit-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\nname: audit-skill\ndescription: a safe skill\n---\n\n# Audit Skill\n\nDo a thing.\n"
        )
        result = _parse(audit(str(skill_dir)))
        assert "grade" in result
        assert "score" in result
        assert "findings" in result
        assert isinstance(result["findings"], list)

    def test_audit_nonexistent_path(self):
        result = _parse(audit("/nonexistent/skill"))
        assert "error" in result or "findings" in result

    def test_returns_json(self):
        json.loads(audit("/nonexistent"))


# ---------------------------------------------------------------------------
# bump
# ---------------------------------------------------------------------------


def _write_skill_yaml(directory: Path, version: str = "1.0.0") -> Path:
    (directory / "skill.yaml").write_text(
        "apiVersion: skillctl.io/v1\n"
        "kind: Skill\n"
        "metadata:\n"
        "  name: test/bump-skill\n"
        f"  version: {version}\n"
        "  description: test\n"
        "spec:\n"
        "  content:\n"
        "    inline: 'hello'\n"
    )
    return directory / "skill.yaml"


class TestBump:
    def test_patch_bump(self, tmp_path):
        _write_skill_yaml(tmp_path, "1.2.3")
        result = _parse(bump(str(tmp_path)))
        assert result["success"] is True
        assert result["old_version"] == "1.2.3"
        assert result["new_version"] == "1.2.4"

    def test_minor_bump(self, tmp_path):
        _write_skill_yaml(tmp_path, "1.2.3")
        result = _parse(bump(str(tmp_path), level="minor"))
        assert result["new_version"] == "1.3.0"

    def test_major_bump(self, tmp_path):
        _write_skill_yaml(tmp_path, "1.2.3")
        result = _parse(bump(str(tmp_path), level="major"))
        assert result["new_version"] == "2.0.0"

    def test_invalid_level(self, tmp_path):
        _write_skill_yaml(tmp_path, "1.2.3")
        result = _parse(bump(str(tmp_path), level="bogus"))
        assert result["success"] is False

    def test_missing_manifest(self, tmp_path):
        result = _parse(bump(str(tmp_path)))
        assert result["success"] is False

    def test_returns_json(self):
        json.loads(bump("/nonexistent"))


# ---------------------------------------------------------------------------
# diff
# ---------------------------------------------------------------------------


class TestDiff:
    def test_diff_bad_refs(self):
        result = _parse(diff("bad-ref", "also-bad"))
        assert "code" in result or "error" in result

    def test_diff_not_found(self):
        result = _parse(diff("no-org/no-skill@0.0.0", "no-org/no-skill@0.0.1"))
        assert "code" in result or "error" in result

    def test_returns_json(self):
        json.loads(diff("bad", "bad"))


# ---------------------------------------------------------------------------
# publish
# ---------------------------------------------------------------------------


class TestPublish:
    def test_publish_dry_run(self, tmp_path):
        skill_dir = tmp_path / "my-skill"
        skill_dir.mkdir()
        (skill_dir / "skill.yaml").write_text(
            "apiVersion: skillctl.io/v1\n"
            "kind: Skill\n"
            "metadata:\n"
            "  name: test/publish-test\n"
            "  version: 0.1.0\n"
            "  description: test\n"
            "spec:\n"
            "  content:\n"
            "    inline: 'hello'\n"
        )
        result = _parse(publish(str(skill_dir / "skill.yaml"), dry_run=True, local=True))
        assert result["success"] is True
        assert result["dry_run"] is True
        assert "hash" in result

    def test_publish_invalid_manifest(self):
        result = _parse(publish(str(FIXTURES / "invalid_skills" / "bad_semver.yaml"), local=True))
        assert result.get("success") is False or "code" in result or "error" in result

    def test_returns_json(self, tmp_path):
        json.loads(publish(str(tmp_path / "nope.yaml"), local=True))
