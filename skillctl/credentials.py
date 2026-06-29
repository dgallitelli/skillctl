"""Local credential storage for authenticated registry sessions.

Stores per-registry tokens in ``~/.skillctl/credentials.json`` with mode
``0600``. Shape::

    {
      "registries": {
        "https://registry.example.com": {
          "token": "sk-...", "user": "alice", "expires_at": "2026-09-01T..."
        }
      }
    }

Reuses the existing ``~/.skillctl`` home (rather than introducing a separate
``~/.skillsops`` directory) so all CLI state lives in one place.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

CREDENTIALS_PATH = Path.home() / ".skillctl" / "credentials.json"


def _normalize(url: str) -> str:
    return url.rstrip("/")


def _load_raw(path: Path) -> dict:
    if not path.exists():
        return {"registries": {}}
    try:
        data = json.loads(path.read_text())
        if not isinstance(data, dict) or "registries" not in data:
            return {"registries": {}}
        return data
    except (json.JSONDecodeError, OSError):
        return {"registries": {}}


def _save_raw(data: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Write with restrictive perms from the start.
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, json.dumps(data, indent=2).encode("utf-8"))
    finally:
        os.close(fd)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def save_credentials(
    registry_url: str,
    token: str,
    user: str,
    expires_at: Optional[str] = None,
    *,
    path: Path = CREDENTIALS_PATH,
) -> None:
    data = _load_raw(path)
    data["registries"][_normalize(registry_url)] = {
        "token": token,
        "user": user,
        "expires_at": expires_at,
    }
    _save_raw(data, path)


def get_credentials(registry_url: str, *, path: Path = CREDENTIALS_PATH) -> Optional[dict]:
    data = _load_raw(path)
    return data["registries"].get(_normalize(registry_url))


def get_token(registry_url: str, *, path: Path = CREDENTIALS_PATH) -> Optional[str]:
    creds = get_credentials(registry_url, path=path)
    return creds["token"] if creds else None


def clear_credentials(registry_url: str, *, path: Path = CREDENTIALS_PATH) -> bool:
    data = _load_raw(path)
    key = _normalize(registry_url)
    if key in data["registries"]:
        del data["registries"][key]
        _save_raw(data, path)
        return True
    return False


def list_registries(*, path: Path = CREDENTIALS_PATH) -> dict:
    return _load_raw(path)["registries"]
