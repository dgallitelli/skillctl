"""Time window policy hook (pre-execution).

Restricts skill invocations to allowed hours/days (maintenance windows,
business hours, embargo periods).
"""

from __future__ import annotations

from datetime import datetime

from skillctl.policy.hooks import PolicyContext, PolicyDecision, PolicyHook, PolicyResult


class TimeWindowHook(PolicyHook):
    """Restricts invocations to allowed time windows.

    Example:
        TimeWindowHook(allowed_hours=(9, 17), allowed_days=[0, 1, 2, 3, 4])
        → only Mon–Fri 09:00–17:00.
    """

    def __init__(self, allowed_hours: tuple = (0, 24), allowed_days: list | None = None, timezone: str = "UTC") -> None:
        self._start_hour, self._end_hour = allowed_hours
        self._allowed_days = allowed_days if allowed_days is not None else list(range(7))
        self._timezone = timezone

    @property
    def name(self) -> str:
        return "time-window"

    @property
    def description(self) -> str:
        return f"Allows invocations {self._start_hour}:00-{self._end_hour}:00 on days {self._allowed_days}"

    @property
    def phase(self) -> str:
        return "pre"

    async def evaluate_pre(self, context: PolicyContext) -> PolicyResult:
        now = datetime.fromisoformat(context.timestamp) if context.timestamp else datetime.utcnow()

        if now.weekday() not in self._allowed_days:
            return PolicyResult(
                decision=PolicyDecision.DENY,
                reason=f"Invocation blocked: day {now.weekday()} not in allowed days {self._allowed_days}",
                hook_name=self.name,
            )

        if not (self._start_hour <= now.hour < self._end_hour):
            return PolicyResult(
                decision=PolicyDecision.DENY,
                reason=f"Invocation blocked: hour {now.hour} outside window {self._start_hour}-{self._end_hour}",
                hook_name=self.name,
            )

        return PolicyResult(
            decision=PolicyDecision.ALLOW,
            reason="Within allowed time window",
            hook_name=self.name,
        )
