"""Registry server — FastAPI application factory.

Creates and configures the FastAPI application, mounts the REST API router,
and manages the application lifespan (DB init, storage backend init, audit
logger init).

Security defaults:

- ``auth_disabled=True`` is **only** allowed in combination with a host
  binding of ``127.0.0.1`` / ``localhost`` / ``::1``.  This prevents a "dev"
  instance from being silently exposed to a network where any caller is
  treated as admin.

- The HMAC key for the audit log is read from (in order):
    1. ``RegistryConfig.hmac_key`` (CLI ``--hmac-key`` / env)
    2. ``SKILLCTL_HMAC_KEY`` env var
    3. An external ``hmac.key`` file outside ``data_dir`` is **not** auto-
       loaded; if no key is supplied, the server only auto-generates one
       when ``RegistryConfig.auto_generate_hmac_key=True``.  Otherwise it
       refuses to start so the operator can wire up real key management.

- The audit log file is created with mode 0o600 atomically.

- ``CORSMiddleware`` and ``TrustedHostMiddleware`` are installed by default,
  scoped to the configured host.

- ``slowapi`` rate limits are installed when the package is available; a
  warning is printed if it is not.
"""

from __future__ import annotations

import os
import secrets
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI  # type: ignore[import-not-found]
from starlette.middleware.cors import CORSMiddleware  # type: ignore[import-not-found]
from starlette.middleware.trustedhost import TrustedHostMiddleware  # type: ignore[import-not-found]

from skillctl._secure import atomic_write_secret
from skillctl.registry.api import api_router
from skillctl.registry.audit import AuditLogger
from skillctl.registry.auth import AuthManager
from skillctl.registry.config import RegistryConfig
from skillctl.registry.db import MetadataDB
from skillctl.registry.storage import FilesystemBackend


_LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


def _resolve_hmac_key(config: RegistryConfig, data_dir: Path) -> bytes:
    """Return the HMAC key to use for audit log signing.

    Resolution order:
        1. ``config.hmac_key`` (set via CLI ``--hmac-key`` or env passthrough)
        2. ``SKILLCTL_HMAC_KEY`` environment variable
        3. Auto-generated key written to ``data_dir/hmac.key`` *only when*
           ``config.auto_generate_hmac_key`` is ``True``.

    If none of these apply, raises ``RuntimeError`` so the operator
    explicitly opts into key management.
    """
    if config.hmac_key is not None and config.hmac_key:
        return config.hmac_key.encode()

    env_key = os.environ.get("SKILLCTL_HMAC_KEY")
    if env_key:
        return env_key.encode()

    if not config.auto_generate_hmac_key:
        raise RuntimeError(
            "No HMAC key configured for the audit log.\n"
            "Set SKILLCTL_HMAC_KEY=<hex>, pass --hmac-key, or pass\n"
            "--auto-generate-hmac-key to create one in data_dir (development only)."
        )

    key_path = data_dir / "hmac.key"
    if key_path.exists():
        return key_path.read_bytes()

    key = secrets.token_bytes(32)
    atomic_write_secret(key_path, key)
    return key


def _validate_security_invariants(config: RegistryConfig) -> None:
    """Refuse to start in unsafe configurations."""
    if config.auth_disabled and config.host not in _LOCAL_HOSTS:
        raise RuntimeError(
            f"auth_disabled=True is only permitted when host is one of "
            f"{sorted(_LOCAL_HOSTS)}; got host={config.host!r}.\n"
            "Either remove --auth-disabled, bind to 127.0.0.1, or run with\n"
            "auth enabled."
        )


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """Initialise subsystems on startup and clean up on shutdown."""
    config: RegistryConfig = app.state.config

    data_dir = config.data_dir
    data_dir.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(data_dir, 0o700)
    except OSError:
        pass

    db = MetadataDB(data_dir / "registry.db", check_same_thread=False)
    db.initialize()

    if config.storage_backend == "github":
        from skillctl.registry.github_backend import GitHubBackend

        if not config.github_repo:
            raise RuntimeError("github_repo is required when storage_backend='github'")
        clone_dir = data_dir / "git-clone"
        storage = GitHubBackend(
            repo_url=config.github_repo,
            clone_dir=clone_dir,
            branch=config.github_branch,
            github_token=config.github_token,
        )
        storage.setup()
        indexed = storage.rebuild_index(db)
        print(f"GitHub backend: cloned {config.github_repo}, indexed {indexed} skills", file=sys.stderr)
        app.state.github_backend = storage
    else:
        storage = FilesystemBackend(data_dir)
        app.state.github_backend = None

    auth_manager = AuthManager(db, disabled=config.auth_disabled)

    hmac_key = _resolve_hmac_key(config, data_dir)
    audit = AuditLogger(data_dir / "audit.jsonl", hmac_key=hmac_key)

    # RBAC (Milestone 1): shares the registry SQLite connection.
    from skillctl.registry.rbac.engine import RBACEngine
    from skillctl.registry.rbac.store import RBACStore

    rbac_store = RBACStore(db.conn)
    rbac_store.initialize()
    rbac_engine = RBACEngine(rbac_store)

    # First-run bootstrap: create an initial admin and print credentials once.
    bootstrap = rbac_store.bootstrap_admin()
    if bootstrap is not None:
        print(
            "\n".join(
                [
                    "",
                    "=" * 64,
                    "  No users found — created the initial admin.",
                    f"  Username: {bootstrap['username']}",
                    f"  Password: {bootstrap['password']}",
                    f"  Token:    {bootstrap['token']}",
                    "  Save these credentials — they won't be shown again.",
                    "  Change the password: skillctl auth change-password",
                    "=" * 64,
                    "",
                ]
            ),
            file=sys.stderr,
            flush=True,
        )

    app.state.db = db
    app.state.storage = storage
    app.state.auth_manager = auth_manager
    app.state.audit = audit
    app.state.rbac_store = rbac_store
    app.state.rbac_engine = rbac_engine
    app.state.registry_config = config

    yield

    db.close()


def _install_rate_limiting(app: FastAPI) -> None:
    """Install slowapi rate limiting if the package is available.

    Limits applied:
      - Global default: 60 requests per minute per IP.
      - ``POST /api/v1/skills`` (publish): 10 per minute per IP.
      - ``POST /api/v1/tokens`` (token mint): 5 per minute per IP.
      - Auth failures inside ``get_current_token`` are logged separately.

    If slowapi is not installed, a warning is printed but the server still
    starts.  Production deployments should always have slowapi available.
    """
    try:
        from slowapi import Limiter  # type: ignore[import-not-found]
        from slowapi.errors import RateLimitExceeded  # type: ignore[import-not-found]
        from slowapi.middleware import SlowAPIMiddleware  # type: ignore[import-not-found]
        from slowapi.util import get_remote_address  # type: ignore[import-not-found]
    except ImportError:
        print(
            "WARNING: slowapi is not installed — rate limiting is disabled. "
            "Install with: pip install 'skillsops[server]'.",
            file=sys.stderr,
        )
        return

    limiter = Limiter(key_func=get_remote_address, default_limits=["60/minute"])
    app.state.limiter = limiter
    app.add_middleware(SlowAPIMiddleware)

    from fastapi.responses import JSONResponse  # type: ignore[import-not-found]

    @app.exception_handler(RateLimitExceeded)
    async def _rate_limit_handler(request, exc):  # noqa: ARG001
        return JSONResponse(
            status_code=429,
            content={
                "code": "E_RATE_LIMITED",
                "what": "Rate limit exceeded",
                "why": str(exc),
                "fix": "Slow down and retry; backoff exponentially.",
            },
        )


def create_app(config: RegistryConfig | None = None) -> FastAPI:
    """Create and configure the registry FastAPI application."""
    if config is None:
        config = RegistryConfig()

    _validate_security_invariants(config)

    app = FastAPI(title="Skill Registry", lifespan=_lifespan)
    app.state.config = config

    # TrustedHostMiddleware: reject Host headers that don't match the
    # configured host.  This is the primary mitigation for DNS-rebind
    # attacks against a localhost registry instance.
    allowed_hosts = sorted({config.host, "localhost", "127.0.0.1", *config.allowed_hosts})
    if "*" in allowed_hosts:
        allowed_hosts = ["*"]
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts)

    # CORS: by default, no cross-origin browser access.  Operators who want
    # to expose the registry to a browser UI must explicitly add origins.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(config.cors_allow_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST", "DELETE", "PUT"],
        allow_headers=["Authorization", "Content-Type"],
    )

    _install_rate_limiting(app)

    app.include_router(api_router)

    if config.auth_disabled:
        print(
            "WARNING: Authentication is disabled — all requests are allowed "
            f"without tokens. Bound to {config.host}:{config.port} (localhost-only).",
            file=sys.stderr,
        )

    return app
