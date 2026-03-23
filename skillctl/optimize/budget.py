"""Budget tracking for optimization runs.

Wraps BudgetState and uses skillctl.eval.cost.estimate_cost() for token pricing.
"""

from __future__ import annotations

from skillctl.eval.cost import estimate_cost
from skillctl.optimize.types import BudgetState, TokenUsage


class BudgetTracker:
    """Tracks cumulative LLM token spend and enforces a USD budget cap.

    Uses skillctl.eval.cost.estimate_cost() for pricing and maintains
    per-cycle cost tracking alongside cumulative totals.
    """

    def __init__(self, budget_usd: float, model: str) -> None:
        self._state = BudgetState(budget_usd=budget_usd)
        self._model = model
        self._cycle_input_tokens: int = 0
        self._cycle_output_tokens: int = 0

    def track(self, usage: TokenUsage) -> None:
        """Add token usage to cumulative and cycle totals."""
        self._state.total_input_tokens += usage.input_tokens
        self._state.total_output_tokens += usage.output_tokens
        self._state.total_cost_usd += usage.cost_usd
        self._cycle_input_tokens += usage.input_tokens
        self._cycle_output_tokens += usage.output_tokens

    @property
    def remaining_usd(self) -> float:
        """Return remaining budget in USD."""
        return self._state.remaining_usd

    @property
    def exhausted(self) -> bool:
        """Return True if the budget is spent."""
        return self._state.exhausted

    @property
    def total_cost_usd(self) -> float:
        """Return total accumulated cost in USD."""
        return self._state.total_cost_usd

    def cycle_cost(self) -> float:
        """Return cost accumulated in the current cycle."""
        result = estimate_cost(
            self._cycle_input_tokens,
            self._cycle_output_tokens,
            self._model,
        )
        return result["total_cost"]

    def start_cycle(self) -> None:
        """Reset per-cycle counters for a new cycle."""
        self._cycle_input_tokens = 0
        self._cycle_output_tokens = 0
