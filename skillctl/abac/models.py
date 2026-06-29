"""Attribute-Based Access Control models (Milestone 4).

ABAC evaluates attributes of the subject, resource, action, and environment to
make dynamic authorization decisions on top of coarse-grained RBAC.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class AttributeContext:
    """Attributes available to ABAC policy evaluation."""

    subject: dict[str, Any] = field(default_factory=dict)
    resource: dict[str, Any] = field(default_factory=dict)
    action: str = ""
    environment: dict[str, Any] = field(default_factory=dict)

    def resolve(self, path: str) -> Any:
        """Resolve a dotted attribute path like ``subject.location`` or ``action``."""
        if path == "action":
            return self.action
        head, _, rest = path.partition(".")
        bucket = {"subject": self.subject, "resource": self.resource, "environment": self.environment}.get(head)
        if bucket is None:
            return None
        if not rest:
            return bucket
        cur: Any = bucket
        for part in rest.split("."):
            if isinstance(cur, dict):
                cur = cur.get(part)
            else:
                return None
        return cur


@dataclass
class Condition:
    """A single attribute condition: ``attribute <operator> value``."""

    attribute: str
    operator: str
    value: Any = None


@dataclass
class ABACPolicy:
    """A policy rule. All conditions must match (logical AND) for it to apply."""

    id: str
    effect: str  # "permit" or "deny"
    conditions: list[Condition] = field(default_factory=list)
    description: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "effect": self.effect,
            "description": self.description,
            "conditions": [
                {"attribute": c.attribute, "operator": c.operator, "value": c.value} for c in self.conditions
            ],
        }


@dataclass
class ABACDecision:
    allowed: bool
    reason: str
    matched_policy: str = ""

    def __bool__(self) -> bool:
        return self.allowed
