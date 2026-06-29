"""Unit tests for the runtime policy core: hooks, engine, store, builtins."""

from __future__ import annotations

import asyncio

import pytest

from skillctl.policy.builtin import (
    DataBoundaryHook,
    OutputSizeHook,
    PIIRedactionHook,
    RateLimitHook,
    TimeWindowHook,
)
from skillctl.policy.engine import PolicyEngine
from skillctl.policy.hooks import PolicyContext, PolicyDecision, PolicyResult, post_hook, pre_hook
from skillctl.policy.store import PolicyStore


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def store():
    s = PolicyStore(":memory:")
    s.initialize()
    yield s
    s.close()


def _ctx(**kw) -> PolicyContext:
    base = dict(actor_id="alice", skill_name="proj/demo", skill_version="1.0.0", skill_namespace="org/test")
    base.update(kw)
    return PolicyContext(**base)


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


class TestEngine:
    def test_all_allow(self):
        engine = PolicyEngine()
        engine.register(DataBoundaryHook())  # allows all by default
        result = run(engine.evaluate_pre(_ctx()))
        assert result.decision == PolicyDecision.ALLOW

    def test_deny_short_circuits(self):
        engine = PolicyEngine()
        calls = []

        @pre_hook("deny-hook")
        async def deny(ctx):
            calls.append("deny")
            return PolicyResult(PolicyDecision.DENY, "nope", "deny-hook")

        @pre_hook("after")
        async def after(ctx):
            calls.append("after")
            return PolicyResult(PolicyDecision.ALLOW, "ok", "after")

        engine.register(deny)
        engine.register(after)
        result = run(engine.evaluate_pre(_ctx()))
        assert result.decision == PolicyDecision.DENY
        assert calls == ["deny"]  # second hook never ran

    def test_warn_aggregates(self):
        engine = PolicyEngine()

        @pre_hook("warner")
        async def warn(ctx):
            return PolicyResult(PolicyDecision.WARN, "heads up", "warner")

        engine.register(warn)
        result = run(engine.evaluate_pre(_ctx()))
        assert result.decision == PolicyDecision.WARN

    def test_post_redact_chains(self):
        engine = PolicyEngine()

        @post_hook("upper")
        async def upper(ctx):
            return PolicyResult(PolicyDecision.REDACT, "upper", "upper", modified_output=str(ctx.output_result).upper())

        @post_hook("exclaim")
        async def exclaim(ctx):
            return PolicyResult(PolicyDecision.REDACT, "bang", "exclaim", modified_output=str(ctx.output_result) + "!")

        engine.register(upper)
        engine.register(exclaim)
        ctx = _ctx()
        ctx.output_result = "hello"
        result = run(engine.evaluate_post(ctx))
        assert result.decision == PolicyDecision.REDACT
        assert result.modified_output == "HELLO!"  # chained

    def test_audit_callback_invoked(self):
        engine = PolicyEngine()
        engine.register(DataBoundaryHook())
        seen = []

        async def cb(entry):
            seen.append(entry)

        engine.set_audit_callback(cb)
        run(engine.evaluate_pre(_ctx()))
        assert seen and seen[0]["type"] == "policy_decision"


# ---------------------------------------------------------------------------
# Rate limit
# ---------------------------------------------------------------------------


class TestRateLimit:
    def test_blocks_after_threshold(self, store):
        hook = RateLimitHook(max_per_minute=3, store=store)
        ctx = _ctx()
        for _ in range(3):
            assert run(hook.evaluate_pre(ctx)).decision == PolicyDecision.ALLOW
        denied = run(hook.evaluate_pre(ctx))
        assert denied.decision == PolicyDecision.DENY
        assert denied.details["current"] == 3

    def test_scope_per_skill_isolates(self, store):
        hook = RateLimitHook(max_per_minute=1, scope="skill", store=store)
        a = _ctx(skill_name="proj/a")
        b = _ctx(skill_name="proj/b")
        assert run(hook.evaluate_pre(a)).decision == PolicyDecision.ALLOW
        assert run(hook.evaluate_pre(b)).decision == PolicyDecision.ALLOW  # different scope key
        assert run(hook.evaluate_pre(a)).decision == PolicyDecision.DENY

    def test_requires_store(self):
        with pytest.raises(ValueError):
            RateLimitHook()


# ---------------------------------------------------------------------------
# Data boundary
# ---------------------------------------------------------------------------


class TestDataBoundary:
    def test_allows_within(self):
        hook = DataBoundaryHook(allowed_namespaces=["org/acme"])
        assert run(hook.evaluate_pre(_ctx(skill_namespace="org/acme/team"))).decision == PolicyDecision.ALLOW

    def test_denies_outside(self):
        hook = DataBoundaryHook(allowed_namespaces=["org/acme"])
        assert run(hook.evaluate_pre(_ctx(skill_namespace="org/other"))).decision == PolicyDecision.DENY

    def test_blocked_pattern(self):
        hook = DataBoundaryHook(blocked_patterns=["http://external"])
        ctx = _ctx(input_params={"url": "http://external/data"})
        assert run(hook.evaluate_pre(ctx)).decision == PolicyDecision.DENY


# ---------------------------------------------------------------------------
# PII redaction
# ---------------------------------------------------------------------------


class TestPII:
    def test_redacts_email(self):
        hook = PIIRedactionHook()
        ctx = _ctx()
        ctx.output_result = "Contact me at jane@example.com please"
        result = run(hook.evaluate_post(ctx))
        assert result.decision == PolicyDecision.REDACT
        assert "jane@example.com" not in result.modified_output
        assert "REDACTED" in result.modified_output

    def test_block_mode(self):
        hook = PIIRedactionHook(mode="block")
        ctx = _ctx()
        ctx.output_result = "SSN 123-45-6789"
        assert run(hook.evaluate_post(ctx)).decision == PolicyDecision.DENY

    def test_clean_output_allows(self):
        hook = PIIRedactionHook()
        ctx = _ctx()
        ctx.output_result = "nothing sensitive here"
        assert run(hook.evaluate_post(ctx)).decision == PolicyDecision.ALLOW


# ---------------------------------------------------------------------------
# Time window
# ---------------------------------------------------------------------------


class TestTimeWindow:
    def test_allows_in_window(self):
        hook = TimeWindowHook(allowed_hours=(0, 24))
        assert run(hook.evaluate_pre(_ctx(timestamp="2026-07-15T12:00:00+00:00"))).decision == PolicyDecision.ALLOW

    def test_denies_outside_hours(self):
        hook = TimeWindowHook(allowed_hours=(9, 17))
        assert run(hook.evaluate_pre(_ctx(timestamp="2026-07-15T22:00:00+00:00"))).decision == PolicyDecision.DENY

    def test_denies_outside_days(self):
        hook = TimeWindowHook(allowed_days=[0, 1, 2, 3, 4])  # Mon-Fri
        # 2026-07-18 is a Saturday
        assert run(hook.evaluate_pre(_ctx(timestamp="2026-07-18T12:00:00+00:00"))).decision == PolicyDecision.DENY


# ---------------------------------------------------------------------------
# Output size
# ---------------------------------------------------------------------------


class TestOutputSize:
    def test_blocks_large(self):
        hook = OutputSizeHook(max_bytes=10)
        ctx = _ctx()
        ctx.output_result = "x" * 100
        assert run(hook.evaluate_post(ctx)).decision == PolicyDecision.DENY

    def test_truncates(self):
        hook = OutputSizeHook(max_bytes=10, truncate=True)
        ctx = _ctx()
        ctx.output_result = "x" * 100
        result = run(hook.evaluate_post(ctx))
        assert result.decision == PolicyDecision.REDACT
        assert "[TRUNCATED]" in result.modified_output

    def test_allows_small(self):
        hook = OutputSizeHook(max_bytes=1000)
        ctx = _ctx()
        ctx.output_result = "small"
        assert run(hook.evaluate_post(ctx)).decision == PolicyDecision.ALLOW
