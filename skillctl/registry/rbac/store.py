"""SQLite-backed RBAC store.

Shares the registry's SQLite connection (``MetadataDB.conn``) so RBAC state
lives in the same database file as skills/tokens. Owns these tables:

    users, role_assignments, namespaces, auth_decisions

and extends the existing ``tokens`` table with nullable ``user_id`` / ``scopes``
columns (idempotent migration) so one token table serves both legacy
permission-string tokens and identity-bound RBAC tokens.

Password hashing uses stdlib ``hashlib.pbkdf2_hmac`` (no bcrypt/argon2
dependency) so the core package keeps a single runtime dependency (pyyaml).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from skillctl.registry.rbac.models import (
    AccessToken,
    Namespace,
    Role,
    RoleAssignment,
    role_from_str,
)

# ---------------------------------------------------------------------------
# Password hashing (stdlib pbkdf2; no external deps)
# ---------------------------------------------------------------------------

_PBKDF2_ALGO = "sha256"
_PBKDF2_ITERATIONS = 600_000
_PBKDF2_SALT_BYTES = 16


def hash_password(password: str, *, iterations: int = _PBKDF2_ITERATIONS) -> str:
    """Hash a password with PBKDF2-HMAC-SHA256.

    Returns a self-describing string: ``pbkdf2_sha256$<iters>$<salt_hex>$<hash_hex>``.
    """
    if not password:
        raise ValueError("password must not be empty")
    salt = secrets.token_bytes(_PBKDF2_SALT_BYTES)
    dk = hashlib.pbkdf2_hmac(_PBKDF2_ALGO, password.encode("utf-8"), salt, iterations)
    return f"pbkdf2_{_PBKDF2_ALGO}${iterations}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """Constant-time verify a password against a stored PBKDF2 hash."""
    try:
        algo_tag, iters_s, salt_hex, hash_hex = stored.split("$")
        if algo_tag != f"pbkdf2_{_PBKDF2_ALGO}":
            return False
        iterations = int(iters_s)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
    except (ValueError, AttributeError):
        return False
    dk = hashlib.pbkdf2_hmac(_PBKDF2_ALGO, password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(dk, expected)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_CREATE_USERS = """\
CREATE TABLE IF NOT EXISTS users (
    user_id TEXT PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1
);
"""

_CREATE_ROLE_ASSIGNMENTS = """\
CREATE TABLE IF NOT EXISTS role_assignments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES users(user_id),
    role TEXT NOT NULL,
    namespace TEXT NOT NULL,
    assigned_by TEXT NOT NULL,
    assigned_at TEXT NOT NULL,
    expires_at TEXT,
    UNIQUE(user_id, role, namespace)
);
"""

_CREATE_NAMESPACES = """\
CREATE TABLE IF NOT EXISTS namespaces (
    path TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    parent TEXT,
    created_at TEXT NOT NULL
);
"""

_CREATE_AUTH_DECISIONS = """\
CREATE TABLE IF NOT EXISTS auth_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    user_id TEXT NOT NULL,
    username TEXT NOT NULL,
    permission TEXT NOT NULL,
    namespace TEXT NOT NULL,
    allowed INTEGER NOT NULL,
    reason TEXT NOT NULL,
    token_id TEXT,
    request_context TEXT
);
"""

_CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_assign_user ON role_assignments(user_id);",
    "CREATE INDEX IF NOT EXISTS idx_authdec_user ON auth_decisions(user_id);",
    "CREATE INDEX IF NOT EXISTS idx_authdec_ts ON auth_decisions(timestamp DESC);",
    "CREATE INDEX IF NOT EXISTS idx_tokens_user ON tokens(user_id);",
]


class RBACStore:
    """RBAC persistence over a shared SQLite connection."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    # -- lifecycle -----------------------------------------------------------

    def initialize(self) -> None:
        """Create RBAC tables and migrate the tokens table. Idempotent."""
        self.conn.executescript(_CREATE_USERS + _CREATE_ROLE_ASSIGNMENTS + _CREATE_NAMESPACES + _CREATE_AUTH_DECISIONS)
        self._migrate_tokens_table()
        for idx in _CREATE_INDEXES:
            self.conn.execute(idx)
        self.conn.commit()

    def _migrate_tokens_table(self) -> None:
        """Add ``user_id`` / ``scopes`` columns to the existing tokens table."""
        cols = {row["name"] for row in self.conn.execute("PRAGMA table_info(tokens)").fetchall()}
        if "user_id" not in cols:
            self.conn.execute("ALTER TABLE tokens ADD COLUMN user_id TEXT")
        if "scopes" not in cols:
            self.conn.execute("ALTER TABLE tokens ADD COLUMN scopes TEXT")
        if "last_used_at" not in cols:
            self.conn.execute("ALTER TABLE tokens ADD COLUMN last_used_at TEXT")

    # -- users ---------------------------------------------------------------

    def create_user(self, username: str, password: str, *, user_id: Optional[str] = None) -> str:
        """Create a user; returns the user_id. Raises on duplicate username."""
        uid = user_id or str(uuid.uuid4())
        self.conn.execute(
            "INSERT INTO users (user_id, username, password_hash, created_at, is_active) VALUES (?, ?, ?, ?, 1)",
            (uid, username, hash_password(password), _now_iso()),
        )
        self.conn.commit()
        return uid

    def get_user_by_username(self, username: str) -> Optional[dict]:
        row = self.conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        return dict(row) if row else None

    def get_user(self, user_id: str) -> Optional[dict]:
        row = self.conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
        return dict(row) if row else None

    def list_users(self) -> list[dict]:
        rows = self.conn.execute("SELECT * FROM users ORDER BY created_at").fetchall()
        return [dict(r) for r in rows]

    def count_users(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]

    def verify_user(self, username: str, password: str) -> Optional[dict]:
        """Return the user dict if username+password match and the user is active."""
        user = self.get_user_by_username(username)
        if user is None or not user["is_active"]:
            return None
        if not verify_password(password, user["password_hash"]):
            return None
        return user

    def set_password(self, user_id: str, password: str) -> bool:
        cur = self.conn.execute(
            "UPDATE users SET password_hash = ? WHERE user_id = ?",
            (hash_password(password), user_id),
        )
        self.conn.commit()
        return cur.rowcount > 0

    # -- role assignments ----------------------------------------------------

    def add_assignment(
        self,
        user_id: str,
        role: Role,
        namespace: str,
        assigned_by: str,
        expires_at: Optional[str] = None,
    ) -> None:
        """Assign a role to a user within a namespace scope (idempotent upsert)."""
        self.conn.execute(
            """INSERT INTO role_assignments (user_id, role, namespace, assigned_by, assigned_at, expires_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(user_id, role, namespace)
               DO UPDATE SET assigned_by=excluded.assigned_by,
                             assigned_at=excluded.assigned_at,
                             expires_at=excluded.expires_at""",
            (user_id, role.value, namespace, assigned_by, _now_iso(), expires_at),
        )
        self.conn.commit()

    def remove_assignment(self, user_id: str, role: Role, namespace: str) -> bool:
        cur = self.conn.execute(
            "DELETE FROM role_assignments WHERE user_id = ? AND role = ? AND namespace = ?",
            (user_id, role.value, namespace),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def get_assignments(self, user_id: str) -> list[RoleAssignment]:
        """Return non-expired role assignments for a user (engine contract)."""
        rows = self.conn.execute(
            "SELECT * FROM role_assignments WHERE user_id = ?",
            (user_id,),
        ).fetchall()
        now = _now_iso()
        out: list[RoleAssignment] = []
        for r in rows:
            if r["expires_at"] is not None and r["expires_at"] <= now:
                continue  # Expired assignment is inert.
            try:
                role = role_from_str(r["role"])
            except ValueError:
                continue  # Unknown role string — ignore defensively.
            out.append(
                RoleAssignment(
                    user_id=r["user_id"],
                    role=role,
                    namespace=r["namespace"],
                    assigned_by=r["assigned_by"],
                    assigned_at=r["assigned_at"],
                    expires_at=r["expires_at"],
                )
            )
        return out

    # -- namespaces ----------------------------------------------------------

    def create_namespace(
        self,
        path: str,
        owner_id: str,
        description: str = "",
        parent: Optional[str] = None,
    ) -> Namespace:
        if parent is None and "/" in path:
            parent = path.rsplit("/", 1)[0]
        self.conn.execute(
            "INSERT INTO namespaces (path, owner_id, description, parent, created_at) VALUES (?, ?, ?, ?, ?)",
            (path, owner_id, description, parent, _now_iso()),
        )
        self.conn.commit()
        return Namespace(path=path, owner_id=owner_id, description=description, parent=parent)

    def get_namespace(self, path: str) -> Optional[Namespace]:
        row = self.conn.execute("SELECT * FROM namespaces WHERE path = ?", (path,)).fetchone()
        if not row:
            return None
        return Namespace(
            path=row["path"],
            owner_id=row["owner_id"],
            description=row["description"],
            parent=row["parent"],
            created_at=row["created_at"],
        )

    def list_namespaces(self) -> list[Namespace]:
        rows = self.conn.execute("SELECT * FROM namespaces ORDER BY path").fetchall()
        return [
            Namespace(
                path=r["path"],
                owner_id=r["owner_id"],
                description=r["description"],
                parent=r["parent"],
                created_at=r["created_at"],
            )
            for r in rows
        ]

    # -- identity-bound tokens (shared tokens table) -------------------------

    def create_token(
        self,
        user_id: str,
        name: str,
        scopes: list[str],
        expires_in_days: Optional[int] = None,
    ) -> tuple[str, str]:
        """Create an identity-bound, namespace-scoped token.

        Returns ``(raw_token, token_id)``. Only the SHA-256 hash is stored.
        """
        raw_token = secrets.token_hex(32)
        token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
        token_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        expires_at = (now + timedelta(days=expires_in_days)).isoformat() if expires_in_days is not None else None
        self.conn.execute(
            """INSERT INTO tokens (id, name, token_hash, permissions, created_at, expires_at, revoked_at,
                                   user_id, scopes, last_used_at)
               VALUES (?, ?, ?, '[]', ?, ?, NULL, ?, ?, NULL)""",
            (token_id, name, token_hash, now.isoformat(), expires_at, user_id, json.dumps(scopes)),
        )
        self.conn.commit()
        return raw_token, token_id

    def create_token_with_expiry_iso(
        self,
        user_id: str,
        name: str,
        scopes: list[str],
        expires_at: Optional[str],
    ) -> tuple[str, str]:
        """Like :meth:`create_token` but with an explicit ISO expiry (used by tests)."""
        raw_token = secrets.token_hex(32)
        token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
        token_id = str(uuid.uuid4())
        self.conn.execute(
            """INSERT INTO tokens (id, name, token_hash, permissions, created_at, expires_at, revoked_at,
                                   user_id, scopes, last_used_at)
               VALUES (?, ?, ?, '[]', ?, ?, NULL, ?, ?, NULL)""",
            (token_id, name, token_hash, _now_iso(), expires_at, user_id, json.dumps(scopes)),
        )
        self.conn.commit()
        return raw_token, token_id

    def get_token_by_hash(self, token_hash: str) -> Optional[AccessToken]:
        row = self.conn.execute("SELECT * FROM tokens WHERE token_hash = ?", (token_hash,)).fetchone()
        if row is None or row["user_id"] is None:
            return None
        scopes = json.loads(row["scopes"]) if row["scopes"] else []
        return AccessToken(
            token_id=row["id"],
            user_id=row["user_id"],
            token_hash=row["token_hash"],
            name=row["name"],
            scopes=scopes,
            created_at=row["created_at"],
            expires_at=row["expires_at"],
            last_used_at=row["last_used_at"],
            revoked=row["revoked_at"] is not None,
        )

    def list_tokens(self, user_id: str) -> list[AccessToken]:
        rows = self.conn.execute(
            "SELECT * FROM tokens WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,),
        ).fetchall()
        out: list[AccessToken] = []
        for row in rows:
            scopes = json.loads(row["scopes"]) if row["scopes"] else []
            out.append(
                AccessToken(
                    token_id=row["id"],
                    user_id=row["user_id"],
                    token_hash=row["token_hash"],
                    name=row["name"],
                    scopes=scopes,
                    created_at=row["created_at"],
                    expires_at=row["expires_at"],
                    last_used_at=row["last_used_at"],
                    revoked=row["revoked_at"] is not None,
                )
            )
        return out

    def find_token_by_name(self, user_id: str, name: str) -> Optional[AccessToken]:
        for tok in self.list_tokens(user_id):
            if tok.name == name and not tok.revoked:
                return tok
        return None

    def touch_token(self, token_id: str) -> None:
        self.conn.execute(
            "UPDATE tokens SET last_used_at = ? WHERE id = ?",
            (_now_iso(), token_id),
        )
        self.conn.commit()

    def revoke_token(self, token_id: str) -> bool:
        cur = self.conn.execute(
            "UPDATE tokens SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL",
            (_now_iso(), token_id),
        )
        self.conn.commit()
        return cur.rowcount > 0

    # -- auth decision audit (SQLite mirror of the HMAC chain) ---------------

    def record_decision(
        self,
        *,
        user_id: str,
        username: str,
        permission: str,
        namespace: str,
        allowed: bool,
        reason: str,
        token_id: Optional[str] = None,
        request_context: Optional[dict] = None,
    ) -> None:
        self.conn.execute(
            """INSERT INTO auth_decisions
               (timestamp, user_id, username, permission, namespace, allowed, reason, token_id, request_context)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                _now_iso(),
                user_id,
                username,
                permission,
                namespace,
                1 if allowed else 0,
                reason,
                token_id,
                json.dumps(request_context) if request_context else None,
            ),
        )
        self.conn.commit()

    def read_decisions(self, *, user_id: Optional[str] = None, limit: int = 100) -> list[dict]:
        if user_id:
            rows = self.conn.execute(
                "SELECT * FROM auth_decisions WHERE user_id = ? ORDER BY id DESC LIMIT ?",
                (user_id, limit),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM auth_decisions ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    # -- bootstrap -----------------------------------------------------------

    def bootstrap_admin(self) -> Optional[dict]:
        """If no users exist, create an initial admin with a random password+token.

        Returns ``{"username", "password", "token"}`` once (or ``None`` if users
        already exist). The admin is granted the ADMIN role globally (``*``).
        """
        if self.count_users() > 0:
            return None

        username = "admin"
        password = secrets.token_urlsafe(24)
        user_id = self.create_user(username, password)
        self.add_assignment(user_id, Role.ADMIN, "*", assigned_by="bootstrap")
        raw_token, _ = self.create_token(user_id, name="bootstrap-admin", scopes=["*"])
        return {"username": username, "password": password, "token": raw_token, "user_id": user_id}
