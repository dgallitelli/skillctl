# Enterprise Scale & Ecosystem (Milestone 4)

Federated identity, attribute-based access control, data lineage, incident
forensics, multi-registry federation, and CI/CD templates — built on the M1–M3
governance core.

## Identity federation (`skillctl/identity/`)

Validate external IdP tokens and map IdP groups → SkillsOps roles, so federated
users flow through the **same RBAC engine** as local users.

- **OIDC (HS256 JWT)** validation is dependency-free (stdlib HMAC): signature,
  `exp`/`nbf`, `aud`, `iss`. RS256/JWKS and SAML are optional extensions.
- `IdentityResolver` validates a token, maps `groups` to `role:namespace` via
  `GroupRoleMapping`, caches with a TTL, and emits an `identity.resolved` audit
  event. `to_rbac_identity()` builds an RBAC `Identity` (with inline role
  assignments) for `RBACEngine.check`.
- A trailing wildcard namespace segment (`org/acme/ml-*`) collapses to its
  parent (`org/acme`) so it is RBAC-coverable.

```bash
skillctl identity inspect --token <jwt> --secret <hs256-secret> \
    --issuer https://idp --audience skillsops --group-map "ml-team=publisher:org/acme"
```

## ABAC (`skillctl/abac/`)

Fine-grained, context-sensitive authorization on top of coarse RBAC. An
`ABACEngine` evaluates permit/deny policies over subject/resource/action/
environment attributes. **Explicit deny wins**, then a matching permit allows,
else the default effect (deny by default). Operators are a fixed safe set
(`eq, ne, in, not_in, contains, startswith, gt/lt/gte/lte, regex, exists`) — no
`eval`. `ABACPolicyHook` plugs ABAC into the M2 runtime interceptor, e.g.
"publish only during business hours" or "EU-data skills only from EU regions".

## Data lineage (`skillctl/lineage/`)

`LineageStore` records what each invocation **read** and **wrote** (one SQLite
row per data item). Queries: `trace_provenance` (transitively back to sources),
`downstream_consumers`, `who_accessed`, and label/skill/window filters — the
foundation for GDPR Article 15 and incident response.

## Incident forensics (`skillctl/forensics/`)

`ForensicQuery` answers investigative questions over lineage + the HMAC audit
log: "which invocations of skill X touched PII between T1 and T2?", "who
accessed customer data?", "trace this output to its sources".

```bash
skillctl forensics invocations --skill org/risky --label pii --since 2026-07-01 --until 2026-07-02
skillctl forensics who-accessed --data db:customers
skillctl forensics provenance --data s3:predictions
```

## Multi-registry federation (`skillctl/federation/`)

`promote_skill` pulls a version from a source registry and publishes it to a
target registry (dev → staging → prod). The **role gate** is enforced by the
target registry's RBAC (a non-publisher gets 403); a **compliance gate**
(`require_compliance`) blocks promotion to higher environments unless the
compliance report passes.

## CI/CD templates (`skillctl/cicd/`)

Ready-to-use governance pipelines for GitHub Actions, GitLab CI, and Jenkins
that encode validate → audit → compliance → publish with real `skillctl`
commands.

```bash
skillctl ci list
skillctl ci init --system github          # writes .github/workflows/skillsops.yml
skillctl ci init --system gitlab
skillctl ci init --system jenkins
```

## Note on the optimizer

Per the roadmap, the LLM-driven optimizer remains a **separate** package
(`packages/skillsops-optimize/`, extracted in M0) — authoring assistance is kept
distinct from governance.
