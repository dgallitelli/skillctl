"""Tests for the skill install module."""

import hashlib
from pathlib import Path

from skillctl.install import (
    TARGETS,
    InstallRecord,
    InstallationTracker,
    detect_targets,
    format_for_claude,
    format_for_copilot,
    format_for_cursor,
    format_for_kiro,
    format_for_windsurf,
)


# ---------------------------------------------------------------------------
# Task 1: Data types and installation tracker
# ---------------------------------------------------------------------------


class TestInstallRecord:
    def test_to_dict(self):
        rec = InstallRecord(
            path="/tmp/.claude/skills/my-skill/SKILL.md",
            scope="project",
            installed_at="2026-04-27T15:00:00Z",
            content_hash="abc123",
        )
        d = rec.to_dict()
        assert d["path"] == "/tmp/.claude/skills/my-skill/SKILL.md"
        assert d["scope"] == "project"
        assert d["content_hash"] == "abc123"

    def test_from_dict(self):
        rec = InstallRecord.from_dict(
            {
                "path": "/tmp/x",
                "scope": "global",
                "installed_at": "2026-01-01T00:00:00Z",
                "content_hash": "def456",
            }
        )
        assert rec.path == "/tmp/x"
        assert rec.scope == "global"


class TestInstallationTracker:
    def test_empty_tracker(self, tmp_path):
        tracker = InstallationTracker(state_path=tmp_path / "installations.json")
        assert tracker.list_all() == {}

    def test_add_and_list(self, tmp_path):
        tracker = InstallationTracker(state_path=tmp_path / "installations.json")
        rec = InstallRecord(
            path="/tmp/x",
            scope="project",
            installed_at="2026-01-01T00:00:00Z",
            content_hash="abc",
        )
        tracker.add("my-org/my-skill@1.0.0", "claude", rec)
        tracker.save()

        tracker2 = InstallationTracker(state_path=tmp_path / "installations.json")
        entries = tracker2.list_all()
        assert "my-org/my-skill@1.0.0" in entries
        assert "claude" in entries["my-org/my-skill@1.0.0"]

    def test_remove(self, tmp_path):
        tracker = InstallationTracker(state_path=tmp_path / "installations.json")
        rec = InstallRecord(path="/tmp/x", scope="project", installed_at="t", content_hash="h")
        tracker.add("ref@1.0", "cursor", rec)
        tracker.save()
        tracker.remove("ref@1.0", "cursor")
        tracker.save()

        tracker2 = InstallationTracker(state_path=tmp_path / "installations.json")
        assert tracker2.list_all() == {}

    def test_is_modified(self, tmp_path):
        target_file = tmp_path / "skill.md"
        content = "# Hello"
        target_file.write_text(content)
        content_hash = hashlib.sha256(content.encode()).hexdigest()

        tracker = InstallationTracker(state_path=tmp_path / "installations.json")
        rec = InstallRecord(
            path=str(target_file),
            scope="project",
            installed_at="t",
            content_hash=content_hash,
        )
        assert not tracker.is_modified(rec)

        target_file.write_text("# Modified")
        assert tracker.is_modified(rec)


# ---------------------------------------------------------------------------
# Task 2: Frontmatter translation
# ---------------------------------------------------------------------------


class TestFormatForClaude:
    def test_passthrough(self):
        result = format_for_claude(
            "my-skill",
            {"description": "Does stuff", "allowed-tools": "Read"},
            "# Body",
        )
        assert "description: Does stuff" in result
        assert "allowed-tools: Read" in result
        assert "# Body" in result

    def test_wraps_in_frontmatter(self):
        result = format_for_claude("s", {"description": "d"}, "body")
        assert result.startswith("---\n")
        assert "\n---\n" in result


class TestFormatForCursor:
    def test_basic(self):
        result = format_for_cursor("my-skill", {"description": "Does stuff"}, "# Body")
        assert "description: Does stuff" in result
        assert "alwaysApply: true" in result
        assert "# Body" in result

    def test_with_paths(self):
        result = format_for_cursor("s", {"description": "d", "paths": "**/*.py"}, "body")
        assert "globs:" in result
        assert "**/*.py" in result

    def test_disable_model_invocation(self):
        result = format_for_cursor("s", {"description": "d", "disable-model-invocation": True}, "body")
        assert "alwaysApply: false" in result

    def test_drops_allowed_tools(self, capsys):
        result = format_for_cursor("s", {"description": "d", "allowed-tools": "Bash(*)"}, "body")
        assert "allowed-tools" not in result
        assert "allowed-tools" in capsys.readouterr().err


class TestFormatForWindsurf:
    def test_basic_always_on(self):
        result = format_for_windsurf("s", {"description": "d"}, "body")
        assert "trigger: always_on" in result

    def test_with_paths(self):
        result = format_for_windsurf("s", {"description": "d", "paths": "**/*.py"}, "body")
        assert "trigger: glob" in result

    def test_manual(self):
        result = format_for_windsurf("s", {"description": "d", "disable-model-invocation": True}, "body")
        assert "trigger: manual" in result

    def test_model_decision(self):
        result = format_for_windsurf("s", {"description": "d", "disable-model-invocation": False}, "body")
        assert "trigger: model_decision" in result


class TestFormatForCopilot:
    def test_basic_no_frontmatter(self):
        result = format_for_copilot("s", {"description": "d"}, "# Body")
        assert "# Body" in result
        assert "---" not in result

    def test_with_paths(self):
        result = format_for_copilot("s", {"description": "d", "paths": "**/*.py"}, "body")
        assert "applyTo:" in result
        assert "**/*.py" in result


class TestFormatForKiro:
    def test_basic_always(self):
        result = format_for_kiro("my-skill", {"description": "Does stuff"}, "body")
        assert "inclusion: always" in result
        assert "name: my-skill" in result

    def test_with_paths(self):
        result = format_for_kiro("s", {"description": "d", "paths": "**/*.py"}, "body")
        assert "inclusion: fileMatch" in result
        assert "fileMatchPattern" in result

    def test_manual(self):
        result = format_for_kiro("s", {"description": "d", "disable-model-invocation": True}, "body")
        assert "inclusion: manual" in result

    def test_auto(self):
        result = format_for_kiro("s", {"description": "d", "disable-model-invocation": False}, "body")
        assert "inclusion: auto" in result


# ---------------------------------------------------------------------------
# Task 3: Target registry and detect_targets
# ---------------------------------------------------------------------------


class TestTargetRegistry:
    def test_all_targets_present(self):
        assert set(TARGETS.keys()) == {
            "claude",
            "cursor",
            "windsurf",
            "copilot",
            "kiro",
        }

    def test_each_target_has_format_fn(self):
        for name, cfg in TARGETS.items():
            assert callable(cfg.format_fn), f"{name} missing format_fn"

    def test_each_target_has_project_path(self):
        for name, cfg in TARGETS.items():
            path = cfg.project_path("test-skill")
            assert isinstance(path, Path)
            assert "test-skill" in str(path)

    def test_claude_creates_directory(self):
        path = TARGETS["claude"].project_path("my-skill")
        assert path.name == "SKILL.md"
        assert path.parent.name == "my-skill"

    def test_cursor_flat_file(self):
        path = TARGETS["cursor"].project_path("my-skill")
        assert path.suffix == ".mdc"

    def test_copilot_instructions_suffix(self):
        path = TARGETS["copilot"].project_path("my-skill")
        assert path.name == "my-skill.instructions.md"

    def test_global_path_none_for_cursor(self):
        assert TARGETS["cursor"].global_path is None

    def test_global_path_none_for_copilot(self):
        assert TARGETS["copilot"].global_path is None

    def test_global_path_exists_for_claude(self):
        assert TARGETS["claude"].global_path is not None


class TestDetectTargets:
    def test_detects_claude(self, tmp_path, monkeypatch):
        (tmp_path / ".claude").mkdir()
        monkeypatch.chdir(tmp_path)
        detected = detect_targets(global_scope=False)
        assert "claude" in detected

    def test_no_targets(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        detected = detect_targets(global_scope=False)
        assert detected == []

    def test_multiple_targets(self, tmp_path, monkeypatch):
        (tmp_path / ".claude").mkdir()
        (tmp_path / ".cursor").mkdir()
        (tmp_path / ".kiro").mkdir()
        monkeypatch.chdir(tmp_path)
        detected = detect_targets(global_scope=False)
        assert set(detected) == {"claude", "cursor", "kiro"}
