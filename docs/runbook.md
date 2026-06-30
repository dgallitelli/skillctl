# SkillsOps Runbook — Getting Started From Scratch

A hands-on walkthrough for a new customer, exercising the full governed
lifecycle end to end: **author → validate → audit → score → register (RBAC) →
publish → runtime policy → compliance → progressive deploy → enterprise**.

Every command below is real. Copy-paste in order. Expected output is described
inline. Tested on Python 3.10–3.13.

> Convention: the CLI is `skillctl`; the package is `skillsops`. Local state
> (credentials, policy/deployment/lineage DBs) lives under `~/.skillctl/`.

---

## 0. Install

```bash
python -m venv .venv && source .venv/bin/activate
pip install skillsops                      # core CLI (only dependency: pyyaml)

# Optional extras, added as you need them:
pip install "skillsops[server]"            # registry server (FastAPI)
pip install "skillsops[observability]"     # OpenTelemetry tracing
pip install "skillsops[policy-opa]"        # OPA policy integration

skillctl version
skillctl --help        # note the command groups: validate, eval, auth, rbac,
                       # policy, observe, compliance, deploy, ci, forensics, ...
```

---

## 1. Author a skill (M0)

```bash
mkdir demo && cd demo
skillctl create skill my-org/hello
# → scaffolds skill.yaml + SKILL.md + evals/

# Edit SKILL.md and set a real description in skill.yaml, then:
skillctl validate .
# → ✓ schema · 0 errors   semver · OK   capabilities · declared
```

A missing/invalid `version` or `name` fails validation with a clear `VAL-*`
code — that is the deterministic schema contract gate.

---

## 2. Security audit + deterministic score (M0)

```bash
skillctl eval audit .
# → A–F grade across 9 threat categories. A CRITICAL finding (e.g. a hardcoded
#   AWS key) exits non-zero and would block publishing.

skillctl eval report . --format json
# → deterministic governance score = 80% security audit + 20% schema contract.
#   Run it again — the score is identical (no LLM, fully reproducible).
```

Try the security gate: drop `AWS_SECRET_ACCESS_KEY = "wJalrXUtnFEMI/...EXAMPLEKEY"`
into SKILL.md and re-run `skillctl eval audit .` → CRITICAL `SEC-001`, exit code 2.
Remove it before continuing.

---

## 3. Stand up a governed registry with RBAC (M1)

In a separate terminal, start the registry. On first run it bootstraps an admin
and prints credentials **once** — copy them.

```bash
skillctl serve --host 127.0.0.1 --port 8080 \
    --data-dir ~/.skillctl/registry --auto-generate-hmac-key
# stderr prints:
#   No users found — created the initial admin.
#   Username: admin
#   Password: <random>
#   Token:    sk-<random>
```

Back in your working terminal, authenticate and inspect your identity:

```bash
export REG=http://127.0.0.1:8080
skillctl auth login --registry $REG --username admin --password '<printed-password>'
skillctl auth whoami --registry $REG
# → User: admin   Roles: admin (*)   Registry: http://127.0.0.1:8080
# Credentials are stored at ~/.skillctl/credentials.json (mode 0600).
```

Create a team member and scope their role (RBAC):

```bash
# admin creates a user and grants 'author' in a namespace
curl -s -X POST $REG/api/v1/users -H "Authorization: Bearer <admin-token>" \
     -H 'Content-Type: application/json' \
     -d '{"username":"alice","password":"pw-alice"}'
skillctl rbac assign --registry $REG --user alice --role author --namespace org/acme/team-ml
skillctl rbac check  --registry $REG --user alice --permission skill:publish --namespace org/acme/team-ml
# → ✗ DENIED — no role grants skill:publish (author can create, not publish)
```

This is the headline RBAC guarantee: **who can do what, to which skills, and
it's all in the HMAC-signed audit chain.**

---

## 4. Publish lifecycle (create vs publish) (M1)

```bash
# A publisher (or admin) creates then publishes. Create = draft; publish = live.
skillctl publish .                 # uses stored creds; pushes to the registry
# Author 'alice' could create the skill (201) but a publish attempt returns 403
# until she's granted the 'publisher' role — verifiable in the audit log:
curl -s "$REG/api/v1/audit?limit=20" -H "Authorization: Bearer <admin-token>"
```

---

## 5. Runtime policy enforcement (M2)

Governance doesn't stop at publish — every *invocation* is checked.

```bash
mkdir -p .skillctl && cat > .skillctl/policies.yaml <<'YAML'
policies:
  - name: rate-limit
    type: builtin.rate_limit
    config: {max_per_minute: 30, scope: actor}
  - name: business-hours
    type: builtin.time_window
    config: {allowed_hours: [9, 17], allowed_days: [0,1,2,3,4]}
  - name: no-pii
    type: builtin.pii_redaction
    config: {mode: redact}
observability:
  enabled: true
  exporter: otlp
  endpoint: http://otel-collector:4317
YAML

skillctl policy list
skillctl policy validate
skillctl policy test --as alice --roles author --namespace org/acme/team-ml
# → per-hook ALLOW/DENY with reasons, then a final VERDICT
skillctl observe status      # OpenTelemetry config/status
```

An agent runtime wraps skill execution with the interceptor (rate limits, data
boundaries, PII redaction, OPA/Cedar), traced via OpenTelemetry and recorded as
`policy_decision` events in the audit chain:

```python
from skillctl.policy import PolicyEngine, SkillInterceptor, PolicyContext
from skillctl.policy.builtin import PIIRedactionHook
engine = PolicyEngine(); engine.register(PIIRedactionHook())
out = await SkillInterceptor(engine).invoke(my_skill_fn,
        PolicyContext(actor_id="alice", skill_name="my-org/hello",
                      skill_version="0.1.0", skill_namespace="org/acme/team-ml"),
        {"q": "email me at a@x.com"})
# → emails in the output are redacted before return
```

---

## 6. Compliance evidence (M3)

```bash
skillctl compliance frameworks
# → eu-ai-act, iso-42001, nist-ai-rmf

skillctl compliance classify .
# → risk level (e.g. MINIMAL, or HIGH if the description implies hiring/biometrics)

skillctl compliance report . --framework eu-ai-act --format md
# → per-control COMPLIANT / PARTIAL / NON-COMPLIANT / PENDING, an overall score,
#   evidence references (SHA-256 hashed), and remediation recommendations

# Some controls require human sign-off:
skillctl compliance attest --control art-9-2-b --skill . \
    --statement "Residual risks reviewed and accepted per assessment v3"

skillctl compliance gaps . --framework eu-ai-act   # only what still needs work
```

---

## 7. Progressive deployment (M3)

```bash
skillctl deploy canary my-org/hello --version 0.2.0 --namespace org/acme/team-ml \
    --from 0.1.0 --stages "1,5,25,50,100" --auto-rollback
skillctl deploy status
skillctl deploy promote <deployment-id>      # advance to the next stage
skillctl deploy rollback <deployment-id> --reason "elevated error rate"
skillctl deploy history --skill my-org/hello
```

Traffic is split by consistent hash (a given user stays on one version); health
checks (error/denial/latency/success) drive auto-rollback. Deployment records
also serve as compliance evidence (EU AI Act Art 14-1-b, intervention mechanism).

---

## 8. Enterprise: identity, forensics, CI/CD (M4)

**Federated identity (OIDC → RBAC):**

```bash
# Mint a demo HS256 token (an IdP would issue this in production):
TOKEN=$(python -c "from skillctl.identity import jwt; print(jwt.encode({'sub':'alice','email':'alice@acme.com','groups':['ml-team'],'aud':'skillsops','iss':'https://idp'},'shared-secret'))")
skillctl identity inspect --token "$TOKEN" --secret shared-secret \
    --issuer https://idp --audience skillsops --group-map "ml-team=publisher:org/acme"
# → Roles: publisher:org/acme  (IdP group mapped to a SkillsOps role)
```

**Incident forensics (over data lineage):**

```bash
skillctl forensics invocations --skill org/risky --label pii \
    --since 2026-07-01 --until 2026-07-02
skillctl forensics who-accessed --data db:customers
skillctl forensics provenance --data s3:predictions   # trace output → sources
```

**CI/CD governance pipeline:**

```bash
skillctl ci list
skillctl ci init --system github      # writes .github/workflows/skillsops.yml
# The pipeline runs validate → audit → compliance → publish on every PR/merge.
```

ABAC and multi-registry federation are available as libraries
(`skillctl.abac`, `skillctl.federation`) — see [enterprise.md](enterprise.md).

---

## 9. What you just proved

| Layer | Capability | Milestone |
|-------|-----------|-----------|
| Supply chain | schema + security gate, deterministic score | M0 |
| Access control | RBAC, scoped tokens, audited decisions | M1 |
| Runtime | policy hooks, OpenTelemetry, redaction | M2 |
| Compliance | EU AI Act / ISO 42001 / NIST evidence | M3 |
| Safe rollout | canary / blue-green / staged + rollback | M3 |
| Enterprise | OIDC→RBAC, ABAC, lineage, forensics, CI/CD | M4 |

Every governance action — publish, auth decision, policy decision, deployment
transition — is recorded in the tamper-evident HMAC audit chain. Verify it:

```bash
curl -s "$REG/api/v1/audit?limit=100" -H "Authorization: Bearer <admin-token>" \
  | python -c "import sys,json; d=json.load(sys.stdin); print('integrity:', d['integrity'])"
# → {'valid': N, 'invalid': 0, 'parse_errors': 0}
```
