"""Auth middleware for the SkillsOps registry.

Resolves the caller's identity from their Bearer token and authorizes
individual operations through the RBAC engine. Every authorization decision
is recorded to BOTH:
  - the SQLite ``auth_decisions`` table (queryable mirror), and
  - the HMAC hash-chained audit log (tamper-evident ``auth_decision`` events).

Backward compatibility:
  - ``--auth-disabled`` → anonymous principal with global admin (actor="anonymous").
  - Legacy permission-string tokens (no bound user) are *bridged* into RBAC
    role assignments so they keep working through the same decision path.
  - An app without an ``rbac_store`` on its state runs in legacy-only mode.

Extensibility seam: identity resolution is centralized here so M4 can swap in
an ``IdentityProvider`` (OIDC/SAML) without touching route handlers.
"""

from __future__ import annotations

from typing import Optional

from fastapi import Depends, HTTPException, Request  # type: ignore[import-not-found]
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer  # type: ignore[import-not-found]

from skillctl.registry.auth import AuthManager, TokenInfo
from skillctl.registry.rbac.engine import RBACEngine
from skillctl.registry.rbac.models import (
    Identity,
    Permission,
    Role,
    RoleAssignment,
)
from skillctl.registry.rbac.store import RBACStore

security = HTTPBearer(auto_error=False)


# ---------------------------------------------------------------------------
# State accessors
# ---------------------------------------------------------------------------


def _rbac_store(request: Request) -> Optional[RBACStore]:
    return getattr(request.app.state, "rbac_store", None)


def _rbac_engine(request: Request) -> RBACEngine:
    engine = getattr(request.app.state, "rbac_engine", None)
    if engine is not None:
        return engine
    # Legacy-only mode (e.g. minimal test apps): an engine over a null store,
    # so decisions rely entirely on the identity's inline assignments.
    return RBACEngine(_NullStore())


class _NullStore:
    def get_assignments(self, user_id: str) -> list[RoleAssignment]:  # noqa: ARG002
        return []


# ---------------------------------------------------------------------------
# Identity bridging
# ---------------------------------------------------------------------------


def _anonymous_identity() -> Identity:
    """Principal used when auth is disabled (localhost-only): global admin."""
    return Identity(
        user_id="anonymous",
        username="anonymous",
        is_anonymous=True,
        token_scopes=None,
        inline_assignments=[
            RoleAssignment(
                user_id="anonymous",
                role=Role.ADMIN,
                namespace="*",
                assigned_by="auth-disabled",
                assigned_at="",
            )
        ],
    )


def _bridge_legacy_token(token: TokenInfo) -> list[RoleAssignment]:
    """Map legacy permission strings to RBAC role assignments.

    - ``admin``      → ADMIN @ ``*``
    - ``read``       → VIEWER @ ``*``
    - ``read:<ns>``  → VIEWER @ ``<ns>``
    - ``write:<ns>`` → PUBLISHER @ ``<ns>`` (write historically implied publish)
    """
    out: list[RoleAssignment] = []
    for perm in token.permissions:
        if perm == "admin":
            out.append(_inline(Role.ADMIN, "*"))
        elif perm == "read":
            out.append(_inline(Role.VIEWER, "*"))
        elif perm.startswith("read:"):
            out.append(_inline(Role.VIEWER, perm.split(":", 1)[1]))
        elif perm.startswith("write:"):
            out.append(_inline(Role.PUBLISHER, perm.split(":", 1)[1]))
    return out


def _inline(role: Role, namespace: str) -> RoleAssignment:
    return RoleAssignment(
        user_id="legacy",
        role=role,
        namespace=namespace,
        assigned_by="legacy-token",
        assigned_at="",
    )


# ---------------------------------------------------------------------------
# Identity resolution
# ---------------------------------------------------------------------------


async def resolve_identity(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> Identity:
    """Resolve the caller's identity from their Bearer token.

    - auth disabled            → anonymous global-admin principal.
    - no / malformed header     → 401.
    - invalid / expired token   → 401.
    - RBAC token (bound user)   → identity from the user's role assignments.
    - legacy permission token   → identity bridged from permission strings.
    """
    auth_manager: AuthManager = request.app.state.auth_manager
    store = _rbac_store(request)

    if auth_manager.disabled:
        return _anonymous_identity()

    if credentials is None or not credentials.credentials:
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")

    raw_token = credentials.credentials

    # RBAC (identity-bound) token first, if an RBAC store is wired.
    if store is not None:
        import hashlib

        token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
        rbac_token = store.get_token_by_hash(token_hash)
        if rbac_token is not None:
            if rbac_token.revoked:
                raise HTTPException(status_code=401, detail="Token has been revoked")
            if _is_expired(rbac_token.expires_at):
                raise HTTPException(status_code=401, detail="Token has expired")
            user = store.get_user(rbac_token.user_id)
            if user is None or not user["is_active"]:
                raise HTTPException(status_code=401, detail="Token's user is inactive or missing")
            store.touch_token(rbac_token.token_id)
            assignments = store.get_assignments(rbac_token.user_id)
            return Identity(
                user_id=rbac_token.user_id,
                username=user["username"],
                roles=sorted({a.role for a in assignments}, key=lambda r: r.value),
                namespaces=sorted({a.namespace for a in assignments}),
                token_id=rbac_token.token_id,
                created_at=user["created_at"],
                token_scopes=rbac_token.scopes if rbac_token.scopes else None,
            )

    # Legacy permission-string token (mechanics handled by AuthManager).
    token_info = auth_manager.verify_token(raw_token)
    if token_info is None:
        raise HTTPException(
            status_code=401,
            detail="Invalid or expired token. Create one with 'skillctl auth token create'.",
        )
    return Identity(
        user_id=f"token:{token_info.token_id}",
        username=token_info.name,
        token_id=token_info.token_id,
        token_scopes=None,
        inline_assignments=_bridge_legacy_token(token_info),
    )


def _is_expired(expires_at: Optional[str]) -> bool:
    if not expires_at:
        return False
    from datetime import datetime, timezone

    try:
        return datetime.now(timezone.utc) >= datetime.fromisoformat(expires_at)
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# Authorization + auditing
# ---------------------------------------------------------------------------


def authorize(
    request: Request,
    identity: Identity,
    permission: Permission,
    namespace: str,
    *,
    resource: Optional[str] = None,
    raise_on_deny: bool = True,
):
    """Authorize *permission* in *namespace* for *identity*; audit the decision.

    Records the decision to the SQLite ``auth_decisions`` mirror (if a store is
    present) and to the HMAC audit chain (``auth_decision`` event), then raises
    ``HTTPException(403)`` on denial unless ``raise_on_deny=False``.
    """
    engine = _rbac_engine(request)
    decision = engine.check(identity, permission, namespace)

    store = _rbac_store(request)
    if store is not None:
        try:
            store.record_decision(
                user_id=identity.user_id,
                username=identity.username,
                permission=permission.value,
                namespace=namespace,
                allowed=bool(decision),
                reason=decision.reason,
                token_id=identity.token_id,
                request_context={"resource": resource} if resource else None,
            )
        except Exception:  # pragma: no cover - audit must never break the request
            pass

    audit = getattr(request.app.state, "audit", None)
    if audit is not None:
        try:
            audit.log(
                action="auth_decision",
                actor=identity.username,
                resource=resource or namespace,
                details={
                    "permission": permission.value,
                    "namespace": namespace,
                    "decision": "allowed" if decision else "denied",
                    "reason": decision.reason,
                    "token_id": identity.token_id,
                },
            )
        except Exception:  # pragma: no cover
            pass

    if raise_on_deny and not decision:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "E_FORBIDDEN",
                "what": f"Permission denied: {permission.value} in '{namespace}'",
                "why": decision.reason,
                "fix": f"You need a role granting '{permission.value}' in '{namespace}'.",
            },
        )
    return decision


def require_permission(permission: Permission, namespace_from: str = "path"):
    """Dependency factory for route-level permission checks on a path namespace.

    ``namespace_from`` names the path parameter that carries the namespace
    (default ``"namespace"`` when ``"path"``). For operations whose namespace
    comes from the request body (e.g. publish), call :func:`authorize`
    directly inside the handler instead.
    """
    param = "namespace" if namespace_from == "path" else namespace_from

    async def _dep(request: Request, identity: Identity = Depends(resolve_identity)) -> Identity:
        namespace = request.path_params.get(param) or "*"
        authorize(request, identity, permission, namespace)
        return identity

    return _dep
