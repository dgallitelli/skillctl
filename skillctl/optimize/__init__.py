"""skillctl.optimize — Automated skill improvement loop.

Evaluates a skill, analyzes failures via LLM, generates candidate rewrites,
re-evaluates them, and promotes the best candidate through an iterative cycle.
"""

from skillctl.optimize.loop import run_optimization
from skillctl.optimize.types import OptimizeConfig, OptimizationRun

__all__ = ["run_optimization", "OptimizeConfig", "OptimizationRun"]
