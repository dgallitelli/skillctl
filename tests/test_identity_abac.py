"""Unit tests for Milestone 4 identity federation and ABAC."""

from __future__ import annotations

import time

import pytest

from skillctl.abac.engine import ABACEngine
from skillctl.abac.models import ABACPolicy, AttributeContext, Condition
from skillctl.identity import jwt as jwtlib
from skillctl.identity.models import GroupRoleMapping, IdentityProviderConfig, IdentityProviderType
from skillctl.identity.resolver import IdentityError, IdentityResolver, map_groups_to_roles, to_rbac_identity


# ---------------------------------------------------------------------------
# JWT
# ---------------------------------------------------------------------------


class TestJWT:
    def test_roundtrip(self):
        tok = jwtlib.encode({"sub": "u1", "email": "u@x.com"}, "secret")
        claims = jwtlib.decode(tok, "secret")
        assert claims["sub"] == "u1"

    def test_wrong_secret_fails(self):
        tok = jwtlib.encode({"sub": "u1"}, "secret")
        with pytest.raises(jwtlib.JWTError):
            jwtlib.decode(tok, "other")

    def test_expired(self):
        tok = jwtlib.encode({"sub": "u1", "exp": int(time.time()) - 10}, "secret")
        with pytest.raises(jwtlib.JWTError):
            jwtlib.decode(tok, "secret")

    def test_audience_and_issuer(self):
        tok = jwtlib.encode({"sub": "u", "aud": "skillsops", "iss": "https://idp"}, "secret")
        assert jwtlib.decode(tok, "secret", audience="skillsops", issuer="https://idp")["sub"] == "u"
        with pytest.raises(jwtlib.JWTError):
            jwtlib.decode(tok, "secret", audience="other")


# ---------------------------------------------------------------------------
# Group → role mapping + resolver
# ---------------------------------------------------------------------------


class TestGroupMapping:
    def test_maps_groups(self):
        mappings = [
            GroupRoleMapping("ml-publishers", "publisher", "org/acme/ml-*"),
            GroupRoleMapping("everyone", "viewer", "*"),
        ]
        roles = map_groups_to_roles(["ml-publishers", "everyone"], mappings)
        assert "publisher:org/acme" in roles  # ml-* segment collapses to parent
        assert "viewer:*" in roles

    def test_wildcard_group(self):
        roles = map_groups_to_roles(["anything"], [GroupRoleMapping("*", "viewer", "org/x")])
        assert roles == ["viewer:org/x"]


def _provider(mappings):
    return IdentityProviderConfig(
        id="okta",
        type=IdentityProviderType.OIDC,
        oidc_issuer_url="https://idp",
        oidc_audience="skillsops",
        group_mappings=mappings,
    )


class TestResolver:
    def test_resolve_and_map(self):
        cfg = _provider([GroupRoleMapping("ml-team", "publisher", "org/acme")])
        secret = "s3cr3t"
        tok = jwtlib.encode(
            {
                "sub": "alice",
                "email": "alice@acme.com",
                "groups": ["ml-team"],
                "aud": "skillsops",
                "iss": "https://idp",
            },
            secret,
        )
        identity = IdentityResolver(cfg, secret=secret).resolve(tok)
        assert identity.subject == "alice"
        assert identity.resolved_roles == ["publisher:org/acme"]

    def test_invalid_token_rejected(self):
        cfg = _provider([])
        with pytest.raises(IdentityError):
            IdentityResolver(cfg, secret="s").resolve("garbage.token.here")

    def test_to_rbac_identity(self):
        cfg = _provider([GroupRoleMapping("ml-team", "publisher", "org/acme")])
        secret = "s"
        tok = jwtlib.encode({"sub": "bob", "groups": ["ml-team"], "aud": "skillsops", "iss": "https://idp"}, secret)
        identity = IdentityResolver(cfg, secret=secret).resolve(tok)
        rbac = to_rbac_identity(identity)
        assert rbac.user_id == "bob"
        assert any(a.namespace == "org/acme" for a in rbac.inline_assignments)


# ---------------------------------------------------------------------------
# ABAC
# ---------------------------------------------------------------------------


class TestABAC:
    def test_default_deny(self):
        engine = ABACEngine([])
        assert not engine.evaluate(AttributeContext(action="invoke"))

    def test_permit_match(self):
        engine = ABACEngine([ABACPolicy("p", "permit", [Condition("subject.location", "eq", "eu-west-1")])])
        ctx = AttributeContext(subject={"location": "eu-west-1"}, action="invoke")
        assert engine.evaluate(ctx)

    def test_deny_wins(self):
        engine = ABACEngine(
            [
                ABACPolicy("allow-all", "permit", []),
                ABACPolicy("block-us", "deny", [Condition("subject.location", "eq", "us-east-1")]),
            ]
        )
        assert not engine.evaluate(AttributeContext(subject={"location": "us-east-1"}, action="invoke"))
        assert engine.evaluate(AttributeContext(subject={"location": "eu-west-1"}, action="invoke"))

    def test_operators(self):
        e = ABACEngine(
            [
                ABACPolicy(
                    "p", "permit", [Condition("environment.hour", "gte", 9), Condition("environment.hour", "lt", 17)]
                )
            ]
        )
        assert e.evaluate(AttributeContext(environment={"hour": 14}, action="invoke"))
        assert not e.evaluate(AttributeContext(environment={"hour": 2}, action="invoke"))

    def test_in_operator(self):
        e = ABACEngine(
            [ABACPolicy("p", "permit", [Condition("subject.location", "in", ["eu-west-1", "eu-central-1"])])]
        )
        assert e.evaluate(AttributeContext(subject={"location": "eu-west-1"}, action="invoke"))
        assert not e.evaluate(AttributeContext(subject={"location": "us-east-1"}, action="invoke"))

    def test_unknown_operator_raises(self):
        e = ABACEngine([ABACPolicy("p", "permit", [Condition("x", "bogus", 1)])])
        with pytest.raises(ValueError):
            e.evaluate(AttributeContext(action="invoke"))
