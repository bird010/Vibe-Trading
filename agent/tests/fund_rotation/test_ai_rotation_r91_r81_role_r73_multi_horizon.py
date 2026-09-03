"""TDD contract tests for R91 role-level multi-horizon rank aggregation."""

from __future__ import annotations

import pandas as pd
import pytest

try:
    from backtest.fund_rotation.strategies.ai_rotation_r86_r81_transition_cap_50.r91_r81_role_r73_multi_horizon import (
        HORIZONS,
        DESCRIPTOR,
        AiRotationR91R81RoleR73MultiHorizonStrategy,
        aggregate_role_multi_horizon_rank_scores,
        compute_role_multi_horizon_returns,
    )
    _IMPORT_ERROR = None
except ImportError as exc:
    HORIZONS = ()
    DESCRIPTOR = None
    AiRotationR91R81RoleR73MultiHorizonStrategy = None
    aggregate_role_multi_horizon_rank_scores = None
    compute_role_multi_horizon_returns = None
    _IMPORT_ERROR = exc


def _require_r91() -> None:
    assert _IMPORT_ERROR is None, f"R91 package missing: {_IMPORT_ERROR}"


def test_r91_aggregates_equal_weight_deterministic_role_ranks_fail_closed():
    _require_r91()
    ranked, diagnostics = aggregate_role_multi_horizon_rank_scores(
        {
            60: {"R1": 0.1, "R2": 0.2, "R3": 0.3},
            120: {"R1": 0.3, "R2": 0.2, "R3": 0.1},
            240: {"R1": 0.2, "R2": 0.1, "R3": 0.3},
        }
    )
    assert ranked == ["R3", "R1", "R2"]
    assert diagnostics["R1"]["rank_60"] == 3
    assert diagnostics["R1"]["rank_120"] == 1
    assert diagnostics["R1"]["rank_240"] == 2
    assert diagnostics["R1"]["aggregate_rank"] == pytest.approx(2.0)

    ranked, diagnostics = aggregate_role_multi_horizon_rank_scores(
        {60: {"R1": 0.1, "R2": 0.2}, 120: {"R1": 0.1}, 240: {"R1": 0.1, "R2": 0.2}}
    )
    assert ranked == ["R1"]
    assert diagnostics["R2"]["status"] == "INCOMPLETE_HORIZON"


def test_r91_requires_horizon_plus_one_causal_observations():
    _require_r91()
    dates = pd.date_range("2020-01-01", periods=240, freq="D")
    closes = pd.DataFrame({"REP": range(1, 241)}, index=dates)
    result = compute_role_multi_horizon_returns(
        closes, signal_date="20200827", representatives={"R1": "REP"}
    )
    assert set(result) == set(HORIZONS) == {60, 120, 240}
    assert result[60]["R1"] is not None
    assert result[120]["R1"] is not None
    assert result[240]["R1"] is None


def test_r91_pipeline_preserves_r88_and_replaces_only_role_ranking():
    _require_r91()
    assert DESCRIPTOR.id == "ai_rotation_r91_r81_role_r73_multi_horizon"
    strategy = AiRotationR91R81RoleR73MultiHorizonStrategy()
    pipeline = strategy.describe_decision_pipeline(strategy.config_model())
    assert pipeline["transition_cap_rule"] == "one-week positive target exposure capped at 50%"
    assert pipeline["selection_rule"] == "role ranking with entry Top3 and exit Top4 rank buffer"
    assert pipeline["medium_trend_gate"] == "adjusted_return_126d > 0 on current representatives"
    assert pipeline["role_rank_horizons"] == [60, 120, 240]
