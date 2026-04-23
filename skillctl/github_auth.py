"""GitHub Device Flow authentication for skillctl.

Implements the OAuth 2.0 Device Authorization Grant so users can authenticate
with ``skillctl login`` instead of manually creating and pasting PATs.

Requires a registered GitHub OAuth App with device flow enabled.
The client_id can be configured via:
  - ``skillctl config set github.client_id <id>``
  - ``SKILLCTL_GITHUB_CLIENT_ID`` env var
  - ``--client-id`` CLI flag
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import yaml

# Default client_id — teams should register their own OAuth App and override.
# This is a placeholder; replace with a real registered app's client_id.
DEFAULT_CLIENT_ID = ""

DEVICE_CODE_URL = "https://github.com/login/device/code"
ACCESS_TOKEN_URL = "https://github.com/login/oauth/access_token"
USER_API_URL = "https://api.github.com/user"


def get_client_id(cli_value: str | None = None) -> str:
    """Resolve the GitHub OAuth App client_id from CLI > env > config > default."""
    if cli_value:
        return cli_value
    env = os.environ.get("SKILLCTL_GITHUB_CLIENT_ID")
    if env:
        return env
    # Try config file
    config_path = Path.home() / ".skillctl" / "config.yaml"
    if config_path.exists():
        cfg = yaml.safe_load(config_path.read_text()) or {}
        cid = cfg.get("github", {}).get("client_id")
        if cid:
            return cid
    if DEFAULT_CLIENT_ID:
        return DEFAULT_CLIENT_ID
    return ""


def device_flow_login(
    client_id: str,
    scopes: str = "repo",
) -> str:
    """Run the GitHub Device Flow interactively.

    Prints instructions to stdout, polls for authorization, and returns
    the access token on success.

    Raises ``SystemExit`` on failure or timeout.
    """
    # Step 1: Request device + user codes
    data = urllib.parse.urlencode({
        "client_id": client_id,
        "scope": scopes,
    }).encode()

    req = urllib.request.Request(
        DEVICE_CODE_URL, data=data, method="POST",
        headers={"Accept": "application/json"},
    )

    try:
        with urllib.request.urlopen(req) as resp:
            body = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        print(f"Error requesting device code: {e.code} {e.read().decode()}", file=sys.stderr)
        sys.exit(1)

    device_code = body["device_code"]
    user_code = body["user_code"]
    verification_uri = body["verification_uri"]
    expires_in = body.get("expires_in", 900)
    interval = body.get("interval", 5)

    # Step 2: Prompt user
    print()
    print("  To authenticate with GitHub, open this URL in your browser:")
    print()
    print(f"    {verification_uri}")
    print()
    print(f"  And enter this code: {user_code}")
    print()
    print("  Waiting for authorization...", end="", flush=True)

    # Step 3: Poll for access token
    deadline = time.time() + expires_in

    while time.time() < deadline:
        time.sleep(interval)
        print(".", end="", flush=True)

        poll_data = urllib.parse.urlencode({
            "client_id": client_id,
            "device_code": device_code,
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        }).encode()

        poll_req = urllib.request.Request(
            ACCESS_TOKEN_URL, data=poll_data, method="POST",
            headers={"Accept": "application/json"},
        )

        try:
            with urllib.request.urlopen(poll_req) as resp:
                result = json.loads(resp.read().decode())
        except urllib.error.HTTPError:
            continue

        if "access_token" in result:
            print(" done!")
            return result["access_token"]

        error = result.get("error", "")
        if error == "authorization_pending":
            continue
        elif error == "slow_down":
            interval = result.get("interval", interval + 5)
            continue
        elif error == "expired_token":
            print("\n\nDevice code expired. Please try again.", file=sys.stderr)
            sys.exit(1)
        elif error == "access_denied":
            print("\n\nAuthorization was denied.", file=sys.stderr)
            sys.exit(1)
        else:
            print(f"\n\nUnexpected error: {error}", file=sys.stderr)
            sys.exit(1)

    print("\n\nTimed out waiting for authorization.", file=sys.stderr)
    sys.exit(1)


def verify_token(token: str) -> dict:
    """Verify a GitHub token by calling the user API.

    Returns the user info dict on success, exits on failure.
    """
    req = urllib.request.Request(
        USER_API_URL, method="GET",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        print(f"Token verification failed: {e.code}", file=sys.stderr)
        sys.exit(1)


def save_github_token(token: str) -> None:
    """Save the GitHub token to ~/.skillctl/config.yaml."""
    config_path = Path.home() / ".skillctl" / "config.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)

    cfg: dict = {}
    if config_path.exists():
        cfg = yaml.safe_load(config_path.read_text()) or {}

    cfg.setdefault("github", {})["token"] = token
    config_path.write_text(yaml.dump(cfg, default_flow_style=False))
    config_path.chmod(0o600)


def load_github_token() -> str | None:
    """Load the GitHub token from env > config file."""
    token = os.environ.get("SKILLCTL_GITHUB_TOKEN")
    if token:
        return token
    config_path = Path.home() / ".skillctl" / "config.yaml"
    if config_path.exists():
        cfg = yaml.safe_load(config_path.read_text()) or {}
        return cfg.get("github", {}).get("token")
    return None
