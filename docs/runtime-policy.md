# Runtime Policy & Observability (Experimental)

> **Trust boundary:** this package supplies opt-in libraries and diagnostic CLI
> commands. The SkillsOps registry, installer, and supported IDEs do not route
> invocations through `SkillInterceptor`. Policies and traces apply only when an
> execution host explicitly integrates that class. Installing or configuring a
> policy does not create runtime enforcement by itself.

The policy package can wrap a host-controlled skill invocation in a policy
pipeline and OpenTelemetry span. It is experimental until a supported runtime
integration makes that wrapper mandatory and end-to-end tests prove it cannot
be bypassed.

## Where it lives

| Module | Responsibility |
|--------|----------------|
| `skillctl/policy/hooks.py` | `PolicyHook`, `PolicyContext`, `PolicyResult`, `PolicyDecision` |
| `skillctl/policy/engine.py` | `PolicyEngine` — pre/post pipeline, short-circuit, REDACT chaining |
| `skillctl/policy/interceptor.py` | `SkillInterceptor` — wraps execution with policy + tracing + audit |
| `skillctl/policy/store.py` | `PolicyStore` — SQLite-backed sliding-window counters |
| `skillctl/policy/builtin/` | rate-limit, data-boundary, pii-redaction, time-window, output-size |
| `skillctl/policy/external/` | `OPAHook`, `CedarHook` |
| `skillctl/policy/config.py` | `load_policy_config()` — build an engine from YAML |
| `skillctl/observability/` | OpenTelemetry tracer + structured JSON logging |

## Decision model

A hook returns a `PolicyResult` with one of four decisions:

| Decision | Pre-execution | Post-execution |
|----------|--------------|----------------|
| ALLOW | proceed | pass output through |
| DENY | block (raise `PolicyViolation`) | suppress output (raise) |
| WARN | proceed, flag | pass through, flag |
| REDACT | — | replace output (chained across hooks) |

When a host uses the interceptor, pipeline rules are:
- Pre-hooks run in registration order; the **first DENY short-circuits**.
- Post-hooks run in registration order; **REDACT results chain** (each hook sees
  the previous hook's modified output).
- **Every** decision (including ALLOW) is sent to the audit callback.

## Built-in hooks

| Hook | Phase | Purpose |
|------|-------|---------|
| `rate-limit` | pre | Sliding-window limit per actor / skill / namespace (SQLite-persisted) |
| `data-boundary` | pre | Restrict to allowed namespaces; block input patterns (e.g. external URLs) |
| `time-window` | pre | Allow only within hours/days (business hours, maintenance windows) |
| `pii-redaction` | post | Redact (or block on) emails, SSNs, cards, phones, IPs in output |
| `output-size` | post | Block or truncate oversized output (exfiltration / runaway generation) |

## External engines

- **OPA** (`external.opa`) — POSTs the context to `/v1/data/{policy_path}`,
  honoring an OPA policy that returns `{"allow", "reason"}`. `fail_mode`
  `"closed"` (deny on error, default) or `"open"` (warn + allow).
- **Cedar** (`external.cedar`) — local `cedarpy` evaluation of `.cedar` files
  (optional `skillsops[policy-cedar]`); degrades per `fail_mode` if unavailable.

## Using the interceptor

```python
from skillctl.policy import PolicyEngine, SkillInterceptor, PolicyContext, PolicyViolation
from skillctl.policy.builtin import RateLimitHook, PIIRedactionHook
from skillctl.policy.store import PolicyStore

store = PolicyStore("~/.skillctl/policy.db"); store.initialize()
engine = PolicyEngine()
engine.register(RateLimitHook(max_per_minute=30, store=store))
engine.register(PIIRedactionHook())

interceptor = SkillInterceptor(policy_engine=engine)
ctx = PolicyContext(actor_id="alice", skill_name="org/demo", skill_version="1.0.0",
                    skill_namespace="org/team-ml")
try:
    output = await interceptor.invoke(my_skill_fn, ctx, {"query": "hi"})
except PolicyViolation as v:
    ...  # v.result.reason explains the denial
```

## Wiring the audit chain

Policy decisions become tamper-evident `policy_decision` events only when the
host provides the audit callback:

```python
from skillctl.policy.config import make_audit_callback
from skillctl.registry.audit import AuditLogger

audit = AuditLogger("audit.jsonl", hmac_key=key)
engine.set_audit_callback(make_audit_callback(audit))
```

## Observability

```python
from skillctl.observability import configure_telemetry
tracer = configure_telemetry(service_name="skillsops-prod", exporter="otlp",
                             endpoint="http://otel-collector:4317")
interceptor = SkillInterceptor(policy_engine=engine, tracer=tracer)
```

Each invocation routed through the configured interceptor produces a
`skill.invoke` span with `skill.*`, `actor.*`,
`environment`, `invocation.id`, `execution.success`, and `execution.duration_ms`
attributes, plus `policy.pre.evaluated` / `policy.post.evaluated` events. If the
OpenTelemetry packages are not installed, the tracer is a no-op (graceful
degradation). Structured JSON logging is available via
`configure_structured_logging()` and does not require OTel.

## Configuration (`.skillctl/policies.yaml`)

```yaml
policies:
  - name: rate-limit
    type: builtin.rate_limit
    config: {max_per_minute: 30, scope: actor}
  - name: business-hours
    type: builtin.time_window
    config: {allowed_hours: [9, 17], allowed_days: [0, 1, 2, 3, 4]}
  - name: no-pii
    type: builtin.pii_redaction
    config: {mode: redact}
  - name: corporate-policy
    type: external.opa
    config: {opa_url: http://opa.internal:8181, policy_path: skillsops/corporate, fail_mode: closed}
observability:
  enabled: true
  exporter: otlp
  endpoint: http://otel-collector:4317
  service_name: skillsops-prod
```

## CLI

```bash
skillctl policy list                      # configured policies
skillctl policy validate                  # validate the config
skillctl policy test --skill ./my-skill --as alice --roles author   # dry-run
skillctl policy history --skill my-skill  # decisions from the audit log
skillctl observe status                   # OTel configuration/status
skillctl observe test                     # emit a test span (console exporter)
```

## Install

```bash
pip install "skillsops[observability]"   # OpenTelemetry tracing
pip install "skillsops[policy-opa]"      # OPA integration (httpx)
pip install "skillsops[policy-cedar]"    # Cedar integration (cedarpy)
```

The core policy engine and built-in hooks need **no extra dependencies** — they
are pure-Python and use the stdlib + SQLite.
