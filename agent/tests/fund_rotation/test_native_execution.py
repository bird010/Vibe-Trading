import math

import pandas as pd
import pytest

from backtest.fund_rotation.config import FundRotationConfig
from backtest.fund_rotation.execution_ledger_v2 import (
    OrderDirection,
    ParentOrderStatus,
)
from backtest.fund_rotation.market_rules import (
    FundInstrumentVersion,
    InMemoryPITMarketRuleSource,
    MarketRuleResolver,
    UnknownExecutionRule,
)
from backtest.fund_rotation.native_execution import (
    FundRotationExecutionEngine,
    NativeExecutionRequest,
    NativeExecutionState,
)
from backtest.fund_rotation.pit_universe import PITQueryMode


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


def _rule_record(
    code: str,
    *,
    lot_size: int = 100,
    tick_size: float = 0.001,
    source_record_id: str | None = None,
) -> dict:
    return {
        "ts_code": code,
        "instrument_type": "domestic_equity_etf",
        "valid_from": "20231201",
        "valid_to": None,
        "known_from": "20231215T000000",
        "snapshot_version": 7,
        "revision_id": f"{code}-r1",
        "revision_order": 1,
        "settlement": "T+1",
        "lot_size": lot_size,
        "tick_size": tick_size,
        "price_limit_pct": 0.10,
        "short_allowed": False,
        "currency": "CNY",
        "source_record_id": source_record_id or f"{code}-src",
        "source_id": "native-test-source",
        "rule_version": f"{code}-rules-v1",
    }


def _resolver_and_instruments(
    codes: tuple[str, ...] = ("A", "B"),
    *,
    lot_size_by_code: dict[str, int] | None = None,
    tick_size_by_code: dict[str, float] | None = None,
) -> tuple[MarketRuleResolver, dict[str, FundInstrumentVersion]]:
    lot_size_by_code = lot_size_by_code or {}
    tick_size_by_code = tick_size_by_code or {}
    resolver = MarketRuleResolver(
        InMemoryPITMarketRuleSource(
            [
                _rule_record(
                    code,
                    lot_size=lot_size_by_code.get(code, 100),
                    tick_size=tick_size_by_code.get(code, 0.001),
                )
                for code in codes
            ]
        )
    )
    instruments = {
        code: FundInstrumentVersion(code, "domestic_equity_etf", f"{code}-v1")
        for code in codes
    }
    return resolver, instruments


def _request(
    targets: dict[str, dict[str, float]],
    *,
    evaluation_dates: list[str] | None = None,
    market: pd.DataFrame | None = None,
    adj: pd.DataFrame | None = None,
    config: FundRotationConfig | None = None,
    initial_capital: float = 100_000.0,
    rule_resolver: MarketRuleResolver | None = None,
    instrument_versions: dict[str, FundInstrumentVersion] | None = None,
    initial_state: NativeExecutionState | None = None,
    decision_ids: dict[str, str] | None = None,
    order_ids: dict[str, dict[str, str]] | None = None,
) -> NativeExecutionRequest:
    dates = evaluation_dates or _dates()
    if market is None or adj is None:
        market, adj = _market(dates=dates)
    if rule_resolver is None or instrument_versions is None:
        codes = tuple(sorted(set(market["ts_code"].astype(str))))
        rule_resolver, instrument_versions = _resolver_and_instruments(codes)
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
        rule_resolver=rule_resolver,
        instrument_versions=instrument_versions,
        rule_mode=PITQueryMode.AS_WAS_KNOWN,
        initial_state=initial_state,
        decision_ids=decision_ids or {},
        order_ids=order_ids or {},
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


def test_native_engine_uses_caller_supplied_decision_and_order_identity():
    result = FundRotationExecutionEngine().execute(
        _request(
            {"20240101": {"A": 0.5}},
            decision_ids={"20240101": "DECISION-001"},
            order_ids={"DECISION-001": {"A": "ORDER-A-001"}},
        )
    )

    assert result.ledger.parent_orders[0].decision_id == "DECISION-001"
    assert result.ledger.parent_orders[0].order_id == "ORDER-A-001"
    assert result.ledger.attempts[0].attempt_id == "ORDER-A-001-A1"
    assert result.ledger.trades[0].trade_id == "ORDER-A-001-A1-T1"


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


def test_native_engine_continues_cash_positions_residual_parent_and_ids_across_calls():
    dates = _dates()
    market, adj = _market(codes=("A",), dates=dates, amount=2.0)
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

    first = FundRotationExecutionEngine().execute(
        _request(
            {"20240101": {"A": 1.0}},
            evaluation_dates=dates[:2],
            market=market,
            adj=adj,
            config=config,
            decision_ids={"20240101": "DECISION-A"},
            order_ids={"DECISION-A": {"A": "ORDER-A"}},
        )
    )
    second = FundRotationExecutionEngine().execute(
        _request(
            {},
            evaluation_dates=dates[2:4],
            market=market,
            adj=adj,
            config=config,
            initial_state=first.state,
        )
    )

    first_parent = first.ledger.parent_orders[0]
    second_parent = {parent.order_id: parent for parent in second.ledger.parent_orders}[
        "ORDER-A"
    ]
    assert first_parent.order_id == second_parent.order_id
    assert second_parent.cumulative_filled_quantity > first_parent.cumulative_filled_quantity
    assert second.ledger.attempts[-1].attempt_id == "ORDER-A-A4"
    assert second.ledger.trades[-1].trade_id == "ORDER-A-A4-T1"
    assert second.ending_positions["A"]["size"] > first.ending_positions["A"]["size"]
    assert second.state.cash == second.ending_cash


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


def test_native_engine_consumes_pit_rules_for_lot_tick_and_provenance():
    dates = _dates()
    market, adj = _market(codes=("A",), dates=dates, price=10.003)
    calls: list[dict] = []
    backing, instruments = _resolver_and_instruments(
        ("A",),
        lot_size_by_code={"A": 200},
        tick_size_by_code={"A": 0.005},
    )

    class SpyResolver:
        def resolve(self, **kwargs):
            calls.append(dict(kwargs))
            return backing.resolve(**kwargs)

    result = FundRotationExecutionEngine().execute(
        _request(
            {"20240101": {"A": 0.03}},
            evaluation_dates=dates,
            market=market,
            adj=adj,
            rule_resolver=SpyResolver(),
            instrument_versions=instruments,
        )
    )

    assert calls
    assert calls[0]["trade_date"] == "20240102"
    assert calls[0]["knowledge_cutoff"] == "20240101T150000"
    assert calls[0]["snapshot_version"] == 7
    assert calls[0]["mode"] is PITQueryMode.AS_WAS_KNOWN
    assert result.ledger.trades[0].quantity == 200
    assert result.ledger.trades[0].price == 10.005
    assert result.orders[0]["rule_version"] == "A-rules-v1"
    assert result.orders[0]["source_record_id"] == "A-src"


def test_native_engine_fails_closed_when_pit_rule_is_missing():
    dates = _dates()
    market, adj = _market(codes=("A",), dates=dates)
    resolver = MarketRuleResolver(InMemoryPITMarketRuleSource([]))
    instruments = {"A": FundInstrumentVersion("A", "domestic_equity_etf", "A-v1")}

    with pytest.raises(UnknownExecutionRule):
        FundRotationExecutionEngine().execute(
            _request(
                {"20240101": {"A": 1.0}},
                evaluation_dates=dates,
                market=market,
                adj=adj,
                rule_resolver=resolver,
                instrument_versions=instruments,
            )
        )


def test_native_engine_orders_include_legacy_audit_fields_and_ledger_identity():
    result = FundRotationExecutionEngine().execute(
        _request({"20240101": {"A": 0.5}})
    )
    order = result.orders[0]

    assert order["order_id"] == result.ledger.parent_orders[0].order_id
    assert order["attempt_id"] == result.ledger.attempts[0].attempt_id
    assert order["attempt_quantity_basis"] == 1.0
    assert order["cumulative_filled_at_attempt"] == result.ledger.attempts[0].filled_quantity
    assert order["corporate_action_adjustments"] == "[]"
    assert order["current_quantity_basis"] == 1.0


def test_native_engine_reports_execution_failure_cash_for_partial_fills():
    dates = _dates()
    market, adj = _market(codes=("A",), dates=dates, amount=2.0)
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
            evaluation_dates=dates[:1],
            market=market,
            adj=adj,
            config=config,
        )
    )

    assert result.positions_history[0]["execution_failure_cash"] > 0.0


def test_native_engine_fails_closed_on_initial_capital_mismatch():
    config = FundRotationConfig(k=1, top_n=1, initial_capital=99_999.0)

    with pytest.raises(ValueError, match="initial_capital"):
        FundRotationExecutionEngine().execute(
            _request(
                {"20240101": {"A": 1.0}},
                config=config,
                initial_capital=100_000.0,
            )
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
