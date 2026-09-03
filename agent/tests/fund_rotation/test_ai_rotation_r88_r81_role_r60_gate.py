"""TDD contract tests for R88: R87 plus a role-level R60 trend gate."""

from __future__ import annotations

import pandas as pd

try:
    from backtest.fund_rotation.strategies.ai_rotation_r86_r81_transition_cap_50.r88_r81_role_r60_gate import (
        DESCRIPTOR,
        AiRotationR88R81RoleR60GateStrategy,
        EconomicRoleR81RoleR60GateSession,
        apply_role_medium_trend_gate,
        compute_adjusted_return_126d,
    )
    _R88_IMPORT_ERROR = None
except ImportError as exc:  # Expected RED state before R88 is added.
    DESCRIPTOR = None
    AiRotationR88R81RoleR60GateStrategy = None
    EconomicRoleR81RoleR60GateSession = None
    apply_role_medium_trend_gate = None
    compute_adjusted_return_126d = None
    _R88_IMPORT_ERROR = exc


def _require_r88() -> None:
    assert _R88_IMPORT_ERROR is None, f"R88 package missing: {_R88_IMPORT_ERROR}"


def _bars(code: str, closes, dates):
    frame = pd.DataFrame({"ts_code": code, "trade_date": dates, "close": closes})
    for field in ("open", "high", "low"):
        frame[field] = frame["close"]
    return frame


def test_r88_uses_causal_strict_127_adjusted_closes():
    _require_r88()
    dates = pd.bdate_range("2024-01-01", periods=128).strftime("%Y%m%d")
    bars = _bars("A", range(1, 129), dates)
    adjustments = pd.DataFrame({"ts_code": "A", "trade_date": dates, "adj_factor": 1.0})

    result = compute_adjusted_return_126d(bars, adjustments, dates[-2])

    assert result["status"] == "VALID"
    assert result["observations"] == 127
    assert result["return_126d"] == (127.0 / 1.0) - 1.0


def test_r88_role_gate_is_positive_only_and_fail_closed():
    _require_r88()
    dates = pd.bdate_range("2024-01-01", periods=127).strftime("%Y%m%d")
    bars = pd.concat(
        [
            _bars("A", range(1, 128), dates),
            _bars("B", range(128, 1, -1), dates),
        ],
        ignore_index=True,
    )
    adjustments = pd.DataFrame(
        {"ts_code": ["A"] * 127 + ["B"] * 127, "trade_date": list(dates) * 2, "adj_factor": 1.0}
    )

    qualified, diagnostics = apply_role_medium_trend_gate(
        {"R1": "A", "R2": "B", "R3": None, "R4": "MISSING"},
        bars,
        adjustments,
        dates[-1],
    )

    assert qualified == {"R1"}
    assert diagnostics["R1"]["medium_trend_positive"] is True
    assert diagnostics["R2"]["medium_trend_positive"] is False
    assert diagnostics["R3"]["status"] == "MISSING_REPRESENTATIVE"
    assert diagnostics["R4"]["status"] in {
        "INSUFFICIENT_OBSERVATIONS",
        "INVALID_DATA",
    }
    assert all("cluster" not in key for key in diagnostics)


def test_r88_preserves_r87_buffer_and_r86_cap_pipeline():
    _require_r88()
    assert DESCRIPTOR.id == "ai_rotation_r88_r81_role_r60_gate"
    strategy = AiRotationR88R81RoleR60GateStrategy()
    pipeline = strategy.describe_decision_pipeline(strategy.config_model())
    assert pipeline["transition_cap_rule"] == (
        "one-week positive target exposure capped at 50%"
    )
    assert pipeline["selection_rule"] == "role ranking with entry Top3 and exit Top4 rank buffer"
    assert pipeline["medium_trend_gate"] == "adjusted_return_126d > 0 on current representatives"
    assert EconomicRoleR81RoleR60GateSession.__mro__[1].__name__ == (
        "EconomicRoleR81RoleRankBufferSession"
    )
