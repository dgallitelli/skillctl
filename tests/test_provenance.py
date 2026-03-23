"""Tests for skillctl.optimize.provenance.ProvenanceStore."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from skillctl.optimize.provenance import ProvenanceStore, _content_hash
from skillctl.optimize.types import (
    CycleRecord,
    EvalResult,
    FailureAnalysis,
    OptimizationRun,
    PromotionDecision,
    TokenUsage,
    Variant,
    VariantRecord,
    Weakness,
)


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


def _make_eval(score: float = 0.7) -> EvalResult:
    return EvalResult(
        overall_score=score,
        overall_grade="B",
        passed=True,
        audit_score=0.8,
        functional_score=0.6,
        trigger_score=0.7,
        sections={},
        report_path="",
    )


def _make_analysis() -> FailureAnalysis:
    return FailureAnalysis(
        weaknesses=[
            Weakness(
                category="audit",
                description="Missing permissions section",
                severity="high",
                evidence=["No permissions declared"],
                hypothesis="Add a permissions section",
            )
        ],
        overall_summary="Needs permissions",
        tokens_used=TokenUsage(input_tokens=100, output_tokens=50, cost_usd=0.01),
    )


def _make_variant(content: str = "# Variant SKILL") -> Variant:
    import hashlib

    h = hashlib.sha256(content.encode()).hexdigest()[:12]
    return Variant(
        id=h,
        content=content,
        hypothesis="Fix permissions",
        target_weakness="Missing permissions section",
        parent_id="abc123abc123",
        tokens_used=TokenUsage(input_tokens=200, output_tokens=300, cost_usd=0.02),
    )


def _make_promotion(promoted: bool = True) -> PromotionDecision:
    return PromotionDecision(
        promoted=promoted,
        variant_id="abc123",
        current_score=0.62,
        best_score=0.71,
        delta=0.09,
        reason="exceeded threshold",
    )


def _make_run(run_id: str = "test-run-001", skill_name: str = "my-skill") -> OptimizationRun:
    return OptimizationRun(
        run_id=run_id,
        skill_name=skill_name,
        skill_path="/path/to/skill",
        original_content_hash="aabbccdd",
        config={"num_variants": 3},
        started_at="2025-01-01T00:00:00Z",
        finished_at="2025-01-01T01:00:00Z",
        status="completed",
        cycles=[],
        final_score=0.85,
        initial_score=0.62,
        total_cost_usd=1.50,
        promoted_variant_id="abc123",
    )


# ------------------------------------------------------------------
# Tests
# ------------------------------------------------------------------


class TestCreateRun:
    def test_creates_directory(self, tmp_path: Path) -> None:
        store = ProvenanceStore("run-abc", base_dir=str(tmp_path))
        result = store.create_run()
        assert result.is_dir()
        assert result == tmp_path / "run-abc"

    def test_idempotent(self, tmp_path: Path) -> None:
        store = ProvenanceStore("run-abc", base_dir=str(tmp_path))
        store.create_run()
        store.create_run()  # should not raise
        assert (tmp_path / "run-abc").is_dir()


class TestSaveOriginal:
    def test_writes_original_md(self, tmp_path: Path) -> None:
        store = ProvenanceStore("run-abc", base_dir=str(tmp_path))
        store.create_run()
        path = store.save_original("# Original SKILL")
        assert path.name == "original.md"
        assert path.read_text(encoding="utf-8") == "# Original SKILL"


class TestSaveCycle:
    def test_creates_cycle_directory_and_files(self, tmp_path: Path) -> None:
        store = ProvenanceStore("run-abc", base_dir=str(tmp_path))
        store.create_run()

        eval_result = _make_eval()
        analysis = _make_analysis()
        variant = _make_variant("# V1 content")
        variant_eval = _make_eval(0.75)
        promotion = _make_promotion()

        cycle_dir = store.save_cycle(
            cycle_number=1,
            eval_result=eval_result,
            failure_analysis=analysis,
            variants_with_evals=[(variant, variant_eval)],
            promotion=promotion,
        )

        assert cycle_dir.name == "cycle-001"
        assert (cycle_dir / "eval-baseline.json").is_file()
        assert (cycle_dir / "analysis.json").is_file()
        assert (cycle_dir / "promotion.json").is_file()

        # Variant files use content hash
        vid = _content_hash("# V1 content")
        assert (cycle_dir / f"variant-{vid}.md").is_file()
        assert (cycle_dir / f"variant-{vid}-eval.json").is_file()

    def test_cycle_without_promotion(self, tmp_path: Path) -> None:
        store = ProvenanceStore("run-abc", base_dir=str(tmp_path))
        store.create_run()

        cycle_dir = store.save_cycle(
            cycle_number=2,
            eval_result=_make_eval(),
            failure_analysis=_make_analysis(),
            variants_with_evals=[(_make_variant(), _make_eval())],
            promotion=None,
        )

        assert cycle_dir.name == "cycle-002"
        assert not (cycle_dir / "promotion.json").exists()

    def test_multiple_variants_in_cycle(self, tmp_path: Path) -> None:
        store = ProvenanceStore("run-abc", base_dir=str(tmp_path))
        store.create_run()

        v1 = _make_variant("# Variant A")
        v2 = _make_variant("# Variant B")

        cycle_dir = store.save_cycle(
            cycle_number=1,
            eval_result=_make_eval(),
            failure_analysis=_make_analysis(),
            variants_with_evals=[(v1, _make_eval(0.7)), (v2, _make_eval(0.8))],
            promotion=_make_promotion(),
        )

        h1 = _content_hash("# Variant A")
        h2 = _content_hash("# Variant B")
        assert (cycle_dir / f"variant-{h1}.md").is_file()
        assert (cycle_dir / f"variant-{h2}.md").is_file()
        assert (cycle_dir / f"variant-{h1}-eval.json").is_file()
        assert (cycle_dir / f"variant-{h2}-eval.json").is_file()

    def test_eval_baseline_content(self, tmp_path: Path) -> None:
        store = ProvenanceStore("run-abc", base_dir=str(tmp_path))
        store.create_run()

        eval_result = _make_eval(0.62)
        store.save_cycle(
            cycle_number=1,
            eval_result=eval_result,
            failure_analysis=_make_analysis(),
            variants_with_evals=[(_make_variant(), _make_eval())],
            promotion=None,
        )

        baseline = json.loads(
            (tmp_path / "run-abc" / "cycle-001" / "eval-baseline.json").read_text()
        )
        assert baseline["overall_score"] == 0.62


class TestSaveRun:
    def test_writes_run_json(self, tmp_path: Path) -> None:
        store = ProvenanceStore("run-abc", base_dir=str(tmp_path))
        store.create_run()

        run = _make_run("run-abc")
        path = store.save_run(run)

        assert path.name == "run.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["run_id"] == "run-abc"
        assert data["skill_name"] == "my-skill"
        assert data["status"] == "completed"
        assert data["final_score"] == 0.85


class TestSavePromoted:
    def test_writes_promoted_md(self, tmp_path: Path) -> None:
        store = ProvenanceStore("run-abc", base_dir=str(tmp_path))
        store.create_run()
        path = store.save_promoted("# Promoted SKILL")
        assert path.name == "promoted.md"
        assert path.read_text(encoding="utf-8") == "# Promoted SKILL"


class TestListRuns:
    def test_empty_when_no_runs(self, tmp_path: Path) -> None:
        runs = ProvenanceStore.list_runs(base_dir=str(tmp_path))
        assert runs == []

    def test_lists_all_runs(self, tmp_path: Path) -> None:
        for rid in ["run-001", "run-002"]:
            store = ProvenanceStore(rid, base_dir=str(tmp_path))
            store.create_run()
            store.save_run(_make_run(rid))

        runs = ProvenanceStore.list_runs(base_dir=str(tmp_path))
        assert len(runs) == 2
        assert {r.run_id for r in runs} == {"run-001", "run-002"}

    def test_filters_by_skill_name(self, tmp_path: Path) -> None:
        s1 = ProvenanceStore("run-001", base_dir=str(tmp_path))
        s1.create_run()
        s1.save_run(_make_run("run-001", skill_name="alpha"))

        s2 = ProvenanceStore("run-002", base_dir=str(tmp_path))
        s2.create_run()
        s2.save_run(_make_run("run-002", skill_name="beta"))

        runs = ProvenanceStore.list_runs(skill_name="alpha", base_dir=str(tmp_path))
        assert len(runs) == 1
        assert runs[0].skill_name == "alpha"

    def test_skips_dirs_without_manifest(self, tmp_path: Path) -> None:
        (tmp_path / "not-a-run").mkdir()
        runs = ProvenanceStore.list_runs(base_dir=str(tmp_path))
        assert runs == []

    def test_nonexistent_base_dir(self, tmp_path: Path) -> None:
        runs = ProvenanceStore.list_runs(base_dir=str(tmp_path / "nope"))
        assert runs == []


class TestLoadRun:
    def test_loads_existing_run(self, tmp_path: Path) -> None:
        store = ProvenanceStore("run-abc", base_dir=str(tmp_path))
        store.create_run()
        store.save_run(_make_run("run-abc"))

        loaded = ProvenanceStore.load_run("run-abc", base_dir=str(tmp_path))
        assert loaded.run_id == "run-abc"
        assert loaded.skill_name == "my-skill"
        assert loaded.final_score == 0.85

    def test_raises_on_missing_run(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError, match="No optimization run found"):
            ProvenanceStore.load_run("nonexistent", base_dir=str(tmp_path))


class TestContentHash:
    def test_deterministic(self) -> None:
        assert _content_hash("hello") == _content_hash("hello")

    def test_different_content_different_hash(self) -> None:
        assert _content_hash("hello") != _content_hash("world")

    def test_returns_12_chars(self) -> None:
        assert len(_content_hash("anything")) == 12
