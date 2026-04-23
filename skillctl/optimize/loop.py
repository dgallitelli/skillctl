"""Main optimization loop orchestrating eval → analyze → generate → promote cycles.

Coordinates all optimizer components (EvalRunner, FailureAnalyzer,
VariantGenerator, PromotionGate, BudgetTracker, ProvenanceStore) into
an iterative improvement loop that terminates on plateau, budget
exhaustion, or iteration cap.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from skillctl.optimize.budget import BudgetTracker
from skillctl.optimize.eval_runner import evaluate_skill
from skillctl.optimize.failure_analyzer import analyze_failures
from skillctl.optimize.llm_client import LLMClient
from skillctl.optimize.promotion_gate import check_promotion
from skillctl.optimize.provenance import ProvenanceStore
from skillctl.optimize.types import (
    CycleRecord,
    OptimizationRun,
    OptimizeConfig,
    VariantRecord,
)
from skillctl.optimize.variant_generator import generate_variants
from skillctl.utils import read_skill_name_from_manifest


def _content_hash(content: str) -> str:
    """Return first 12 hex chars of the SHA-256 digest."""
    return hashlib.sha256(content.encode()).hexdigest()[:12]


def _iso_now() -> str:
    """Return current UTC time as ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()


def run_optimization(config: OptimizeConfig) -> OptimizationRun:
    """Execute the full optimization loop.

    Orchestrates: initial eval → cycle(analyze → generate → eval variants
    → promote) → termination.  Handles plateau detection, budget
    enforcement, dry-run mode, approve mode, and error resilience.
    """
    run_id = uuid4().hex[:12]
    store = ProvenanceStore(run_id)
    store.create_run()
    llm_client = LLMClient(model=config.model, region=config.aws_region)
    budget = BudgetTracker(config.budget_usd, config.model)

    skill_path = Path(config.skill_path)
    started_at = _iso_now()

    # Read original skill content
    original_content = (skill_path / "SKILL.md").read_text()
    current_content = original_content
    store.save_original(original_content)

    # Read skill name from skill.yaml
    skill_name = read_skill_name_from_manifest(config.skill_path)

    # Initial evaluation
    current_eval = evaluate_skill(
        config.skill_path, timeout=config.timeout, agent=config.agent
    )
    initial_score = current_eval.overall_score
    current_score = initial_score if initial_score is not None else 0.0

    plateau_counter = 0
    cycles: list[CycleRecord] = []
    promoted_variant_id: str | None = None

    for cycle_num in range(1, config.max_iterations + 1):
        budget.start_cycle()

        # --- Phase 1: Failure Analysis (skip cycle on error) ---
        try:
            analysis = analyze_failures(current_eval, current_content, llm_client)
        except Exception:
            # Req 11.2: skip cycle on failure analysis error
            continue
        budget.track(analysis.tokens_used)

        if budget.exhausted:
            # Save what we have so far for this cycle, then break
            break

        # --- Phase 2: Variant Generation (partial failure OK) ---
        variants = []
        try:
            variants = generate_variants(
                current_content, analysis, config.num_variants, llm_client
            )
        except Exception:
            # Req 11.3: evaluate whatever we got
            pass
        for v in variants:
            budget.track(v.tokens_used)

        if not variants:
            continue  # nothing to evaluate

        # --- Phase 3: Evaluate Each Variant ---
        scored_variants = []
        for variant in variants:
            variant_eval = evaluate_skill(
                config.skill_path,
                content=variant.content,
                timeout=config.timeout,
                agent=config.agent,
            )
            scored_variants.append((variant, variant_eval))

        # --- Phase 4: Promotion Decision ---
        decision = check_promotion(
            scored_variants, current_score, config.threshold, config.approve
        )

        # Save cycle data to provenance store
        store.save_cycle(
            cycle_num, current_eval, analysis, scored_variants, decision
        )

        # Build variant records for the cycle
        cycle_dir = f"cycle-{cycle_num:03d}"
        variant_records = []
        for variant, v_eval in scored_variants:
            vid = _content_hash(variant.content)
            variant_records.append(
                VariantRecord(
                    variant_id=variant.id,
                    hypothesis=variant.hypothesis,
                    target_weakness=variant.target_weakness,
                    score=v_eval.overall_score,
                    content_path=f"{cycle_dir}/variant-{vid}.md",
                    eval_result_path=f"{cycle_dir}/variant-{vid}-eval.json",
                )
            )

        cycle_record = CycleRecord(
            cycle_number=cycle_num,
            current_score=current_score,
            eval_result_path=f"{cycle_dir}/eval-baseline.json",
            failure_analysis_path=f"{cycle_dir}/analysis.json",
            variants=variant_records,
            promotion=decision,
            cycle_cost_usd=budget.cycle_cost(),
        )
        cycles.append(cycle_record)

        if decision.promoted:
            # Find the promoted variant
            best_variant = next(
                v for v, e in scored_variants if v.id == decision.variant_id
            )
            current_content = best_variant.content
            current_score = decision.best_score
            current_eval = next(
                e for v, e in scored_variants if v.id == decision.variant_id
            )
            plateau_counter = 0
            promoted_variant_id = decision.variant_id

            if not config.dry_run:
                (skill_path / "SKILL.md").write_text(current_content)
        else:
            plateau_counter += 1

        # --- Termination Checks ---
        if plateau_counter >= config.plateau_limit:
            break
        if budget.exhausted:
            break

    # Save promoted content if any change occurred
    if current_content != original_content:
        store.save_promoted(current_content)

    # Determine final status
    if plateau_counter >= config.plateau_limit:
        status = "plateau"
    elif budget.exhausted:
        status = "budget_exhausted"
    else:
        status = "completed"

    run = OptimizationRun(
        run_id=run_id,
        skill_name=skill_name,
        skill_path=str(skill_path),
        original_content_hash=_content_hash(original_content),
        config=config.to_dict(),
        started_at=started_at,
        finished_at=_iso_now(),
        status=status,
        cycles=cycles,
        final_score=current_score,
        initial_score=initial_score,
        total_cost_usd=budget.total_cost_usd,
        promoted_variant_id=promoted_variant_id,
    )
    store.save_run(run)
    return run
