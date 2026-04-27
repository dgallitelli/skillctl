"""Tests for skillctl.optimize.variant_generator."""

from __future__ import annotations

import hashlib
from unittest.mock import MagicMock

from skillctl.optimize.types import (
    FailureAnalysis,
    LLMResponse,
    TokenUsage,
    Weakness,
)
from skillctl.optimize.variant_generator import (
    _build_variant_prompt,
    _extract_skill_content,
    generate_variants,
)


def _make_weakness(**overrides) -> Weakness:
    defaults = {
        "category": "functional",
        "description": "Low accuracy",
        "severity": "high",
        "evidence": ["accuracy=0.4"],
        "hypothesis": "Add more specific instructions",
    }
    defaults.update(overrides)
    return Weakness(**defaults)


def _make_failure_analysis(weaknesses: list[Weakness] | None = None) -> FailureAnalysis:
    return FailureAnalysis(
        weaknesses=weaknesses or [_make_weakness()],
        overall_summary="summary",
        tokens_used=TokenUsage(input_tokens=0, output_tokens=0, cost_usd=0.0),
    )


def _make_mock_client(variant_contents: list[str] | None = None) -> MagicMock:
    """Create a mock LLM client that returns different content per call."""
    client = MagicMock()
    client.model = "sonnet"

    if variant_contents is None:
        variant_contents = ["# Rewritten Skill\nImproved content"]

    responses = [LLMResponse(content=c, input_tokens=100, output_tokens=200) for c in variant_contents]
    client.call.side_effect = responses
    return client


class TestExtractSkillContent:
    def test_plain_text(self):
        assert _extract_skill_content("# My Skill\nContent") == "# My Skill\nContent"

    def test_markdown_fenced(self):
        response = "```markdown\n# My Skill\nContent\n```"
        assert _extract_skill_content(response) == "# My Skill\nContent"

    def test_md_fenced(self):
        response = "```md\n# My Skill\nContent\n```"
        assert _extract_skill_content(response) == "# My Skill\nContent"

    def test_bare_fenced(self):
        response = "```\n# My Skill\nContent\n```"
        assert _extract_skill_content(response) == "# My Skill\nContent"

    def test_strips_whitespace(self):
        assert _extract_skill_content("  \n# Skill\n  ") == "# Skill"

    def test_fenced_with_surrounding_text(self):
        response = "Here is the rewritten skill:\n```markdown\n# Skill\nNew\n```\nDone."
        assert _extract_skill_content(response) == "# Skill\nNew"


class TestBuildVariantPrompt:
    def test_includes_skill_content(self):
        weakness = _make_weakness()
        prompt = _build_variant_prompt("# Original Skill", weakness, 0, 3)
        assert "# Original Skill" in prompt

    def test_includes_weakness_details(self):
        weakness = _make_weakness(
            description="Missing permissions",
            hypothesis="Add permission block",
        )
        prompt = _build_variant_prompt("skill", weakness, 0, 1)
        assert "Missing permissions" in prompt
        assert "Add permission block" in prompt

    def test_includes_variant_index(self):
        weakness = _make_weakness()
        prompt = _build_variant_prompt("skill", weakness, 2, 5)
        assert "variant 3 of 5" in prompt

    def test_includes_evidence(self):
        weakness = _make_weakness(evidence=["accuracy=0.4", "missing section"])
        prompt = _build_variant_prompt("skill", weakness, 0, 1)
        assert "accuracy=0.4" in prompt
        assert "missing section" in prompt

    def test_no_evidence_line_when_empty(self):
        weakness = _make_weakness(evidence=[])
        prompt = _build_variant_prompt("skill", weakness, 0, 1)
        assert "**Evidence:**" not in prompt


class TestGenerateVariants:
    def test_returns_correct_count(self):
        contents = [f"# Variant {i}" for i in range(3)]
        client = _make_mock_client(contents)
        analysis = _make_failure_analysis()

        variants = generate_variants("# Original", analysis, num_variants=3, llm_client=client)

        assert len(variants) == 3

    def test_variant_ids_are_content_hashes(self):
        content = "# Rewritten"
        client = _make_mock_client([content])
        analysis = _make_failure_analysis()

        variants = generate_variants("# Original", analysis, num_variants=1, llm_client=client)

        expected_hash = hashlib.sha256(content.encode()).hexdigest()[:12]
        assert variants[0].id == expected_hash

    def test_parent_id_is_input_hash(self):
        original = "# Original Skill"
        client = _make_mock_client(["# Rewritten"])
        analysis = _make_failure_analysis()

        variants = generate_variants(original, analysis, num_variants=1, llm_client=client)

        expected_parent = hashlib.sha256(original.encode()).hexdigest()[:12]
        assert variants[0].parent_id == expected_parent

    def test_round_robin_weakness_assignment(self):
        w1 = _make_weakness(description="weakness-A", hypothesis="fix-A")
        w2 = _make_weakness(description="weakness-B", hypothesis="fix-B")
        analysis = _make_failure_analysis([w1, w2])

        contents = [f"# Variant {i}" for i in range(5)]
        client = _make_mock_client(contents)

        variants = generate_variants("# Skill", analysis, num_variants=5, llm_client=client)

        assert variants[0].target_weakness == "weakness-A"
        assert variants[1].target_weakness == "weakness-B"
        assert variants[2].target_weakness == "weakness-A"
        assert variants[3].target_weakness == "weakness-B"
        assert variants[4].target_weakness == "weakness-A"

    def test_hypothesis_from_weakness(self):
        w = _make_weakness(hypothesis="Add error handling")
        analysis = _make_failure_analysis([w])
        client = _make_mock_client(["# Fixed"])

        variants = generate_variants("# Skill", analysis, num_variants=1, llm_client=client)

        assert variants[0].hypothesis == "Add error handling"

    def test_token_usage_tracked(self):
        client = _make_mock_client(["# Rewritten"])
        analysis = _make_failure_analysis()

        variants = generate_variants("# Skill", analysis, num_variants=1, llm_client=client)

        assert variants[0].tokens_used.input_tokens == 100
        assert variants[0].tokens_used.output_tokens == 200
        assert variants[0].tokens_used.cost_usd > 0

    def test_calls_llm_with_system_prompt(self):
        client = _make_mock_client(["# Rewritten"])
        analysis = _make_failure_analysis()

        generate_variants("# Skill", analysis, num_variants=1, llm_client=client)

        client.call.assert_called_once()
        call_kwargs = client.call.call_args
        assert "system" in call_kwargs.kwargs or len(call_kwargs.args) >= 1

    def test_extracts_content_from_fenced_response(self):
        fenced = "```markdown\n# Clean Content\n```"
        client = _make_mock_client([fenced])
        analysis = _make_failure_analysis()

        variants = generate_variants("# Skill", analysis, num_variants=1, llm_client=client)

        assert variants[0].content == "# Clean Content"

    def test_single_weakness_all_variants_target_same(self):
        w = _make_weakness(description="only-weakness")
        analysis = _make_failure_analysis([w])
        contents = [f"# V{i}" for i in range(3)]
        client = _make_mock_client(contents)

        variants = generate_variants("# Skill", analysis, num_variants=3, llm_client=client)

        for v in variants:
            assert v.target_weakness == "only-weakness"
