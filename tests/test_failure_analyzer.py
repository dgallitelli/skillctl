"""Tests for skillctl.optimize.failure_analyzer."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

from skillctl.optimize.failure_analyzer import (
    _build_analysis_prompt,
    _extract_evidence,
    _parse_weaknesses,
    analyze_failures,
)
from skillctl.optimize.types import EvalResult, LLMResponse


def _make_eval_result(**overrides) -> EvalResult:
    defaults = {
        "overall_score": 0.5,
        "overall_grade": "C",
        "passed": False,
        "sections": {},
    }
    defaults.update(overrides)
    return EvalResult(**defaults)


def _make_llm_response(weaknesses: list[dict]) -> LLMResponse:
    return LLMResponse(
        content=json.dumps({"weaknesses": weaknesses}),
        input_tokens=100,
        output_tokens=50,
    )


class TestExtractEvidence:
    def test_empty_sections(self):
        result = _make_eval_result(sections={})
        assert _extract_evidence(result) == []

    def test_audit_with_critical(self):
        result = _make_eval_result(
            sections={
                "audit": {"critical": 2, "warning": 0, "score": 0.4, "findings": ["f1"]},
            }
        )
        evidence = _extract_evidence(result)
        assert len(evidence) == 1
        assert evidence[0]["category"] == "audit"
        assert evidence[0]["issues"] == ["f1"]

    def test_audit_with_warning(self):
        result = _make_eval_result(
            sections={
                "audit": {"critical": 0, "warning": 3, "score": 0.6},
            }
        )
        evidence = _extract_evidence(result)
        assert len(evidence) == 1
        assert evidence[0]["category"] == "audit"

    def test_audit_no_issues_skipped(self):
        result = _make_eval_result(
            sections={
                "audit": {"critical": 0, "warning": 0, "score": 1.0},
            }
        )
        assert _extract_evidence(result) == []

    def test_functional_failing_dimensions(self):
        result = _make_eval_result(
            sections={
                "functional": {"scores": {"accuracy": 0.5, "completeness": 0.9}, "overall": 0.7},
            }
        )
        evidence = _extract_evidence(result)
        assert len(evidence) == 1
        assert evidence[0]["category"] == "functional"
        assert "accuracy" in evidence[0]["failing_dimensions"]
        assert "completeness" not in evidence[0]["failing_dimensions"]

    def test_functional_all_passing(self):
        result = _make_eval_result(
            sections={
                "functional": {"scores": {"accuracy": 0.8, "completeness": 0.9}},
            }
        )
        assert _extract_evidence(result) == []

    def test_trigger_low_pass_rate(self):
        result = _make_eval_result(
            sections={
                "trigger": {"pass_rate": 0.6},
            }
        )
        evidence = _extract_evidence(result)
        assert len(evidence) == 1
        assert evidence[0]["category"] == "trigger"
        assert evidence[0]["pass_rate"] == 0.6

    def test_trigger_passing(self):
        result = _make_eval_result(
            sections={
                "trigger": {"pass_rate": 0.9},
            }
        )
        assert _extract_evidence(result) == []

    def test_multiple_sections(self):
        result = _make_eval_result(
            sections={
                "audit": {"critical": 1, "warning": 0, "score": 0.3},
                "functional": {"scores": {"accuracy": 0.4}, "overall": 0.4},
                "trigger": {"pass_rate": 0.5},
            }
        )
        evidence = _extract_evidence(result)
        assert len(evidence) == 3
        categories = {e["category"] for e in evidence}
        assert categories == {"audit", "functional", "trigger"}


class TestBuildAnalysisPrompt:
    def test_includes_skill_content(self):
        prompt = _build_analysis_prompt("# My Skill\nDo stuff", [])
        assert "# My Skill" in prompt
        assert "Do stuff" in prompt

    def test_includes_evidence(self):
        evidence = [{"category": "audit", "score": 0.3, "issues": ["bad perm"]}]
        prompt = _build_analysis_prompt("skill content", evidence)
        assert "Audit Section" in prompt
        assert "bad perm" in prompt

    def test_no_evidence_message(self):
        prompt = _build_analysis_prompt("skill content", [])
        assert "No specific failures detected" in prompt


class TestParseWeaknesses:
    def test_valid_json(self):
        content = json.dumps(
            {
                "weaknesses": [
                    {
                        "category": "audit",
                        "description": "Missing permissions",
                        "severity": "high",
                        "evidence": ["no perms declared"],
                        "hypothesis": "Add permission block",
                    },
                ]
            }
        )
        weaknesses = _parse_weaknesses(content)
        assert len(weaknesses) == 1
        assert weaknesses[0].category == "audit"
        assert weaknesses[0].severity == "high"
        assert weaknesses[0].hypothesis == "Add permission block"

    def test_multiple_weaknesses(self):
        content = json.dumps(
            {
                "weaknesses": [
                    {"category": "audit", "description": "a", "severity": "low", "evidence": [], "hypothesis": "h1"},
                    {
                        "category": "functional",
                        "description": "b",
                        "severity": "high",
                        "evidence": [],
                        "hypothesis": "h2",
                    },
                ]
            }
        )
        weaknesses = _parse_weaknesses(content)
        assert len(weaknesses) == 2

    def test_invalid_json_fallback(self):
        weaknesses = _parse_weaknesses("this is not json at all")
        assert len(weaknesses) == 1
        assert weaknesses[0].severity == "high"
        assert weaknesses[0].category == "functional"
        assert "this is not json at all" in weaknesses[0].evidence[0]

    def test_empty_weaknesses_fallback(self):
        content = json.dumps({"weaknesses": []})
        weaknesses = _parse_weaknesses(content)
        assert len(weaknesses) == 1
        assert weaknesses[0].severity == "high"

    def test_missing_fields_defaults(self):
        content = json.dumps({"weaknesses": [{"description": "something"}]})
        weaknesses = _parse_weaknesses(content)
        assert len(weaknesses) == 1
        assert weaknesses[0].category == "functional"
        assert weaknesses[0].severity == "medium"
        assert weaknesses[0].hypothesis != ""

    def test_long_content_truncated_in_fallback(self):
        long_text = "x" * 1000
        weaknesses = _parse_weaknesses(long_text)
        assert len(weaknesses[0].evidence[0]) == 500


class TestAnalyzeFailures:
    def test_returns_sorted_weaknesses(self):
        llm_response = _make_llm_response(
            [
                {"category": "functional", "description": "low", "severity": "low", "evidence": [], "hypothesis": "h1"},
                {"category": "audit", "description": "high", "severity": "high", "evidence": [], "hypothesis": "h2"},
                {"category": "trigger", "description": "med", "severity": "medium", "evidence": [], "hypothesis": "h3"},
            ]
        )

        mock_client = MagicMock()
        mock_client.call.return_value = llm_response
        mock_client.model = "sonnet"

        eval_result = _make_eval_result(
            sections={
                "audit": {"critical": 1, "warning": 0, "score": 0.3},
            }
        )

        analysis = analyze_failures(eval_result, "skill content", mock_client)

        assert len(analysis.weaknesses) == 3
        assert analysis.weaknesses[0].severity == "high"
        assert analysis.weaknesses[1].severity == "medium"
        assert analysis.weaknesses[2].severity == "low"

    def test_returns_token_usage(self):
        llm_response = _make_llm_response(
            [
                {"category": "audit", "description": "d", "severity": "high", "evidence": [], "hypothesis": "h"},
            ]
        )

        mock_client = MagicMock()
        mock_client.call.return_value = llm_response
        mock_client.model = "sonnet"

        eval_result = _make_eval_result()
        analysis = analyze_failures(eval_result, "skill", mock_client)

        assert analysis.tokens_used.input_tokens == 100
        assert analysis.tokens_used.output_tokens == 50
        assert analysis.tokens_used.cost_usd > 0

    def test_calls_llm_with_system_prompt(self):
        llm_response = _make_llm_response(
            [
                {"category": "audit", "description": "d", "severity": "high", "evidence": [], "hypothesis": "h"},
            ]
        )

        mock_client = MagicMock()
        mock_client.call.return_value = llm_response
        mock_client.model = "sonnet"

        eval_result = _make_eval_result()
        analyze_failures(eval_result, "my skill content", mock_client)

        mock_client.call.assert_called_once()
        call_kwargs = mock_client.call.call_args
        assert "system" in call_kwargs.kwargs or len(call_kwargs.args) >= 1
