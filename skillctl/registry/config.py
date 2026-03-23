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
    storage_backend: str = "filesystem"  # "filesystem" or "s3"
    s3_bucket: str | None = None
    s3_prefix: str = "blobs/"
    auth_disabled: bool = False
    hmac_key: str | None = None  # Auto-generated if not set
    log_level: str = "info"
