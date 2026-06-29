"""Unit tests for the RBAC core: models, engine, and SQLite store."""

from __future__ import annotations

import pytest

from skillctl.registry.db import MetadataDB
from skillctl.registry.rbac.engine import RBACEngine, namespace_covers
from skillctl.registry.rbac.models import (
    ROLE_PERMISSIONS,
    Identity,
    Namespace,
    Permission,
    Role,
)
from skillctl.registry.rbac.store import RBACStore, hash_password, verify_password


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class TestRoleHierarchy:
    def test_strictly_nested(self):
        assert ROLE_PERMISSIONS[Role.VIEWER] < ROLE_PERMISSIONS[Role.AUTHOR]
        assert ROLE_PERMISSIONS[Role.AUTHOR] < ROLE_PERMISSIONS[Role.PUBLISHER]
        assert ROLE_PERMISSIONS[Role.PUBLISHER] < ROLE_PERMISSIONS[Role.ADMIN]

    def test_admin_has_all(self):
        assert ROLE_PERMISSIONS[Role.ADMIN] == set(Permission)

    def test_viewer_cannot_publish(self):
        assert Permission.SKILL_PUBLISH not in ROLE_PERMISSIONS[Role.VIEWER]
        assert Permission.SKILL_PUBLISH not in ROLE_PERMISSIONS[Role.AUTHOR]
        assert Permission.SKILL_PUBLISH in ROLE_PERMISSIONS[Role.PUBLISHER]


class TestNamespaceCovers:
    def test_global(self):
        assert namespace_covers("*", "org/acme/team")

    def test_exact(self):
        assert namespace_covers("org/acme", "org/acme")

    def test_parent_covers_child(self):
        assert namespace_covers("org/acme", "org/acme/team-ml/skill")

    def test_sibling_not_covered(self):
        assert not namespace_covers("org/acme", "org/other")

    def test_prefix_not_a_path_boundary(self):
        # "org/acme" must not cover "org/acme-corp" (substring, not child).
        assert not namespace_covers("org/acme", "org/acme-corp")

    def test_namespace_is_ancestor_of(self):
        ns = Namespace(path="org/acme", owner_id="u")
        assert ns.is_ancestor_of("org/acme/team")
        assert not ns.is_ancestor_of("org/acme")


# ---------------------------------------------------------------------------
# Passwords
# ---------------------------------------------------------------------------


class TestPasswords:
    def test_roundtrip(self):
        h = hash_password("hunter2", iterations=1000)
        assert verify_password("hunter2", h)
        assert not verify_password("wrong", h)

    def test_distinct_salts(self):
        assert hash_password("x", iterations=1000) != hash_password("x", iterations=1000)

    def test_empty_rejected(self):
        with pytest.raises(ValueError):
            hash_password("")

    def test_garbage_stored_value(self):
        assert not verify_password("x", "not-a-valid-hash")


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


@pytest.fixture
def store():
    db = MetadataDB(":memory:")
    db.initialize()
    s = RBACStore(db.conn)
    s.initialize()
    yield s
    db.close()


class TestStoreUsers:
    def test_create_and_verify(self, store):
        uid = store.create_user("alice", "pw-alice")
        assert store.get_user(uid)["username"] == "alice"
        assert store.verify_user("alice", "pw-alice")["user_id"] == uid
        assert store.verify_user("alice", "nope") is None
        assert store.verify_user("ghost", "x") is None

    def test_duplicate_username_rejected(self, store):
        store.create_user("bob", "pw")
        with pytest.raises(Exception):
            store.create_user("bob", "pw2")

    def test_set_password(self, store):
        uid = store.create_user("carol", "old")
        assert store.set_password(uid, "new")
        assert store.verify_user("carol", "new")
        assert store.verify_user("carol", "old") is None


class TestStoreAssignments:
    def test_add_get_remove(self, store):
        uid = store.create_user("dave", "pw")
        store.add_assignment(uid, Role.PUBLISHER, "org/acme", assigned_by="admin")
        assigns = store.get_assignments(uid)
        assert len(assigns) == 1
        assert assigns[0].role == Role.PUBLISHER
        assert store.remove_assignment(uid, Role.PUBLISHER, "org/acme")
        assert store.get_assignments(uid) == []

    def test_idempotent_upsert(self, store):
        uid = store.create_user("erin", "pw")
        store.add_assignment(uid, Role.VIEWER, "org/x", assigned_by="a")
        store.add_assignment(uid, Role.VIEWER, "org/x", assigned_by="b")
        assert len(store.get_assignments(uid)) == 1

    def test_expired_assignment_inert(self, store):
        uid = store.create_user("frank", "pw")
        store.add_assignment(uid, Role.ADMIN, "*", assigned_by="a", expires_at="2000-01-01T00:00:00+00:00")
        assert store.get_assignments(uid) == []


class TestStoreTokens:
    def test_create_get_revoke(self, store):
        uid = store.create_user("gina", "pw")
        raw, tid = store.create_token(uid, "ci", ["org/acme"])
        import hashlib

        th = hashlib.sha256(raw.encode()).hexdigest()
        tok = store.get_token_by_hash(th)
        assert tok is not None and tok.user_id == uid and tok.scopes == ["org/acme"]
        assert store.revoke_token(tid)
        assert store.get_token_by_hash(th).revoked

    def test_list_and_find_by_name(self, store):
        uid = store.create_user("hugo", "pw")
        store.create_token(uid, "t1", ["*"])
        assert len(store.list_tokens(uid)) == 1
        assert store.find_token_by_name(uid, "t1") is not None
        assert store.find_token_by_name(uid, "missing") is None


class TestStoreNamespacesAndDecisions:
    def test_namespace_crud(self, store):
        uid = store.create_user("ivy", "pw")
        store.create_namespace("org/acme", uid, "ACME")
        ns = store.create_namespace("org/acme/team", uid)
        assert ns.parent == "org/acme"
        assert store.get_namespace("org/acme").description == "ACME"
        assert {n.path for n in store.list_namespaces()} == {"org/acme", "org/acme/team"}

    def test_record_and_read_decisions(self, store):
        uid = store.create_user("jane", "pw")
        store.record_decision(
            user_id=uid,
            username="jane",
            permission="skill:publish",
            namespace="org/x",
            allowed=False,
            reason="denied",
        )
        decisions = store.read_decisions(user_id=uid)
        assert len(decisions) == 1 and decisions[0]["allowed"] == 0


class TestStoreBootstrap:
    def test_bootstrap_once(self, store):
        creds = store.bootstrap_admin()
        assert creds["username"] == "admin"
        assert store.verify_user("admin", creds["password"])
        # Admin has global ADMIN role.
        assigns = store.get_assignments(creds["user_id"])
        assert any(a.role == Role.ADMIN and a.namespace == "*" for a in assigns)
        # Second call is a no-op.
        assert store.bootstrap_admin() is None


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


@pytest.fixture
def engine_and_store(store):
    return RBACEngine(store), store


class TestEngine:
    def _identity(self, uid, token_scopes=None):
        return Identity(user_id=uid, username="u", token_scopes=token_scopes)

    def test_publisher_can_publish_in_scope(self, engine_and_store):
        engine, store = engine_and_store
        uid = store.create_user("p", "pw")
        store.add_assignment(uid, Role.PUBLISHER, "org/acme", assigned_by="a")
        d = engine.check(self._identity(uid), Permission.SKILL_PUBLISH, "org/acme/team-ml")
        assert d.allowed and "publisher" in d.reason

    def test_author_cannot_publish(self, engine_and_store):
        engine, store = engine_and_store
        uid = store.create_user("a", "pw")
        store.add_assignment(uid, Role.AUTHOR, "org/acme", assigned_by="a")
        d = engine.check(self._identity(uid), Permission.SKILL_PUBLISH, "org/acme")
        assert not d.allowed and "No role grants 'skill:publish'" in d.reason

    def test_inheritance_parent_covers_child_not_sibling(self, engine_and_store):
        engine, store = engine_and_store
        uid = store.create_user("ad", "pw")
        store.add_assignment(uid, Role.ADMIN, "org/acme", assigned_by="a")
        assert engine.check(self._identity(uid), Permission.SKILL_PUBLISH, "org/acme/team-ml")
        assert not engine.check(self._identity(uid), Permission.SKILL_PUBLISH, "org/other")

    def test_token_scope_gate(self, engine_and_store):
        engine, store = engine_and_store
        uid = store.create_user("s", "pw")
        store.add_assignment(uid, Role.PUBLISHER, "*", assigned_by="a")
        # Role would allow anywhere, but the token is scoped to org/team-a.
        ident = self._identity(uid, token_scopes=["org/team-a"])
        assert engine.check(ident, Permission.SKILL_CREATE, "org/team-a")
        d = engine.check(ident, Permission.SKILL_CREATE, "org/team-b")
        assert not d.allowed and "Token scope" in d.reason

    def test_no_roles_denied(self, engine_and_store):
        engine, store = engine_and_store
        uid = store.create_user("z", "pw")
        d = engine.check(self._identity(uid), Permission.SKILL_READ, "org/x")
        assert not d.allowed
