"""RBAC authorization engine.

Core logic:
1. Resolve identity from a token (done in middleware; passed in here).
2. Determine effective permissions (role + namespace + inheritance).
3. Constrain by the token's namespace scope.
4. Produce an auditable decision (always includes a reason).

Design for extensibility:
- M2 will add runtime policy hooks that wrap this engine.
- M4 will add ABAC (attribute-based) on top of RBAC.

This module is pure: it depends only on the models and a store protocol with
``get_assignments(user_id) -> list[RoleAssignment]``. No FastAPI, no SQLite
specifics — so it is trivially unit-testable and reusable.
"""

from __future__ import annotations

from typing import Protocol

from skillctl.registry.rbac.models import (
    ROLE_PERMISSIONS,
    Identity,
    Permission,
    Role,
    RoleAssignment,
)


class _AssignmentSource(Protocol):
    """Minimal store contract the engine needs."""

    def get_assignments(self, user_id: str) -> list[RoleAssignment]: ...


def namespace_covers(granted_ns: str, requested_ns: str) -> bool:
    """Return True if *granted_ns* covers *requested_ns*.

    Rules:
    - ``"*"`` covers everything (global scope).
    - exact match covers itself.
    - a parent covers all children (``org/acme`` covers ``org/acme/team-ml/x``).
    """
    if granted_ns == "*":
        return True
    if granted_ns == requested_ns:
        return True
    return requested_ns.startswith(granted_ns + "/")


class AuthorizationDecision:
    """Result of an authorization check — always includes a reason for auditability."""

    def __init__(
        self,
        allowed: bool,
        reason: str,
        identity: Identity,
        permission: Permission,
        namespace: str,
    ) -> None:
        self.allowed = allowed
        self.reason = reason
        self.identity = identity
        self.permission = permission
        self.namespace = namespace

    def __bool__(self) -> bool:
        return self.allowed

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        verb = "ALLOW" if self.allowed else "DENY"
        return f"<AuthorizationDecision {verb} {self.permission.value}@{self.namespace}: {self.reason}>"


class RBACEngine:
    """Evaluates authorization decisions.

    Usage::

        engine = RBACEngine(store=rbac_store)
        decision = engine.check(identity, Permission.SKILL_PUBLISH, "org/acme/team-ml")
        if not decision:
            raise PermissionDenied(decision.reason)
    """

    def __init__(self, store: _AssignmentSource) -> None:
        self.store = store

    def check(
        self,
        identity: Identity,
        permission: Permission,
        namespace: str,
    ) -> AuthorizationDecision:
        """Check if *identity* has *permission* in *namespace*.

        Resolution order:
        1. Enforce the token's namespace scope (a scoped token can never act
           outside its scopes, regardless of the user's roles).
        2. Collect role assignments whose namespace covers the requested one
           (exact match, ancestor, or global ``*``).
        3. Union the permissions of those roles.
        4. Allow iff the requested permission is in the union.
        """
        # 1. Token scope gate — independent of roles.
        scopes = identity.token_scopes
        if scopes is not None and not any(namespace_covers(s, namespace) for s in scopes):
            return AuthorizationDecision(
                allowed=False,
                reason=(f"Token scope {scopes} does not cover namespace '{namespace}'"),
                identity=identity,
                permission=permission,
                namespace=namespace,
            )

        # 2. Role assignments covering this namespace (with inheritance).
        #    Union of store-backed assignments (real users) and inline
        #    assignments carried on the identity (bridged legacy/anonymous
        #    principals) — one decision path for all principal types.
        assignments = list(self.store.get_assignments(identity.user_id))
        assignments.extend(identity.inline_assignments)
        effective_roles: set[Role] = set()
        for assignment in assignments:
            if namespace_covers(assignment.namespace, namespace):
                effective_roles.add(assignment.role)

        # 3. Union of permissions from effective roles.
        effective_permissions: set[Permission] = set()
        for role in effective_roles:
            effective_permissions.update(ROLE_PERMISSIONS[role])

        # 4. Decision.
        if permission in effective_permissions:
            role_names = sorted(r.value for r in effective_roles)
            return AuthorizationDecision(
                allowed=True,
                reason=f"Granted via role(s) {role_names} in namespace '{namespace}'",
                identity=identity,
                permission=permission,
                namespace=namespace,
            )

        return AuthorizationDecision(
            allowed=False,
            reason=f"No role grants '{permission.value}' in namespace '{namespace}'",
            identity=identity,
            permission=permission,
            namespace=namespace,
        )
