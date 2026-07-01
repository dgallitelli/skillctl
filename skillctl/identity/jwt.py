"""Minimal, dependency-free JWT (HS256) encode/verify.

Real HMAC-SHA256 signing using the stdlib — no PyJWT/cryptography needed for the
HS256 path. RS256 (asymmetric, JWKS) is left to an optional adapter; HS256 with
a shared secret is sufficient for self-hosted OIDC and is fully testable.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Optional


class JWTError(Exception):
    pass


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(segment: str) -> bytes:
    padding = "=" * (-len(segment) % 4)
    return base64.urlsafe_b64decode(segment + padding)


def encode(claims: dict, secret: str, *, algorithm: str = "HS256") -> str:
    if algorithm != "HS256":
        raise JWTError(f"Unsupported algorithm {algorithm!r} (only HS256 is built in)")
    header = {"alg": "HS256", "typ": "JWT"}
    h = _b64url_encode(json.dumps(header, separators=(",", ":")).encode())
    p = _b64url_encode(json.dumps(claims, separators=(",", ":")).encode())
    signing_input = f"{h}.{p}".encode()
    sig = hmac.new(secret.encode(), signing_input, hashlib.sha256).digest()
    return f"{h}.{p}.{_b64url_encode(sig)}"


def decode(
    token: str,
    secret: str,
    *,
    audience: Optional[str] = None,
    issuer: Optional[str] = None,
    verify_exp: bool = True,
    leeway: int = 0,
) -> dict:
    """Verify an HS256 JWT and return its claims. Raises ``JWTError`` on failure."""
    try:
        h_seg, p_seg, s_seg = token.split(".")
    except ValueError as exc:
        raise JWTError("Malformed token (expected 3 segments)") from exc

    try:
        header = json.loads(_b64url_decode(h_seg))
    except Exception as exc:  # noqa: BLE001
        raise JWTError("Malformed header") from exc
    if header.get("alg") != "HS256":
        raise JWTError(f"Unexpected alg {header.get('alg')!r}")

    signing_input = f"{h_seg}.{p_seg}".encode()
    expected = hmac.new(secret.encode(), signing_input, hashlib.sha256).digest()
    if not hmac.compare_digest(expected, _b64url_decode(s_seg)):
        raise JWTError("Signature verification failed")

    try:
        claims = json.loads(_b64url_decode(p_seg))
    except Exception as exc:  # noqa: BLE001
        raise JWTError("Malformed payload") from exc

    now = int(time.time())
    if verify_exp and "exp" in claims and now > int(claims["exp"]) + leeway:
        raise JWTError("Token has expired")
    if "nbf" in claims and now + leeway < int(claims["nbf"]):
        raise JWTError("Token not yet valid")
    if audience is not None:
        aud = claims.get("aud")
        aud_list = aud if isinstance(aud, list) else [aud]
        if audience not in aud_list:
            raise JWTError(f"Invalid audience (expected {audience!r})")
    if issuer is not None and claims.get("iss") != issuer:
        raise JWTError(f"Invalid issuer (expected {issuer!r})")
    return claims
