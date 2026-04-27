"""Tests for the skillctl MCP server tool handlers."""

import json
import tempfile
from pathlib import Path

import pytest

from plugin.scripts.mcp_server import (
    skillctl_validate,
    skillctl_apply,
    skillctl_list,
    skillctl_describe,
    skillctl_delete,
    skillctl_diff,
    skillctl_create,
    skillctl_eval_audit,
    skillctl_optimize_history,
)


FIXTURES = Path(__file__).parent / "fixtures"


def _parse(result: str) -> dict:
    return json.loads(result)


# ---------------------------------------------------------------------------
# skillctl_validate
# ---------------------------------------------------------------------------


class TestValidate:
    def test_valid_manifest(self):
        result = _parse(skillctl_validate(str(FIXTURES / "valid_skill.yaml")))
        assert result["valid"] is True
        assert result["exit_code"] == 0
        assert result["errors"] == []
        assert result["skill_name"] == "test-org/valid-skill"
        assert result["version"] == "1.2.3"

    def test_invalid_manifest(self):
        result = _parse(skillctl_validate(str(FIXTURES / "invalid_skills" / "bad_semver.yaml")))
        assert result.get("valid") is False or "code" in result

    def test_nonexistent_path(self):
        result = _parse(skillctl_validate("/nonexistent/path/skill.yaml"))
        assert "code" in result or "error" in result

    def test_plain_markdown(self):
        result = _parse(skillctl_validate(str(FIXTURES / "plain_skill.md")))
        assert "skill_name" in result or "code" in result


# ---------------------------------------------------------------------------
# skillctl_list
# ---------------------------------------------------------------------------


class TestList:
    def test_list_returns_json(self):
        result = _parse(skillctl_list())
        assert "count" in result
        assert "skills" in result
        assert isinstance(result["skills"], list)

    def test_list_with_namespace(self):
        result = _parse(skillctl_list(namespace="nonexistent-ns"))
        assert result["count"] == 0

    def test_list_with_tag(self):
        result = _parse(skillctl_list(tag="nonexistent-tag"))
        assert result["count"] == 0


# ---------------------------------------------------------------------------
# skillctl_create
# ---------------------------------------------------------------------------


class TestCreate:
    def test_scaffold_creates_files(self, tmp_path):
        result = _parse(skillctl_create("test-ns/test-skill", "A test skill", target_dir=str(tmp_path)))
        assert result["success"] is True
        assert result["name"] == "test-ns/test-skill"

        skill_dir = tmp_path / "test-ns-test-skill"
        assert (skill_dir / "skill.yaml").exists()
        assert (skill_dir / "SKILL.md").exists()

    def test_scaffold_existing_dir_fails(self, tmp_path):
        (tmp_path / "test-ns-dupe").mkdir()
        result = _parse(skillctl_create("test-ns/dupe", target_dir=str(tmp_path)))
        assert result["success"] is False


# ---------------------------------------------------------------------------
# skillctl_eval_audit
# ---------------------------------------------------------------------------


class TestEvalAudit:
    def test_audit_returns_structured_report(self):
        skill_dir = FIXTURES.parent.parent / "plugin" / "skills" / "skill-lifecycle"
        result = _parse(skillctl_eval_audit(str(skill_dir)))
        assert "grade" in result
        assert "score" in result
        assert "findings" in result
        assert isinstance(result["findings"], list)

    def test_audit_nonexistent_path(self):
        result = _parse(skillctl_eval_audit("/nonexistent/skill"))
        assert "error" in result or "findings" in result


# ---------------------------------------------------------------------------
# skillctl_describe / skillctl_delete / skillctl_diff (store-dependent)
# ---------------------------------------------------------------------------


class TestStoreTools:
    def test_describe_bad_ref(self):
        result = _parse(skillctl_describe("no-at-sign"))
        assert "code" in result or "error" in result

    def test_delete_bad_ref(self):
        result = _parse(skillctl_delete("no-at-sign"))
        assert "code" in result or "error" in result

    def test_diff_bad_refs(self):
        result = _parse(skillctl_diff("bad-ref", "also-bad"))
        assert "code" in result or "error" in result

    def test_describe_not_found(self):
        result = _parse(skillctl_describe("no-org/no-skill@0.0.0"))
        assert result["code"] == "E_NOT_FOUND"

    def test_delete_not_found(self):
        result = _parse(skillctl_delete("no-org/no-skill@0.0.0"))
        assert result["code"] == "E_NOT_FOUND"


# ---------------------------------------------------------------------------
# skillctl_apply
# ---------------------------------------------------------------------------


class TestApply:
    def test_apply_dry_run(self, tmp_path):
        """Create a self-contained skill and dry-run apply it."""
        skill_dir = tmp_path / "my-skill"
        skill_dir.mkdir()
        (skill_dir / "skill.yaml").write_text(
            "apiVersion: skillctl.io/v1\n"
            "kind: Skill\n"
            "metadata:\n"
            "  name: test/apply-test\n"
            "  version: 0.1.0\n"
            "  description: test\n"
            "spec:\n"
            "  content:\n"
            "    inline: 'hello'\n"
        )
        result = _parse(skillctl_apply(str(skill_dir / "skill.yaml"), dry_run=True, local=True))
        assert result["success"] is True
        assert result["dry_run"] is True
        assert "hash" in result

    def test_apply_invalid_manifest(self):
        result = _parse(skillctl_apply(str(FIXTURES / "invalid_skills" / "bad_semver.yaml"), local=True))
        assert result.get("success") is False or "code" in result or "error" in result


# ---------------------------------------------------------------------------
# skillctl_optimize_history
# ---------------------------------------------------------------------------


class TestOptimizeHistory:
    def test_returns_list(self):
        result = _parse(skillctl_optimize_history())
        assert result["count"] == 0
        assert result["runs"] == []

    def test_with_skill_name_filter(self):
        result = _parse(skillctl_optimize_history(skill_name="nonexistent/skill"))
        assert result["count"] == 0


# ---------------------------------------------------------------------------
# All tools return valid JSON (smoke test)
# ---------------------------------------------------------------------------


class TestAllToolsReturnJSON:
    """Every tool should return valid JSON, never raise unhandled."""

    def test_validate_returns_json(self):
        json.loads(skillctl_validate("/nonexistent"))

    def test_list_returns_json(self):
        json.loads(skillctl_list())

    def test_describe_returns_json(self):
        json.loads(skillctl_describe("bad"))

    def test_delete_returns_json(self):
        json.loads(skillctl_delete("bad"))

    def test_diff_returns_json(self):
        json.loads(skillctl_diff("bad", "bad"))

    def test_create_returns_json(self, tmp_path):
        json.loads(skillctl_create("t/t", target_dir=str(tmp_path)))

    def test_eval_audit_returns_json(self):
        json.loads(skillctl_eval_audit("/nonexistent"))

    def test_optimize_history_returns_json(self):
        json.loads(skillctl_optimize_history())
