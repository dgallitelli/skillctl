"""Provenance store for optimization runs.

Persists all optimization state to ~/.skillctl/optimize/{run-id}/ as
content-addressed JSON, forming a complete audit trail from original
skill through every variant to the final promoted version.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Optional

from skillctl.optimize.types import (
    EvalResult,
    FailureAnalysis,
    OptimizationRun,
    PromotionDecision,
    Variant,
)


class ProvenanceStore:
    """Persist and query optimization run data.

    Storage layout::

        {base_dir}/{run_id}/
        ├── run.json
        ├── original.md
        ├── cycle-001/
        │   ├── eval-baseline.json
        │   ├── analysis.json
        │   ├── variant-{hash}.md
        │   ├── variant-{hash}-eval.json
        │   └── promotion.json
        └── promoted.md
    """

    def __init__(self, run_id: str, base_dir: Optional[str] = None) -> None:
        if base_dir is None:
            self.base_dir = Path.home() / ".skillctl" / "optimize"
        else:
            self.base_dir = Path(base_dir)
        self.run_id = run_id
        self.run_dir = self.base_dir / run_id

    # ------------------------------------------------------------------
    # Write helpers
    # ------------------------------------------------------------------

    def create_run(self) -> Path:
        """Create the run directory, returning its path."""
        self.run_dir.mkdir(parents=True, exist_ok=True)
        return self.run_dir

    def save_original(self, content: str) -> Path:
        """Save a copy of the original SKILL.md."""
        path = self.run_dir / "original.md"
        path.write_text(content, encoding="utf-8")
        return path

    def save_cycle(
        self,
        cycle_number: int,
        eval_result: EvalResult,
        failure_analysis: FailureAnalysis,
        variants_with_evals: list[tuple[Variant, EvalResult]],
        promotion: Optional[PromotionDecision],
    ) -> Path:
        """Persist all data for a single optimization cycle.

        Creates ``cycle-{NNN}/`` with baseline eval, analysis, variant
        contents + evals, and the promotion decision.
        """
        cycle_dir = self.run_dir / f"cycle-{cycle_number:03d}"
        cycle_dir.mkdir(parents=True, exist_ok=True)

        # Baseline eval for the current skill at the start of this cycle
        _write_json(cycle_dir / "eval-baseline.json", eval_result.to_dict())

        # Failure analysis
        _write_json(cycle_dir / "analysis.json", failure_analysis.to_dict())

        # Variants and their evals
        for variant, variant_eval in variants_with_evals:
            vid = _content_hash(variant.content)
            (cycle_dir / f"variant-{vid}.md").write_text(variant.content, encoding="utf-8")
            _write_json(
                cycle_dir / f"variant-{vid}-eval.json",
                variant_eval.to_dict(),
            )

        # Promotion decision
        if promotion is not None:
            _write_json(cycle_dir / "promotion.json", promotion.to_dict())

        return cycle_dir

    def save_run(self, run: OptimizationRun) -> Path:
        """Write the run.json manifest with full run metadata."""
        path = self.run_dir / "run.json"
        _write_json(path, run.to_dict())
        return path

    def save_promoted(self, content: str) -> Path:
        """Write the final promoted SKILL.md to the run directory."""
        path = self.run_dir / "promoted.md"
        path.write_text(content, encoding="utf-8")
        return path

    # ------------------------------------------------------------------
    # Read / query helpers
    # ------------------------------------------------------------------

    @classmethod
    def list_runs(cls, skill_name: Optional[str] = None, base_dir: Optional[str] = None) -> list[OptimizationRun]:
        """List all runs, optionally filtered by skill name."""
        if base_dir is None:
            root = Path.home() / ".skillctl" / "optimize"
        else:
            root = Path(base_dir)

        if not root.is_dir():
            return []

        runs: list[OptimizationRun] = []
        for entry in sorted(root.iterdir()):
            manifest = entry / "run.json"
            if entry.is_dir() and manifest.is_file():
                run = _load_run_manifest(manifest)
                if skill_name is None or run.skill_name == skill_name:
                    runs.append(run)
        return runs

    @classmethod
    def load_run(cls, run_id: str, base_dir: Optional[str] = None) -> OptimizationRun:
        """Load a run manifest by run-id."""
        if base_dir is None:
            root = Path.home() / ".skillctl" / "optimize"
        else:
            root = Path(base_dir)

        manifest = root / run_id / "run.json"
        if not manifest.is_file():
            raise FileNotFoundError(f"No optimization run found for id '{run_id}' (looked in {manifest})")
        return _load_run_manifest(manifest)


# ------------------------------------------------------------------
# Module-level helpers
# ------------------------------------------------------------------


def _content_hash(content: str) -> str:
    """Return the first 12 hex chars of the SHA-256 digest."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:12]


def _write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _load_run_manifest(path: Path) -> OptimizationRun:
    data = json.loads(path.read_text(encoding="utf-8"))
    return OptimizationRun.from_dict(data)
