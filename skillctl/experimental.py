"""Shared notices for preview features whose trust boundaries are not integrated."""

from __future__ import annotations

import sys


def warn_experimental(feature: str, boundary: str) -> None:
    """Emit a consistent warning before an experimental command mutates or reports state."""
    print(
        f"WARNING: {feature} is experimental. {boundary}",
        file=sys.stderr,
    )
