"""Experimental local identity-token utilities for SkillsOps.

HS256 JWT validation and group→role mapping are available to embedding
applications. The registry does not use this package for authentication, and
OIDC discovery/JWKS validation is not implemented.
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
