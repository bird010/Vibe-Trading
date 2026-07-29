"""Tests for execution policy: T+1, limits, capacity, multi-day exit."""

from __future__ import annotations

import pandas as pd

from backtest.stockpred.execution.costs import DEFAULT_COST_POLICY
from backtest.stockpred.execution.policy import (
    ExecutionPolicy,
    MarketView,
    PositionInfo,
)


def _market(
    days: int = 30,
    open_price: float = 10.0,
    amount_cny: float = 5_000_000.0,
    up_limit: float | None = None,
    down_limit: float | None = None,
) -> pd.DataFrame:
    """Build a simple market DataFrame for one stock."""
    dates = [f"202501{d:02d}" for d in range(1, days + 1)]
    n = len(dates)
    up = up_limit if up_limit else open_price * 1.1
    down = down_limit if down_limit else open_price * 0.9
    return pd.DataFrame(
        {
            "ts_code": ["000001.SZ"] * n,
            "trade_date": dates,
            "open": [open_price] * n,
            "high": [open_price * 1.05] * n,
            "low": [open_price * 0.95] * n,
            "close": [open_price] * n,
            "vol": [100000.0] * n,
            "amount": [amount_cny / 1000.0] * n,  # 千元
            "up_limit": [up] * n,
            "down_limit": [down] * n,
            "adj_open": [open_price] * n,
            "adj_close": [open_price] * n,
        }
    )


def _policy() -> ExecutionPolicy:
    return ExecutionPolicy(
        cost_policy=DEFAULT_COST_POLICY,
        max_participation=0.05,
        adv_lookback_days=20,
        max_exit_extension_days=20,
        lot_size=100,
    )


class TestEntry:
    def test_execution_event_has_nonempty_cohort_and_requested_value(self):
        mkt = _market(30)
        event = _policy().execute_entry(
            code="000001.SZ", signal_date="20250110", cash_budget=20_000.0,
            target_value=10_000.0, cohort_id="cohort_1", market_view=MarketView(market=mkt, trade_dates=sorted(mkt["trade_date"].unique())),
        )

        assert event.cohort_id == "cohort_1"
        assert event.requested_value == 10_000.0

    def test_execution_uses_raw_open_when_adj_open_exists(self):
        mkt = _market(30, open_price=10.0, up_limit=11.0)
        mkt["adj_open"] = 5.0
        view = MarketView(market=mkt, trade_dates=sorted(mkt["trade_date"].unique()))

        event = _policy().execute_entry(
            code="000001.SZ",
            signal_date="20250110",
            cash_budget=20_000.0,
            target_value=10_000.0,
            market_view=view,
        )

        assert event.status == "FILLED"
        assert event.price == 10.0
        assert event.executed_quantity == 1_000
        assert event.executed_value == 10_000.0

    def test_t_plus_1_entry_at_open(self):
        mkt = _market(30)
        view = MarketView(market=mkt, trade_dates=sorted(mkt["trade_date"].unique()))
        policy = _policy()

        event = policy.execute_entry(
            code="000001.SZ",
            signal_date="20250110",
            cash_budget=200_000.0,
            target_value=200_000.0,
            market_view=view,
        )

        assert event is not None
        assert event.trade_date == "20250111"  # T+1
        assert event.price == 10.0  # open price
        assert event.side == "BUY"
        assert event.executed_quantity > 0
        assert event.executed_quantity % 100 == 0

    def test_limit_up_blocks_entry(self):
        mkt = _market(30, open_price=11.0, up_limit=11.0)  # open == up_limit
        view = MarketView(market=mkt, trade_dates=sorted(mkt["trade_date"].unique()))
        policy = _policy()

        event = policy.execute_entry(
            code="000001.SZ",
            signal_date="20250110",
            cash_budget=200_000.0,
            target_value=200_000.0,
            market_view=view,
        )

        assert event is not None
        assert event.status == "REJECTED"
        assert event.reason_code == "limit_up"
        assert event.executed_quantity == 0
        assert event.price == 11.0
        assert event.requested_quantity == int(200_000.0 / 11.0)
        assert event.remaining_quantity == event.requested_quantity
        assert event.requested_quantity_known is True

    def test_suspension_blocks_entry(self):
        mkt = _market(30)
        mkt.loc[mkt["trade_date"] == "20250111", "vol"] = 0.0
        view = MarketView(market=mkt, trade_dates=sorted(mkt["trade_date"].unique()))
        policy = _policy()

        event = policy.execute_entry(
            code="000001.SZ",
            signal_date="20250110",
            cash_budget=200_000.0,
            target_value=200_000.0,
            market_view=view,
        )

        assert event is not None
        assert event.status == "REJECTED"
        assert event.reason_code == "suspended"
        assert event.price == 10.0
        assert event.requested_quantity == 20_000
        assert event.remaining_quantity == 20_000
        assert event.requested_quantity_known is True

    def test_no_market_data_rejects(self):
        mkt = _market(30)
        view = MarketView(market=mkt, trade_dates=sorted(mkt["trade_date"].unique()))
        policy = _policy()

        event = policy.execute_entry(
            code="UNKNOWN.SZ",
            signal_date="20250110",
            cash_budget=200_000.0,
            target_value=200_000.0,
            market_view=view,
        )

        assert event is not None
        assert event.status == "REJECTED"
        assert event.reason_code == "no_market_data"
        assert event.price == 0.0
        assert event.requested_quantity == 0
        assert event.remaining_quantity == 0
        assert event.requested_quantity_known is False

    def test_capacity_uses_causal_adv_not_same_day(self):
        # Signal on day 10, entry on day 11
        # ADV should be computed as of day 10 (signal date), not day 11
        mkt = _market(30, amount_cny=5_000_000.0)
        # Make day 11 have very low amount - should NOT affect capacity
        mkt.loc[mkt["trade_date"] == "20250111", "amount"] = 1.0  # 1千元 = 1000 CNY
        view = MarketView(market=mkt, trade_dates=sorted(mkt["trade_date"].unique()))
        policy = _policy()

        event = policy.execute_entry(
            code="000001.SZ",
            signal_date="20250110",
            cash_budget=250_000.0,  # extra to cover fees
            target_value=200_000.0,
            market_view=view,
        )

        # Should still get reasonable fill based on ADV20 as of day 10
        assert event is not None
        assert event.executed_quantity > 0
        # Capacity = 5M * 0.05 = 250K, so 200K target should fill
        assert event.status == "FILLED"

    def test_partial_fill_when_capacity_limited(self):
        # Low ADV -> limited capacity
        mkt = _market(30, amount_cny=1_000_000.0)  # ADV = 1M
        view = MarketView(market=mkt, trade_dates=sorted(mkt["trade_date"].unique()))
        policy = _policy()

        # capacity = 1M * 0.05 = 50K; target = 200K -> partial
        event = policy.execute_entry(
            code="000001.SZ",
            signal_date="20250110",
            cash_budget=200_000.0,
            target_value=200_000.0,
            market_view=view,
        )

        assert event is not None
        assert event.status == "PARTIAL"
        assert event.executed_quantity > 0
        assert event.executed_quantity * 10.0 <= 50_000.0
        assert event.requested_quantity == event.executed_quantity + event.remaining_quantity
        assert event.remaining_quantity > 0


class TestExit:
    def test_exit_event_retains_cohort_and_requested_value(self):
        mkt = _market(30)
        position = PositionInfo(
            code="000001.SZ", quantity=1_000, entry_date="20250111",
            target_exit_date="20250116", cohort_id="cohort_1",
        )

        event = _policy().execute_exit(
            position, market_view=MarketView(market=mkt, trade_dates=sorted(mkt["trade_date"].unique())),
        )[0]

        assert event.cohort_id == "cohort_1"
        assert event.requested_value > 0

    def test_exit_uses_raw_open_when_adj_open_exists(self):
        mkt = _market(30, open_price=10.0, down_limit=9.0)
        mkt["adj_open"] = 5.0
        view = MarketView(market=mkt, trade_dates=sorted(mkt["trade_date"].unique()))

        events = _policy().execute_exit(
            PositionInfo(
                code="000001.SZ",
                quantity=1_000,
                entry_date="20250111",
                target_exit_date="20250116",
            ),
            market_view=view,
        )

        assert len(events) == 1
        assert events[0].price == 10.0
        assert events[0].executed_quantity == 1_000
        assert events[0].total_fees == DEFAULT_COST_POLICY.estimate_sell_fees(
            1_000, 10.0, 5_000_000.0
        ).total

    def test_exit_at_target_date(self):
        mkt = _market(30)
        view = MarketView(market=mkt, trade_dates=sorted(mkt["trade_date"].unique()))
        policy = _policy()

        position = PositionInfo(
            code="000001.SZ",
            quantity=1000,
            entry_date="20250111",
            target_exit_date="20250116",  # 5 days later
        )

        events = policy.execute_exit(position, market_view=view)

        assert len(events) >= 1
        total_sold = sum(e.executed_quantity for e in events)
        assert total_sold == 1000
        assert events[0].trade_date == "20250116"
        assert all(e.side == "SELL" for e in events)

    def test_exit_delayed_by_limit_down(self):
        mkt = _market(30, open_price=10.0, down_limit=9.0)
        # Make target exit day limit-down (open hits down_limit)
        mkt.loc[mkt["trade_date"] == "20250116", "open"] = 9.0
        mkt.loc[mkt["trade_date"] == "20250116", "adj_open"] = 9.0
        mkt.loc[mkt["trade_date"] == "20250116", "down_limit"] = 9.0
        # Next day is normal
        mkt.loc[mkt["trade_date"] == "20250117", "open"] = 10.0
        mkt.loc[mkt["trade_date"] == "20250117", "adj_open"] = 10.0
        mkt.loc[mkt["trade_date"] == "20250117", "down_limit"] = 9.0
        view = MarketView(market=mkt, trade_dates=sorted(mkt["trade_date"].unique()))
        policy = _policy()

        position = PositionInfo(
            code="000001.SZ",
            quantity=1000,
            entry_date="20250111",
            target_exit_date="20250116",
        )

        events = policy.execute_exit(position, market_view=view)

        # Target-day failure is retained, then execution is delayed to 20250117.
        assert len(events) >= 1
        assert events[0].status == "REJECTED"
        assert events[0].trade_date == "20250116"
        assert next(event for event in events if event.executed_quantity > 0).trade_date == "20250117"

    def test_limit_down_exit_attempt_is_audited_as_rejected_before_retry(self):
        mkt = _market(30, open_price=10.0, down_limit=9.0)
        mkt.loc[mkt["trade_date"] == "20250116", "open"] = 9.0
        mkt.loc[mkt["trade_date"] == "20250116", "down_limit"] = 9.0
        view = MarketView(market=mkt, trade_dates=sorted(mkt["trade_date"].unique()))

        events = _policy().execute_exit(
            PositionInfo(code="000001.SZ", quantity=1_000, entry_date="20250111", target_exit_date="20250116"),
            market_view=view,
        )

        assert events[0].status == "REJECTED"
        assert events[0].reason_code == "limit_down"
        assert events[0].requested_quantity == 1_000
        assert events[0].executed_quantity == 0
        assert events[0].remaining_quantity == 1_000
        assert sum(event.executed_quantity for event in events) == 1_000

    def test_multi_day_exit_when_capacity_insufficient(self):
        # Very low ADV -> need multiple days to exit
        mkt = _market(30, amount_cny=100_000.0)  # ADV=100K, capacity=5K/day
        view = MarketView(market=mkt, trade_dates=sorted(mkt["trade_date"].unique()))
        policy = _policy()

        # 1000 shares * 10 = 10K value; capacity = 5K/day -> need 2+ days
        position = PositionInfo(
            code="000001.SZ",
            quantity=1000,
            entry_date="20250111",
            target_exit_date="20250116",
        )

        events = policy.execute_exit(position, market_view=view)

        assert len(events) >= 2  # multiple days
        total_sold = sum(e.executed_quantity for e in events)
        assert total_sold == 1000
        assert all(
            event.requested_quantity == event.executed_quantity + event.remaining_quantity
            for event in events
        )

    def test_max_extension_days_terminates_exit(self):
        # All days are limit-down -> can never sell
        mkt = _market(30, open_price=9.0, down_limit=9.0)
        view = MarketView(market=mkt, trade_dates=sorted(mkt["trade_date"].unique()))
        policy = ExecutionPolicy(
            cost_policy=DEFAULT_COST_POLICY,
            max_participation=0.05,
            adv_lookback_days=20,
            max_exit_extension_days=5,  # short limit for test
            lot_size=100,
        )

        position = PositionInfo(
            code="000001.SZ",
            quantity=1000,
            entry_date="20250111",
            target_exit_date="20250116",
        )

        events = policy.execute_exit(position, market_view=view)

        # Should terminate after max_exit_extension_days with remaining
        total_sold = sum(e.executed_quantity for e in events)
        assert total_sold < 1000  # could not fully exit

    def test_odd_lot_allowed_on_final_exit(self):
        mkt = _market(30)
        view = MarketView(market=mkt, trade_dates=sorted(mkt["trade_date"].unique()))
        policy = _policy()

        # 150 shares - not a round lot
        position = PositionInfo(
            code="000001.SZ",
            quantity=150,
            entry_date="20250111",
            target_exit_date="20250116",
        )

        events = policy.execute_exit(position, market_view=view)

        total_sold = sum(e.executed_quantity for e in events)
        assert total_sold == 150  # all sold including odd lot
