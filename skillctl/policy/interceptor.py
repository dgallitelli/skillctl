"""Skill invocation interceptor — the runtime governance entry point.

Wraps skill execution with pre/post policy evaluation, timing, error handling,
OpenTelemetry spans, and audit recording. This is the "air marshal": it sits
between an agent runtime and a skill, evaluating every invocation.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from skillctl.observability.tracer import SkillsOpsTracer
from skillctl.policy.engine import PolicyEngine
from skillctl.policy.hooks import PolicyContext, PolicyDecision


class PolicyViolation(Exception):
    """Raised when a policy hook denies an invocation."""

    def __init__(self, result):
        self.result = result
        super().__init__(f"Policy violation: {result.reason} (hook: {result.hook_name})")


class SkillInterceptor:
    """Wraps skill execution with full runtime governance."""

    def __init__(
        self,
        policy_engine: PolicyEngine,
        tracer: Optional[SkillsOpsTracer] = None,
        audit_logger=None,
    ):
        self.policy_engine = policy_engine
        self.tracer = tracer or SkillsOpsTracer(enabled=False)
        self.audit_logger = audit_logger

    async def invoke(self, skill_fn: Callable, context: PolicyContext, params: Optional[dict] = None) -> Any:
        """Execute *skill_fn* with pre/post policy evaluation and tracing."""
        context.invocation_id = context.invocation_id or str(uuid.uuid4())
        context.timestamp = context.timestamp or datetime.now(timezone.utc).isoformat()
        context.input_params = params or {}

        async with self.tracer.skill_invocation_span(context) as span:
            # === PRE-EXECUTION ===
            pre_result = await self.policy_engine.evaluate_pre(context)
            span.add_policy_event("pre", pre_result.hook_name, pre_result.decision.value, pre_result.reason)
            if pre_result.decision == PolicyDecision.DENY:
                span.set_result(success=False, duration_ms=0)
                raise PolicyViolation(pre_result)

            # === EXECUTION ===
            start = time.perf_counter()
            try:
                if not callable(skill_fn):
                    raise TypeError(f"skill_fn must be callable, got {type(skill_fn)}")
                if asyncio.iscoroutinefunction(skill_fn):
                    result = await skill_fn(**(params or {}))
                else:
                    result = skill_fn(**(params or {}))
                duration_ms = (time.perf_counter() - start) * 1000
                context.output_result = result
                context.execution_duration_ms = duration_ms
            except PolicyViolation:
                raise
            except Exception as e:
                duration_ms = (time.perf_counter() - start) * 1000
                context.execution_error = str(e)
                context.execution_duration_ms = duration_ms
                span.set_error(e)
                span.set_result(success=False, duration_ms=duration_ms)
                raise

            # === POST-EXECUTION ===
            post_result = await self.policy_engine.evaluate_post(context)
            span.add_policy_event("post", post_result.hook_name, post_result.decision.value, post_result.reason)
            if post_result.decision == PolicyDecision.DENY:
                span.set_result(success=False, duration_ms=duration_ms)
                raise PolicyViolation(post_result)
            if post_result.decision == PolicyDecision.REDACT:
                result = post_result.modified_output

            span.set_result(success=True, duration_ms=duration_ms)
            return result
