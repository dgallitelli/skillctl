"""Rate limiting policy hook (pre-execution).

Enforces maximum invocation frequency per actor / skill / namespace using a
sliding window over a SQLite-backed counter store (persists across restarts).
"""

from __future__ import annotations

import time

from skillctl.policy.hooks import PolicyContext, PolicyDecision, PolicyHook, PolicyResult
from skillctl.policy.store import PolicyStore


class RateLimitHook(PolicyHook):
    """Rate-limits skill invocations.

    Args:
        max_per_minute: Max invocations per minute per scope.
        max_per_hour: Max invocations per hour per scope.
        scope: "actor" | "skill" | "namespace".
        store: A :class:`PolicyStore` (required).
    """

    def __init__(
        self,
        max_per_minute: int = 60,
        max_per_hour: int = 1000,
        scope: str = "actor",
        store: PolicyStore | None = None,
    ) -> None:
        if store is None:
            raise ValueError("RateLimitHook requires a PolicyStore (store=...)")
        self._max_per_minute = max_per_minute
        self._max_per_hour = max_per_hour
        self._scope = scope
        self._store = store

    @property
    def name(self) -> str:
        return "rate-limit"

    @property
    def description(self) -> str:
        return f"Limits invocations to {self._max_per_minute}/min, {self._max_per_hour}/hr per {self._scope}"

    @property
    def phase(self) -> str:
        return "pre"

    async def evaluate_pre(self, context: PolicyContext) -> PolicyResult:
        scope_key = self._compute_scope_key(context)
        now = int(time.time())

        minute_count = await self._store.count_in_window(scope_key, now - 60, now)
        if minute_count >= self._max_per_minute:
            return PolicyResult(
                decision=PolicyDecision.DENY,
                reason=f"Rate limit exceeded: {minute_count}/{self._max_per_minute} per minute",
                hook_name=self.name,
                details={"limit": self._max_per_minute, "current": minute_count, "window": "1m"},
            )

        hour_count = await self._store.count_in_window(scope_key, now - 3600, now)
        if hour_count >= self._max_per_hour:
            return PolicyResult(
                decision=PolicyDecision.DENY,
                reason=f"Rate limit exceeded: {hour_count}/{self._max_per_hour} per hour",
                hook_name=self.name,
                details={"limit": self._max_per_hour, "current": hour_count, "window": "1h"},
            )

        await self._store.increment(scope_key, now)
        return PolicyResult(
            decision=PolicyDecision.ALLOW,
            reason=f"Within limits: {minute_count + 1}/{self._max_per_minute}/min",
            hook_name=self.name,
            details={"current": minute_count + 1, "limit": self._max_per_minute},
        )

    def _compute_scope_key(self, context: PolicyContext) -> str:
        if self._scope == "skill":
            return f"{context.actor_id}:{context.skill_name}"
        if self._scope == "namespace":
            return f"{context.actor_id}:{context.skill_namespace}"
        return context.actor_id
