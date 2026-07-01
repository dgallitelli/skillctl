"""AWS Cedar policy engine integration hook (pre-execution).

Evaluates skill invocations against Cedar policies. Local mode uses the
optional ``cedarpy`` package against ``.cedar`` files; if it is not installed,
the hook degrades according to ``fail_mode`` rather than crashing.

Cedar request mapping:
    principal: User::"{actor_id}"
    action:    Action::"skill:invoke"
    resource:  Skill::"{skill_namespace}/{skill_name}"
    context:   {environment, hour, roles, attributes}
"""

from __future__ import annotations

from pathlib import Path

from skillctl.policy.hooks import PolicyContext, PolicyDecision, PolicyHook, PolicyResult


class CedarHook(PolicyHook):
    """Evaluates policies using Cedar (local ``cedarpy`` mode)."""

    def __init__(
        self,
        mode: str = "local",
        policies_dir: str | None = None,
        policy_store_id: str | None = None,
        fail_mode: str = "closed",
    ) -> None:
        self._mode = mode
        self._policies_dir = policies_dir
        self._policy_store_id = policy_store_id
        self._fail_mode = fail_mode

    @property
    def name(self) -> str:
        return "cedar"

    @property
    def description(self) -> str:
        return f"Cedar policy evaluation (mode={self._mode})"

    @property
    def phase(self) -> str:
        return "pre"

    async def evaluate_pre(self, context: PolicyContext) -> PolicyResult:
        if self._mode != "local":
            return self._degrade(f"Cedar mode '{self._mode}' not implemented (use 'local')")

        try:
            import cedarpy  # type: ignore[import-not-found]
        except ImportError:
            return self._degrade("cedarpy not installed (pip install 'skillsops[policy-cedar]')")

        try:
            policies = self._load_policies()
            if not policies:
                return self._degrade("No .cedar policy files found")

            from datetime import datetime

            hour = datetime.fromisoformat(context.timestamp).hour if context.timestamp else 0
            request = {
                "principal": f'User::"{context.actor_id}"',
                "action": 'Action::"skill:invoke"',
                "resource": f'Skill::"{context.skill_namespace}/{context.skill_name}"',
                "context": {
                    "environment": context.environment,
                    "hour": hour,
                    "roles": context.actor_roles,
                    **context.attributes,
                },
            }
            result = cedarpy.is_authorized(request, policies, entities=[])
            decision_cls = getattr(cedarpy, "Decision", None)
            allow_value = getattr(decision_cls, "Allow", None)
            allowed = getattr(result, "decision", None) == allow_value
            if allowed:
                return PolicyResult(decision=PolicyDecision.ALLOW, reason="Cedar permitted", hook_name=self.name)
            return PolicyResult(decision=PolicyDecision.DENY, reason="Cedar denied", hook_name=self.name)
        except Exception as e:  # noqa: BLE001
            return self._degrade(f"Cedar evaluation error: {e}")

    def _load_policies(self) -> str:
        if not self._policies_dir:
            return ""
        parts = [p.read_text() for p in Path(self._policies_dir).glob("*.cedar")]
        return "\n".join(parts)

    def _degrade(self, reason: str) -> PolicyResult:
        if self._fail_mode == "open":
            return PolicyResult(decision=PolicyDecision.WARN, reason=f"{reason}; fail-open", hook_name=self.name)
        return PolicyResult(decision=PolicyDecision.DENY, reason=f"{reason}; fail-closed", hook_name=self.name)
