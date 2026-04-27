"""Tests for skillctl.optimize.loop — the main optimization loop."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch


from skillctl.optimize.loop import run_optimization, _content_hash
from skillctl.utils import read_skill_name_from_manifest as _read_skill_name
from skillctl.optimize.types import (
    EvalResult,
    FailureAnalysis,
    OptimizeConfig,
    PromotionDecision,
    TokenUsage,
    Variant,
    Weakness,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_skill_dir(tmp_path: Path, content: str = "# Original Skill") -> Path:
    """Create a minimal skill directory with SKILL.md and skill.yaml."""
    skill_dir = tmp_path / "test-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(content)
    (skill_dir / "skill.yaml").write_text(
        "apiVersion: skillctl.io/v1\n"
        "kind: Skill\n"
        "metadata:\n"
        "  name: test-skill\n"
        "  version: 1.0.0\n"
        "spec:\n"
        "  content:\n"
        "    path: SKILL.md\n"
    )
    return skill_dir


def _eval_result(score: float | None = 0.6) -> EvalResult:
    return EvalResult(
        overall_score=score,
        overall_grade="C" if score and score < 0.7 else "B",
        passed=bool(score and score >= 0.6),
    )


def _failure_analysis() -> FailureAnalysis:
    return FailureAnalysis(
        weaknesses=[
            Weakness(
                category="functional",
                description="Missing error handling",
                severity="high",
                evidence=["test_error failed"],
                hypothesis="Add try/except blocks",
            )
        ],
        overall_summary="Needs error handling",
        tokens_used=TokenUsage(input_tokens=100, output_tokens=50, cost_usd=0.001),
    )


def _variant(content: str = "# Improved Skill", vid: str | None = None) -> Variant:
    import hashlib

    h = hashlib.sha256(content.encode()).hexdigest()[:12]
    return Variant(
        id=vid or h,
        content=content,
        hypothesis="improve error handling",
        target_weakness="Missing error handling",
        parent_id="parent000000",
        tokens_used=TokenUsage(input_tokens=200, output_tokens=100, cost_usd=0.002),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestRunOptimization:
    """Unit tests for run_optimization()."""

    @patch("skillctl.optimize.loop.LLMClient")
    @patch("skillctl.optimize.loop.evaluate_skill")
    @patch("skillctl.optimize.loop.analyze_failures")
    @patch("skillctl.optimize.loop.generate_variants")
    @patch("skillctl.optimize.loop.check_promotion")
    def test_basic_promotion_cycle(self, mock_promote, mock_gen, mock_analyze, mock_eval, mock_llm, tmp_path):
        """A single cycle that promotes a variant writes SKILL.md and returns correctly."""
        skill_dir = _make_skill_dir(tmp_path)
        improved = "# Improved Skill v1"
        variant = _variant(content=improved)

        mock_eval.return_value = _eval_result(0.6)
        mock_analyze.return_value = _failure_analysis()
        mock_gen.return_value = [variant]
        mock_promote.return_value = PromotionDecision(
            promoted=True,
            variant_id=variant.id,
            current_score=0.6,
            best_score=0.8,
            delta=0.2,
            reason="exceeded threshold",
        )
        # After promotion, next cycle's eval returns higher score
        # but plateau_limit=1 means we stop after one non-promotion
        # With promotion on first cycle, plateau resets; second cycle won't promote
        mock_eval.side_effect = [
            _eval_result(0.6),  # initial eval
            _eval_result(0.8),  # variant eval
            _eval_result(0.8),  # cycle 2 variant eval
        ]
        mock_promote.side_effect = [
            PromotionDecision(
                promoted=True,
                variant_id=variant.id,
                current_score=0.6,
                best_score=0.8,
                delta=0.2,
                reason="exceeded threshold",
            ),
            PromotionDecision(
                promoted=False,
                variant_id=None,
                current_score=0.8,
                best_score=0.8,
                delta=0.0,
                reason="below threshold",
            ),
        ]

        config = OptimizeConfig(
            skill_path=str(skill_dir),
            max_iterations=2,
            plateau_limit=1,
            num_variants=1,
            budget_usd=100.0,
        )

        with patch("skillctl.optimize.loop.ProvenanceStore") as mock_store_cls:
            mock_store = MagicMock()
            mock_store_cls.return_value = mock_store
            run = run_optimization(config)

        assert run.status == "plateau"
        assert run.initial_score == 0.6
        assert run.final_score == 0.8
        assert run.promoted_variant_id == variant.id
        assert len(run.cycles) == 2
        # SKILL.md should be updated on disk
        assert (skill_dir / "SKILL.md").read_text() == improved

    @patch("skillctl.optimize.loop.LLMClient")
    @patch("skillctl.optimize.loop.evaluate_skill")
    @patch("skillctl.optimize.loop.analyze_failures")
    @patch("skillctl.optimize.loop.generate_variants")
    @patch("skillctl.optimize.loop.check_promotion")
    def test_dry_run_does_not_write_skill(self, mock_promote, mock_gen, mock_analyze, mock_eval, mock_llm, tmp_path):
        """In dry-run mode, SKILL.md on disk is never modified."""
        original = "# Original Skill"
        skill_dir = _make_skill_dir(tmp_path, content=original)
        improved = "# Improved Skill"
        variant = _variant(content=improved)

        mock_eval.return_value = _eval_result(0.6)
        mock_analyze.return_value = _failure_analysis()
        mock_gen.return_value = [variant]
        mock_promote.return_value = PromotionDecision(
            promoted=True,
            variant_id=variant.id,
            current_score=0.6,
            best_score=0.8,
            delta=0.2,
            reason="exceeded threshold",
        )

        config = OptimizeConfig(
            skill_path=str(skill_dir),
            max_iterations=1,
            plateau_limit=5,
            num_variants=1,
            budget_usd=100.0,
            dry_run=True,
        )

        with patch("skillctl.optimize.loop.ProvenanceStore") as mock_store_cls:
            mock_store = MagicMock()
            mock_store_cls.return_value = mock_store
            run = run_optimization(config)

        # SKILL.md on disk must remain unchanged
        assert (skill_dir / "SKILL.md").read_text() == original
        assert run.promoted_variant_id == variant.id

    @patch("skillctl.optimize.loop.LLMClient")
    @patch("skillctl.optimize.loop.evaluate_skill")
    @patch("skillctl.optimize.loop.analyze_failures")
    @patch("skillctl.optimize.loop.generate_variants")
    @patch("skillctl.optimize.loop.check_promotion")
    def test_plateau_termination(self, mock_promote, mock_gen, mock_analyze, mock_eval, mock_llm, tmp_path):
        """Loop terminates with 'plateau' after plateau_limit non-promotions."""
        skill_dir = _make_skill_dir(tmp_path)
        variant = _variant()

        mock_eval.return_value = _eval_result(0.6)
        mock_analyze.return_value = _failure_analysis()
        mock_gen.return_value = [variant]
        mock_promote.return_value = PromotionDecision(
            promoted=False,
            variant_id=None,
            current_score=0.6,
            best_score=0.62,
            delta=0.02,
            reason="below threshold",
        )

        config = OptimizeConfig(
            skill_path=str(skill_dir),
            max_iterations=10,
            plateau_limit=3,
            num_variants=1,
            budget_usd=100.0,
        )

        with patch("skillctl.optimize.loop.ProvenanceStore") as mock_store_cls:
            mock_store = MagicMock()
            mock_store_cls.return_value = mock_store
            run = run_optimization(config)

        assert run.status == "plateau"
        assert len(run.cycles) == 3

    @patch("skillctl.optimize.loop.LLMClient")
    @patch("skillctl.optimize.loop.evaluate_skill")
    @patch("skillctl.optimize.loop.analyze_failures")
    @patch("skillctl.optimize.loop.generate_variants")
    @patch("skillctl.optimize.loop.check_promotion")
    def test_max_iterations_cap(self, mock_promote, mock_gen, mock_analyze, mock_eval, mock_llm, tmp_path):
        """Loop terminates with 'completed' when max_iterations is reached."""
        skill_dir = _make_skill_dir(tmp_path)
        variant = _variant()

        mock_eval.return_value = _eval_result(0.6)
        mock_analyze.return_value = _failure_analysis()
        mock_gen.return_value = [variant]
        # Always promote so plateau never triggers
        mock_promote.return_value = PromotionDecision(
            promoted=True,
            variant_id=variant.id,
            current_score=0.6,
            best_score=0.8,
            delta=0.2,
            reason="exceeded threshold",
        )

        config = OptimizeConfig(
            skill_path=str(skill_dir),
            max_iterations=3,
            plateau_limit=100,
            num_variants=1,
            budget_usd=100.0,
        )

        with patch("skillctl.optimize.loop.ProvenanceStore") as mock_store_cls:
            mock_store = MagicMock()
            mock_store_cls.return_value = mock_store
            run = run_optimization(config)

        assert run.status == "completed"
        assert len(run.cycles) == 3

    @patch("skillctl.optimize.loop.LLMClient")
    @patch("skillctl.optimize.loop.evaluate_skill")
    @patch("skillctl.optimize.loop.analyze_failures")
    def test_failure_analysis_error_skips_cycle(self, mock_analyze, mock_eval, mock_llm, tmp_path):
        """When failure analysis raises, the cycle is skipped."""
        skill_dir = _make_skill_dir(tmp_path)

        mock_eval.return_value = _eval_result(0.6)
        mock_analyze.side_effect = RuntimeError("LLM down")

        config = OptimizeConfig(
            skill_path=str(skill_dir),
            max_iterations=3,
            plateau_limit=5,
            num_variants=1,
            budget_usd=100.0,
        )

        with patch("skillctl.optimize.loop.ProvenanceStore") as mock_store_cls:
            mock_store = MagicMock()
            mock_store_cls.return_value = mock_store
            run = run_optimization(config)

        # All cycles skipped → no cycle records, status completed
        assert run.status == "completed"
        assert len(run.cycles) == 0

    @patch("skillctl.optimize.loop.LLMClient")
    @patch("skillctl.optimize.loop.evaluate_skill")
    @patch("skillctl.optimize.loop.analyze_failures")
    @patch("skillctl.optimize.loop.generate_variants")
    @patch("skillctl.optimize.loop.check_promotion")
    def test_budget_exhaustion_terminates(self, mock_promote, mock_gen, mock_analyze, mock_eval, mock_llm, tmp_path):
        """Loop terminates with 'budget_exhausted' when budget runs out."""
        skill_dir = _make_skill_dir(tmp_path)
        variant = _variant()

        mock_eval.return_value = _eval_result(0.6)
        # Analysis uses expensive tokens that exhaust budget
        expensive_analysis = _failure_analysis()
        expensive_analysis.tokens_used = TokenUsage(input_tokens=1000000, output_tokens=1000000, cost_usd=50.0)
        mock_analyze.return_value = expensive_analysis
        mock_gen.return_value = [variant]
        mock_promote.return_value = PromotionDecision(
            promoted=False,
            variant_id=None,
            current_score=0.6,
            best_score=0.62,
            delta=0.02,
            reason="below threshold",
        )

        config = OptimizeConfig(
            skill_path=str(skill_dir),
            max_iterations=10,
            plateau_limit=10,
            num_variants=1,
            budget_usd=10.0,
        )

        with patch("skillctl.optimize.loop.ProvenanceStore") as mock_store_cls:
            mock_store = MagicMock()
            mock_store_cls.return_value = mock_store
            run = run_optimization(config)

        assert run.status == "budget_exhausted"

    @patch("skillctl.optimize.loop.LLMClient")
    @patch("skillctl.optimize.loop.evaluate_skill")
    @patch("skillctl.optimize.loop.analyze_failures")
    @patch("skillctl.optimize.loop.generate_variants")
    @patch("skillctl.optimize.loop.check_promotion")
    def test_variant_generation_failure_continues(
        self, mock_promote, mock_gen, mock_analyze, mock_eval, mock_llm, tmp_path
    ):
        """When variant generation fails completely, cycle is skipped."""
        skill_dir = _make_skill_dir(tmp_path)

        mock_eval.return_value = _eval_result(0.6)
        mock_analyze.return_value = _failure_analysis()
        mock_gen.side_effect = RuntimeError("LLM error")

        config = OptimizeConfig(
            skill_path=str(skill_dir),
            max_iterations=2,
            plateau_limit=5,
            num_variants=1,
            budget_usd=100.0,
        )

        with patch("skillctl.optimize.loop.ProvenanceStore") as mock_store_cls:
            mock_store = MagicMock()
            mock_store_cls.return_value = mock_store
            run = run_optimization(config)

        # No variants → cycles skipped → completed with 0 cycles
        assert run.status == "completed"
        assert len(run.cycles) == 0

    @patch("skillctl.optimize.loop.LLMClient")
    @patch("skillctl.optimize.loop.evaluate_skill")
    @patch("skillctl.optimize.loop.analyze_failures")
    @patch("skillctl.optimize.loop.generate_variants")
    @patch("skillctl.optimize.loop.check_promotion")
    def test_promotion_resets_plateau_counter(
        self, mock_promote, mock_gen, mock_analyze, mock_eval, mock_llm, tmp_path
    ):
        """A promotion resets the plateau counter to zero."""
        skill_dir = _make_skill_dir(tmp_path)
        variant = _variant()

        mock_eval.return_value = _eval_result(0.6)
        mock_analyze.return_value = _failure_analysis()
        mock_gen.return_value = [variant]

        # Cycle 1: no promotion (plateau=1)
        # Cycle 2: promotion (plateau=0)
        # Cycle 3: no promotion (plateau=1)
        # Cycle 4: no promotion (plateau=2 → stop with plateau_limit=2)
        mock_promote.side_effect = [
            PromotionDecision(
                promoted=False,
                variant_id=None,
                current_score=0.6,
                best_score=0.62,
                delta=0.02,
                reason="below threshold",
            ),
            PromotionDecision(
                promoted=True,
                variant_id=variant.id,
                current_score=0.6,
                best_score=0.8,
                delta=0.2,
                reason="exceeded threshold",
            ),
            PromotionDecision(
                promoted=False,
                variant_id=None,
                current_score=0.8,
                best_score=0.82,
                delta=0.02,
                reason="below threshold",
            ),
            PromotionDecision(
                promoted=False,
                variant_id=None,
                current_score=0.8,
                best_score=0.82,
                delta=0.02,
                reason="below threshold",
            ),
        ]

        config = OptimizeConfig(
            skill_path=str(skill_dir),
            max_iterations=10,
            plateau_limit=2,
            num_variants=1,
            budget_usd=100.0,
        )

        with patch("skillctl.optimize.loop.ProvenanceStore") as mock_store_cls:
            mock_store = MagicMock()
            mock_store_cls.return_value = mock_store
            run = run_optimization(config)

        assert run.status == "plateau"
        assert len(run.cycles) == 4

    @patch("skillctl.optimize.loop.LLMClient")
    @patch("skillctl.optimize.loop.evaluate_skill")
    @patch("skillctl.optimize.loop.analyze_failures")
    @patch("skillctl.optimize.loop.generate_variants")
    @patch("skillctl.optimize.loop.check_promotion")
    def test_monotonic_score_improvement(self, mock_promote, mock_gen, mock_analyze, mock_eval, mock_llm, tmp_path):
        """Final score is always >= initial score."""
        skill_dir = _make_skill_dir(tmp_path)
        variant = _variant()

        mock_eval.return_value = _eval_result(0.6)
        mock_analyze.return_value = _failure_analysis()
        mock_gen.return_value = [variant]
        mock_promote.return_value = PromotionDecision(
            promoted=False,
            variant_id=None,
            current_score=0.6,
            best_score=0.62,
            delta=0.02,
            reason="below threshold",
        )

        config = OptimizeConfig(
            skill_path=str(skill_dir),
            max_iterations=5,
            plateau_limit=2,
            num_variants=1,
            budget_usd=100.0,
        )

        with patch("skillctl.optimize.loop.ProvenanceStore") as mock_store_cls:
            mock_store = MagicMock()
            mock_store_cls.return_value = mock_store
            run = run_optimization(config)

        assert run.final_score >= run.initial_score


class TestContentHash:
    def test_deterministic(self):
        assert _content_hash("hello") == _content_hash("hello")

    def test_different_content_different_hash(self):
        assert _content_hash("hello") != _content_hash("world")

    def test_returns_12_chars(self):
        assert len(_content_hash("test")) == 12


class TestReadSkillName:
    def test_reads_name_from_yaml(self, tmp_path):
        skill_dir = _make_skill_dir(tmp_path)
        assert _read_skill_name(str(skill_dir)) == "test-skill"

    def test_falls_back_to_dir_name(self, tmp_path):
        skill_dir = tmp_path / "my-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("# Skill")
        # No skill.yaml → ManifestLoader wraps SKILL.md with dir name
        name = _read_skill_name(str(skill_dir))
        assert name == "my-skill"
