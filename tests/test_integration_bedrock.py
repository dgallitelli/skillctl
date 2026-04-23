"""Integration tests that call real Amazon Bedrock.

Run with: pytest tests/test_integration_bedrock.py -m integration -v
Requires valid AWS credentials with Bedrock model access.

These tests verify the actual LLM-powered components work end-to-end,
not just the plumbing around them.
"""

from __future__ import annotations

import json

import pytest

# All tests in this module require real AWS credentials + Bedrock access.
pytestmark = pytest.mark.integration


def _make_llm_client():
    """Create a real LLMClient pointing at Bedrock Opus."""
    from skillctl.optimize.llm_client import LLMClient
    return LLMClient()


# ===================================================================
# LLM Client — real Bedrock calls
# ===================================================================

class TestBedrockLLMClient:
    """Verify the LLM client actually works against real Bedrock."""

    def test_simple_call(self):
        client = _make_llm_client()
        resp = client.call(
            system="You are a test assistant. Reply with exactly one word.",
            prompt="Say 'hello'.",
            max_tokens=16,
        )
        assert len(resp.content.strip()) > 0
        assert resp.input_tokens > 0
        assert resp.output_tokens > 0

    def test_structured_json_output(self):
        client = _make_llm_client()
        resp = client.call(
            system="You return only valid JSON. No markdown fences, no explanation.",
            prompt='Return {"status": "ok", "count": 42}',
            max_tokens=64,
        )
        data = json.loads(resp.content)
        assert data["status"] == "ok"
        assert data["count"] == 42

    def test_uses_opus_model(self):
        client = _make_llm_client()
        assert "opus" in client.model.lower()
        assert "us.anthropic" in client.model


# ===================================================================
# Failure Analyzer — real LLM analysis
# ===================================================================

class TestBedrockFailureAnalyzer:
    """Verify the failure analyzer actually identifies weaknesses via LLM."""

    def test_analyze_failures_returns_weaknesses(self):
        from skillctl.optimize.failure_analyzer import analyze_failures
        from skillctl.optimize.types import EvalResult

        client = _make_llm_client()

        skill_content = """# Code Reviewer
## Instructions
Review the code for bugs.
"""

        eval_result = EvalResult(
            overall_score=0.3,
            overall_grade="D",
            passed=False,
            sections={
                "audit": {"score": 40, "findings": [
                    {"code": "SEC-003", "title": "Subprocess execution detected",
                     "detail": "subprocess.run found in SKILL.md"},
                ]},
            },
            report_path="/tmp/fake-report.json",
        )

        analysis = analyze_failures(eval_result, skill_content, llm_client=client)

        assert analysis is not None
        assert len(analysis.weaknesses) > 0
        assert analysis.tokens_used.input_tokens > 0
        for w in analysis.weaknesses:
            assert w.description
            assert w.hypothesis


# ===================================================================
# Variant Generator — real LLM generation
# ===================================================================

class TestBedrockVariantGenerator:
    """Verify the variant generator actually produces rewritten SKILL.md content."""

    def test_generate_single_variant(self):
        from skillctl.optimize.variant_generator import generate_variants
        from skillctl.optimize.types import FailureAnalysis, Weakness

        client = _make_llm_client()

        skill_content = """# Greeter Skill
## Instructions
When the user says hello, respond with a greeting.
"""

        from skillctl.optimize.types import TokenUsage
        analysis = FailureAnalysis(
            weaknesses=[
                Weakness(
                    category="completeness",
                    description="Skill does not handle different languages",
                    severity="medium",
                    evidence=["Only responds in English"],
                    hypothesis="Adding multilingual greeting support would improve the skill",
                ),
            ],
            overall_summary="Skill only greets in English",
            tokens_used=TokenUsage(input_tokens=0, output_tokens=0, cost_usd=0.0),
        )

        variants = generate_variants(
            skill_content, analysis, num_variants=1, llm_client=client,
        )

        assert len(variants) == 1
        v = variants[0]
        assert len(v.content) > 0
        assert v.content != skill_content
        assert v.tokens_used.input_tokens > 0
        assert v.parent_id


# ===================================================================
# Cost Estimation — verify Opus model pricing exists
# ===================================================================

class TestCostEstimation:
    """Verify cost estimation works with the default Opus model."""

    def test_opus_model_has_pricing(self):
        from skillctl.eval.cost import estimate_cost

        cost = estimate_cost(1000, 500, "us.anthropic.claude-opus-4-6-v1")
        assert cost["total_cost"] > 0
        assert cost["model"] == "us.anthropic.claude-opus-4-6-v1"

    def test_opus_pricing_matches_expected(self):
        from skillctl.eval.cost import MODEL_PRICING

        pricing = MODEL_PRICING["us.anthropic.claude-opus-4-6-v1"]
        assert pricing["input"] == 15.00
        assert pricing["output"] == 75.00
