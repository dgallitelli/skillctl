"""PII redaction post-hook.

Best-effort regex scanner for PII in skill outputs. Redacts (default) or blocks.
For production PII compliance, integrate a dedicated service via a custom hook.
"""

from __future__ import annotations

import re

from skillctl.policy.hooks import PolicyContext, PolicyDecision, PolicyHook, PolicyResult


class PIIRedactionHook(PolicyHook):
    """Redacts PII from skill outputs."""

    DEFAULT_PATTERNS = {
        "email": r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b",
        "ssn": r"\b\d{3}-\d{2}-\d{4}\b",
        "credit_card": r"\b(?:\d{4}[-\s]?){3}\d{4}\b",
        "phone": r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b",
        "ipv4": r"\b(?:\d{1,3}\.){3}\d{1,3}\b",
    }

    def __init__(self, patterns: dict | None = None, replacement: str = "[REDACTED]", mode: str = "redact") -> None:
        self._patterns = patterns or self.DEFAULT_PATTERNS
        self._replacement = replacement
        self._mode = mode

    @property
    def name(self) -> str:
        return "pii-redaction"

    @property
    def description(self) -> str:
        return f"Scans outputs for PII ({', '.join(self._patterns.keys())})"

    @property
    def phase(self) -> str:
        return "post"

    async def evaluate_post(self, context: PolicyContext) -> PolicyResult:
        output_str = str(context.output_result)
        findings = []
        # Order matters: redact more specific patterns (ssn, credit_card, phone)
        # before generic ones so an SSN isn't partially eaten by the phone regex.
        for pattern_name, regex in self._patterns.items():
            matches = re.findall(regex, output_str)
            if matches:
                findings.append({"type": pattern_name, "count": len(matches)})

        if not findings:
            return PolicyResult(
                decision=PolicyDecision.ALLOW,
                reason="No PII detected in output",
                hook_name=self.name,
            )

        if self._mode == "block":
            return PolicyResult(
                decision=PolicyDecision.DENY,
                reason=f"PII detected in output: {findings}",
                hook_name=self.name,
                details={"findings": findings},
            )

        redacted_output = output_str
        for pattern_name, regex in self._patterns.items():
            redacted_output = re.sub(regex, f"{self._replacement}:{pattern_name}", redacted_output)

        return PolicyResult(
            decision=PolicyDecision.REDACT,
            reason=f"Redacted {sum(f['count'] for f in findings)} PII instance(s)",
            hook_name=self.name,
            modified_output=redacted_output,
            details={"findings": findings},
        )
