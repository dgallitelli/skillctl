"""Role-Based Access Control for the SkillsOps registry (Milestone 1).

Layout note: RBAC lives under ``skillctl.registry.rbac`` (co-located with the
registry it governs) rather than the ``src/skillsops/rbac`` path used in the
milestone spec — this matches the actual package layout and avoids a second
top-level package.

Public surface:
    - models:   Permission, Role, Identity, Namespace, RoleAssignment, AccessToken
    - engine:   RBACEngine, AuthorizationDecision
    - store:    RBACStore (SQLite-backed), password hashing helpers
"""

from skillctl.registry.rbac.engine import AuthorizationDecision, RBACEngine
from skillctl.registry.rbac.models import (
    ROLE_PERMISSIONS,
    AccessToken,
    Identity,
    Namespace,
    Permission,
    Role,
    RoleAssignment,
)

__all__ = [
    "AuthorizationDecision",
    "RBACEngine",
    "ROLE_PERMISSIONS",
    "AccessToken",
    "Identity",
    "Namespace",
    "Permission",
    "Role",
    "RoleAssignment",
]
