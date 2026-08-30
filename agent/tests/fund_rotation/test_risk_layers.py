from __future__ import annotations

import math

import pytest

from backtest.fund_rotation.risk_layers import (
    apply_volatility_target,
    count_identity_breadth,
    compute_portfolio_volatility,
    select_defense_asset,
)


def test_volatility_target_caps_exposure_without_leverage():
    weights, cash, diagnostics = apply_volatility_target(
        {"A": 0.6, "B": 0.4}, portfolio_volatility=0.30, target_volatility=0.15
    )
    assert weights == {"A": pytest.approx(0.3), "B": pytest.approx(0.2)}
    assert cash == pytest.approx(0.5)
    assert diagnostics["exposure"] == pytest.approx(0.5)
    assert diagnostics["leverage"] is False


def test_volatility_target_rejects_already_levered_input():
    weights, cash, diagnostics = apply_volatility_target(
        {"A": 1.1}, portfolio_volatility=0.10, target_volatility=0.15
    )
    assert weights == {}
    assert cash == pytest.approx(1.0)
    assert diagnostics["reason"] == "target_weights_invalid"


def test_portfolio_volatility_fails_closed_for_missing_columns_or_nan_rows():
    import pandas as pd

    returns = pd.DataFrame({"A": [0.01, 0.02], "B": [0.01, float("nan")]})
    assert compute_portfolio_volatility(returns, {"A": 0.5, "B": 0.5}) is None
    assert compute_portfolio_volatility(returns, {"A": 1.0, "C": 0.0}) is None
    weights, cash, diagnostics = apply_volatility_target(
        {"A": 0.5, "B": 0.5}, portfolio_volatility=None, target_volatility=0.15
    )
    assert weights == {}
    assert cash == 1.0
    assert diagnostics["reason"] == "portfolio_volatility_unavailable"


@pytest.mark.parametrize("volatility", [None, 0.0, math.nan, -0.1])
def test_volatility_target_fails_closed_for_missing_or_invalid_volatility(volatility):
    weights, cash, diagnostics = apply_volatility_target(
        {"A": 1.0}, portfolio_volatility=volatility, target_volatility=0.15
    )
    assert weights == {}
    assert cash == pytest.approx(1.0)
    assert diagnostics["reason"] == "portfolio_volatility_unavailable"


def test_defense_arms_are_fixed_and_relative_momentum_is_positive_only():
    assert select_defense_asset("cash", fixed_short_bond="BOND", relative_scores={"GOLD": 0.9}) is None
    assert select_defense_asset("fixed_short_bond", fixed_short_bond="BOND", relative_scores={}) == "BOND"
    assert select_defense_asset(
        "relative_momentum",
        fixed_short_bond="BOND",
        relative_scores={"GOLD": 0.1, "BOND": 0.2, "CASH": -0.1},
    ) == "BOND"
    assert select_defense_asset(
        "relative_momentum",
        fixed_short_bond="BOND",
        relative_scores={"GOLD": -0.1},
    ) is None


def test_breadth_counts_unique_identities_and_fails_closed_on_missing_identity():
    result = count_identity_breadth(
        positive_codes=["A", "B", "C"],
        available_codes=["A", "B", "C", "D"],
        identity_by_code={"A": "IDX-1", "B": "IDX-1", "C": "IDX-2", "D": "IDX-3"},
    )
    assert result == {
        "positive_identity_count": 2,
        "available_identity_count": 3,
        "breadth": pytest.approx(2 / 3),
        "status": "VALID",
    }

    unavailable = count_identity_breadth(
        positive_codes=["A"],
        available_codes=["A", "B"],
        identity_by_code={"A": "IDX-1"},
    )
    assert unavailable["status"] == "UNAVAILABLE"
    assert unavailable["breadth"] is None


def test_batch_5_strategies_are_registered_without_absolute_momentum():
    from backtest.fund_rotation.strategies.registry import default_fund_rotation_strategies

    registered = {
        strategy.descriptor.id: strategy
        for strategy in default_fund_rotation_strategies()
        if strategy.descriptor.id.startswith("ai_rotation_r7")
    }
    assert set(registered) >= {
        "ai_rotation_r75_r39_vol_target",
        "ai_rotation_r76_cash_defense_baseline",
        "ai_rotation_r77_defense_relative_momentum",
    }
    for strategy in registered.values():
        assert "absolute momentum" not in strategy.descriptor.description.lower()
