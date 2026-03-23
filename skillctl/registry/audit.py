"""Audit logger — append-only JSONL with HMAC-SHA256 signatures.

Records every mutating operation (publish, delete, token create/revoke) as a
signed JSONL entry.  Supports filtered reads and full-log integrity
verification.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class AuditEvent:
    """A single audit log entry."""

    timestamp: str  # ISO 8601
    action: str  # "skill.published", "skill.deleted", "token.created", etc.
    actor: str  # token name or "anonymous"
    resource: str  # "my-org/code-reviewer@1.0.0"
    details: dict  # action-specific metadata
    hmac_signature: str  # HMAC-SHA256 of the event payload


class AuditLogger:
    """Append-only JSONL audit log with HMAC-SHA256 integrity signatures."""

    def __init__(self, log_path: Path, hmac_key: bytes) -> None:
        self.log_path = log_path
        self.hmac_key = hmac_key

    def log(
        self,
        action: str,
        actor: str,
        resource: str,
        details: dict | None = None,
    ) -> None:
        """Append a signed event to the audit log."""
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "action": action,
            "actor": actor,
            "resource": resource,
            "details": details or {},
        }
        payload_bytes = json.dumps(payload, sort_keys=True).encode()
        signature = hmac.new(
            self.hmac_key, payload_bytes, hashlib.sha256
        ).hexdigest()
        event = {**payload, "hmac_signature": signature}

        with open(self.log_path, "a") as f:
            f.write(json.dumps(event) + "\n")
            f.flush()
            os.fsync(f.fileno())

    def read(
        self,
        since: str | None = None,
        until: str | None = None,
        action: str | None = None,
        limit: int = 100,
    ) -> list[AuditEvent]:
        """Read audit events, optionally filtered by time range and action."""
        if not self.log_path.exists():
            return []

        events: list[AuditEvent] = []
        with open(self.log_path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                entry = json.loads(line)
                ts = entry["timestamp"]

                if since is not None and ts < since:
                    continue
                if until is not None and ts > until:
                    continue
                if action is not None and entry["action"] != action:
                    continue

                events.append(
                    AuditEvent(
                        timestamp=entry["timestamp"],
                        action=entry["action"],
                        actor=entry["actor"],
                        resource=entry["resource"],
                        details=entry.get("details", {}),
                        hmac_signature=entry["hmac_signature"],
                    )
                )
                if len(events) >= limit:
                    break

        return events

    def verify_integrity(self) -> tuple[int, int]:
        """Verify HMAC signatures of all entries.

        Returns ``(valid_count, invalid_count)``.
        """
        if not self.log_path.exists():
            return (0, 0)

        valid = 0
        invalid = 0

        with open(self.log_path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                entry = json.loads(line)
                payload = {
                    "timestamp": entry["timestamp"],
                    "action": entry["action"],
                    "actor": entry["actor"],
                    "resource": entry["resource"],
                    "details": entry.get("details", {}),
                }
                payload_bytes = json.dumps(payload, sort_keys=True).encode()
                expected = hmac.new(
                    self.hmac_key, payload_bytes, hashlib.sha256
                ).hexdigest()

                if hmac.compare_digest(expected, entry.get("hmac_signature", "")):
                    valid += 1
                else:
                    invalid += 1

        return (valid, invalid)
