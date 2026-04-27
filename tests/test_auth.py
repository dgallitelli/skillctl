"""Unit tests for AuthManager — Task 4.4."""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

import pytest

from skillctl.registry.auth import AuthManager, TokenInfo
from skillctl.registry.db import MetadataDB


@pytest.fixture
def db(tmp_path):
    """Create an initialized MetadataDB backed by a temp file."""
    mdb = MetadataDB(tmp_path / "test.db")
    mdb.initialize()
    yield mdb
    mdb.close()


@pytest.fixture
def auth(db):
    """AuthManager with auth enabled."""
    return AuthManager(db, disabled=False)


@pytest.fixture
def auth_disabled(db):
    """AuthManager with auth disabled."""
    return AuthManager(db, disabled=True)


# -- create_token -----------------------------------------------------------


def test_create_token_returns_64_hex_chars(auth: AuthManager):
    raw = auth.create_token("ci-bot", ["read"])
    assert len(raw) == 64
    # Must be valid hex
    int(raw, 16)


def test_create_token_stores_hash_not_raw(auth: AuthManager, db: MetadataDB):
    raw = auth.create_token("ci-bot", ["read"])
    expected_hash = hashlib.sha256(raw.encode()).hexdigest()
    row = db.conn.execute(
        "SELECT token_hash FROM tokens WHERE token_hash = ?",
        (expected_hash,),
    ).fetchone()
    assert row is not None
    # Raw token should NOT appear anywhere in the DB
    rows = db.conn.execute("SELECT * FROM tokens").fetchall()
    for r in rows:
        assert raw not in str(dict(r))


# -- verify_token -----------------------------------------------------------


def test_verify_valid_token(auth: AuthManager):
    raw = auth.create_token("test-token", ["read", "write:my-org"])
    info = auth.verify_token(raw)
    assert info is not None
    assert isinstance(info, TokenInfo)
    assert info.name == "test-token"
    assert info.permissions == ["read", "write:my-org"]
    assert info.token_id  # non-empty UUID


def test_verify_invalid_token_returns_none(auth: AuthManager):
    assert auth.verify_token("not-a-real-token") is None


def test_verify_expired_token_returns_none(auth: AuthManager, db: MetadataDB):
    raw = auth.create_token("expiring", ["read"], expires_in_days=1)
    # Manually set expires_at to the past
    token_hash = hashlib.sha256(raw.encode()).hexdigest()
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    db.conn.execute(
        "UPDATE tokens SET expires_at = ? WHERE token_hash = ?",
        (past, token_hash),
    )
    db.conn.commit()
    assert auth.verify_token(raw) is None


def test_verify_revoked_token_returns_none(auth: AuthManager):
    raw = auth.create_token("revokable", ["admin"])
    info = auth.verify_token(raw)
    assert info is not None
    auth.revoke_token(info.token_id)
    assert auth.verify_token(raw) is None


# -- revoke_token -----------------------------------------------------------


def test_revoke_token_returns_true(auth: AuthManager):
    raw = auth.create_token("to-revoke", ["read"])
    info = auth.verify_token(raw)
    assert info is not None
    assert auth.revoke_token(info.token_id) is True


def test_revoke_nonexistent_token_returns_false(auth: AuthManager):
    assert auth.revoke_token("no-such-id") is False


def test_revoke_already_revoked_returns_false(auth: AuthManager):
    raw = auth.create_token("double-revoke", ["read"])
    info = auth.verify_token(raw)
    assert info is not None
    assert auth.revoke_token(info.token_id) is True
    assert auth.revoke_token(info.token_id) is False


# -- check_permission -------------------------------------------------------


def test_admin_grants_all(auth: AuthManager):
    token = TokenInfo(
        token_id="t1",
        name="admin",
        permissions=["admin"],
        created_at="",
        expires_at=None,
    )
    assert auth.check_permission(token, "read") is True
    assert auth.check_permission(token, "write", "any-ns") is True
    assert auth.check_permission(token, "admin") is True


def test_write_ns_grants_write_and_read(auth: AuthManager):
    token = TokenInfo(
        token_id="t2",
        name="writer",
        permissions=["write:my-org"],
        created_at="",
        expires_at=None,
    )
    assert auth.check_permission(token, "write", "my-org") is True
    assert auth.check_permission(token, "read") is True
    assert auth.check_permission(token, "admin") is False


def test_write_wrong_namespace_returns_false(auth: AuthManager):
    token = TokenInfo(
        token_id="t3",
        name="writer",
        permissions=["write:my-org"],
        created_at="",
        expires_at=None,
    )
    assert auth.check_permission(token, "write", "other-org") is False


def test_read_grants_read_only(auth: AuthManager):
    token = TokenInfo(
        token_id="t4",
        name="reader",
        permissions=["read"],
        created_at="",
        expires_at=None,
    )
    assert auth.check_permission(token, "read") is True
    assert auth.check_permission(token, "write", "my-org") is False
    assert auth.check_permission(token, "admin") is False


def test_write_without_namespace_returns_false(auth: AuthManager):
    token = TokenInfo(
        token_id="t5",
        name="writer",
        permissions=["write:my-org"],
        created_at="",
        expires_at=None,
    )
    assert auth.check_permission(token, "write", None) is False


# -- auth_disabled mode -----------------------------------------------------


def test_auth_disabled_verify_returns_anonymous(auth_disabled: AuthManager):
    info = auth_disabled.verify_token("")
    assert info is not None
    assert info.name == "anonymous"
    assert info.token_id == "anonymous"
    assert "admin" in info.permissions


def test_auth_disabled_verify_any_token_returns_anonymous(auth_disabled: AuthManager):
    info = auth_disabled.verify_token("literally-anything")
    assert info is not None
    assert info.name == "anonymous"


# -- token with expiry set (not yet expired) --------------------------------


def test_verify_token_with_future_expiry(auth: AuthManager):
    raw = auth.create_token("future", ["read"], expires_in_days=30)
    info = auth.verify_token(raw)
    assert info is not None
    assert info.expires_at is not None
