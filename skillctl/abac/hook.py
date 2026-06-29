"""ABAC as a runtime policy hook.

Bridges the ABAC engine into the M2 policy pipeline: builds an
``AttributeContext`` from the runtime ``PolicyContext`` and denies the
invocation when ABAC denies.
"""

from __future__ import annotations

from datetime import datetime

from skillctl.abac.engine import ABACEngine
from skillctl.abac.models import AttributeContext
from skillctl.policy.hooks import PolicyContext, PolicyDecision, PolicyHook, PolicyResult


class ABACPolicyHook(PolicyHook):
    """Runs ABAC evaluation as a pre-execution policy hook."""

    def __init__(self, engine: ABACEngine):
        self._engine = engine

    @property
    def name(self) -> str:
        return "abac"

    @property
    def description(self) -> str:
        return f"ABAC evaluation over {len(self._engine.policies)} policy/policies"

    @property
    def phase(self) -> str:
        return "pre"

    def build_context(self, ctx: PolicyContext) -> AttributeContext:
        hour = None
        weekday = None
        if ctx.timestamp:
            try:
                dt = datetime.fromisoformat(ctx.timestamp)
                hour = dt.hour
                weekday = dt.weekday()
            except ValueError:
                pass
        return AttributeContext(
            subject={
                "id": ctx.actor_id,
                "roles": ctx.actor_roles,
                "namespace": ctx.actor_namespace,
                **ctx.attributes.get("subject", {}),
            },
            resource={
                "skill": ctx.skill_name,
                "version": ctx.skill_version,
                "namespace": ctx.skill_namespace,
                "category": ctx.skill_category,
                **ctx.attributes.get("resource", {}),
            },
            action=ctx.attributes.get("action", "invoke"),
            environment={
                "environment": ctx.environment,
                "hour": hour,
                "weekday": weekday,
                **ctx.attributes.get("environment", {}),
            },
        )

    async def evaluate_pre(self, context: PolicyContext) -> PolicyResult:
        decision = self._engine.evaluate(self.build_context(context))
        return PolicyResult(
            decision=PolicyDecision.ALLOW if decision.allowed else PolicyDecision.DENY,
            reason=decision.reason,
            hook_name=self.name,
            details={"matched_policy": decision.matched_policy},
        )
