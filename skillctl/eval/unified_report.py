"""Unified deterministic skill report (Milestone 0).

Combines the security audit and the schema-contract validation into a
single governance score. Both inputs are fully deterministic, so the
overall score is reproducible: running it N times on unchanged inputs
yields the identical result. This replaced the previous non-deterministic
LLM-as-judge functional/trigger evaluation.

    overall = 0.80 * security_audit + 0.20 * schema_contract
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Optional

from skillctl.eval.cli import run_audit
from skillctl.eval.contract import contract_grade, validate_contract

# ---------------------------------------------------------------------------
# Scoring weights (deterministic governance score)
# ---------------------------------------------------------------------------

SECURITY_AUDIT_WEIGHT = 0.80
CONTRACT_VALIDATION_WEIGHT = 0.20


# ---------------------------------------------------------------------------
# Grading helpers
# ---------------------------------------------------------------------------


def _letter_grade(score: float) -> str:
    """Map a 0-1 score to a letter grade."""
    if score >= 0.9:
        return "A"
    elif score >= 0.8:
        return "B"
    elif score >= 0.7:
        return "C"
    elif score >= 0.6:
        return "D"
    else:
        return "F"


def _bar(value: float, width: int = 10) -> str:
    """Render a value (0-1) as a block bar."""
    filled = round(value * width)
    return "\u2588" * filled + "\u2591" * (width - filled)


def compute_weighted_score(
    audit_score: Optional[float],
    contract_score: Optional[float],
) -> float:
    """Compute overall 0-1 score from the two deterministic components.

    Weights: security audit = 80%, schema contract = 20%.
    If a component is None (skipped), its weight is redistributed to the
    remaining component so the score still spans 0-1.
    """
    components: list[tuple[float, float]] = []  # (score, weight)
    if audit_score is not None:
        components.append((audit_score, SECURITY_AUDIT_WEIGHT))
    if contract_score is not None:
        components.append((contract_score, CONTRACT_VALIDATION_WEIGHT))

    if not components:
        return 0.0

    total_weight = sum(w for _, w in components)
    return sum(s * (w / total_weight) for s, w in components)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def run_unified_report(
    skill_path: str,
    format: str = "text",
    output_path: Optional[str] = None,
    include_audit: bool = True,
    include_contract: bool = True,
    include_all: bool = False,
) -> int:
    """Run the deterministic evaluation and produce a unified report.

    Args:
        skill_path: Path to the skill directory.
        format: Output format ("text", "json", or "html").
        output_path: Path to write report file (default: <skill>/evals/report.json).
        include_audit: Run the security audit phase.
        include_contract: Run the schema-contract validation phase.
        include_all: If True, audit scans the entire directory tree.

    Returns:
        Exit code: 0 = passed, 1 = failed.
    """
    path = Path(skill_path).resolve()
    skill_name = _read_skill_name(path) or path.name

    sections: dict = {}
    audit_norm: Optional[float] = None
    contract_norm: Optional[float] = None
    # A report with no checks is not a successful governance decision.
    overall_passed = include_audit or include_contract

    # ---- Security audit (80%) ----
    if include_audit:
        try:
            report = run_audit(str(path), include_all=include_all)
            audit_norm = report.score / 100.0
            sections["audit"] = {
                "score": report.score,
                "grade": report.grade,
                "passed": report.passed,
                "normalized": round(audit_norm, 4),
                "critical": report.critical_count,
                "warning": report.warning_count,
                "info": report.info_count,
            }
            if not report.passed:
                overall_passed = False
        except Exception as exc:
            print(f"Audit error: {exc}", file=sys.stderr)
            sections["audit"] = {"error": str(exc)}
            overall_passed = False
    else:
        sections["audit"] = {"skipped": True}

    # ---- Schema contract (20%) ----
    if include_contract:
        try:
            contract = validate_contract(str(path))
            contract_norm = contract.score
            sections["contract"] = {
                "score": round(contract.score, 4),
                "grade": contract_grade(contract.score),
                "passed": contract.passed,
                "checks": [c.to_dict() for c in contract.checks],
            }
            if not contract.passed:
                overall_passed = False
        except Exception as exc:
            print(f"Contract error: {exc}", file=sys.stderr)
            sections["contract"] = {"error": str(exc)}
            overall_passed = False
    else:
        sections["contract"] = {"skipped": True}

    # ---- Overall grade ----
    overall_score = compute_weighted_score(audit_norm, contract_norm)
    overall_grade = _letter_grade(overall_score)

    report_data = {
        "skill_name": skill_name,
        "skill_path": str(path),
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "overall_score": round(overall_score, 4),
        "overall_grade": overall_grade,
        "passed": overall_passed,
        "weights": {
            "security_audit": SECURITY_AUDIT_WEIGHT,
            "schema_contract": CONTRACT_VALIDATION_WEIGHT,
        },
        "sections": sections,
    }

    # ---- Write report file ----
    if output_path:
        out_file = Path(output_path)
    else:
        out_file = path / "evals" / "report.json"
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(json.dumps(report_data, indent=2))

    # ---- Print ----
    if format == "json":
        print(json.dumps(report_data, indent=2))
    elif format == "html":
        from skillctl.eval.html_report import generate_html_report

        html_content = generate_html_report(report_data)
        html_file = out_file.with_suffix(".html")
        html_file.write_text(html_content, encoding="utf-8")
        print(html_content)
        print(f"\nHTML report written to: {html_file}", file=sys.stderr)
    else:
        _print_text_report(report_data)

    return 0 if overall_passed else 1


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

from skillctl.utils import read_skill_name_from_frontmatter as _read_skill_name  # noqa: E402


def _print_text_report(data: dict) -> None:
    """Print a clean text summary of the unified report."""
    w = 43
    sections = data.get("sections", {})

    print()
    print("\u2550" * w)
    print("  Unified Skill Report (deterministic)")
    print("\u2550" * w)
    print(f"  Skill: {data['skill_name']}")
    print(f"  Overall Grade: {data['overall_grade']} ({data['overall_score']:.2f})")
    print("\u2500" * w)

    # Audit (80%)
    audit = sections.get("audit", {})
    if "error" not in audit and "skipped" not in audit and audit:
        score = audit["score"]
        grade = audit["grade"]
        norm = audit["normalized"]
        print(f"  Security (80%): {score}/100 ({grade})  {_bar(norm)}")

    # Contract (20%)
    contract = sections.get("contract", {})
    if "error" not in contract and "skipped" not in contract and contract:
        cscore = contract["score"]
        cgrade = contract["grade"]
        passed_n = sum(1 for c in contract.get("checks", []) if c.get("passed"))
        total_n = len(contract.get("checks", []))
        print(f"  Contract (20%): {passed_n}/{total_n} checks ({cgrade})  {_bar(cscore)}")
        for c in contract.get("checks", []):
            mark = "\u2713" if c.get("passed") else "\u2717"
            print(f"      {mark} {c.get('name')}")

    print("\u2500" * w)

    if data["passed"]:
        print("  Result: PASSED")
    else:
        print("  Result: FAILED")
    print("\u2550" * w)
    print()
