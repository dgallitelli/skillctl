"""Data boundary enforcement hook (pre-execution).

Ensures skills only operate within authorized namespaces and that inputs do
not contain blocked patterns (e.g. external URLs).
"""

from __future__ import annotations

from skillctl.policy.hooks import PolicyContext, PolicyDecision, PolicyHook, PolicyResult


class DataBoundaryHook(PolicyHook):
    """Enforces data access boundaries based on namespace hierarchy."""

    def __init__(self, allowed_namespaces: list[str] | None = None, blocked_patterns: list[str] | None = None) -> None:
        self._allowed = allowed_namespaces or ["*"]
        self._blocked = blocked_patterns or []

    @property
    def name(self) -> str:
        return "data-boundary"

    @property
    def description(self) -> str:
        return f"Restricts data access to namespaces: {self._allowed}"

    @property
    def phase(self) -> str:
        return "pre"

    async def evaluate_pre(self, context: PolicyContext) -> PolicyResult:
        if "*" not in self._allowed:
            if not any(
                context.skill_namespace == ns
                or context.skill_namespace.startswith(ns + "/")
                or context.skill_namespace.startswith(ns)
                for ns in self._allowed
            ):
                return PolicyResult(
                    decision=PolicyDecision.DENY,
                    reason=f"Skill namespace '{context.skill_namespace}' outside allowed boundaries: {self._allowed}",
                    hook_name=self.name,
                )

        input_str = str(context.input_params)
        for pattern in self._blocked:
            if pattern in input_str:
                return PolicyResult(
                    decision=PolicyDecision.DENY,
                    reason=f"Input contains blocked pattern: '{pattern}'",
                    hook_name=self.name,
                    details={"blocked_pattern": pattern},
                )

        return PolicyResult(
            decision=PolicyDecision.ALLOW,
            reason="Within data boundaries",
            hook_name=self.name,
        )
