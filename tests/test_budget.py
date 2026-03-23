"""Unit tests for BudgetTracker."""

from skillctl.optimize.budget import BudgetTracker
from skillctl.optimize.types import TokenUsage


def test_initial_state():
    bt = BudgetTracker(budget_usd=10.0, model="sonnet")
    assert bt.remaining_usd == 10.0
    assert bt.total_cost_usd == 0.0
    assert not bt.exhausted


def test_track_accumulates_cost():
    bt = BudgetTracker(budget_usd=10.0, model="sonnet")
    usage = TokenUsage(input_tokens=1000, output_tokens=500, cost_usd=0.05)
    bt.track(usage)
    assert bt.total_cost_usd == 0.05
    assert bt.remaining_usd == 9.95


def test_track_multiple_calls():
    bt = BudgetTracker(budget_usd=1.0, model="sonnet")
    bt.track(TokenUsage(input_tokens=100, output_tokens=50, cost_usd=0.3))
    bt.track(TokenUsage(input_tokens=200, output_tokens=100, cost_usd=0.4))
    assert bt.total_cost_usd == 0.7
    assert abs(bt.remaining_usd - 0.3) < 1e-9
    assert not bt.exhausted


def test_exhausted_when_budget_reached():
    bt = BudgetTracker(budget_usd=1.0, model="sonnet")
    bt.track(TokenUsage(input_tokens=1000, output_tokens=500, cost_usd=1.0))
    assert bt.exhausted
    assert bt.remaining_usd == 0.0


def test_exhausted_when_budget_exceeded():
    bt = BudgetTracker(budget_usd=1.0, model="sonnet")
    bt.track(TokenUsage(input_tokens=1000, output_tokens=500, cost_usd=1.5))
    assert bt.exhausted
    assert bt.remaining_usd == 0.0


def test_cycle_cost_uses_estimate_cost():
    bt = BudgetTracker(budget_usd=10.0, model="sonnet")
    # sonnet pricing: input $3/1M, output $15/1M
    bt.track(TokenUsage(input_tokens=1_000_000, output_tokens=0, cost_usd=3.0))
    assert bt.cycle_cost() == 3.0


def test_start_cycle_resets_cycle_counters():
    bt = BudgetTracker(budget_usd=10.0, model="sonnet")
    bt.track(TokenUsage(input_tokens=1000, output_tokens=500, cost_usd=0.05))
    assert bt.cycle_cost() > 0

    bt.start_cycle()
    assert bt.cycle_cost() == 0.0
    # Total cost is NOT reset
    assert bt.total_cost_usd == 0.05


def test_cycle_cost_only_counts_current_cycle():
    bt = BudgetTracker(budget_usd=10.0, model="sonnet")
    bt.track(TokenUsage(input_tokens=1000, output_tokens=500, cost_usd=0.05))
    bt.start_cycle()
    bt.track(TokenUsage(input_tokens=2000, output_tokens=1000, cost_usd=0.10))

    # cycle_cost should only reflect the second track call's tokens
    from skillctl.eval.cost import estimate_cost
    expected = estimate_cost(2000, 1000, "sonnet")["total_cost"]
    assert bt.cycle_cost() == expected
