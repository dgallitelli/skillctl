"""Promotion gate: decides whether a candidate variant should replace the current skill."""

from __future__ import annotations

from skillctl.optimize.types import EvalResult, PromotionDecision, Variant


def check_promotion(
    variants_with_scores: list[tuple[Variant, EvalResult]],
    current_score: float,
    threshold: float,
    approve: bool = False,
) -> PromotionDecision:
    """Decide whether the best variant should replace the current skill.

    Filters out variants whose eval score is None, finds the highest-scoring
    variant, and promotes it only if the improvement exceeds *threshold*.
    When *approve* is True the user is prompted for confirmation via stdin.
    """

    # Filter to variants with a valid (non-None) score
    valid = [(v, e) for v, e in variants_with_scores if e.overall_score is not None]

    if not valid:
        return PromotionDecision(
            promoted=False,
            variant_id=None,
            current_score=current_score,
            best_score=current_score,
            delta=0.0,
            reason="no valid variants",
        )

    # Find the best variant by score
    best_variant, best_eval = max(valid, key=lambda pair: pair[1].overall_score)
    best_score = best_eval.overall_score
    delta = best_score - current_score

    if delta < threshold:
        return PromotionDecision(
            promoted=False,
            variant_id=None,
            current_score=current_score,
            best_score=best_score,
            delta=delta,
            reason="below threshold",
        )

    # In approve mode, ask the user before promoting
    if approve:
        answer = input(
            f"Promote variant {best_variant.id}? Score {current_score:.2f} → {best_score:.2f} (+{delta:.2f}) [y/N] "
        )
        if answer.strip().lower() != "y":
            return PromotionDecision(
                promoted=False,
                variant_id=None,
                current_score=current_score,
                best_score=best_score,
                delta=delta,
                reason="rejected by user",
            )

    return PromotionDecision(
        promoted=True,
        variant_id=best_variant.id,
        current_score=current_score,
        best_score=best_score,
        delta=delta,
        reason="exceeded threshold",
    )
