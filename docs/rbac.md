# Role-Based Access Control (Milestone 1)

SkillsOps answers the enterprise question **"who is allowed to do what, to
which skills, and can we prove it?"** Every authorization decision flows through
a single engine and is recorded twice: in a queryable SQLite table and in the
tamper-evident HMAC audit chain.

## Where it lives

RBAC is part of the registry: `skillctl/registry/rbac/`.

| Module | Responsibility |
|--------|----------------|
| `rbac/models.py` | `Permission`, `Role`, `Identity`, `Namespace`, `RoleAssignment`, `AccessToken` |
| `rbac/engine.py` | `RBACEngine.check()` — the single, pure decision function |
| `rbac/store.py` | SQLite persistence + PBKDF2 password hashing + bootstrap |
| `rbac/middleware.py` | `resolve_identity` (token → `Identity`) and `authorize` (check + audit) |

## Model

### Permissions (fine-grained)
`skill:create/read/update/delete/publish/unpublish`, `audit:read/export`,
`eval:run`, `rbac:assign/revoke`, `namespace:create/manage`,
`token:create/revoke`.

### Roles (coarse, strictly nested)
`viewer < author < publisher < admin`.

| Role | Highlights |
|------|-----------|
| viewer | read skills, read audit |
| author | + create / update / delete / eval |
| publisher | + publish / unpublish / audit export / mint tokens |
| admin | all permissions |

### Namespaces (hierarchical, inherited)
A role granted at `org/acme` covers `org/acme/team-ml/anything`. `*` is global.
The RBAC namespace for an operation is **explicit** (e.g. `--namespace
org/acme/team-ml`), decoupled from the skill's 2-part name so hierarchy isn't
constrained by the name format.

### Decision order (`RBACEngine.check`)
1. **Token scope gate** — a scoped token can never act outside its scopes,
   regardless of the user's roles (a token only ever *narrows*).
2. Collect role assignments (store-backed + inline) whose namespace covers the
   request, with inheritance.
3. Union their permissions; allow iff the requested permission is present.
4. Return an `AuthorizationDecision` that always carries a human-readable reason.

## Auditing

`authorize()` records every decision to:
- the `auth_decisions` SQLite table (queryable), and
- the HMAC hash-chained audit log as an `auth_decision` event.

Mutating operations (`skill.created`, `skill.published`, `skill.deleted`, …)
also carry `actor` (the resolved username) and `token_id`.

## Create vs publish

`POST /api/v1/skills` **creates** a draft (requires `skill:create`).
`POST /api/v1/skills/publish` **publishes** it (requires `skill:publish`).
This is why an `author` can create but not publish.

## Bootstrap

On first run against an empty database the server creates an initial `admin`
and prints its username, password, and a token **once** to stderr (Kubernetes /
Vault style). Change the password immediately:

```bash
skillctl auth change-password --old <printed> --new <secret> --registry <url>
```

## Backward compatibility

- `skillctl serve --auth-disabled` (localhost only) → anonymous principal with
  global admin; audit actor is `anonymous`.
- Legacy permission-string tokens (`read`, `read:<ns>`, `write:<ns>`, `admin`)
  are **bridged** into role assignments, so existing tokens keep working through
  the same decision path.
- With auth enabled, a missing or invalid token is rejected with `401`.

## CLI

```bash
# Authentication
skillctl auth login --username <u> --password <p> --registry <url>
skillctl auth whoami
skillctl auth change-password --old <old> --new <new>
skillctl auth logout

# Scoped tokens (shown once)
skillctl auth token create --name ci --scope org/acme/team-ml --expires 90d
skillctl auth token list
skillctl auth token revoke --name ci

# Roles (admin)
skillctl rbac assign --user alice --role publisher --namespace org/acme/team-ml
skillctl rbac revoke --user alice --role publisher --namespace org/acme/team-ml
skillctl rbac list   --user alice
skillctl rbac check  --user alice --permission skill:publish --namespace org/acme/team-ml

# Namespaces
skillctl namespace create org/acme/team-ml --description "ML team"
skillctl namespace list
skillctl namespace grant --namespace org/acme/team-ml --user alice --role author
```

Credentials are stored in `~/.skillctl/credentials.json` (mode `0600`).

## Password hashing

Passwords are hashed with stdlib `hashlib.pbkdf2_hmac` (SHA-256, 600k
iterations, per-password random salt), verified with `hmac.compare_digest`. This
keeps the core package dependency-free (`pyyaml` only) — no `bcrypt`/`argon2`.
The stored format is self-describing (`pbkdf2_sha256$<iters>$<salt>$<hash>`), so
the algorithm/cost can be upgraded later without a migration.

## Extensibility seams

- Identity resolution is centralized in `resolve_identity`, ready for an
  `IdentityProvider` (OIDC/SAML) in M4.
- `RBACEngine.check` is pure and wrappable by runtime policy hooks (M2).
- ABAC can layer attributes on top of the role decision (M4).
