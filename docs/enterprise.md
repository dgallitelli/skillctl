# Enterprise Utilities (Experimental)

These are composable libraries and starter templates, not an integrated
enterprise control plane. Their boundaries are intentionally explicit below.

## Identity token inspection (`skillctl/identity/`)

> **Trust boundary:** the registry does not accept these identities. The
> utility validates only locally signed HS256 JWTs using a caller-provided
> shared secret; it has no OIDC discovery, JWKS rotation, RS256 verification,
> token revocation, or registry middleware integration.

- The HS256 JWT utility checks signature, `exp`/`nbf`, `aud`, and `iss`.
- `IdentityResolver` validates a token, maps `groups` to `role:namespace` via
  `GroupRoleMapping`, caches with a TTL, and emits an `identity.resolved` audit
  event only when an audit logger is supplied.
- `to_rbac_identity()` builds an in-memory RBAC `Identity` for an embedding
  application. It does not create a registry user, role assignment, or session.
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
`eval`. This is a library only; the registry does not call it.
`ABACPolicyHook` can plug ABAC into the experimental runtime interceptor, e.g.
"publish only during business hours" or "EU-data skills only from EU regions".

## Data lineage (`skillctl/lineage/`)

> **Trust boundary:** no SkillsOps runtime records lineage automatically.
> Completeness, data labels, actor identity, and references are supplied by the
> caller and are not independently verified.

`LineageStore` can record what an embedding application says each invocation
read and wrote (one SQLite row per data item). Queries include
`trace_provenance`, `downstream_consumers`, `who_accessed`, and
label/skill/window filters.

## Incident forensics (`skillctl/forensics/`)

`ForensicQuery` answers questions over the available local lineage rows and,
when configured by a library caller, an audit-log file. The CLI commands query
lineage only. They do not verify completeness or provide legal-grade forensic
preservation.

```bash
skillctl forensics invocations --skill org/risky --label pii --since 2026-07-01 --until 2026-07-02
skillctl forensics who-accessed --data db:customers
skillctl forensics provenance --data s3:predictions
```

## Multi-registry federation (`skillctl/federation/`)

`promote_skill` is a programmatic helper that copies and verifies one immutable
artifact between two supplied HTTP clients. The target registry enforces its
normal create/publish RBAC. When `require_compliance=True`, promotion currently
fails closed in all cases because no trusted signed-evidence verifier exists;
caller-supplied booleans and local mapping previews are never accepted.

## CI/CD templates (`skillctl/cicd/`)

Starter pipelines for GitHub Actions, GitLab CI, and Jenkins encode validate →
audit → control-mapping preview → publish with real `skillctl` commands.
Generated files do not pin all dependencies or configure organization-specific
permissions, approvals, protected environments, evidence retention, or secret
management. Review and adapt them before use.

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
