"""Registry server — FastAPI application factory.

Creates and configures the FastAPI application, mounts API and web UI routers,
static files, Jinja2 templates, and manages the application lifespan (DB init,
storage backend init, audit logger init).
"""

from __future__ import annotations

import secrets
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from skillctl.registry.api import api_router
from skillctl.registry.audit import AuditLogger
from skillctl.registry.web import web_router
from skillctl.registry.auth import AuthManager
from skillctl.registry.config import RegistryConfig
from skillctl.registry.db import MetadataDB
from skillctl.registry.storage import FilesystemBackend

_PACKAGE_DIR = Path(__file__).parent
_STATIC_DIR = _PACKAGE_DIR / "static"
_TEMPLATES_DIR = _PACKAGE_DIR / "templates"


def _resolve_hmac_key(config: RegistryConfig, data_dir: Path) -> bytes:
    """Return the HMAC key to use for audit log signing.

    If *config.hmac_key* is set, use it directly.  Otherwise, read (or
    generate) a persistent key file at ``data_dir/hmac.key``.
    """
    if config.hmac_key is not None:
        return config.hmac_key.encode()

    key_path = data_dir / "hmac.key"
    if key_path.exists():
        return key_path.read_bytes()

    # Generate a new 32-byte random key and persist it.
    key = secrets.token_bytes(32)
    key_path.write_bytes(key)
    return key


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """Initialise subsystems on startup and clean up on shutdown."""
    config: RegistryConfig = app.state.config

    # Ensure data directory exists.
    data_dir = config.data_dir
    data_dir.mkdir(parents=True, exist_ok=True)

    # --- startup -----------------------------------------------------------
    db = MetadataDB(data_dir / "registry.db", check_same_thread=False)
    db.initialize()

    storage = FilesystemBackend(data_dir)

    auth_manager = AuthManager(db, disabled=config.auth_disabled)

    hmac_key = _resolve_hmac_key(config, data_dir)
    audit = AuditLogger(data_dir / "audit.jsonl", hmac_key=hmac_key)

    templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

    # Store everything on app.state for route handlers.
    app.state.db = db
    app.state.storage = storage
    app.state.auth_manager = auth_manager
    app.state.audit = audit
    app.state.templates = templates

    yield

    # --- shutdown ----------------------------------------------------------
    db.close()


def create_app(config: RegistryConfig | None = None) -> FastAPI:
    """Create and configure the registry FastAPI application."""
    if config is None:
        config = RegistryConfig()

    app = FastAPI(title="Skill Registry", lifespan=_lifespan)

    # Stash config so the lifespan can read it.
    app.state.config = config

    # Mount routers.
    app.include_router(api_router)
    app.include_router(web_router)

    # Mount static files.
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    if config.auth_disabled:
        print(
            "WARNING: Authentication is disabled — all requests are allowed "
            "without tokens. Do NOT use this in production.",
            file=sys.stderr,
        )

    return app
