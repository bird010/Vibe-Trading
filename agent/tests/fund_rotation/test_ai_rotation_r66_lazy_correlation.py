import math
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

import backtest.fund_rotation.strategies.ai_rotation_r66_lazy_correlation.strategy as r66_module

from backtest.fund_rotation.strategies.ai_rotation_r66_lazy_correlation.strategy import (
    PairwiseCorrelationLookup,
    select_lazy_direct_correlation_diversified,
    AiRotationR66LazyCorrelationStrategy,
)
from backtest.fund_rotation.contracts import StrategyDecisionContext
from backtest.fund_rotation.strategies.ai_rotation_r64_direct_corr_diversification.strategy import (
    AiRotationR64DirectCorrDiversificationStrategy,
)


def test_lazy_lookup_caches_pair_and_preserves_pandas_pair_contract():
    import pandas as pd

    returns = pd.DataFrame(
        {
            "A": [1.0, 2.0, 3.0, 4.0],
            "B": [4.0, 3.0, 2.0, 1.0],
            "C": [1.0, 1.0, 2.0, 2.0],
        }
    )
    lookup = PairwiseCorrelationLookup(returns, ["A", "B", "C"], 3)

    first = lookup("B", "A")
    second = lookup("A", "B")

    assert first == second
    assert first[1] == 4
    assert first[0] == -1.0
    assert lookup.computation_count == 1


def test_lazy_selector_only_requests_pairs_used_by_greedy_selection():
    calls = []
    values = {
        ("A", "B"): (0.10, 20),
        ("A", "C"): (0.20, 20),
        ("B", "C"): (0.30, 20),
    }

    def lookup(left, right):
        calls.append((left, right))
        return values[tuple(sorted((left, right)))]

    selected, diagnostics = select_lazy_direct_correlation_diversified(
        ["A", "B", "C", "D"], lookup, top_n=3
    )

    assert selected == ["A", "B", "C"]
    assert {tuple(sorted(pair)) for pair in calls} == {
        ("A", "B"),
        ("A", "C"),
        ("B", "C"),
    }
    assert diagnostics["selected_codes"] == ["A", "B", "C"]


def test_lazy_selector_keeps_unavailable_reason_before_later_high_corr():
    def lookup(left, right):
        pair = tuple(sorted((left, right)))
        if pair == ("A", "B"):
            return (0.90, 20)
        return (math.nan, 0)

    selected, diagnostics = select_lazy_direct_correlation_diversified(
        ["A", "B", "C"], lookup, top_n=3
    )

    assert selected == ["A"]
    assert diagnostics["correlation_rejected_candidates"]["B"] == (
        "PAIRWISE_CORRELATION_TOO_HIGH"
    )


def test_lazy_selector_checks_later_pair_after_high_correlation():
    values = {
        ("A", "B"): (0.10, 20),
        ("A", "C"): (0.90, 20),
        ("B", "C"): (math.nan, 0),
    }

    def lookup(left, right):
        return values[tuple(sorted((left, right)))]

    selected, diagnostics = select_lazy_direct_correlation_diversified(
        ["A", "B", "C"], lookup, top_n=3
    )

    assert selected == ["A", "B"]
    assert diagnostics["correlation_rejected_candidates"]["C"] == (
        "PAIRWISE_CORRELATION_UNAVAILABLE"
    )


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        ({("A", "B"): (0.80, 20)}, "PAIRWISE_CORRELATION_TOO_HIGH"),
        ({("A", "B"): (0.10, 19)}, "PAIRWISE_CORRELATION_UNAVAILABLE"),
        ({("A", "B"): (math.nan, 20)}, "PAIRWISE_CORRELATION_UNAVAILABLE"),
        ({("A", "B"): (-0.90, 20)}, None),
    ],
)
def test_lazy_selector_boundary_and_missing_pair_behavior(values, expected):
    def lookup(left, right):
        return values[tuple(sorted((left, right)))]

    selected, diagnostics = select_lazy_direct_correlation_diversified(
        ["A", "B"], lookup, top_n=2
    )

    if expected is None:
        assert selected == ["A", "B"]
    else:
        assert selected == ["A"]
        assert diagnostics["correlation_rejected_candidates"]["B"] == expected


def test_r66_uses_independent_grouped_factor_builder():
    assert hasattr(r66_module, "build_grouped_factor_rows")
    assert r66_module.build_grouped_factor_rows is not None


def test_grouped_factor_rows_match_legacy_factor_rows():
    import backtest.fund_rotation.strategies.ai_rotation_r58_r39_signal_r57.strategy as r58_module

    dates = pd.date_range("2024-01-01", periods=49, freq="D")
    bars = pd.concat(
        [
            pd.DataFrame(
                {
                    "ts_code": code,
                    "trade_date": dates.strftime("%Y%m%d"),
                    "open": np.arange(100.0, 149.0),
                    "high": np.arange(101.0, 150.0),
                    "low": np.arange(99.0, 148.0),
                    "close": np.arange(100.0, 149.0),
                    "vol": 1.0,
                    "amount": 1.0,
                }
            )
            for code in ("A", "B", "C")
        ],
        ignore_index=True,
    )
    adjustments = bars[["ts_code", "trade_date"]].assign(adj_factor=1.0)
    view = SimpleNamespace(
        daily_bars=lambda fields, lookback: bars,
        fund_adjustments=lambda lookback: adjustments,
    )
    legacy = SimpleNamespace(_representatives={1: "A", 2: "B", 3: "C"})
    grouped = SimpleNamespace(_representatives={1: "A", 2: "B", 3: "C"})

    expected = r58_module.AiRotationR58R39SignalR57Session._factor_rows(
        legacy, view, "20240218"
    )
    actual = r66_module.build_grouped_factor_rows(grouped, view, "20240218")

    assert actual == expected


def test_r66_descriptor_and_frozen_configuration():
    strategy = AiRotationR66LazyCorrelationStrategy()
    assert strategy.descriptor.id == "ai_rotation_r66_lazy_correlation"
    assert strategy.descriptor.name.startswith("R66 ")
    assert strategy.config_model().top_n == 3


def test_r66_lazy_selector_matches_r64_selector_for_same_pairs():
    from backtest.fund_rotation.strategies.ai_rotation_r64_direct_corr_diversification.strategy import (
        select_direct_correlation_diversified,
    )

    returns = pd.DataFrame(
        {
            "A": np.arange(20, dtype=float),
            "B": np.arange(20, dtype=float) ** 2,
            "C": np.arange(20, dtype=float)[::-1],
            "D": np.arange(20, dtype=float) * 0.5 + 3.0,
        }
    )
    ranked = ["A", "B", "C", "D"]
    pairs = {}
    observations = {}
    for index, left in enumerate(ranked):
        for right in ranked[index + 1:]:
            pair = returns[[left, right]].dropna()
            key = "|".join(sorted((left, right)))
            observations[key] = len(pair)
            pairs[key] = float(pair[left].corr(pair[right]))

    old_selected, old_diag = select_direct_correlation_diversified(
        ranked, pairs, observations
    )
    lookup = PairwiseCorrelationLookup(returns, ranked, 20)
    new_selected, new_diag = select_lazy_direct_correlation_diversified(
        ranked, lookup
    )

    assert new_selected == old_selected
    assert new_diag == old_diag
    assert lookup.computation_count < len(pairs)


def test_r66_session_keeps_r64_targets_and_diagnostics(monkeypatch):
    rows = {
        code: {
            "ts_code": code,
            "bias": value,
            "slope": value,
            "raw_slope_25d": value,
            "efficiency": value,
        }
        for code, value in (("A", 5.0), ("B", 4.0), ("C", 3.0), ("D", 2.0))
    }
    weekly_returns = pd.DataFrame(
        {code: np.random.default_rng(index).normal(size=52) for index, code in enumerate(rows, 1)}
    )
    for module in (
        __import__(
            "backtest.fund_rotation.strategies.ai_rotation_r64_direct_corr_diversification.strategy",
            fromlist=["x"],
        ),
        __import__(
            "backtest.fund_rotation.strategies.ai_rotation_r66_lazy_correlation.strategy",
            fromlist=["x"],
        ),
    ):
        monkeypatch.setattr(module, "ensure_instrument_pool", lambda view, lookback_trade_days: pd.DataFrame())
        monkeypatch.setattr(module, "check_historical_eligibility", lambda pool, date: (list(rows), []))
        monkeypatch.setattr(module, "signal_date_eligible", lambda view, eligible, date: (list(eligible), []))
    monkeypatch.setattr(
        __import__(
            "backtest.fund_rotation.strategies.ai_rotation_r58_r39_signal_r57.strategy",
            fromlist=["AiRotationR58R39SignalR57Session"],
        ).AiRotationR58R39SignalR57Session,
        "_factor_rows",
        lambda self, view, date: {code: dict(row) for code, row in rows.items()},
    )
    monkeypatch.setattr(
        r66_module,
        "build_grouped_factor_rows",
        lambda session, view, date: {code: dict(row) for code, row in rows.items()},
    )
    view = SimpleNamespace(returns=lambda frequency, lookback: weekly_returns)
    context = StrategyDecisionContext(signal_date="20240105", data_view=view)

    old = AiRotationR64DirectCorrDiversificationStrategy().create_session(
        None, AiRotationR64DirectCorrDiversificationStrategy().config_model()
    ).evaluate(context)
    new = AiRotationR66LazyCorrelationStrategy().create_session(
        None, AiRotationR66LazyCorrelationStrategy().config_model()
    ).evaluate(context)

    assert new.target_weights == old.target_weights
    assert new.cash_weight == old.cash_weight
    assert new.reason_code == old.reason_code
    assert new.diagnostics["selected_codes"] == old.diagnostics["selected_codes"]
    assert new.diagnostics["correlation"] == old.diagnostics["correlation"]
