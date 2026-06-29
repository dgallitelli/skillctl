"""Policy evaluation engine.

Orchestrates a pipeline of policy hooks:
- Pre-hooks run in registration order; first DENY short-circuits.
- Post-hooks run in registration order; REDACT results chain (each hook sees
  the previous hook's modified output).
- Every individual hook result is logged to the audit chain (even ALLOW).
"""

from __future__ import annotations

from typing import Awaitable, Callable, Optional

from skillctl.policy.hooks import PolicyContext, PolicyDecision, PolicyHook, PolicyResult

AuditCallback = Callable[[dict], Awaitable[None]]


class PolicyEngine:
    """Central policy evaluation engine."""

    def __init__(self) -> None:
        self._hooks: list[PolicyHook] = []
        self._audit_callback: Optional[AuditCallback] = None
        self._otel_tracer = None

    def register(self, hook: PolicyHook) -> None:
        """Register a policy hook. Hooks execute in registration order."""
        self._hooks.append(hook)

    def unregister(self, hook_name: str) -> None:
        self._hooks = [h for h in self._hooks if h.name != hook_name]

    def set_audit_callback(self, callback: Optional[AuditCallback]) -> None:
        """Set an async callback invoked for every policy decision."""
        self._audit_callback = callback

    @property
    def hooks(self) -> list[PolicyHook]:
        return list(self._hooks)

    async def evaluate_pre(self, context: PolicyContext) -> PolicyResult:
        """Run all pre-execution hooks; short-circuit on the first DENY."""
        results: list[PolicyResult] = []

        for hook in self._hooks:
            if hook.phase in ("pre", "both"):
                result = await hook.evaluate_pre(context)
                results.append(result)
                await self._log_policy_decision(context, result, phase="pre")
                if result.decision == PolicyDecision.DENY:
                    return result

        warnings = [r for r in results if r.decision == PolicyDecision.WARN]
        if warnings:
            return PolicyResult(
                decision=PolicyDecision.WARN,
                reason=f"{len(warnings)} warning(s): {'; '.join(w.reason for w in warnings)}",
                hook_name="policy_engine",
                details={"warnings": [w.to_dict() for w in warnings]},
            )

        return PolicyResult(
            decision=PolicyDecision.ALLOW,
            reason=f"All {len(results)} pre-hook(s) passed",
            hook_name="policy_engine",
        )

    async def evaluate_post(self, context: PolicyContext) -> PolicyResult:
        """Run all post-execution hooks; chain REDACT modifications."""
        current_output = context.output_result
        redacted = False

        for hook in self._hooks:
            if hook.phase in ("post", "both"):
                context.output_result = current_output
                result = await hook.evaluate_post(context)
                await self._log_policy_decision(context, result, phase="post")

                if result.decision == PolicyDecision.DENY:
                    return result
                if result.decision == PolicyDecision.REDACT:
                    current_output = result.modified_output
                    redacted = True

        if redacted:
            return PolicyResult(
                decision=PolicyDecision.REDACT,
                reason="Output modified by post-execution policies",
                hook_name="policy_engine",
                modified_output=current_output,
            )

        return PolicyResult(
            decision=PolicyDecision.ALLOW,
            reason="All post-hook(s) passed",
            hook_name="policy_engine",
        )

    async def _log_policy_decision(self, context: PolicyContext, result: PolicyResult, phase: str) -> None:
        if self._audit_callback is None:
            return
        await self._audit_callback(
            {
                "type": "policy_decision",
                "phase": phase,
                "hook_name": result.hook_name,
                "decision": result.decision.value,
                "reason": result.reason,
                "actor": context.actor_id,
                "skill": f"{context.skill_name}@{context.skill_version}",
                "namespace": context.skill_namespace,
                "invocation_id": context.invocation_id,
                "timestamp": context.timestamp,
                "details": result.details,
            }
        )
