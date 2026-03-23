"""LLM-powered variant generation targeting identified weaknesses.

Produces N candidate SKILL.md rewrites, each targeting a specific weakness
from the failure analysis via round-robin assignment.
"""

from __future__ import annotations

import hashlib
import re

from skillctl.eval.cost import estimate_cost
from skillctl.optimize.llm_client import LLMClient
from skillctl.optimize.types import FailureAnalysis, TokenUsage, Variant

VARIANT_SYSTEM_PROMPT = """\
You are a skill rewriter. Given an existing SKILL.md and a specific weakness \
identified during evaluation, produce a rewritten SKILL.md that addresses the \
weakness while preserving the skill's core purpose.

Rules:
- Return the COMPLETE rewritten SKILL.md content.
- You may wrap the content in markdown code fences (```markdown ... ```), but \
the content inside must be the full SKILL.md.
- Preserve the skill's original intent, structure, and key instructions.
- Make targeted changes that specifically address the identified weakness.
- Do NOT remove existing functionality unless it directly conflicts with the fix.
- Keep the same general format and section headings where possible.\
"""


def generate_variants(
    skill_content: str,
    failure_analysis: FailureAnalysis,
    num_variants: int = 3,
    llm_client: LLMClient | None = None,
) -> list[Variant]:
    """Generate N candidate skill rewrites targeting identified weaknesses.

    Each variant targets a specific weakness from the failure analysis using
    round-robin assignment when num_variants exceeds the number of weaknesses.
    """
    parent_hash = hashlib.sha256(skill_content.encode()).hexdigest()
    variants: list[Variant] = []

    for i in range(num_variants):
        # Round-robin weakness assignment
        weakness = failure_analysis.weaknesses[i % len(failure_analysis.weaknesses)]

        prompt = _build_variant_prompt(skill_content, weakness, i, num_variants)
        response = llm_client.call(system=VARIANT_SYSTEM_PROMPT, prompt=prompt)
        variant_content = _extract_skill_content(response.content)
        variant_hash = hashlib.sha256(variant_content.encode()).hexdigest()

        cost = estimate_cost(response.input_tokens, response.output_tokens, llm_client.model)

        variants.append(Variant(
            id=variant_hash[:12],
            content=variant_content,
            hypothesis=weakness.hypothesis,
            target_weakness=weakness.description,
            parent_id=parent_hash[:12],
            tokens_used=TokenUsage(
                input_tokens=response.input_tokens,
                output_tokens=response.output_tokens,
                cost_usd=cost["total_cost"],
            ),
        ))

    return variants


def _build_variant_prompt(
    skill_content: str,
    weakness,
    variant_index: int,
    total_variants: int,
) -> str:
    """Build a prompt instructing the LLM to rewrite the skill targeting a weakness."""
    parts = [
        "## Current SKILL.md\n\n",
        skill_content,
        "\n\n## Identified Weakness\n\n",
        f"**Category:** {weakness.category}\n",
        f"**Description:** {weakness.description}\n",
        f"**Severity:** {weakness.severity}\n",
    ]

    if weakness.evidence:
        parts.append(f"**Evidence:** {'; '.join(weakness.evidence)}\n")

    parts.append(f"**Hypothesis:** {weakness.hypothesis}\n")

    parts.append(
        f"\n## Task\n\n"
        f"This is variant {variant_index + 1} of {total_variants}. "
        f"Rewrite the SKILL.md above to address the identified weakness. "
        f"Focus specifically on: {weakness.hypothesis}\n\n"
        f"Return the complete rewritten SKILL.md content."
    )

    return "".join(parts)


def _extract_skill_content(response: str) -> str:
    """Extract SKILL.md content from LLM response, stripping code fences if present."""
    # Try to extract content from markdown code fences
    # Match ```markdown ... ``` or ``` ... ```
    pattern = r"```(?:markdown|md)?\s*\n(.*?)```"
    match = re.search(pattern, response, re.DOTALL)
    if match:
        return match.group(1).strip()

    # No code fences found — return the response as-is, stripped
    return response.strip()
