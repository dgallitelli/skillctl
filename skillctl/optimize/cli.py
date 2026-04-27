"""CLI subcommands for skillctl optimize."""

from __future__ import annotations
import argparse
import difflib
import json
import sys
from pathlib import Path
from skillctl.optimize.provenance import ProvenanceStore
from skillctl.optimize.types import OptimizeConfig


def register_optimize_commands(subparsers):
    """Add optimize command to the skillctl CLI."""
    subparsers.add_parser(
        "optimize",
        help="Optimize a skill via automated eval loop (or: optimize history, optimize diff)",
    )


def _build_run_parser():
    """Build a standalone parser for optimize run flags."""
    p = argparse.ArgumentParser(prog="skillctl optimize", add_help=False)
    p.add_argument("path", nargs="?", default=".", help="Skill directory")
    p.add_argument("--variants", type=int, default=3)
    p.add_argument("--threshold", type=float, default=0.05)
    p.add_argument("--max-iterations", type=int, default=50)
    p.add_argument("--plateau", type=int, default=3)
    p.add_argument("--budget", type=float, default=10.0)
    p.add_argument(
        "--model",
        default=None,
        help="LiteLLM model ID (default: bedrock/us.anthropic.claude-opus-4-6-v1). "
        "Examples: openai/gpt-4o, anthropic/claude-sonnet-4-6, ollama/llama3",
    )
    p.add_argument("--approve", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--timeout", type=int, default=120)
    p.add_argument("--agent", default="claude")
    p.add_argument("--json", action="store_true", dest="json_output")
    return p


def handle_optimize(args, remaining=None):
    """Dispatch to the appropriate optimize subcommand."""
    remaining = remaining or []
    if remaining and remaining[0] == "history":
        _handle_history(remaining[1:])
    elif remaining and remaining[0] == "diff":
        _handle_diff(remaining[1:])
    else:
        run_parser = _build_run_parser()
        run_args = run_parser.parse_args(remaining)
        _handle_optimize_run(run_args)


def _handle_optimize_run(args):
    """Run the optimization loop."""
    from skillctl.config import load_config
    from skillctl.optimize.loop import run_optimization

    skillctl_cfg = load_config()
    model = args.model or skillctl_cfg.optimize.model
    budget = args.budget if args.budget != 10.0 else skillctl_cfg.optimize.budget_usd

    config = OptimizeConfig(
        skill_path=args.path,
        num_variants=args.variants,
        threshold=args.threshold,
        max_iterations=args.max_iterations,
        plateau_limit=args.plateau,
        budget_usd=budget,
        model=model,
        approve=args.approve,
        dry_run=args.dry_run,
        timeout=args.timeout,
        agent=args.agent,
    )
    run = run_optimization(config)
    if args.json_output:
        print(json.dumps(run.to_dict(), indent=2))
    else:
        _print_optimize_summary(run)


def _handle_history(remaining):
    """List past optimization runs."""
    p = argparse.ArgumentParser(prog="skillctl optimize history")
    p.add_argument("--skill", default=None, help="Filter by skill name")
    p.add_argument("--json", action="store_true", dest="json_output")
    args = p.parse_args(remaining)

    runs = ProvenanceStore.list_runs(skill_name=args.skill)
    if args.json_output:
        print(json.dumps([r.to_dict() for r in runs], indent=2))
        return
    if not runs:
        print("No optimization runs found.")
        return
    header = "RUN ID         SKILL                     SCORE DELTA        COST STATUS"
    print(header)
    print("-" * len(header))
    for r in runs:
        initial = r.initial_score if r.initial_score is not None else 0.0
        final = r.final_score if r.final_score is not None else 0.0
        delta = final - initial
        sign = "+" if delta >= 0 else ""
        score_str = "{:.0%} -> {:.0%} ({}{:.0%})".format(initial, final, sign, delta)
        cost_str = "${:.2f}".format(r.total_cost_usd)
        print("{:<14} {:<25} {:<18} {:>5} {}".format(r.run_id, r.skill_name, score_str, cost_str, r.status))


def _handle_diff(remaining):
    """Show unified diff between original and promoted skill."""
    p = argparse.ArgumentParser(prog="skillctl optimize diff")
    p.add_argument("run_id", help="Run ID to show diff for")
    p.add_argument("--json", action="store_true", dest="json_output")
    args = p.parse_args(remaining)

    run = ProvenanceStore.load_run(args.run_id)
    run_dir = Path.home() / ".skillctl" / "optimize" / args.run_id
    original_path = run_dir / "original.md"
    promoted_path = run_dir / "promoted.md"
    if not original_path.is_file():
        print("Error: original.md not found for run {}".format(args.run_id), file=sys.stderr)
        sys.exit(1)
    original = original_path.read_text(encoding="utf-8")
    if not promoted_path.is_file():
        if args.json_output:
            print(
                json.dumps(
                    {"run_id": run.run_id, "diff": None, "reason": "No variant was promoted in this run."}, indent=2
                )
            )
        else:
            print("No variant was promoted in this run -- nothing to diff.")
        return
    promoted = promoted_path.read_text(encoding="utf-8")
    diff_lines = list(
        difflib.unified_diff(
            original.splitlines(keepends=True),
            promoted.splitlines(keepends=True),
            fromfile="original SKILL.md",
            tofile="promoted SKILL.md",
        )
    )
    if args.json_output:
        initial = run.initial_score if run.initial_score is not None else 0.0
        final = run.final_score if run.final_score is not None else 0.0
        print(
            json.dumps(
                {
                    "run_id": run.run_id,
                    "skill_name": run.skill_name,
                    "initial_score": initial,
                    "final_score": final,
                    "delta": final - initial,
                    "diff": "".join(diff_lines),
                },
                indent=2,
            )
        )
    else:
        if diff_lines:
            sys.stdout.writelines(diff_lines)
        else:
            print("No differences found.")


def _print_optimize_summary(run):
    """Print a human-readable summary of an optimization run."""
    initial = run.initial_score if run.initial_score is not None else 0.0
    final = run.final_score if run.final_score is not None else 0.0
    delta = final - initial
    sign = "+" if delta >= 0 else ""
    print("")
    print("Optimization complete -- {}".format(run.status))
    print("  Run ID:   {}".format(run.run_id))
    print("  Skill:    {}".format(run.skill_name))
    print("  Score:    {:.0%} -> {:.0%} ({}{:.0%})".format(initial, final, sign, delta))
    print("  Cycles:   {}".format(len(run.cycles)))
    print("  Cost:     ${:.2f}".format(run.total_cost_usd))
    if run.promoted_variant_id:
        if run.config.get("dry_run"):
            print("  Promoted: {} (dry-run, not written)".format(run.promoted_variant_id))
        else:
            print("  Promoted: {}".format(run.promoted_variant_id))
    else:
        print("  Promoted: none")
