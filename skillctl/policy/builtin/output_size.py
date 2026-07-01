"""Output size limit hook (post-execution).

Prevents skills from returning excessively large outputs (possible data
exfiltration or runaway generation). Blocks or truncates.
"""

from __future__ import annotations

from skillctl.policy.hooks import PolicyContext, PolicyDecision, PolicyHook, PolicyResult


class OutputSizeHook(PolicyHook):
    """Limits the size of skill outputs."""

    def __init__(self, max_bytes: int = 1_000_000, max_lines: int = 10_000, truncate: bool = False) -> None:
        self._max_bytes = max_bytes
        self._max_lines = max_lines
        self._truncate = truncate

    @property
    def name(self) -> str:
        return "output-size"

    @property
    def description(self) -> str:
        return f"Limits output to {self._max_bytes} bytes / {self._max_lines} lines"

    @property
    def phase(self) -> str:
        return "post"

    async def evaluate_post(self, context: PolicyContext) -> PolicyResult:
        output_str = str(context.output_result)
        output_bytes = len(output_str.encode("utf-8"))
        output_lines = output_str.count("\n") + 1

        if output_bytes > self._max_bytes:
            if self._truncate:
                truncated = output_str.encode("utf-8")[: self._max_bytes].decode("utf-8", errors="ignore")
                return PolicyResult(
                    decision=PolicyDecision.REDACT,
                    reason=f"Output truncated: {output_bytes} bytes > {self._max_bytes} limit",
                    hook_name=self.name,
                    modified_output=truncated + "\n[TRUNCATED]",
                )
            return PolicyResult(
                decision=PolicyDecision.DENY,
                reason=f"Output too large: {output_bytes} bytes > {self._max_bytes} limit",
                hook_name=self.name,
            )

        if output_lines > self._max_lines:
            if self._truncate:
                lines = output_str.split("\n")[: self._max_lines]
                return PolicyResult(
                    decision=PolicyDecision.REDACT,
                    reason=f"Output truncated: {output_lines} lines > {self._max_lines} limit",
                    hook_name=self.name,
                    modified_output="\n".join(lines) + "\n[TRUNCATED]",
                )
            return PolicyResult(
                decision=PolicyDecision.DENY,
                reason=f"Output too many lines: {output_lines} > {self._max_lines} limit",
                hook_name=self.name,
            )

        return PolicyResult(
            decision=PolicyDecision.ALLOW,
            reason="Output within size limits",
            hook_name=self.name,
        )
