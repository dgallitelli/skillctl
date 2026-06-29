"""Open Policy Agent (OPA) integration hook (pre-execution).

Delegates policy decisions to an external OPA server so organizations can
reuse existing Rego policies. POSTs the PolicyContext to
``/v1/data/{policy_path}`` and interprets ``{"result": {"allow", "reason"}}``.
"""

from __future__ import annotations

from skillctl.policy.hooks import PolicyContext, PolicyDecision, PolicyHook, PolicyResult


class OPAHook(PolicyHook):
    """Delegates policy evaluation to an OPA server.

    Args:
        opa_url: Base URL of the OPA server (e.g. "http://localhost:8181").
        policy_path: OPA data path (e.g. "skillsops/authz").
        timeout_seconds: HTTP timeout.
        fail_mode: "open" (allow on OPA failure, WARN) or "closed" (deny).
    """

    def __init__(
        self,
        opa_url: str,
        policy_path: str = "skillsops/authz",
        timeout_seconds: float = 5.0,
        fail_mode: str = "closed",
    ) -> None:
        self._opa_url = opa_url.rstrip("/")
        self._policy_path = policy_path
        self._timeout = timeout_seconds
        self._fail_mode = fail_mode

    @property
    def name(self) -> str:
        return "opa"

    @property
    def description(self) -> str:
        return f"OPA policy evaluation at {self._opa_url}/v1/data/{self._policy_path}"

    @property
    def phase(self) -> str:
        return "pre"

    async def evaluate_pre(self, context: PolicyContext) -> PolicyResult:
        import httpx  # Optional dependency (skillsops[policy-opa])

        input_payload = {
            "input": {
                "actor": context.actor_id,
                "roles": context.actor_roles,
                "namespace": context.skill_namespace,
                "skill": context.skill_name,
                "version": context.skill_version,
                "environment": context.environment,
                "timestamp": context.timestamp,
                "attributes": context.attributes,
            }
        }

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(
                    f"{self._opa_url}/v1/data/{self._policy_path}",
                    json=input_payload,
                )
                response.raise_for_status()
                result = response.json().get("result", {})

            if isinstance(result, dict) and result.get("allow", False):
                return PolicyResult(
                    decision=PolicyDecision.ALLOW,
                    reason=result.get("reason", "OPA allowed"),
                    hook_name=self.name,
                    details=result.get("details", {}),
                )
            reason = result.get("reason", "OPA denied") if isinstance(result, dict) else "OPA denied"
            return PolicyResult(
                decision=PolicyDecision.DENY,
                reason=reason,
                hook_name=self.name,
                details=result.get("details", {}) if isinstance(result, dict) else {},
            )

        except Exception as e:  # noqa: BLE001 - fail mode handles all errors
            if self._fail_mode == "open":
                return PolicyResult(
                    decision=PolicyDecision.WARN,
                    reason=f"OPA unreachable ({e}), fail-open: allowing",
                    hook_name=self.name,
                )
            return PolicyResult(
                decision=PolicyDecision.DENY,
                reason=f"OPA unreachable ({e}), fail-closed: denying",
                hook_name=self.name,
            )
