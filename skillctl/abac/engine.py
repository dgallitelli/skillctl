"""ABAC policy engine.

Evaluates an :class:`AttributeContext` against a set of permit/deny policies.
Explicit deny wins; otherwise a matching permit allows; otherwise the default
effect (deny by default) applies. Operators are a fixed safe set — no ``eval``.
"""

from __future__ import annotations

import re
from typing import Any

from skillctl.abac.models import ABACDecision, ABACPolicy, AttributeContext, Condition


def _as_number(v: Any) -> float:
    return float(v)


_OPERATORS = {
    "eq": lambda a, b: a == b,
    "ne": lambda a, b: a != b,
    "in": lambda a, b: a in b if b is not None else False,
    "not_in": lambda a, b: a not in b if b is not None else True,
    "contains": lambda a, b: b in a if a is not None else False,
    "startswith": lambda a, b: str(a).startswith(str(b)) if a is not None else False,
    "gt": lambda a, b: _as_number(a) > _as_number(b),
    "lt": lambda a, b: _as_number(a) < _as_number(b),
    "gte": lambda a, b: _as_number(a) >= _as_number(b),
    "lte": lambda a, b: _as_number(a) <= _as_number(b),
    "regex": lambda a, b: re.search(str(b), str(a)) is not None if a is not None else False,
    "exists": lambda a, b: (a is not None) == bool(b),
}


class ABACEngine:
    def __init__(self, policies: list[ABACPolicy] | None = None, default_allow: bool = False):
        self.policies = policies or []
        self.default_allow = default_allow

    def add_policy(self, policy: ABACPolicy) -> None:
        self.policies.append(policy)

    def _matches(self, policy: ABACPolicy, ctx: AttributeContext) -> bool:
        for cond in policy.conditions:
            if not self._eval_condition(cond, ctx):
                return False
        return True

    def _eval_condition(self, cond: Condition, ctx: AttributeContext) -> bool:
        op = _OPERATORS.get(cond.operator)
        if op is None:
            raise ValueError(f"Unknown ABAC operator: {cond.operator!r}")
        actual = ctx.resolve(cond.attribute)
        try:
            return bool(op(actual, cond.value))
        except (TypeError, ValueError):
            return False

    def evaluate(self, ctx: AttributeContext) -> ABACDecision:
        # 1. Explicit deny wins.
        for p in self.policies:
            if p.effect == "deny" and self._matches(p, ctx):
                return ABACDecision(False, f"Denied by policy '{p.id}': {p.description or p.id}", p.id)
        # 2. A matching permit allows.
        for p in self.policies:
            if p.effect == "permit" and self._matches(p, ctx):
                return ABACDecision(True, f"Permitted by policy '{p.id}'", p.id)
        # 3. Default effect.
        if self.default_allow:
            return ABACDecision(True, "Default allow (no matching deny)")
        return ABACDecision(False, "Default deny (no matching permit policy)")
