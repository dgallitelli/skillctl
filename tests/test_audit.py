"""Unit tests for AuditLogger — Task 5.2."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone, timedelta

import pytest

from skillctl.registry.audit import AuditEvent, AuditLogger


HMAC_KEY = b"test-secret-key-for-audit"


@pytest.fixture
def logger(tmp_path):
    """AuditLogger backed by a temp JSONL file."""
    return AuditLogger(tmp_path / "audit.jsonl", HMAC_KEY)


# -- write / read round-trip ------------------------------------------------

def test_log_and_read_round_trip(logger: AuditLogger):
    logger.log(
        action="skill.published",
        actor="ci-bot",
        resource="my-org/code-reviewer@1.0.0",
        details={"content_hash": "abc123", "size": 42},
    )
    events = logger.read()
    assert len(events) == 1
    ev = events[0]
    assert ev.action == "skill.published"
    assert ev.actor == "ci-bot"
    assert ev.resource == "my-org/code-reviewer@1.0.0"
    assert ev.details == {"content_hash": "abc123", "size": 42}
    assert ev.hmac_signature  # non-empty
    # timestamp should be valid ISO 8601
    datetime.fromisoformat(ev.timestamp)


def test_log_with_no_details_defaults_to_empty_dict(logger: AuditLogger):
    logger.log(action="token.created", actor="admin", resource="token-1")
    events = logger.read()
    assert len(events) == 1
    assert events[0].details == {}


# -- HMAC verification ------------------------------------------------------

def test_verify_integrity_all_valid(logger: AuditLogger):
    logger.log("skill.published", "ci", "org/skill@1.0.0")
    logger.log("skill.deleted", "admin", "org/skill@1.0.0")
    valid, invalid = logger.verify_integrity()
    assert valid == 2
    assert invalid == 0


def test_tamper_detection(logger: AuditLogger):
    logger.log("skill.published", "ci", "org/skill@1.0.0")
    logger.log("token.created", "admin", "token-1")

    # Tamper with the first line
    lines = logger.log_path.read_text().splitlines()
    entry = json.loads(lines[0])
    entry["actor"] = "evil-actor"
    lines[0] = json.dumps(entry)
    logger.log_path.write_text("\n".join(lines) + "\n")

    valid, invalid = logger.verify_integrity()
    assert valid == 1
    assert invalid == 1


def test_tamper_signature_field(logger: AuditLogger):
    logger.log("skill.published", "ci", "org/skill@1.0.0")

    lines = logger.log_path.read_text().splitlines()
    entry = json.loads(lines[0])
    entry["hmac_signature"] = "0" * 64  # bogus signature
    lines[0] = json.dumps(entry)
    logger.log_path.write_text("\n".join(lines) + "\n")

    valid, invalid = logger.verify_integrity()
    assert valid == 0
    assert invalid == 1


# -- time-range filtering ---------------------------------------------------

def test_filter_by_since_and_until(logger: AuditLogger):
    # Log three events with slightly different timestamps
    t1 = datetime(2025, 1, 1, 0, 0, 0, tzinfo=timezone.utc).isoformat()
    t2 = datetime(2025, 6, 1, 0, 0, 0, tzinfo=timezone.utc).isoformat()
    t3 = datetime(2025, 12, 1, 0, 0, 0, tzinfo=timezone.utc).isoformat()

    # Write events with controlled timestamps directly
    for ts, action in [(t1, "skill.published"), (t2, "token.created"), (t3, "skill.deleted")]:
        _write_event(logger, ts, action, "actor", "resource")

    # Filter: only events in the middle of the year
    since = datetime(2025, 3, 1, tzinfo=timezone.utc).isoformat()
    until = datetime(2025, 9, 1, tzinfo=timezone.utc).isoformat()
    events = logger.read(since=since, until=until)
    assert len(events) == 1
    assert events[0].action == "token.created"


def test_filter_by_since_only(logger: AuditLogger):
    t1 = datetime(2025, 1, 1, tzinfo=timezone.utc).isoformat()
    t2 = datetime(2025, 6, 1, tzinfo=timezone.utc).isoformat()
    _write_event(logger, t1, "skill.published", "a", "r")
    _write_event(logger, t2, "skill.deleted", "a", "r")

    since = datetime(2025, 3, 1, tzinfo=timezone.utc).isoformat()
    events = logger.read(since=since)
    assert len(events) == 1
    assert events[0].action == "skill.deleted"


def test_filter_by_until_only(logger: AuditLogger):
    t1 = datetime(2025, 1, 1, tzinfo=timezone.utc).isoformat()
    t2 = datetime(2025, 6, 1, tzinfo=timezone.utc).isoformat()
    _write_event(logger, t1, "skill.published", "a", "r")
    _write_event(logger, t2, "skill.deleted", "a", "r")

    until = datetime(2025, 3, 1, tzinfo=timezone.utc).isoformat()
    events = logger.read(until=until)
    assert len(events) == 1
    assert events[0].action == "skill.published"


# -- action filtering -------------------------------------------------------

def test_filter_by_action(logger: AuditLogger):
    logger.log("skill.published", "ci", "org/a@1.0.0")
    logger.log("token.created", "admin", "token-1")
    logger.log("skill.published", "ci", "org/b@1.0.0")

    events = logger.read(action="skill.published")
    assert len(events) == 2
    assert all(e.action == "skill.published" for e in events)


def test_filter_by_action_no_match(logger: AuditLogger):
    logger.log("skill.published", "ci", "org/a@1.0.0")
    events = logger.read(action="token.revoked")
    assert len(events) == 0


# -- multiple events --------------------------------------------------------

def test_multiple_events_preserved_in_order(logger: AuditLogger):
    for i in range(5):
        logger.log("skill.published", f"actor-{i}", f"org/skill@{i}.0.0")

    events = logger.read()
    assert len(events) == 5
    for i, ev in enumerate(events):
        assert ev.actor == f"actor-{i}"
        assert ev.resource == f"org/skill@{i}.0.0"


def test_limit_parameter(logger: AuditLogger):
    for i in range(10):
        logger.log("skill.published", "ci", f"org/skill@{i}.0.0")

    events = logger.read(limit=3)
    assert len(events) == 3


# -- empty log file ----------------------------------------------------------

def test_read_empty_log(logger: AuditLogger):
    # File doesn't exist yet
    events = logger.read()
    assert events == []


def test_verify_integrity_empty_log(logger: AuditLogger):
    valid, invalid = logger.verify_integrity()
    assert valid == 0
    assert invalid == 0


def test_read_empty_existing_file(logger: AuditLogger):
    logger.log_path.write_text("")
    events = logger.read()
    assert events == []


def test_verify_integrity_empty_existing_file(logger: AuditLogger):
    logger.log_path.write_text("")
    valid, invalid = logger.verify_integrity()
    assert valid == 0
    assert invalid == 0


# -- helpers -----------------------------------------------------------------

def _write_event(
    logger: AuditLogger,
    timestamp: str,
    action: str,
    actor: str,
    resource: str,
    details: dict | None = None,
) -> None:
    """Write an event with a controlled timestamp (bypasses log() for time control)."""
    import hashlib
    import hmac as _hmac

    payload = {
        "timestamp": timestamp,
        "action": action,
        "actor": actor,
        "resource": resource,
        "details": details or {},
    }
    payload_bytes = json.dumps(payload, sort_keys=True).encode()
    signature = _hmac.new(
        logger.hmac_key, payload_bytes, hashlib.sha256
    ).hexdigest()
    event = {**payload, "hmac_signature": signature}

    with open(logger.log_path, "a") as f:
        f.write(json.dumps(event) + "\n")
        f.flush()
