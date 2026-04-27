"""Structured errors for skillctl eval."""

from skillctl.errors import SkillctlError


class EvalError(SkillctlError):
    """Eval-specific error — inherits code/what/why/fix from SkillctlError."""

    pass
