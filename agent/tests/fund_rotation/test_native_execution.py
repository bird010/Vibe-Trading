import math

import pandas as pd

from backtest.fund_rotation.config import FundRotationConfig
from backtest.fund_rotation.execution_ledger_v2 import (
    OrderDirection,
    ParentOrderStatus,
)
from backtest.fund_rotation.native_execution import (
    FundRotationExecutionEngine,
    NativeExecutionRequest,
)


def _dates() -> list[str]:
    return pd.bdate_range("2024-01-02", "2024-01-12").strftime("%Y%m%d").tolist()


def _market(
    codes: tuple[str, ...] = ("A", "B"),
    dates: list[str] | None = None,
    *,
    price: float = 10.0,
    amount: float = 1_000_000.0,
    adj_change: tuple[str, str, float] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    all_dates = ["20231229", *(dates or _dates())]
    rows: list[dict] = []
    adj_rows: list[dict] = []
    for code in codes:
        factor = 1.0
        for trade_date in all_dates:
            rows.append(
                {
                    "ts_code": code,
                    "trade_date": trade_date,
                    "open": price,
                    "close": price,
                    "high": price + 0.1,
                    "low": price - 0.1,
                    "pre_close": price,
                    "vol": 1_000_000,
                    "amount": amount,
                }
            )
            if adj_change and code == adj_change[0] and trade_date >= adj_change[1]:
                factor = adj_change[2]
            adj_rows.append(
                {"ts_code": code, "trade_date": trade_date, "adj_factor": factor}
            )
    return pd.DataFrame(rows), pd.DataFrame(adj_rows)


def _request(
    targets: dict[str, dict[str, float]],
    *,
    evaluation_dates: list[str] | None = None,
    market: pd.DataFrame | None = None,
    adj: pd.DataFrame | None = None,
    config: FundRotationConfig | None = None,
    initial_capital: float = 100_000.0,
) -> NativeExecutionRequest:
    dates = evaluation_dates or _dates()
    if market is None or adj is None:
        market, adj = _market(dates=dates)
    return NativeExecutionRequest(
        targets=targets,
        evaluation_dates=tuple(dates),
        fund_daily=market,
        fund_adj=adj,
        execution=config
        or FundRotationConfig(
            k=1,
            top_n=1,
            initial_capital=initial_capital,
            commission_rate=0.0,
            commission_min=0.0,
            other_fee_rate=0.0,
            adv_min_observations=1,
            max_participation_rate=1.0,
            base_slippage_bps=0.0,
            max_slippage_bps=0.0,
        ),
        initial_capital=initial_capital,
        knowledge_cutoff="20240101T150000",
        snapshot_version=7,
        run_id="native-test-run",
    )


def test_native_engine_creates_direct_parent_attempt_trade_reference_chain():
    result = FundRotationExecutionEngine().execute(
        _request({"20240101": {"A": 0.5, "B": 0.5}})
    )

    parent_ids = {parent.order_id for parent in result.ledger.parent_orders}
    attempt_ids = {attempt.attempt_id for attempt in result.ledger.attempts}

    assert len(parent_ids) == 2
    assert result.ledger.trades
    assert all(attempt.order_id in parent_ids for attempt in result.ledger.attempts)
    assert all(trade.order_id in parent_ids for trade in result.ledger.trades)
    assert all(trade.attempt_id in attempt_ids for trade in result.ledger.trades)
    assert {
        trade.direction for trade in result.ledger.trades
    } == {OrderDirection.BUY}


def test_native_engine_does_not_call_legacy_loop_or_pipeline_ledger_adapter(monkeypatch):
    from backtest.fund_rotation import execution, execution_ledger_v2, native_execution

    def forbidden(*args, **kwargs):  # pragma: no cover - assertion is the call itself
        raise AssertionError("legacy execution path must not be called")

    monkeypatch.setattr(execution, "run_execution_loop", forbidden)
    monkeypatch.setattr(
        execution_ledger_v2, "build_execution_ledger_from_pipeline_result", forbidden
    )
    monkeypatch.setattr(native_execution, "run_execution_loop", forbidden, raising=False)
    monkeypatch.setattr(
        native_execution,
        "build_execution_ledger_from_pipeline_result",
        forbidden,
        raising=False,
    )

    result = FundRotationExecutionEngine().execute(_request({"20240101": {"A": 1.0}}))

    assert result.ledger.parent_orders
    assert result.ledger.trades


def test_native_engine_sells_before_buys_and_carries_residual_attempts():
    dates = _dates()
    market, adj = _market(dates=dates, amount=2.0)
    config = FundRotationConfig(
        k=1,
        top_n=1,
        initial_capital=100_000.0,
        commission_rate=0.0,
        commission_min=0.0,
        other_fee_rate=0.0,
        adv_min_observations=1,
        max_participation_rate=0.5,
        base_slippage_bps=0.0,
        max_slippage_bps=0.0,
    )

    result = FundRotationExecutionEngine().execute(
        _request(
            {
                "20240101": {"A": 1.0},
                "20240104": {"B": 1.0},
            },
            evaluation_dates=dates,
            market=market,
            adj=adj,
            config=config,
        )
    )

    a_buy_attempts = [
        attempt
        for attempt in result.ledger.attempts
        if attempt.order_id.endswith("-A") and attempt.trade_date < "20240105"
    ]
    assert len(a_buy_attempts) >= 2
    assert all(attempt.filled_quantity == 100 for attempt in a_buy_attempts[:2])

    rotation_events = [
        event for event in result.trade_events if event.get("trade_date") == "20240105"
    ]
    sell_indexes = [
        index for index, event in enumerate(rotation_events) if event.get("action") == "SELL"
    ]
    buy_indexes = [
        index for index, event in enumerate(rotation_events) if event.get("action") == "BUY"
    ]
    assert sell_indexes and buy_indexes
    assert max(sell_indexes) < min(buy_indexes)


def test_native_engine_records_corporate_action_replacement_lineage():
    dates = _dates()
    market, adj = _market(
        codes=("A",),
        dates=dates,
        amount=2.0,
        adj_change=("A", "20240103", 2.0),
    )
    config = FundRotationConfig(
        k=1,
        top_n=1,
        initial_capital=100_000.0,
        commission_rate=0.0,
        commission_min=0.0,
        other_fee_rate=0.0,
        adv_min_observations=1,
        max_participation_rate=0.5,
        base_slippage_bps=0.0,
        max_slippage_bps=0.0,
    )

    result = FundRotationExecutionEngine().execute(
        _request(
            {"20240101": {"A": 1.0}},
            evaluation_dates=dates,
            market=market,
            adj=adj,
            config=config,
        )
    )

    assert len(result.ledger.corporate_actions) == 1
    action = result.ledger.corporate_actions[0]
    replacements = [
        parent
        for parent in result.ledger.parent_orders
        if parent.replacement_of_order_id
    ]
    assert len(replacements) == 1
    replacement = replacements[0]
    old_parent = {
        parent.order_id: parent for parent in result.ledger.parent_orders
    }[replacement.replacement_of_order_id]
    assert old_parent.status is ParentOrderStatus.CANCELED
    assert old_parent.cancel_reason == "CORPORATE_ACTION_REPLACED"
    assert replacement.corporate_action_id == action.corporate_action_id
    assert replacement.replacement_chain_id == old_parent.order_id
    assert replacement.original_requested_quantity == old_parent.remaining_quantity * 2


def test_native_engine_preserves_cash_nav_and_exact_evaluation_calendar():
    dates = _dates()
    result = FundRotationExecutionEngine().execute(
        _request({"20240101": {"A": 0.5, "B": 0.25}}, evaluation_dates=dates)
    )

    assert list(result.executed_equity.index) == dates
    last_equity = float(result.executed_equity.iloc[-1]) * 100_000.0
    position_value = sum(
        pos["size"] * 10.0 for pos in result.ending_positions.values()
    )
    assert math.isclose(
        last_equity,
        result.ending_cash + position_value,
        rel_tol=1e-9,
        abs_tol=1e-7,
    )


def test_native_engine_handles_empty_targets_and_cancellation_checkpoint():
    dates = _dates()
    empty = FundRotationExecutionEngine().execute(_request({}, evaluation_dates=dates))

    assert empty.ledger.parent_orders == ()
    assert empty.ledger.attempts == ()
    assert empty.ledger.trades == ()
    assert list(empty.executed_equity.index) == dates
    assert empty.executed_equity.tolist() == [1.0] * len(dates)
    assert empty.ending_cash == 100_000.0

    calls = {"count": 0}

    def should_cancel() -> bool:
        calls["count"] += 1
        return calls["count"] > 1

    canceled = FundRotationExecutionEngine().execute(
        _request({"20240101": {"A": 1.0}}, evaluation_dates=dates),
        should_cancel=should_cancel,
    )

    assert list(canceled.executed_equity.index) == [dates[0]]
    assert canceled.ledger.parent_orders
