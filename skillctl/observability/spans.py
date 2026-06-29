"""Span helpers for SkillsOps observability.

``SkillInvocationSpan`` is a thin semantic marker re-exported for callers that
want a typed name for the skill-invocation span produced by
``SkillsOpsTracer.skill_invocation_span``.
"""

from __future__ import annotations

from skillctl.observability.tracer import NoOpSpan, OTelSpan

# Public alias — the concrete span type returned by the tracer context manager.
SkillInvocationSpan = OTelSpan

__all__ = ["SkillInvocationSpan", "OTelSpan", "NoOpSpan"]
