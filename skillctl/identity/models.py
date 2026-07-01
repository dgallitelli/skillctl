"""Federated identity models (Milestone 4).

Internal, IdP-agnostic representation of an authenticated identity, plus
group→role mapping and provider configuration.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class IdentityProviderType(Enum):
    SAML = "saml"
    OIDC = "oidc"
    LOCAL = "local"


@dataclass
class IdentityToken:
    """Normalized identity after IdP validation (feeds RBAC + ABAC)."""

    subject: str
    email: str = ""
    display_name: str = ""
    provider_type: IdentityProviderType = IdentityProviderType.OIDC
    provider_id: str = ""
    issuer: str = ""
    groups: list[str] = field(default_factory=list)
    attributes: dict[str, str] = field(default_factory=dict)
    issued_at: str = ""
    expires_at: str = ""
    session_id: Optional[str] = None
    resolved_roles: list[str] = field(default_factory=list)
    is_agent: bool = False
    parent_identity: Optional[str] = None
    delegation_chain: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "subject": self.subject,
            "email": self.email,
            "display_name": self.display_name,
            "provider_type": self.provider_type.value,
            "provider_id": self.provider_id,
            "issuer": self.issuer,
            "groups": self.groups,
            "attributes": self.attributes,
            "resolved_roles": self.resolved_roles,
            "is_agent": self.is_agent,
            "delegation_chain": self.delegation_chain,
        }


@dataclass
class GroupRoleMapping:
    """Maps an IdP group to a SkillsOps role within a namespace scope."""

    idp_group: str
    skillsops_role: str
    namespace_pattern: str
    priority: int = 0
    conditions: dict = field(default_factory=dict)


@dataclass
class IdentityProviderConfig:
    """Configuration for an identity provider (stored in .skillctl/identity.yaml)."""

    id: str
    type: IdentityProviderType
    display_name: str = ""
    enabled: bool = True

    # OIDC
    oidc_issuer_url: Optional[str] = None
    oidc_client_id: Optional[str] = None  # also used as audience
    oidc_secret_env: Optional[str] = None  # env var holding the HS256 secret
    oidc_audience: Optional[str] = None

    # SAML (production validation requires the optional [identity-saml] extra)
    saml_metadata_url: Optional[str] = None
    saml_entity_id: Optional[str] = None

    group_mappings: list[GroupRoleMapping] = field(default_factory=list)
    attribute_mappings: dict[str, str] = field(default_factory=dict)
    groups_claim: str = "groups"
    token_cache_ttl_seconds: int = 300
