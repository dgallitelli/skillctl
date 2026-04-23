"""EvalRunner — wraps skillctl.eval to evaluate a skill and return structured results.

Handles temporary SKILL.md content swaps with guaranteed restoration,
parses unified report JSON into EvalResult, and degrades gracefully
when eval components (e.g. evals/evals.json) are missing.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from skillctl.eval.unified_report import run_unified_report

from skillctl.optimize.types import EvalResult


def evaluate_skill(
    skill_path: str,
    content: Optional[str] = None,
    timeout: int = 120,
    agent: str = "claude",
) -> EvalResult:
    """Run the full eval suite on a skill and return structured results.

    If *content* is provided, it is written to ``{skill_path}/SKILL.md``
    before evaluation.  The original file is **always** restored afterward
    (even when the evaluation raises).

    Returns an ``EvalResult`` with ``overall_score=None`` on any
    unrecoverable evaluation failure.
    """
    path = Path(skill_path).resolve()
    skill_md = path / "SKILL.md"

    original_content: Optional[str] = None

    try:
        # --- Swap SKILL.md if variant content supplied ---
        if content is not None:
            if skill_md.is_file():
                original_content = skill_md.read_text()
            skill_md.write_text(content)

        # --- Run unified report ---
        try:
            exit_code = run_unified_report(
                str(path),
                format="json",
                timeout=timeout,
                agent=agent,
            )
        except Exception:
            return _failure_result()

        # --- Parse report.json ---
        report_file = path / "evals" / "report.json"
        if not report_file.is_file():
            return _failure_result()

        try:
            report_data = json.loads(report_file.read_text())
        except (json.JSONDecodeError, OSError):
            return _failure_result()

        return _parse_report(report_data, str(report_file))

    finally:
        # --- Guaranteed restoration of original SKILL.md ---
        if content is not None:
            if original_content is not None:
                skill_md.write_text(original_content)
            elif skill_md.is_file():
                skill_md.unlink()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _parse_report(data: dict, report_path: str) -> EvalResult:
    """Convert a unified report dict into an ``EvalResult``."""
    sections = data.get("sections", {})

    audit_score = _extract_normalized(sections.get("audit"))
    functional_score = _extract_normalized(sections.get("functional"))
    trigger_score = _extract_normalized(sections.get("trigger"))

    audit_findings = []
    audit_section = sections.get("audit", {})
    for f in audit_section.get("findings", []):
        audit_findings.append({
            "code": f.get("code", ""),
            "severity": f.get("severity", ""),
            "title": f.get("title", ""),
            "detail": f.get("detail", ""),
            "file_path": f.get("file_path", ""),
        })

    return EvalResult(
        overall_score=data.get("overall_score"),
        overall_grade=data.get("overall_grade", "F"),
        passed=data.get("passed", False),
        audit_score=audit_score,
        functional_score=functional_score,
        trigger_score=trigger_score,
        audit_findings=audit_findings,
        sections=sections,
        report_path=report_path,
    )


def _extract_normalized(section: Optional[dict]) -> Optional[float]:
    """Pull the normalized 0-1 score from a report section, if present."""
    if section is None:
        return None
    if "error" in section or section.get("skipped"):
        return None
    # Audit uses "normalized", functional uses "overall", trigger uses "pass_rate"
    for key in ("normalized", "overall", "pass_rate"):
        val = section.get(key)
        if val is not None:
            return float(val)
    return None


def _failure_result() -> EvalResult:
    """Return a sentinel ``EvalResult`` indicating evaluation failure."""
    return EvalResult(
        overall_score=None,
        overall_grade="F",
        passed=False,
        sections={},
        report_path="",
    )
