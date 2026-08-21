"""Focused tests for R11 diagnostic behavior comparison."""

from __future__ import annotations

from backtest.fund_rotation.champion_validation.behavior_comparison import compare_behavior


def test_same_selection_weight_trade_and_cash_path_is_behaviorally_equivalent() -> None:
    path = {
        "eligibility": {"w1": {"A", "B"}},
        "ranking": {"w1": ["A", "B"]},
        "selection": {"w1": {"A"}},
        "weights": {"w1": {"A": 1.0}},
        "trades": {"w1": [("BUY", "A", 1.0)]},
        "cash": {"w1": 0.0},
    }

    result = compare_behavior(path, path)

    assert result.behaviorally_equivalent is True
    assert result.difference_ratios == {
        "eligibility": 0.0,
        "ranking": 0.0,
        "selection": 0.0,
        "weights": 0.0,
        "trades": 0.0,
        "cash": 0.0,
    }


def test_behavior_comparison_reports_differences_and_uses_selection_trade_equivalence_gate() -> None:
    reference = {
        "eligibility": {"w1": {"A", "B"}, "w2": {"A", "B"}},
        "ranking": {"w1": ["A", "B"], "w2": ["A", "B"]},
        "selection": {"w1": {"A"}, "w2": {"A"}},
        "weights": {"w1": {"A": 1.0}, "w2": {"A": 1.0}},
        "trades": {"w1": [("BUY", "A", 1.0)], "w2": []},
        "cash": {"w1": 0.0, "w2": 0.0},
    }
    candidate = {
        **reference,
        "eligibility": {"w1": {"A"}, "w2": {"A", "B"}},
        "ranking": {"w1": ["B", "A"], "w2": ["A", "B"]},
        "weights": {"w1": {"A": 0.9}, "w2": {"A": 1.0}},
        "trades": {"w1": [("BUY", "A", 0.8)], "w2": []},
        "cash": {"w1": 0.2, "w2": 0.0},
    }

    result = compare_behavior(reference, candidate)

    assert result.behaviorally_equivalent is False
    assert result.difference_ratios["eligibility"] == 0.5
    assert result.difference_ratios["ranking"] == 0.5
    assert result.difference_ratios["selection"] == 0.0
    assert result.difference_ratios["trades"] == 0.5
    assert result.difference_ratios["cash"] == 0.5

