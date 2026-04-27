"""Unit tests for skillctl.optimize.eval_runner."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from skillctl.optimize.eval_runner import evaluate_skill, _parse_report, _failure_result


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def skill_dir(tmp_path: Path) -> Path:
    """Create a minimal skill directory with SKILL.md and evals/."""
    skill_md = tmp_path / "SKILL.md"
    skill_md.write_text("# Original Skill\nDo something useful.")
    evals_dir = tmp_path / "evals"
    evals_dir.mkdir()
    return tmp_path


def _write_report(skill_dir: Path, report_data: dict) -> Path:
    """Write a report.json into the skill's evals/ directory."""
    report_file = skill_dir / "evals" / "report.json"
    report_file.write_text(json.dumps(report_data))
    return report_file


SAMPLE_REPORT = {
    "skill_name": "test-skill",
    "skill_path": "/tmp/test-skill",
    "overall_score": 0.72,
    "overall_grade": "C",
    "passed": False,
    "sections": {
        "audit": {
            "score": 85,
            "grade": "B",
            "passed": True,
            "normalized": 0.85,
            "critical": 0,
            "warning": 1,
            "info": 2,
        },
        "functional": {
            "overall": 0.65,
            "grade": "D",
            "passed": False,
            "scores": {"accuracy": 0.7, "completeness": 0.6},
        },
        "trigger": {
            "pass_rate": 0.60,
            "grade": "D",
            "passed": False,
            "total_queries": 10,
        },
    },
}


# ---------------------------------------------------------------------------
# _parse_report tests
# ---------------------------------------------------------------------------


class TestParseReport:
    def test_parses_all_fields(self):
        result = _parse_report(SAMPLE_REPORT, "/tmp/report.json")
        assert result.overall_score == 0.72
        assert result.overall_grade == "C"
        assert result.passed is False
        assert result.audit_score == 0.85
        assert result.functional_score == 0.65
        assert result.trigger_score == 0.60
        assert result.report_path == "/tmp/report.json"

    def test_handles_missing_sections(self):
        data = {"overall_score": 0.5, "overall_grade": "D", "passed": False, "sections": {}}
        result = _parse_report(data, "")
        assert result.audit_score is None
        assert result.functional_score is None
        assert result.trigger_score is None

    def test_handles_skipped_sections(self):
        data = {
            "overall_score": 0.4,
            "overall_grade": "F",
            "passed": False,
            "sections": {
                "functional": {"skipped": True, "reason": "evals/evals.json not found"},
            },
        }
        result = _parse_report(data, "")
        assert result.functional_score is None

    def test_handles_error_sections(self):
        data = {
            "overall_score": 0.3,
            "overall_grade": "F",
            "passed": False,
            "sections": {
                "audit": {"error": "something broke"},
            },
        }
        result = _parse_report(data, "")
        assert result.audit_score is None


# ---------------------------------------------------------------------------
# _failure_result tests
# ---------------------------------------------------------------------------


class TestFailureResult:
    def test_returns_none_score(self):
        result = _failure_result()
        assert result.overall_score is None
        assert result.overall_grade == "F"
        assert result.passed is False
        assert result.sections == {}
        assert result.report_path == ""


# ---------------------------------------------------------------------------
# evaluate_skill tests
# ---------------------------------------------------------------------------


class TestEvaluateSkill:
    def test_basic_evaluation(self, skill_dir: Path):
        """Successful eval returns parsed EvalResult."""

        def mock_run(skill_path, **kwargs):
            _write_report(Path(skill_path), SAMPLE_REPORT)
            return 0

        with patch("skillctl.optimize.eval_runner.run_unified_report", side_effect=mock_run):
            result = evaluate_skill(str(skill_dir))

        assert result.overall_score == 0.72
        assert result.overall_grade == "C"
        assert result.audit_score == 0.85

    def test_content_swap_and_restore(self, skill_dir: Path):
        """When content is provided, SKILL.md is swapped and restored."""
        original = (skill_dir / "SKILL.md").read_text()
        written_content = None

        def mock_run(skill_path, **kwargs):
            nonlocal written_content
            written_content = (Path(skill_path) / "SKILL.md").read_text()
            _write_report(Path(skill_path), SAMPLE_REPORT)
            return 0

        with patch("skillctl.optimize.eval_runner.run_unified_report", side_effect=mock_run):
            evaluate_skill(str(skill_dir), content="# Variant\nNew content.")

        # During eval, the variant content was active
        assert written_content == "# Variant\nNew content."
        # After eval, original is restored
        assert (skill_dir / "SKILL.md").read_text() == original

    def test_content_restored_on_eval_exception(self, skill_dir: Path):
        """Original SKILL.md is restored even when run_unified_report raises."""
        original = (skill_dir / "SKILL.md").read_text()

        with patch("skillctl.optimize.eval_runner.run_unified_report", side_effect=RuntimeError("boom")):
            result = evaluate_skill(str(skill_dir), content="# Bad variant")

        # Original restored
        assert (skill_dir / "SKILL.md").read_text() == original
        # Failure result returned
        assert result.overall_score is None

    def test_returns_failure_on_missing_report(self, skill_dir: Path):
        """If report.json is not written, returns failure result."""
        with patch("skillctl.optimize.eval_runner.run_unified_report", return_value=1):
            result = evaluate_skill(str(skill_dir))

        assert result.overall_score is None

    def test_returns_failure_on_corrupt_report(self, skill_dir: Path):
        """If report.json is invalid JSON, returns failure result."""

        def mock_run(skill_path, **kwargs):
            report_file = Path(skill_path) / "evals" / "report.json"
            report_file.write_text("not valid json {{{")
            return 0

        with patch("skillctl.optimize.eval_runner.run_unified_report", side_effect=mock_run):
            result = evaluate_skill(str(skill_dir))

        assert result.overall_score is None

    def test_no_restore_when_no_content_provided(self, skill_dir: Path):
        """When content=None, SKILL.md is not touched."""
        original = (skill_dir / "SKILL.md").read_text()

        def mock_run(skill_path, **kwargs):
            _write_report(Path(skill_path), SAMPLE_REPORT)
            return 0

        with patch("skillctl.optimize.eval_runner.run_unified_report", side_effect=mock_run):
            result = evaluate_skill(str(skill_dir))

        assert (skill_dir / "SKILL.md").read_text() == original
        assert result.overall_score == 0.72
