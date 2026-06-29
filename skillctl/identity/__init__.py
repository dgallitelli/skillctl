"""Federated identity for SkillsOps (Milestone 4).

OIDC (HS256 JWT) identity validation with group→role mapping that feeds the
existing RBAC engine. SAML and RS256/JWKS are optional extensions.
"""

from skillctl.identity.models import (
    GroupRoleMapping,
    IdentityProviderConfig,
    IdentityProviderType,
    IdentityToken,
)
from skillctl.identity.resolver import (
    IdentityError,
    IdentityResolver,
    OIDCAdapter,
    map_groups_to_roles,
    to_rbac_identity,
)

__all__ = [
    "IdentityToken",
    "GroupRoleMapping",
    "IdentityProviderConfig",
    "IdentityProviderType",
    "IdentityResolver",
    "OIDCAdapter",
    "IdentityError",
    "map_groups_to_roles",
    "to_rbac_identity",
]
