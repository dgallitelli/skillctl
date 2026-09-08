"""Authentication system — token-based auth with scoped permissions.

Implements ``AuthManager`` for creating, verifying, and revoking API tokens
with scoped permissions (``read``, ``read:<ns>``, ``write:<ns>``, ``admin``).
Provides a FastAPI dependency for bearer-token middleware.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, Request  # type: ignore[import-not-found]

from skillctl.registry.db import MetadataDB

# Permission strings accepted in legacy tokens. Namespace scopes use the same
# slash-delimited hierarchy as RBAC (for example ``org/acme/team-ml``).
PERMISSION_PATTERN = re.compile(r"^(admin|read|(?:read|write):[a-z0-9-]+(?:/[a-z0-9-]+)*)$")


def validate_permissions(permissions: list[str]) -> None:
    """Validate legacy token permission strings without creating a token."""
    if not isinstance(permissions, list) or not all(isinstance(p, str) for p in permissions):
        raise ValueError("permissions must be a list of strings")
    for permission in permissions:
        if not PERMISSION_PATTERN.fullmatch(permission):
            raise ValueError(
                f"Invalid permission {permission!r}. Allowed: 'admin', 'read', "
                "'read:<namespace>', 'write:<namespace>' "
                "(namespace = slash-delimited [a-z0-9-]+ segments)."
            )


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class TokenInfo:
    """Verified token information returned by ``AuthManager.verify_token``."""

    token_id: str
    name: str
    permissions: list[str]
    created_at: str
    expires_at: str | None


# ---------------------------------------------------------------------------
# AuthManager
# ---------------------------------------------------------------------------


class AuthManager:
    """Token-based authentication with scoped permissions.

    Parameters
    ----------
    db : MetadataDB
        Database instance (must be initialised) that contains the ``tokens`` table.
    disabled : bool
        When *True* every request is treated as authenticated with full access.
    """

    def __init__(self, db: MetadataDB, disabled: bool = False) -> None:
        self._db = db
        self.disabled = disabled

    # -- token lifecycle -----------------------------------------------------

    def create_token(
        self,
        name: str,
        permissions: list[str],
        expires_in_days: int | None = None,
    ) -> str:
        """Create a new API token.

        Generates 32 random bytes (64 hex chars), stores only the SHA-256 hash
        in the database, and returns the raw token string (shown once).

        Raises ``ValueError`` if any permission string fails to match
        :data:`PERMISSION_PATTERN`.
        """
        validate_permissions(permissions)

        raw_token = secrets.token_hex(32)  # 64 hex chars
        token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
        token_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)

        expires_at: str | None = None
        if expires_in_days is not None:
            expires_at = (now + timedelta(days=expires_in_days)).isoformat()

        self._db.conn.execute(
            """INSERT INTO tokens (id, name, token_hash, permissions,
                                   created_at, expires_at, revoked_at)
               VALUES (?, ?, ?, ?, ?, ?, NULL)""",
            (
                token_id,
                name,
                token_hash,
                json.dumps(permissions),
                now.isoformat(),
                expires_at,
            ),
        )
        self._db.conn.commit()
        return raw_token

    def verify_token(self, raw_token: str) -> TokenInfo | None:
        """Verify a raw token and return its info, or *None* if invalid.

        A token is invalid if it does not exist, has been revoked, or has
        expired.
        """
        if self.disabled:
            return _anonymous_token()

        token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
        row = self._db.conn.execute(
            "SELECT * FROM tokens WHERE token_hash = ?",
            (token_hash,),
        ).fetchone()

        if row is None:
            return None

        # Check revocation
        if row["revoked_at"] is not None:
            return None

        # Check expiry
        if row["expires_at"] is not None:
            expires = datetime.fromisoformat(row["expires_at"])
            if datetime.now(timezone.utc) >= expires:
                return None

        return TokenInfo(
            token_id=row["id"],
            name=row["name"],
            permissions=json.loads(row["permissions"]),
            created_at=row["created_at"],
            expires_at=row["expires_at"],
        )

    # -- permission scoping (Task 4.2) ---------------------------------------

    def check_permission(
        self,
        token_info: TokenInfo,
        required: str,
        namespace: str | None = None,
    ) -> bool:
        """Check whether *token_info* satisfies the *required* permission.

        Permission hierarchy:
        - ``"admin"`` grants everything.
        - ``"write:<ns>"`` grants write **and** read on *<ns>*.
        - ``"read:<ns>"`` grants read on *<ns>*.
        - ``"read"`` (unscoped) grants read on every namespace.

        Parameters
        ----------
        token_info : TokenInfo
            The verified token whose permissions are checked.
        required : str
            ``"read"``, ``"write"``, or ``"admin"``.
        namespace : str | None
            Required when *required* is ``"write"`` (target namespace) and
            optional when *required* is ``"read"`` (caller passes the
            namespace being read; ``None`` means "any namespace" — only
            satisfied by an unscoped ``read`` or ``admin``).
        """
        perms = token_info.permissions

        # admin grants everything
        if "admin" in perms:
            return True

        if required == "read":
            # Unscoped 'read' grants access to all namespaces.
            if "read" in perms:
                return True
            if namespace is None:
                # Caller didn't pass a namespace.  Refuse — only an unscoped
                # 'read' or 'admin' token can read across all namespaces.
                # Endpoints that currently call this without a namespace
                # (e.g. list-skills) must filter by an allowed namespace
                # set or require unscoped read.
                return False
            return f"read:{namespace}" in perms or f"write:{namespace}" in perms

        if required == "write":
            if namespace is None:
                return False
            return f"write:{namespace}" in perms

        if required == "admin":
            return "admin" in perms

        return False

    @staticmethod
    def allowed_namespaces(token_info: TokenInfo) -> set[str] | None:
        """Return the set of namespaces *token_info* may read.

        ``None`` means "all namespaces" (unscoped ``read`` or ``admin``).
        An empty set means "none".
        """
        perms = token_info.permissions
        if "admin" in perms or "read" in perms:
            return None
        out: set[str] = set()
        for p in perms:
            if p.startswith("read:"):
                out.add(p.split(":", 1)[1])
            elif p.startswith("write:"):
                out.add(p.split(":", 1)[1])
        return out

    def revoke_token(self, token_id: str) -> bool:
        """Revoke a token by setting its ``revoked_at`` timestamp.

        Returns *True* if a token was found and revoked, *False* otherwise.
        """
        now = datetime.now(timezone.utc).isoformat()
        cur = self._db.conn.execute(
            "UPDATE tokens SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL",
            (now, token_id),
        )
        self._db.conn.commit()
        return cur.rowcount > 0


# ---------------------------------------------------------------------------
# FastAPI dependency (Task 4.3)
# ---------------------------------------------------------------------------


def get_auth_manager(request: Request) -> AuthManager:
    """Retrieve the ``AuthManager`` stored on ``request.app.state``."""
    return request.app.state.auth_manager


def _anonymous_token() -> TokenInfo:
    """Synthetic TokenInfo used when auth is disabled (localhost-only)."""
    return TokenInfo(
        token_id="anonymous",
        name="anonymous",
        permissions=["admin"],
        created_at=datetime.now(timezone.utc).isoformat(),
        expires_at=None,
    )


async def get_current_token(
    request: Request,
    auth_manager: AuthManager = Depends(get_auth_manager),
) -> TokenInfo:
    """FastAPI dependency that extracts and verifies a Bearer token.

    If ``auth_manager.disabled`` is *True*, returns a synthetic anonymous
    ``TokenInfo`` without requiring a header.  ``auth_disabled`` is enforced
    to bind only to loopback at server startup, so this only fires for
    localhost callers.

    Raises ``HTTPException(401)`` when the token is missing or invalid.
    """
    if auth_manager.disabled:
        return _anonymous_token()

    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")

    raw_token = auth_header[len("Bearer ") :]
    token_info = auth_manager.verify_token(raw_token)
    if token_info is None:
        raise HTTPException(
            status_code=401,
            detail="Invalid or expired token. Verify with 'skillctl token create' or check expiry.",
        )

    return token_info
