"""Registry configuration — RegistryConfig dataclass.

Centralises all server configuration with sensible defaults.  Values can be
overridden via CLI flags passed to ``skillctl serve``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class RegistryConfig:
    """Configuration for the skill registry server."""

    host: str = "0.0.0.0"
    port: int = 8080
    data_dir: Path = field(default_factory=lambda: Path("~/.skillctl/registry").expanduser())
    storage_backend: str = "filesystem"  # "filesystem" or "github"
    github_repo: str | None = None  # e.g. "https://github.com/org/skill-registry.git"
    github_token: str | None = None  # PAT for push access
    github_branch: str = "main"
    auth_disabled: bool = False
    hmac_key: str | None = None  # Auto-generated if not set
    log_level: str = "info"
