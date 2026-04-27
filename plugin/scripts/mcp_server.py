#!/usr/bin/env python3
"""MCP stdio server exposing skillctl operations as tools.

Wraps skillctl as a Python library — no shell-out, structured errors,
full access to validators, store, eval, and optimizer.
"""

from __future__ import annotations

import json
import traceback
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from skillctl.diff import diff_skills
from skillctl.errors import SkillctlError
from skillctl.manifest import ManifestLoader
from skillctl.store import ContentStore
from skillctl.validator import SchemaValidator

mcp = FastMCP(
    "skillctl",
    instructions=(
        "skillctl governance tools for agent skills. "
        "Use these to validate, evaluate, optimize, and manage skills."
    ),
)

# Shared instances
_loader = ManifestLoader()
_validator = SchemaValidator()


def _store() -> ContentStore:
    return ContentStore()


def _error_response(e: Exception) -> str:
    if isinstance(e, SkillctlError):
        return json.dumps(e.format_json(), indent=2)
    return json.dumps({"error": str(e), "traceback": traceback.format_exc()}, indent=2)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


@mcp.tool()
def skillctl_validate(skill_path: str) -> str:
    """Validate a skill manifest (skill.yaml or SKILL.md) against the skillctl schema.

    Checks apiVersion, kind, name format, semver, parameter types, capability
    declarations, and content references. Returns structured validation results.

    Args:
        skill_path: Path to the skill directory, skill.yaml, or SKILL.md file.
    """
    try:
        manifest, load_warnings = _loader.load(skill_path)
        result = _validator.validate(manifest)

        content_text = ""
        try:
            content_text = _loader.resolve_content(manifest, str(Path(skill_path).resolve().parent if Path(skill_path).is_file() else skill_path))
        except Exception:
            pass

        cap_warnings = _validator.check_capabilities(manifest, content_text) if content_text else []

        output = {
            "valid": result.valid,
            "exit_code": result.exit_code,
            "errors": [{"code": i.code, "message": i.message, "path": i.path, "hint": i.hint, "severity": i.severity} for i in result.errors],
            "warnings": [{"code": i.code, "message": i.message, "path": i.path, "hint": i.hint, "severity": i.severity} for i in result.warnings],
            "capability_warnings": [{"code": i.code, "message": i.message, "path": i.path, "hint": i.hint, "severity": i.severity} for i in cap_warnings],
            "load_warnings": [str(w) for w in load_warnings],
            "skill_name": manifest.metadata.name,
            "version": manifest.metadata.version,
        }
        return json.dumps(output, indent=2)
    except Exception as e:
        return _error_response(e)


# ---------------------------------------------------------------------------
# Store operations
# ---------------------------------------------------------------------------


@mcp.tool()
def skillctl_apply(
    skill_path: str,
    dry_run: bool = False,
    local: bool = False,
) -> str:
    """Validate and push a skill to the local content-addressed store.

    Runs full validation, resolves content, computes SHA-256 hash, and stores
    the skill. Blocks on CRITICAL audit findings unless local=True.

    Args:
        skill_path: Path to the skill directory or skill.yaml.
        dry_run: If True, validate and compute hash without writing.
        local: If True, skip the security audit gate.
    """
    try:
        manifest, _ = _loader.load(skill_path)
        result = _validator.validate(manifest)
        if not result.valid:
            return json.dumps({
                "success": False,
                "reason": "validation_failed",
                "errors": [{"code": i.code, "message": i.message, "hint": i.hint} for i in result.errors],
            }, indent=2)

        base_dir = str(Path(skill_path).resolve().parent if Path(skill_path).is_file() else Path(skill_path).resolve())
        content = _loader.resolve_content(manifest, base_dir)

        if not local:
            from skillctl.eval.cli import run_audit
            report = run_audit(base_dir)
            if report.critical_count > 0:
                return json.dumps({
                    "success": False,
                    "reason": "security_gate_blocked",
                    "audit_grade": report.grade,
                    "audit_score": report.score,
                    "critical_findings": [{"code": f.code, "title": f.title, "fix": f.fix} for f in report.findings if f.severity.value == "CRITICAL"],
                    "hint": "Fix CRITICAL findings or use local=True to skip the security gate.",
                }, indent=2)

        store = _store()
        push_result = store.push(manifest, content.encode("utf-8"), dry_run=dry_run)
        return json.dumps({
            "success": True,
            "hash": push_result.hash,
            "path": push_result.path,
            "size": push_result.size,
            "created": push_result.created,
            "dry_run": dry_run,
            "skill_name": manifest.metadata.name,
            "version": manifest.metadata.version,
        }, indent=2)
    except Exception as e:
        return _error_response(e)


@mcp.tool()
def skillctl_list(
    namespace: str | None = None,
    tag: str | None = None,
) -> str:
    """List skills in the local store, optionally filtered by namespace or tag.

    Args:
        namespace: Filter to skills under this namespace (e.g., "my-org").
        tag: Filter to skills with this tag.
    """
    store = _store()
    entries = store.list_skills(namespace=namespace, tag=tag)
    return json.dumps({
        "count": len(entries),
        "skills": [
            {
                "name": e.name,
                "version": e.version,
                "hash": e.hash,
                "tags": e.tags,
                "pushed_at": e.pushed_at,
                "size": e.size,
            }
            for e in entries
        ],
    }, indent=2)


@mcp.tool()
def skillctl_describe(skill_ref: str) -> str:
    """Show detailed metadata and content for a skill in the local store.

    Args:
        skill_ref: Skill reference in "namespace/name@version" format.
    """
    try:
        from skillctl.utils import parse_ref
        name, version = parse_ref(skill_ref)
        store = _store()
        content_bytes, entry = store.pull(name, version)
        content_text = content_bytes.decode("utf-8", errors="replace")

        prefix = entry["hash"][:2]
        manifest_path = store.store_dir / prefix / f"{entry['hash']}.manifest.yaml"
        manifest_dict = {}
        if manifest_path.exists():
            import yaml
            manifest_dict = yaml.safe_load(manifest_path.read_text()) or {}

        return json.dumps({
            "name": entry["name"],
            "version": entry["version"],
            "hash": entry["hash"],
            "pushed_at": entry["pushed_at"],
            "size": entry["size"],
            "tags": entry["tags"],
            "manifest": manifest_dict,
            "content": content_text,
        }, indent=2)
    except Exception as e:
        return _error_response(e)


@mcp.tool()
def skillctl_delete(skill_ref: str) -> str:
    """Delete a skill version from the local store.

    Args:
        skill_ref: Skill reference in "namespace/name@version" format.
    """
    try:
        from skillctl.utils import parse_ref
        name, version = parse_ref(skill_ref)
        store = _store()
        store.delete_skill(name, version)
        return json.dumps({"deleted": True, "name": name, "version": version})
    except Exception as e:
        return _error_response(e)


@mcp.tool()
def skillctl_diff(ref_a: str, ref_b: str) -> str:
    """Compare two skill versions from the local store.

    Shows metadata changes, breaking changes (removed parameters or capabilities),
    and a unified content diff.

    Args:
        ref_a: First skill reference in "namespace/name@version" format.
        ref_b: Second skill reference in "namespace/name@version" format.
    """
    try:
        store = _store()
        result = diff_skills(store, ref_a, ref_b)
        return json.dumps(result.to_dict(), indent=2)
    except Exception as e:
        return _error_response(e)


# ---------------------------------------------------------------------------
# Create / scaffold
# ---------------------------------------------------------------------------


@mcp.tool()
def skillctl_create(name: str, description: str = "A new skill", target_dir: str | None = None) -> str:
    """Scaffold a new skill directory with skill.yaml and SKILL.md.

    Args:
        name: Skill name in "namespace/skill-name" format (lowercase, hyphens ok).
        description: One-line description of the skill.
        target_dir: Parent directory to create the skill in (default: current working directory).
    """
    skill_dir_name = name.replace("/", "-")
    parent = Path(target_dir) if target_dir else Path.cwd()
    skill_dir = parent / skill_dir_name
    if skill_dir.exists():
        return json.dumps({
            "success": False,
            "reason": f"Directory {skill_dir} already exists",
        })

    skill_dir.mkdir(parents=True)

    manifest_content = f"""apiVersion: skillctl.io/v1
kind: Skill

metadata:
  name: {name}
  version: 0.1.0
  description: "{description}"
  authors:
    - name: ""
  tags: []

spec:
  content:
    path: SKILL.md
  capabilities: []
  parameters: []
"""

    skill_md_content = f"""---
name: {name.split('/')[-1]}
description: {description}
---

# {name.split('/')[-1].replace('-', ' ').title()}

Instructions for the skill go here.
"""

    (skill_dir / "skill.yaml").write_text(manifest_content)
    (skill_dir / "SKILL.md").write_text(skill_md_content)

    return json.dumps({
        "success": True,
        "path": str(skill_dir),
        "files": ["skill.yaml", "SKILL.md"],
        "name": name,
        "next_steps": [
            "Edit SKILL.md with the skill's instructions",
            "Fill in metadata in skill.yaml (authors, tags, capabilities)",
            "Run skillctl_validate to check the manifest",
        ],
    }, indent=2)


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------


@mcp.tool()
def skillctl_eval_audit(
    skill_path: str,
    verbose: bool = False,
    ignore_codes: str = "",
    include_all: bool = False,
) -> str:
    """Run a security audit on a skill, producing an A-F grade and findings.

    Scans for structure issues (STR-*), security vulnerabilities (SEC-*),
    and permission problems (PERM-*). Each finding includes a code, severity,
    title, detail, and suggested fix.

    Args:
        skill_path: Path to the skill directory.
        verbose: Include INFO-level findings.
        ignore_codes: Comma-separated finding codes to suppress (e.g., "STR-017,SEC-002").
        include_all: Scan entire directory tree instead of just skill-standard directories.
    """
    try:
        from skillctl.eval.cli import run_audit

        ignore_set = set(c.strip() for c in ignore_codes.split(",") if c.strip()) if ignore_codes else None
        report = run_audit(
            skill_path,
            verbose=verbose,
            ignore_codes=ignore_set,
            include_all=include_all,
        )
        return json.dumps({
            "skill_name": report.skill_name,
            "skill_path": report.skill_path,
            "score": report.score,
            "grade": report.grade,
            "passed": report.passed,
            "critical_count": report.critical_count,
            "warning_count": report.warning_count,
            "info_count": report.info_count,
            "findings": [f.to_dict() for f in report.findings],
            "metadata": report.metadata,
        }, indent=2)
    except Exception as e:
        return _error_response(e)


@mcp.tool()
def skillctl_eval_functional(
    skill_path: str,
    evals_path: str | None = None,
    runs_per_eval: int = 1,
    dry_run: bool = False,
    timeout: int = 120,
    agent: str = "claude",
) -> str:
    """Run functional quality evaluation on a skill.

    Tests the skill with and without installation, grading outputs on
    outcome (40%), process (30%), style (20%), and efficiency (10%).
    Requires evals/evals.json in the skill directory.

    Args:
        skill_path: Path to the skill directory.
        evals_path: Path to evals.json (default: <skill>/evals/evals.json).
        runs_per_eval: Number of runs per eval case.
        dry_run: Validate evals without executing.
        timeout: Timeout per agent invocation in seconds.
        agent: Agent runner to use.
    """
    try:
        from skillctl.eval.functional import run_functional_eval
        exit_code = run_functional_eval(
            skill_path,
            evals_path=evals_path,
            runs_per_eval=runs_per_eval,
            format="json",
            dry_run=dry_run,
            timeout=timeout,
            agent=agent,
        )

        report_path = Path(skill_path) / "evals" / "benchmark.json"
        if report_path.exists():
            report = json.loads(report_path.read_text())
            report["exit_code"] = exit_code
            return json.dumps(report, indent=2)

        return json.dumps({"exit_code": exit_code, "format": "json"})
    except Exception as e:
        return _error_response(e)


@mcp.tool()
def skillctl_eval_trigger(
    skill_path: str,
    queries_path: str | None = None,
    runs_per_query: int = 3,
    dry_run: bool = False,
    timeout: int = 60,
    agent: str = "claude",
) -> str:
    """Run trigger reliability evaluation on a skill.

    Tests whether the skill's description causes correct activation
    for a set of queries, measuring precision and recall.
    Requires evals/eval_queries.json in the skill directory.

    Args:
        skill_path: Path to the skill directory.
        queries_path: Path to eval_queries.json (default: <skill>/evals/eval_queries.json).
        runs_per_query: Number of runs per query.
        dry_run: Validate queries without executing.
        timeout: Timeout per agent invocation in seconds.
        agent: Agent runner to use.
    """
    try:
        from skillctl.eval.trigger import run_trigger_eval
        exit_code = run_trigger_eval(
            skill_path,
            queries_path=queries_path,
            runs_per_query=runs_per_query,
            format="json",
            dry_run=dry_run,
            timeout=timeout,
            agent=agent,
        )

        report_path = Path(skill_path) / "evals" / "trigger_report.json"
        if report_path.exists():
            report = json.loads(report_path.read_text())
            report["exit_code"] = exit_code
            return json.dumps(report, indent=2)

        return json.dumps({"exit_code": exit_code, "format": "json"})
    except Exception as e:
        return _error_response(e)


@mcp.tool()
def skillctl_eval_report(
    skill_path: str,
    skip_audit: bool = False,
    skip_functional: bool = False,
    skip_trigger: bool = False,
    dry_run: bool = False,
    timeout: int = 120,
    agent: str = "claude",
    include_all: bool = False,
) -> str:
    """Run a unified evaluation report combining audit, functional, and trigger scores.

    Weighted: 40% audit + 40% functional + 20% trigger.
    Produces an overall grade and pass/fail decision.

    Args:
        skill_path: Path to the skill directory.
        skip_audit: Skip the audit phase.
        skip_functional: Skip functional evaluation.
        skip_trigger: Skip trigger evaluation.
        dry_run: Validate inputs without executing agent calls.
        timeout: Timeout per agent invocation in seconds.
        agent: Agent runner to use.
        include_all: Audit scans entire directory tree.
    """
    try:
        from skillctl.eval.unified_report import run_unified_report
        exit_code = run_unified_report(
            skill_path,
            format="json",
            include_audit=not skip_audit,
            include_functional=not skip_functional,
            include_trigger=not skip_trigger,
            dry_run=dry_run,
            timeout=timeout,
            agent=agent,
            include_all=include_all,
        )

        report_path = Path(skill_path) / "evals" / "report.json"
        if report_path.exists():
            report = json.loads(report_path.read_text())
            report["exit_code"] = exit_code
            return json.dumps(report, indent=2)

        return json.dumps({"exit_code": exit_code, "format": "json"})
    except Exception as e:
        return _error_response(e)


# ---------------------------------------------------------------------------
# Optimizer
# ---------------------------------------------------------------------------


@mcp.tool()
def skillctl_optimize(
    skill_path: str,
    num_variants: int = 3,
    budget_usd: float = 10.0,
    max_iterations: int = 50,
    threshold: float = 0.05,
    dry_run: bool = False,
    approve: bool = False,
    model: str = "bedrock/us.anthropic.claude-opus-4-6-v1",
    timeout: int = 120,
    agent: str = "claude",
) -> str:
    """Run the automated skill optimizer.

    Iteratively generates variants via LLM, evaluates them, and promotes
    the best one. Terminates on plateau, budget exhaustion, or iteration cap.

    Requires the [optimize] extra (litellm).

    Args:
        skill_path: Path to the skill directory.
        num_variants: Number of variants to generate per cycle.
        budget_usd: Maximum spend in USD.
        max_iterations: Maximum optimization cycles.
        threshold: Minimum score improvement to accept a variant.
        dry_run: Run without writing changes.
        approve: Auto-approve promotion without confirmation.
        model: LiteLLM model ID for variant generation.
        timeout: Timeout per agent invocation in seconds.
        agent: Agent runner to use.
    """
    try:
        from skillctl.optimize.loop import run_optimization
        from skillctl.optimize.types import OptimizeConfig

        config = OptimizeConfig(
            skill_path=skill_path,
            num_variants=num_variants,
            budget_usd=budget_usd,
            max_iterations=max_iterations,
            threshold=threshold,
            dry_run=dry_run,
            approve=approve,
            model=model,
            timeout=timeout,
            agent=agent,
        )
        run = run_optimization(config)
        return json.dumps(run.to_dict(), indent=2)
    except Exception as e:
        return _error_response(e)


@mcp.tool()
def skillctl_optimize_history(skill_name: str | None = None) -> str:
    """List past optimization runs, optionally filtered by skill name.

    Args:
        skill_name: Filter to runs for this skill name (e.g., "my-org/my-skill").
    """
    try:
        from skillctl.optimize.provenance import ProvenanceStore
        runs = ProvenanceStore.list_runs(skill_name=skill_name)
        return json.dumps({
            "count": len(runs),
            "runs": [r.to_dict() for r in runs],
        }, indent=2)
    except Exception as e:
        return _error_response(e)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    mcp.run(transport="stdio")
