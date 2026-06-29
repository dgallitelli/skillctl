"""Policy hook interface for runtime governance.

Design principles:
- Hooks are classes with structured decisions (not just bool).
- Hooks receive full context (who, what, where, when).
- Hooks are composable — multiple hooks form a pipeline.
- Hooks are async-first.
- External engines (OPA, Cedar) are just another hook implementation.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class PolicyDecision(Enum):
    """Possible outcomes of a policy evaluation."""

    ALLOW = "allow"  # Invocation may proceed
    DENY = "deny"  # Invocation is blocked
    REDACT = "redact"  # Output must be modified (post-hooks only)
    WARN = "warn"  # Allow but flag for review


@dataclass
class PolicyContext:
    """Full context available to policy hooks during evaluation.

    The "evidence packet" hooks use to make decisions. Extensible — add
    fields without breaking existing hooks.
    """

    # Who
    actor_id: str
    actor_roles: list[str] = field(default_factory=list)
    actor_namespace: str = ""
    token_id: Optional[str] = None

    # What
    skill_name: str = ""
    skill_version: str = ""
    skill_namespace: str = ""
    skill_category: str = ""
    skill_allowed_tools: list[str] = field(default_factory=list)

    # Where
    environment: str = "production"
    registry_url: str = ""

    # When
    timestamp: str = ""
    invocation_id: str = ""

    # Input (for pre-hooks)
    input_params: dict[str, Any] = field(default_factory=dict)

    # Output (for post-hooks, populated after execution)
    output_result: Any = None
    execution_duration_ms: float = 0.0
    execution_error: Optional[str] = None

    # Extensible metadata (for ABAC in M4)
    attributes: dict[str, Any] = field(default_factory=dict)


@dataclass
class PolicyResult:
    """Structured result from a policy hook evaluation. Always includes a reason."""

    decision: PolicyDecision
    reason: str
    hook_name: str
    details: dict[str, Any] = field(default_factory=dict)
    modified_output: Any = None

    def to_dict(self) -> dict:
        return {
            "decision": self.decision.value,
            "reason": self.reason,
            "hook_name": self.hook_name,
            "details": self.details,
        }


class PolicyHook(ABC):
    """Base class for all policy hooks.

    Implement ``evaluate_pre`` (before execution), ``evaluate_post`` (after),
    or both. The default implementations allow everything, so a hook only needs
    to override the phase it cares about.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique identifier for this hook (used in audit logs)."""
        ...

    @property
    def description(self) -> str:
        return ""

    @property
    def phase(self) -> str:
        """Which phase this hook runs in: 'pre', 'post', or 'both'."""
        return "both"

    async def evaluate_pre(self, context: PolicyContext) -> PolicyResult:
        return PolicyResult(
            decision=PolicyDecision.ALLOW,
            reason="No pre-execution policy defined",
            hook_name=self.name,
        )

    async def evaluate_post(self, context: PolicyContext) -> PolicyResult:
        return PolicyResult(
            decision=PolicyDecision.ALLOW,
            reason="No post-execution policy defined",
            hook_name=self.name,
        )


class FunctionPolicyHook(PolicyHook):
    """Convenience wrapper for simple function-based hooks."""

    def __init__(self, hook_name: str, pre_fn=None, post_fn=None, desc: str = ""):
        self._name = hook_name
        self._pre_fn = pre_fn
        self._post_fn = post_fn
        self._desc = desc

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._desc

    async def evaluate_pre(self, context: PolicyContext) -> PolicyResult:
        if self._pre_fn:
            return await self._pre_fn(context)
        return await super().evaluate_pre(context)

    async def evaluate_post(self, context: PolicyContext) -> PolicyResult:
        if self._post_fn:
            return await self._post_fn(context)
        return await super().evaluate_post(context)


def pre_hook(name: str, description: str = ""):
    """Decorator to create a pre-execution policy hook from a function."""

    def decorator(fn):
        return FunctionPolicyHook(hook_name=name, pre_fn=fn, desc=description)

    return decorator


def post_hook(name: str, description: str = ""):
    """Decorator to create a post-execution policy hook from a function."""

    def decorator(fn):
        return FunctionPolicyHook(hook_name=name, post_fn=fn, desc=description)

    return decorator
