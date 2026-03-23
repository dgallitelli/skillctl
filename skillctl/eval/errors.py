"""Structured errors for skillctl eval — every error includes what/why/fix."""

from dataclasses import dataclass


@dataclass
class EvalError(Exception):
    """Every user-facing error includes what/why/fix."""

    code: str
    what: str
    why: str
    fix: str

    def __str__(self):
        return f"{self.code}: {self.what}"

    def format_human(self) -> str:
        return (
            f"Error: {self.what}\n"
            f"  Why: {self.why}\n"
            f"  Fix: {self.fix}\n"
        )

    def format_json(self) -> dict:
        return {
            "code": self.code,
            "what": self.what,
            "why": self.why,
            "fix": self.fix,
        }
