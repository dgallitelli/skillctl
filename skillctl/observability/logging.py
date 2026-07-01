"""Structured JSON logging for SkillsOps.

JSON log output with correlation fields for log aggregation (ELK, CloudWatch,
Datadog). Standalone — does not depend on OpenTelemetry.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone

_CORRELATION_FIELDS = (
    "correlation_id",
    "trace_id",
    "span_id",
    "actor",
    "skill",
    "namespace",
    "decision",
    "hook_name",
    "invocation_id",
    "duration_ms",
)


class StructuredFormatter(logging.Formatter):
    """JSON log formatter for SkillsOps."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for field in _CORRELATION_FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                log_entry[field] = value
        if hasattr(record, "extra_fields"):
            log_entry.update(record.extra_fields)  # type: ignore[arg-type]
        return json.dumps(log_entry, default=str)


def configure_structured_logging(level: str = "INFO", output: str = "stderr") -> logging.Logger:
    """Configure structured JSON logging; returns the ``skillsops`` logger."""
    logger = logging.getLogger("skillsops")
    logger.setLevel(getattr(logging, level.upper()))
    logger.handlers.clear()
    stream = sys.stderr if output == "stderr" else sys.stdout
    handler = logging.StreamHandler(stream)
    handler.setFormatter(StructuredFormatter())
    logger.addHandler(handler)
    logger.propagate = False
    return logger
