"""Experimental HS256 identity resolution and group-to-role mapping.

The registry does not call this module. Embedding applications may validate a
locally signed HS256 JWT and turn it into an in-memory RBAC ``Identity``.
"""

from __future__ import annotations

import hashlib
import time
from typing import Optional

from skillctl.identity import jwt as _jwt
from skillctl.identity.models import (
    GroupRoleMapping,
    IdentityProviderConfig,
    IdentityProviderType,
    IdentityToken,
)


class IdentityError(Exception):
    pass


class OIDCAdapter:
    """Validate a caller-configured HS256 JWT into an :class:`IdentityToken`.

    This is not OIDC discovery or JWKS-based identity-provider integration.
    """

    def __init__(self, config: IdentityProviderConfig, secret: str):
        self.config = config
        self.secret = secret

    def validate(self, token_str: str) -> IdentityToken:
        try:
            claims = _jwt.decode(
                token_str,
                self.secret,
                audience=self.config.oidc_audience or self.config.oidc_client_id,
                issuer=self.config.oidc_issuer_url,
            )
        except _jwt.JWTError as e:
            raise IdentityError(f"OIDC token validation failed: {e}") from e

        groups = claims.get(self.config.groups_claim, []) or []
        if isinstance(groups, str):
            groups = [g.strip() for g in groups.split(",") if g.strip()]

        attributes = {}
        for claim_name, attr_name in self.config.attribute_mappings.items():
            if claim_name in claims:
                attributes[attr_name] = str(claims[claim_name])

        return IdentityToken(
            subject=str(claims.get("sub", "")),
            email=str(claims.get("email", "")),
            display_name=str(claims.get("name", claims.get("preferred_username", ""))),
            provider_type=IdentityProviderType.OIDC,
            provider_id=self.config.id,
            issuer=str(claims.get("iss", "")),
            groups=list(groups),
            attributes=attributes,
            issued_at=str(claims.get("iat", "")),
            expires_at=str(claims.get("exp", "")),
            session_id=claims.get("sid"),
            is_agent=bool(claims.get("is_agent", False)),
            parent_identity=claims.get("parent_identity"),
            delegation_chain=claims.get("delegation_chain", []) or [],
        )


def _normalize_namespace(pattern: str) -> str:
    """Map a namespace pattern to an RBAC scope the engine can cover.

    ``*`` → ``*`` (global); a trailing wildcard segment collapses to its parent
    (``org/acme/*`` and ``org/acme/ml-*`` both → ``org/acme``); an exact path is
    kept as-is.
    """
    if pattern in ("*", ""):
        return "*"
    last = pattern.rstrip("/").rsplit("/", 1)[-1]
    if "*" in last:
        parent = pattern.rstrip("/").rsplit("/", 1)[0] if "/" in pattern.rstrip("/") else "*"
        return parent or "*"
    return pattern.rstrip("/")


def map_groups_to_roles(groups: list[str], mappings: list[GroupRoleMapping]) -> list[str]:
    """Return resolved role strings ``"role:namespace"`` for a set of groups."""
    resolved: dict[str, int] = {}  # "role:ns" -> priority
    for group in groups:
        for m in mappings:
            if m.idp_group == group or m.idp_group == "*":
                key = f"{m.skillsops_role}:{_normalize_namespace(m.namespace_pattern)}"
                if key not in resolved or m.priority > resolved[key]:
                    resolved[key] = m.priority
    return sorted(resolved)


class IdentityResolver:
    """Resolves IdP tokens into identities with mapped roles, with TTL caching."""

    def __init__(self, config: IdentityProviderConfig, secret: Optional[str] = None, audit_logger=None):
        self.config = config
        self.audit_logger = audit_logger
        if config.type == IdentityProviderType.OIDC:
            if secret is None:
                raise IdentityError("OIDC identity provider requires a signing secret")
            self._adapter = OIDCAdapter(config, secret)
        else:
            self._adapter = None
        self._cache: dict[str, tuple[IdentityToken, float]] = {}

    def resolve(self, token_str: str) -> IdentityToken:
        key = hashlib.sha256(token_str.encode()).hexdigest()
        cached = self._cache.get(key)
        if cached and cached[1] > time.time():
            return cached[0]

        if self._adapter is None:
            raise IdentityError(f"No adapter for provider type {self.config.type.value}")

        identity = self._adapter.validate(token_str)
        identity.resolved_roles = map_groups_to_roles(identity.groups, self.config.group_mappings)
        self._cache[key] = (identity, time.time() + self.config.token_cache_ttl_seconds)

        if self.audit_logger is not None:
            self.audit_logger.log(
                action="identity.resolved",
                actor=identity.subject,
                resource=f"idp:{self.config.id}",
                details={"groups": identity.groups, "resolved_roles": identity.resolved_roles},
            )
        return identity


def to_rbac_identity(identity: IdentityToken):
    """Build an RBAC ``Identity`` (with inline role assignments) from resolved roles."""
    from skillctl.registry.rbac.models import Identity as RBACIdentity
    from skillctl.registry.rbac.models import RoleAssignment, role_from_str

    inline = []
    roles = []
    namespaces = []
    for spec in identity.resolved_roles:
        role_str, _, namespace = spec.partition(":")
        try:
            role = role_from_str(role_str)
        except ValueError:
            continue
        ns = namespace or "*"
        inline.append(
            RoleAssignment(user_id=identity.subject, role=role, namespace=ns, assigned_by="idp", assigned_at="")
        )
        roles.append(role)
        namespaces.append(ns)

    return RBACIdentity(
        user_id=identity.subject,
        username=identity.email or identity.subject,
        roles=roles,
        namespaces=namespaces,
        inline_assignments=inline,
    )
