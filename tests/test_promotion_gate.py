"""Tests for skillctl.optimize.promotion_gate."""

from __future__ import annotations

from unittest.mock import patch

from skillctl.optimize.promotion_gate import check_promotion
from skillctl.optimize.types import EvalResult, TokenUsage, Variant


def _variant(vid: str = "abc123") -> Variant:
    return Variant(
        id=vid,
        content="# Skill",
        hypothesis="improve audit",
        target_weakness="audit gap",
        parent_id="parent0",
        tokens_used=TokenUsage(input_tokens=10, output_tokens=10, cost_usd=0.001),
    )


def _eval(score: float | None) -> EvalResult:
    return EvalResult(
        overall_score=score,
        overall_grade="B" if score and score >= 0.7 else "C",
        passed=bool(score and score >= 0.6),
    )


class TestCheckPromotion:
    """Unit tests for check_promotion()."""

    def test_promotes_when_delta_exceeds_threshold(self):
        pairs = [(_variant("v1"), _eval(0.80))]
        decision = check_promotion(pairs, current_score=0.60, threshold=0.05)

        assert decision.promoted is True
        assert decision.variant_id == "v1"
        assert decision.current_score == 0.60
        assert decision.best_score == 0.80
        assert decision.delta == 0.80 - 0.60
        assert decision.reason == "exceeded threshold"

    def test_not_promoted_below_threshold(self):
        pairs = [(_variant("v1"), _eval(0.62))]
        decision = check_promotion(pairs, current_score=0.60, threshold=0.05)

        assert decision.promoted is False
        assert decision.variant_id is None
        assert decision.reason == "below threshold"
        assert decision.best_score == 0.62
        assert decision.delta == 0.62 - 0.60

    def test_not_promoted_exact_threshold_boundary(self):
        # delta == threshold should still promote (>= check)
        pairs = [(_variant("v1"), _eval(0.65))]
        decision = check_promotion(pairs, current_score=0.60, threshold=0.05)

        assert decision.promoted is True
        assert decision.delta == 0.65 - 0.60

    def test_excludes_none_scores(self):
        pairs = [
            (_variant("v1"), _eval(None)),
            (_variant("v2"), _eval(0.80)),
        ]
        decision = check_promotion(pairs, current_score=0.60, threshold=0.05)

        assert decision.promoted is True
        assert decision.variant_id == "v2"
        assert decision.best_score == 0.80

    def test_all_none_scores_returns_no_valid_variants(self):
        pairs = [
            (_variant("v1"), _eval(None)),
            (_variant("v2"), _eval(None)),
        ]
        decision = check_promotion(pairs, current_score=0.60, threshold=0.05)

        assert decision.promoted is False
        assert decision.reason == "no valid variants"
        assert decision.best_score == 0.60
        assert decision.delta == 0.0

    def test_picks_highest_scoring_variant(self):
        pairs = [
            (_variant("v1"), _eval(0.70)),
            (_variant("v2"), _eval(0.85)),
            (_variant("v3"), _eval(0.75)),
        ]
        decision = check_promotion(pairs, current_score=0.60, threshold=0.05)

        assert decision.promoted is True
        assert decision.variant_id == "v2"
        assert decision.best_score == 0.85

    def test_approve_mode_accepted(self):
        pairs = [(_variant("v1"), _eval(0.80))]
        with patch("builtins.input", return_value="y"):
            decision = check_promotion(
                pairs, current_score=0.60, threshold=0.05, approve=True
            )

        assert decision.promoted is True
        assert decision.variant_id == "v1"

    def test_approve_mode_rejected(self):
        pairs = [(_variant("v1"), _eval(0.80))]
        with patch("builtins.input", return_value="n"):
            decision = check_promotion(
                pairs, current_score=0.60, threshold=0.05, approve=True
            )

        assert decision.promoted is False
        assert decision.reason == "rejected by user"
        assert decision.best_score == 0.80

    def test_approve_mode_empty_input_rejects(self):
        pairs = [(_variant("v1"), _eval(0.80))]
        with patch("builtins.input", return_value=""):
            decision = check_promotion(
                pairs, current_score=0.60, threshold=0.05, approve=True
            )

        assert decision.promoted is False
        assert decision.reason == "rejected by user"

    def test_approve_not_triggered_when_below_threshold(self):
        """Approve prompt should not appear if delta < threshold."""
        pairs = [(_variant("v1"), _eval(0.62))]
        with patch("builtins.input") as mock_input:
            decision = check_promotion(
                pairs, current_score=0.60, threshold=0.05, approve=True
            )

        mock_input.assert_not_called()
        assert decision.promoted is False
        assert decision.reason == "below threshold"

    def test_decision_fields_match_postconditions(self):
        pairs = [
            (_variant("v1"), _eval(0.70)),
            (_variant("v2"), _eval(None)),
            (_variant("v3"), _eval(0.90)),
        ]
        decision = check_promotion(pairs, current_score=0.65, threshold=0.05)

        assert decision.current_score == 0.65
        assert decision.best_score == 0.90
        assert decision.delta == decision.best_score - decision.current_score
