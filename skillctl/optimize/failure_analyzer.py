"""LLM-powered failure pattern extraction from eval results.

Reads eval results and uses an LLM to identify actionable failure patterns,
returning a ranked list of weaknesses with targeted hypotheses.
"""

from __future__ import annotations

import json

from skillctl.eval.cost import estimate_cost
from skillctl.optimize.llm_client import LLMClient
from skillctl.optimize.types import EvalResult, FailureAnalysis, TokenUsage, Weakness

ANALYSIS_SYSTEM_PROMPT = """\
You are a skill evaluation analyst. Given a skill's content and evidence of \
evaluation failures, identify the root-cause weaknesses and suggest fixes.

Return your analysis as JSON with this exact structure:
{
  "weaknesses": [
    {
      "category": "audit" | "functional" | "trigger",
      "description": "human-readable description of the weakness",
      "severity": "high" | "medium" | "low",
      "evidence": ["specific finding or failing assertion", ...],
      "hypothesis": "what change to the skill content might fix this"
    }
  ]
}

Rules:
- Return ONLY valid JSON, no markdown fences or extra text.
- Include at least one weakness.
- Order weaknesses from most to least severe.
- Each weakness must have a non-empty hypothesis.
- category must be one of: audit, functional, trigger.
- severity must be one of: high, medium, low.\
"""

_SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def analyze_failures(
    eval_result: EvalResult,
    skill_content: str,
    llm_client: LLMClient,
) -> FailureAnalysis:
    """Use LLM to identify failure patterns from eval results."""
    evidence = _extract_evidence(eval_result)
    prompt = _build_analysis_prompt(skill_content, evidence)
    response = llm_client.call(system=ANALYSIS_SYSTEM_PROMPT, prompt=prompt)

    weaknesses = _parse_weaknesses(response.content)
    weaknesses.sort(key=lambda w: _SEVERITY_ORDER.get(w.severity, 3))

    cost = estimate_cost(response.input_tokens, response.output_tokens, llm_client.model)

    return FailureAnalysis(
        weaknesses=weaknesses,
        overall_summary=response.content,
        tokens_used=TokenUsage(
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            cost_usd=cost["total_cost"],
        ),
    )


def _extract_evidence(eval_result: EvalResult) -> list[dict]:
    """Extract structured evidence from each eval section."""
    evidence: list[dict] = []

    # Audit findings (prefer structured audit_findings when available)
    if eval_result.audit_findings:
        evidence.append(
            {
                "category": "audit",
                "score": eval_result.sections.get("audit", {}).get("score", 0),
                "issues": eval_result.audit_findings,
            }
        )
    elif "audit" in eval_result.sections:
        audit = eval_result.sections["audit"]
        if audit.get("critical", 0) > 0 or audit.get("warning", 0) > 0:
            evidence.append(
                {
                    "category": "audit",
                    "score": audit.get("score", 0),
                    "issues": audit.get("findings", []),
                }
            )

    # Functional eval failures
    if "functional" in eval_result.sections:
        func = eval_result.sections["functional"]
        if func.get("scores"):
            failing = {k: v for k, v in func["scores"].items() if v < 0.7}
            if failing:
                evidence.append(
                    {
                        "category": "functional",
                        "overall": func.get("overall", 0),
                        "failing_dimensions": failing,
                    }
                )

    # Trigger eval failures
    if "trigger" in eval_result.sections:
        trig = eval_result.sections["trigger"]
        if trig.get("pass_rate", 1.0) < 0.8:
            evidence.append(
                {
                    "category": "trigger",
                    "pass_rate": trig.get("pass_rate", 0),
                }
            )

    return evidence


def _build_analysis_prompt(skill_content: str, evidence: list[dict]) -> str:
    """Build a structured prompt with skill content and evidence."""
    parts = [
        "## Skill Content\n",
        skill_content,
        "\n\n## Evaluation Evidence\n",
    ]

    if not evidence:
        parts.append("No specific failures detected, but the overall score is low.")
    else:
        for item in evidence:
            parts.append(f"\n### {item['category'].title()} Section\n")
            parts.append(json.dumps(item, indent=2))

    parts.append(
        "\n\n## Task\n"
        "Analyze the skill content and evaluation evidence above. "
        "Identify the weaknesses that caused the evaluation failures "
        "and suggest specific changes to the skill content that would fix them."
    )

    return "".join(parts)


def _parse_weaknesses(content: str) -> list[Weakness]:
    """Parse LLM response JSON into Weakness objects.

    Falls back to a single high-severity weakness from the raw response
    if JSON parsing fails.
    """
    try:
        data = json.loads(content)
        raw_weaknesses = data.get("weaknesses", [])
        if not raw_weaknesses:
            raise ValueError("Empty weaknesses list")

        weaknesses = []
        for item in raw_weaknesses:
            weaknesses.append(
                Weakness(
                    category=item.get("category", "functional"),
                    description=item.get("description", "Unknown weakness"),
                    severity=item.get("severity", "medium"),
                    evidence=item.get("evidence", []),
                    hypothesis=item.get("hypothesis", "Review and revise the skill content"),
                )
            )
        return weaknesses
    except (json.JSONDecodeError, ValueError, KeyError, TypeError):
        return [
            Weakness(
                category="functional",
                description="Unable to parse structured failure analysis",
                severity="high",
                evidence=[content[:500] if len(content) > 500 else content],
                hypothesis="Review the skill content and address the evaluation failures described in the evidence",
            )
        ]
