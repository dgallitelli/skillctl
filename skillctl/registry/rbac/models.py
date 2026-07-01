"""RBAC permission model for SkillsOps.

Design principles:
- Roles are coarse-grained (4 built-in roles, hierarchical).
- Permissions are fine-grained (per-action).
- Namespaces provide scoping (org/team/skill hierarchy) with inheritance.
- Everything is stored in SQLite alongside the registry.
- Every authorization decision is logged to the audit chain.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class Permission(Enum):
    """Fine-grained permissions for skill operations."""

    # Skill lifecycle
    SKILL_CREATE = "skill:create"
    SKILL_READ = "skill:read"
    SKILL_UPDATE = "skill:update"
    SKILL_DELETE = "skill:delete"
    SKILL_PUBLISH = "skill:publish"
    SKILL_UNPUBLISH = "skill:unpublish"

    # Audit & evaluation
    AUDIT_READ = "audit:read"
    AUDIT_EXPORT = "audit:export"
    EVAL_RUN = "eval:run"

    # Administration
    RBAC_ASSIGN = "rbac:assign"
    RBAC_REVOKE = "rbac:revoke"
    NAMESPACE_CREATE = "namespace:create"
    NAMESPACE_MANAGE = "namespace:manage"
    TOKEN_CREATE = "token:create"
    TOKEN_REVOKE = "token:revoke"


class Role(Enum):
    """Built-in roles with predefined permission sets.

    Hierarchy: viewer < author < publisher < admin.
    Each higher role is a strict superset of the ones below it.
    """

    VIEWER = "viewer"
    AUTHOR = "author"
    PUBLISHER = "publisher"
    ADMIN = "admin"


# Role → Permission mapping. Built incrementally so the hierarchy is explicit
# and provably nested (each role includes everything below it).
_VIEWER_PERMS: set[Permission] = {
    Permission.SKILL_READ,
    Permission.AUDIT_READ,
}

_AUTHOR_PERMS: set[Permission] = _VIEWER_PERMS | {
    Permission.SKILL_CREATE,
    Permission.SKILL_UPDATE,
    Permission.SKILL_DELETE,
    Permission.EVAL_RUN,
}

_PUBLISHER_PERMS: set[Permission] = _AUTHOR_PERMS | {
    Permission.SKILL_PUBLISH,
    Permission.SKILL_UNPUBLISH,
    Permission.AUDIT_EXPORT,
    Permission.TOKEN_CREATE,
    Permission.TOKEN_REVOKE,
}

ROLE_PERMISSIONS: dict[Role, set[Permission]] = {
    Role.VIEWER: _VIEWER_PERMS,
    Role.AUTHOR: _AUTHOR_PERMS,
    Role.PUBLISHER: _PUBLISHER_PERMS,
    Role.ADMIN: {p for p in Permission},  # All permissions.
}


def role_from_str(value: str) -> Role:
    """Parse a role name into a :class:`Role`, raising ``ValueError`` if unknown."""
    try:
        return Role(value)
    except ValueError as exc:
        valid = ", ".join(r.value for r in Role)
        raise ValueError(f"Unknown role {value!r}. Valid roles: {valid}") from exc


def permission_from_str(value: str) -> Permission:
    """Parse a permission string into a :class:`Permission`."""
    try:
        return Permission(value)
    except ValueError as exc:
        raise ValueError(f"Unknown permission {value!r}") from exc


@dataclass
class Identity:
    """Represents an authenticated actor in the system."""

    user_id: str
    username: str
    roles: list[Role] = field(default_factory=list)
    namespaces: list[str] = field(default_factory=list)  # Scoped access (informational)
    token_id: Optional[str] = None  # Which token was used
    created_at: Optional[str] = None
    is_anonymous: bool = False
    # Namespace scopes carried by the token used to authenticate.
    # ``None`` means the token is unscoped (limited only by the user's roles);
    # a list constrains the identity to those namespaces (and their children).
    token_scopes: Optional[list[str]] = None
    # Role assignments carried directly on the identity (not from the store).
    # Used to bridge legacy permission-string tokens and the anonymous/no-auth
    # principal into the RBAC engine, so there is a single decision path.
    inline_assignments: list["RoleAssignment"] = field(default_factory=list)

    def __str__(self) -> str:
        return self.username


@dataclass
class Namespace:
    """Hierarchical namespace for permission scoping.

    Examples:
        "org/acme"                  → organization level
        "org/acme/team-ml"          → team level
        "org/acme/team-ml/my-skill" → skill level

    Permissions granted at a parent namespace are inherited by children.
    """

    path: str
    owner_id: str
    description: str = ""
    parent: Optional[str] = None
    created_at: Optional[str] = None

    @property
    def parts(self) -> list[str]:
        return self.path.split("/")

    @property
    def depth(self) -> int:
        return len(self.parts)

    def is_ancestor_of(self, other_path: str) -> bool:
        """Check if this namespace is a strict parent of another."""
        return other_path.startswith(self.path + "/")


@dataclass
class RoleAssignment:
    """Binds a user to a role within a namespace scope."""

    user_id: str
    role: Role
    namespace: str  # The scope where this role applies ("*" = global)
    assigned_by: str  # Who granted this role
    assigned_at: str  # ISO timestamp
    expires_at: Optional[str] = None  # Optional expiry


@dataclass
class AccessToken:
    """Scoped API token tied to a user identity.

    Tokens:
    - are bound to a specific user,
    - are scoped to specific namespaces (cannot exceed the user's own scope),
    - have optional expiry,
    - are revocable,
    - record their last usage.

    Only the SHA-256 hash of the actual token is ever stored.
    """

    token_id: str
    user_id: str
    token_hash: str
    name: str
    scopes: list[str]  # Namespace scopes this token can access ("*" = all)
    created_at: str
    expires_at: Optional[str] = None
    last_used_at: Optional[str] = None
    revoked: bool = False
