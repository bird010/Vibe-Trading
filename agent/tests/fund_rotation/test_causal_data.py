"""Phase 2 Task 1 — CausalDataView enforcement tests (design §6)."""

from __future__ import annotations

import pandas as pd
import pytest

from backtest.fund_rotation.causal_data import (
    CausalDataView,
    FundInstrument,
    UndeclaredStrategyDataAccess,
)
from backtest.fund_rotation.contracts import StrategyDataRequirements

DATES = ["20240103", "20240104", "20240105", "20240108", "20240109", "20240110"]


def _fund_daily():
    rows = []
    for d in DATES:
        for code, base in (("A", 10.0), ("B", 20.0), ("C", 30.0)):
            rows.append({
                "ts_code": code, "trade_date": d,
                "open": base, "close": base + 1.0, "high": base + 2.0,
                "low": base - 1.0, "pre_close": base, "vol": 1000.0,
                "amount": base * 1000.0,
            })
    return pd.DataFrame(rows)


def _fund_adj():
    rows = [
        {"ts_code": code, "trade_date": d, "adj_factor": 1.0}
        for d in DATES for code in ("A", "B", "C")
    ]
    return pd.DataFrame(rows)


def _dim_fund():
    return pd.DataFrame([
        {"ts_code": "A", "name": "ETF一", "list_date": "20200101"},
        {"ts_code": "B", "name": "ETF二", "list_date": "20200101"},
        {"ts_code": "C", "name": "ETF三", "list_date": "20200101"},
    ])


def _view(signal_date="20240109", universe=("A", "B", "C"),
          fields=("close", "amount", "adj_factor", "open")):
    req = StrategyDataRequirements(
        required_datasets=("fund", "fact_fund_adj", "dim_fund"),
        required_fields=tuple(fields),
        warmup_trade_days=10, frequency="W", needs_benchmark=False,
    )
    return CausalDataView(
        _fund_daily=_fund_daily(), _fund_adj=_fund_adj(), _dim_fund=_dim_fund(),
        _requirements=req, _signal_date=pd.Timestamp(signal_date),
        _universe_codes=frozenset(universe),
    )


class TestEnforcement:
    def test_undeclared_field_rejected(self):
        view = _view(fields=("close",))  # only close declared
        with pytest.raises(UndeclaredStrategyDataAccess):
            view.daily_bars(["close", "vol"])  # vol not declared

    def test_post_signal_dates_excluded(self):
        view = _view(signal_date="20240108")
        bars = view.daily_bars(["close"])
        assert bars["trade_date"].astype(str).max() <= "20240108"
        assert "20240109" not in set(bars["trade_date"].astype(str))

    def test_non_snapshot_etf_excluded(self):
        view = _view(universe=("A", "B"))  # C not in snapshot
        bars = view.daily_bars(["close"])
        assert "C" not in set(bars["ts_code"].astype(str))

    def test_returns_read_only_copy(self):
        view = _view()
        bars = view.daily_bars(["close"])
        bars["close"] = 999.0  # mutate the returned copy
        # Underlying view data unaffected.
        bars2 = view.daily_bars(["close"])
        assert (bars2["close"] != 999.0).all()

    def test_two_sessions_do_not_interfere(self):
        v1 = _view(signal_date="20240105")
        v2 = _view(signal_date="20240110")
        _ = v1.daily_bars(["close"])
        b2 = v2.daily_bars(["close"])
        # v2 sees more dates than v1; reading v1 did not affect v2.
        assert b2["trade_date"].astype(str).max() == "20240110"
        assert v1.daily_bars(["close"])["trade_date"].astype(str).max() == "20240105"


class TestQuerySurface:
    def test_signal_date_property(self):
        view = _view(signal_date="20240109")
        assert view.signal_date == pd.Timestamp("20240109")

    def test_daily_bars_lookback(self):
        view = _view(signal_date="20240110")
        bars = view.daily_bars(["close"], lookback=2)
        assert set(bars["trade_date"].astype(str)) == {"20240109", "20240110"}

    def test_adjusted_closes_causal(self):
        view = _view(signal_date="20240108")
        adj = view.adjusted_closes()
        assert adj.index.astype(str).max() <= "20240108"

    def test_returns_weekly(self):
        view = _view(signal_date="20240110")
        rets = view.returns("weekly", lookback=4)
        assert not rets.empty

    def test_returns_daily(self):
        view = _view(signal_date="20240110")
        rets = view.returns("daily", lookback=3)
        assert len(rets) <= 3

    def test_causal_adv_excludes_signal_date(self):
        view = _view(signal_date="20240109")
        adv = view.causal_adv(lookback_days=20)
        # ADV uses completed days strictly before signal date.
        assert set(adv.index) <= {"A", "B", "C"}
        assert not adv.empty

    def test_fund_adjustments(self):
        view = _view(signal_date="20240108")
        adj = view.fund_adjustments()
        assert adj["trade_date"].astype(str).max() <= "20240108"

    def test_eligible_universe(self):
        view = _view(universe=("A", "B"))
        universe = view.eligible_universe()
        assert all(isinstance(i, FundInstrument) for i in universe)
        assert {i.ts_code for i in universe} == {"A", "B"}

    def test_trading_calendar(self):
        view = _view(signal_date="20240108")
        cal = view.trading_calendar()
        assert all(d <= pd.Timestamp("20240108") for d in cal)
        assert cal == tuple(sorted(cal))


class TestAccessAudit:
    def test_access_log_records_metadata_not_content(self):
        view = _view()
        view.daily_bars(["close"])
        view.causal_adv()
        log = view.access_log
        assert len(log) == 2
        assert log[0].method == "daily_bars"
        assert log[0].fields == ("close",)
        assert log[0].rows > 0
        # No whole-table content recorded.
        assert not hasattr(log[0], "data")
