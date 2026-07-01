"""End-to-end tests for Milestone 2: Runtime Policy & Observability.

Real SQLite rate-limit counters, real HMAC audit chain, real in-memory
OpenTelemetry collector, and a real HTTP server speaking OPA's data API.
No mocks. No monkeypatching.

Run with:  pytest tests/e2e/test_milestone_2.py -v
"""

from __future__ import annotations

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from skillctl.policy.builtin import PIIRedactionHook, RateLimitHook
from skillctl.policy.config import make_audit_callback
from skillctl.policy.engine import PolicyEngine
from skillctl.policy.external import OPAHook
from skillctl.policy.hooks import PolicyContext, PolicyDecision
from skillctl.policy.interceptor import PolicyViolation, SkillInterceptor
from skillctl.policy.store import PolicyStore
from skillctl.registry.audit import AuditLogger

pytestmark = pytest.mark.integration


def run(coro):
    return asyncio.run(coro)


async def echo(**kwargs):
    return kwargs or {"ok": True}


def _ctx(**kw) -> PolicyContext:
    base = dict(actor_id="alice", skill_name="proj/demo", skill_version="1.0.0", skill_namespace="org/test")
    base.update(kw)
    return PolicyContext(**base)


# ---------------------------------------------------------------------------
# Test 1: Rate limiting blocks after threshold (real SQLite counter)
# ---------------------------------------------------------------------------


def test_e2e_rate_limit_blocks_after_threshold(tmp_path):
    store = PolicyStore(tmp_path / "rl.db")
    store.initialize()
    engine = PolicyEngine()
    engine.register(RateLimitHook(max_per_minute=5, store=store))
    interceptor = SkillInterceptor(policy_engine=engine)

    # 5 invocations succeed.
    for _ in range(5):
        assert run(interceptor.invoke(echo, _ctx(), {"x": 1}))["x"] == 1

    # 6th is blocked.
    with pytest.raises(PolicyViolation) as exc:
        run(interceptor.invoke(echo, _ctx(), {"x": 1}))
    assert exc.value.result.hook_name == "rate-limit"
    assert exc.value.result.details["current"] == 5

    # Counter persists across a fresh store instance (same DB file).
    store2 = PolicyStore(tmp_path / "rl.db")
    store2.initialize()
    assert run(store2.count_in_window("alice", 0, 9_999_999_999)) == 5
    store.close()
    store2.close()


# ---------------------------------------------------------------------------
# Test 2: PII redaction modifies output
# ---------------------------------------------------------------------------


def test_e2e_pii_redaction(tmp_path):
    engine = PolicyEngine()
    engine.register(PIIRedactionHook())
    interceptor = SkillInterceptor(policy_engine=engine)

    async def leaky(**kwargs):
        return "Reach me at jane@example.com or 123-45-6789"

    result = run(interceptor.invoke(leaky, _ctx(), {}))
    assert "jane@example.com" not in result
    assert "123-45-6789" not in result
    assert "REDACTED" in result


# ---------------------------------------------------------------------------
# Test 3: OpenTelemetry spans emitted (in-memory collector)
# ---------------------------------------------------------------------------


def test_e2e_otel_spans_emitted(tmp_path):
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from skillctl.observability.tracer import tracer_from_provider

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = tracer_from_provider(provider)
    assert tracer.enabled

    engine = PolicyEngine()
    engine.register(PIIRedactionHook())
    interceptor = SkillInterceptor(policy_engine=engine, tracer=tracer)

    run(interceptor.invoke(echo, _ctx(skill_category="testing"), {"q": "hi"}))

    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    span = spans[0]
    assert span.name == "skill.invoke"
    assert span.attributes["skill.name"] == "proj/demo"
    assert span.attributes["skill.namespace"] == "org/test"
    assert span.attributes["execution.success"] is True
    # Policy evaluation recorded as span events.
    event_names = {e.name for e in span.events}
    assert "policy.pre.evaluated" in event_names
    assert "policy.post.evaluated" in event_names


# ---------------------------------------------------------------------------
# Test 4: Policy denial recorded in the HMAC audit chain
# ---------------------------------------------------------------------------


def test_e2e_policy_audit_trail(tmp_path):
    audit = AuditLogger(tmp_path / "audit.jsonl", hmac_key=b"m2-policy-key")
    store = PolicyStore(tmp_path / "rl.db")
    store.initialize()

    engine = PolicyEngine()
    engine.register(RateLimitHook(max_per_minute=1, store=store))
    engine.set_audit_callback(make_audit_callback(audit))
    interceptor = SkillInterceptor(policy_engine=engine, audit_logger=audit)

    run(interceptor.invoke(echo, _ctx(), {}))  # allowed
    with pytest.raises(PolicyViolation):
        run(interceptor.invoke(echo, _ctx(), {}))  # denied

    events = audit.read(action="policy_decision", limit=100)
    decisions = [e.details.get("decision") for e in events]
    assert "allow" in decisions and "deny" in decisions
    deny = [e for e in events if e.details.get("decision") == "deny"][0]
    assert deny.details["hook_name"] == "rate-limit"
    assert "Rate limit exceeded" in deny.details["reason"]

    # HMAC hash chain still verifies (tamper-evident).
    valid, invalid, parse_errors = audit.verify_integrity()
    assert invalid == 0 and parse_errors == 0 and valid >= 2
    store.close()


# ---------------------------------------------------------------------------
# Test 5: External OPA integration (real HTTP call)
# ---------------------------------------------------------------------------


class _OPAHandler(BaseHTTPRequestHandler):
    """Minimal HTTP server speaking OPA's /v1/data response shape."""

    def log_message(self, *args):  # silence
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        actor = body.get("input", {}).get("actor", "")
        # Policy: allow everyone except "mallory".
        allow = actor != "mallory"
        payload = json.dumps(
            {"result": {"allow": allow, "reason": "authorized" if allow else "actor is blocklisted"}}
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


@pytest.fixture
def opa_server():
    server = HTTPServer(("127.0.0.1", 0), _OPAHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.shutdown()


def test_e2e_external_opa_integration(opa_server):
    engine = PolicyEngine()
    engine.register(OPAHook(opa_url=opa_server, policy_path="skillsops/authz"))
    interceptor = SkillInterceptor(policy_engine=engine)

    # Allowed actor.
    assert run(interceptor.invoke(echo, _ctx(actor_id="alice"), {"x": 1}))["x"] == 1

    # Blocklisted actor → OPA denies → PolicyViolation.
    with pytest.raises(PolicyViolation) as exc:
        run(interceptor.invoke(echo, _ctx(actor_id="mallory"), {"x": 1}))
    assert exc.value.result.hook_name == "opa"
    assert "blocklisted" in exc.value.result.reason


def test_e2e_opa_fail_closed_when_unreachable():
    # Nothing listening on this port → fail-closed denies.
    engine = PolicyEngine()
    engine.register(OPAHook(opa_url="http://127.0.0.1:1", fail_mode="closed", timeout_seconds=1.0))
    interceptor = SkillInterceptor(policy_engine=engine)
    with pytest.raises(PolicyViolation) as exc:
        run(interceptor.invoke(echo, _ctx(), {}))
    assert exc.value.result.decision == PolicyDecision.DENY
