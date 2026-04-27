"""Registry server — FastAPI application factory.

Creates and configures the FastAPI application, mounts the REST API router,
and manages the application lifespan (DB init, storage backend init, audit
logger init).
"""

from __future__ import annotations

import secrets
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI  # type: ignore[import-not-found]

from skillctl.registry.api import api_router
from skillctl.registry.audit import AuditLogger
from skillctl.registry.auth import AuthManager
from skillctl.registry.config import RegistryConfig
from skillctl.registry.db import MetadataDB
from skillctl.registry.storage import FilesystemBackend


def _resolve_hmac_key(config: RegistryConfig, data_dir: Path) -> bytes:
    """Return the HMAC key to use for audit log signing."""
    if config.hmac_key is not None:
        return config.hmac_key.encode()

    key_path = data_dir / "hmac.key"
    if key_path.exists():
        return key_path.read_bytes()

    key = secrets.token_bytes(32)
    key_path.write_bytes(key)
    key_path.chmod(0o600)
    return key


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """Initialise subsystems on startup and clean up on shutdown."""
    config: RegistryConfig = app.state.config

    data_dir = config.data_dir
    data_dir.mkdir(parents=True, exist_ok=True)

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

    app.state.db = db
    app.state.storage = storage
    app.state.auth_manager = auth_manager
    app.state.audit = audit
    app.state.registry_config = config

    yield

    db.close()


def create_app(config: RegistryConfig | None = None) -> FastAPI:
    """Create and configure the registry FastAPI application."""
    if config is None:
        config = RegistryConfig()

    app = FastAPI(title="Skill Registry", lifespan=_lifespan)
    app.state.config = config
    app.include_router(api_router)

    if config.auth_disabled:
        print(
            "WARNING: Authentication is disabled — all requests are allowed "
            "without tokens. Do NOT use this in production.",
            file=sys.stderr,
        )

    return app
