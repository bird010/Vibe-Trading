import math
from dataclasses import replace

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
    PITInvalidMarketRule,
    UnknownExecutionRule,
)
from backtest.fund_rotation.etf_rules import ChinaETFExecutionRules
from backtest.fund_rotation.executor import PortfolioExecutor
from backtest.fund_rotation.orders import OrderManager
from backtest.fund_rotation.native_execution import (
    FundRotationExecutionEngine,
    NativeExecutionRequest,
    NativeExecutionState,
    _ParentState,
    _apply_corporate_actions,
    _rules_from_market_rules,
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


def test_native_execution_state_snapshot_round_trips_for_shadow_recovery() -> None:
    state = NativeExecutionState(
        cash=123.45,
        positions={"A": {"size": 100, "entry_date": "20240102"}},
        active_orders=({"ts_code": "A", "remaining": 10},),
        corporate_actions=({"corporate_action_id": "CA-1", "ts_code": "A"},),
        event_counter=7,
    )

    restored = NativeExecutionState.from_snapshot(state.to_snapshot())

    assert restored == state


def _rule_record(
    code: str,
    *,
    lot_size: int = 100,
    tick_size: float = 0.001,
    short_allowed: bool = False,
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
        "short_allowed": short_allowed,
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
    short_allowed_by_code: dict[str, bool] | None = None,
) -> tuple[MarketRuleResolver, dict[str, FundInstrumentVersion]]:
    lot_size_by_code = lot_size_by_code or {}
    tick_size_by_code = tick_size_by_code or {}
    short_allowed_by_code = short_allowed_by_code or {}
    resolver = MarketRuleResolver(
        InMemoryPITMarketRuleSource(
            [
                _rule_record(
                    code,
                    lot_size=lot_size_by_code.get(code, 100),
                    tick_size=tick_size_by_code.get(code, 0.001),
                    short_allowed=short_allowed_by_code.get(code, False),
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
    knowledge_cutoffs: dict[str, str] | None = None,
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
        knowledge_cutoffs=(
            {date: "20240101T150000" for date in dates}
            if knowledge_cutoffs is None
            else knowledge_cutoffs
        ),
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
    assert all(event["signal_event_id"] for event in rotation_events)
    assert all(event["signal_week"] for event in rotation_events)


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


def test_native_engine_empty_targets_continue_existing_holdings_without_active_orders():
    dates = _dates()
    market, adj = _market(codes=("A",), dates=dates)
    first = FundRotationExecutionEngine().execute(
        _request(
            {"20240101": {"A": 0.5}},
            evaluation_dates=dates[:1],
            market=market,
            adj=adj,
            decision_ids={"20240101": "DECISION-HOLD"},
            order_ids={"DECISION-HOLD": {"A": "ORDER-HOLD-A"}},
        )
    )
    assert first.state.active_orders == ()
    assert first.ending_positions["A"]["size"] > 0

    second_market = market.copy()
    second_market.loc[
        second_market["trade_date"].isin(dates[1:3]),
        ["open", "close", "high", "low", "pre_close"],
    ] = [12.0, 12.0, 12.1, 11.9, 10.0]
    second = FundRotationExecutionEngine().execute(
        _request(
            {},
            evaluation_dates=dates[1:3],
            market=second_market,
            adj=adj,
            initial_state=first.state,
        )
    )

    assert second.ledger.parent_orders[0].order_id == "ORDER-HOLD-A"
    assert second.ledger.trades == first.ledger.trades
    assert second.ending_positions == first.ending_positions
    assert second.state.positions == first.state.positions
    assert list(second.executed_equity.index) == dates[1:3]
    expected_equity = first.ending_cash + first.ending_positions["A"]["size"] * 12.0
    assert math.isclose(
        float(second.executed_equity.iloc[-1]) * 100_000.0,
        expected_equity,
        rel_tol=1e-9,
        abs_tol=1e-7,
    )


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

    event = next(
        event for event in result.trade_events
        if event.get("event_type") == "CORPORATE_ACTION"
    )
    assert event["price"] == 0.0
    assert event["commission"] == 0.0
    assert event["adv20"] == 0.0
    assert event["post_holding"] == action.new_quantity
    assert event["remaining"] == 0
    assert event["last_valid_close_date"] == "20240102"
    assert event["valuation_anchor_date"] == "20240102"
    assert event["valuation_anchor_source"] == "close"


def test_native_engine_records_completed_parent_corporate_action_lineage():
    dates = _dates()
    market, adj = _market(
        codes=("A",),
        dates=dates,
        amount=2_000_000.0,
        adj_change=("A", "20240103", 2.0),
    )

    result = FundRotationExecutionEngine().execute(
        _request(
            {"20240101": {"A": 0.5}},
            evaluation_dates=dates,
            market=market,
            adj=adj,
        )
    )

    parents = [parent for parent in result.ledger.parent_orders if parent.ts_code == "A"]
    assert len(parents) == 1
    parent = parents[0]
    assert parent.replacement_of_order_id == ""
    assert parent.replacement_chain_id == ""
    assert parent.original_requested_quantity == 5_000
    assert parent.cumulative_filled_quantity == 5_000
    assert parent.remaining_quantity == 0
    assert parent.quantity_basis_id == "A:shares:1"

    order = next(row for row in result.orders if row["order_id"] == parent.order_id)
    assert order["requested"] == 5_000
    assert order["filled"] == 5_000
    assert order["current_quantity_basis"] == 1.0
    assert order["corporate_action_adjustments"] == "[]"
    assert result.ending_positions["A"]["size"] == 10_000


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


def test_native_engine_lot_rounds_buy_parent_before_capacity_residual() -> None:
    dates = _dates()
    market, adj = _market(codes=("A",), dates=dates, amount=1_000_000.0)
    result = FundRotationExecutionEngine().execute(
        _request(
            {"20240101": {"A": 0.031}},
            evaluation_dates=dates[:3],
            market=market,
            adj=adj,
        )
    )

    parent = result.ledger.parent_orders[0]
    assert parent.original_requested_quantity == 300
    assert parent.original_requested_quantity % 100 == 0
    assert parent.status is ParentOrderStatus.FILLED
    assert parent.remaining_quantity == 0
    assert [attempt.filled_quantity for attempt in result.ledger.attempts] == [300]


def test_native_engine_preserves_existing_odd_lot_when_target_is_unchanged() -> None:
    dates = _dates()
    market, adj = _market(codes=("A",), dates=dates, amount=1_000_000.0)
    result = FundRotationExecutionEngine().execute(
        _request(
            {"20240101": {"A": 0.0090001}},
            evaluation_dates=dates[:1],
            market=market,
            adj=adj,
            initial_state=NativeExecutionState(
                cash=99_100.0,
                positions={"A": {"size": 90}},
            ),
        )
    )

    assert result.ledger.parent_orders == ()
    assert result.ending_positions["A"]["size"] == 90


def test_native_engine_rounds_buy_delta_without_rounding_away_odd_lot_holdings() -> None:
    dates = _dates()
    market, adj = _market(codes=("A",), dates=dates, amount=1_000_000.0)
    result = FundRotationExecutionEngine().execute(
        _request(
            {"20240101": {"A": 0.015}},
            evaluation_dates=dates[:1],
            market=market,
            adj=adj,
            initial_state=NativeExecutionState(
                cash=99_500.0,
                positions={"A": {"size": 50}},
            ),
        )
    )

    assert len(result.ledger.parent_orders) == 1
    assert result.ledger.parent_orders[0].original_requested_quantity == 100
    assert result.ending_positions["A"]["size"] == 150


def test_native_engine_uses_trade_date_specific_rule_knowledge_cutoffs():
    dates = _dates()
    market, adj = _market(codes=("A",), dates=dates, amount=2.0)
    resolver, instruments = _resolver_and_instruments(("A",))
    calls: list[dict] = []

    class SpyResolver:
        def resolve(self, **kwargs):
            calls.append(dict(kwargs))
            return resolver.resolve(**kwargs)

    request = replace(
        _request(
            {"20240101": {"A": 0.03}},
            evaluation_dates=dates,
            market=market,
            adj=adj,
            config=FundRotationConfig(
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
            ),
            rule_resolver=SpyResolver(),
            instrument_versions=instruments,
        ),
        knowledge_cutoffs={
            date: f"{date}T15:00:00"
            for date in dates
        },
    )

    FundRotationExecutionEngine().execute(request)

    assert calls
    assert len({call["knowledge_cutoff"] for call in calls}) >= 2
    assert calls[0]["knowledge_cutoff"] == f"{dates[0]}T15:00:00"
    assert request.knowledge_cutoffs[dates[0]] == f"{dates[0]}T15:00:00"


def test_native_engine_fails_fast_when_pit_trade_date_cutoff_is_missing():
    dates = _dates()
    market, adj = _market(codes=("A",), dates=dates)
    resolver, instruments = _resolver_and_instruments(("A",))

    with pytest.raises(PITInvalidMarketRule, match="knowledge cutoff"):
        FundRotationExecutionEngine().execute(
            _request(
                {"20240101": {"A": 0.5}},
                evaluation_dates=dates,
                market=market,
                adj=adj,
                rule_resolver=resolver,
                instrument_versions=instruments,
                knowledge_cutoffs={},
            )
        )


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


def test_native_rules_honor_explicit_no_price_limit_and_settlement():
    from backtest.fund_rotation.market_rules import MarketRules

    rules = _rules_from_market_rules(
        MarketRules(
            instrument_type="bond_etf",
            settlement="T+0",
            lot_size=100,
            tick_size=0.001,
            price_limit_pct=None,
            price_limit_rule="NONE",
            short_allowed=False,
            currency="CNY",
            rule_version="bond-rules-v1",
        ),
        FundRotationConfig(k=1, top_n=1),
    )

    limit_bar = {
        "open": 10.0,
        "close": 11.0,
        "high": 11.0,
        "low": 11.0,
        "pre_close": 10.0,
        "vol": 1_000_000,
    }
    assert rules.can_buy(limit_bar)
    assert rules.can_sell(limit_bar)
    assert rules.can_sell_today("20240106", "20240106")


def test_native_corporate_action_replaces_pending_buy_without_position():
    order_mgr = OrderManager()
    order_mgr.create_orders({"A": 1_000}, event_id="D-1")
    parent = _ParentState(
        order_id="P-1",
        decision_id="D-1",
        signal_week="20240101",
        ts_code="A",
        direction="BUY",
        created_date="20240102",
        original_requested_quantity=1_000,
        quantity_basis=1.0,
        lot_size=100,
    )
    parent_states = {"P-1": parent}
    active_parent_by_code = {"A": "P-1"}
    actions = []

    _apply_corporate_actions(
        trade_date="20240103",
        executor=PortfolioExecutor(100_000.0, ChinaETFExecutionRules()),
        order_mgr=order_mgr,
        bar_lookup={},
        adj_lookup={("20240103", "A"): 2.0},
        position_adj_factor={"A": 1.0},
        parent_states=parent_states,
        active_parent_by_code=active_parent_by_code,
        replacement_counts={},
        corporate_actions=actions,
        trade_events=[],
    )

    assert len(actions) == 1
    assert parent_states["P-1"].status is ParentOrderStatus.CANCELED
    replacement = next(
        value for key, value in parent_states.items() if key != "P-1"
    )
    assert replacement.original_requested_quantity == 2_000


def test_native_engine_fails_closed_on_short_target_even_when_rule_allows_short():
    dates = _dates()
    market, adj = _market(codes=("A",), dates=dates)
    resolver, instruments = _resolver_and_instruments(
        ("A",),
        short_allowed_by_code={"A": True},
    )

    with pytest.raises(ValueError, match="short"):
        FundRotationExecutionEngine().execute(
            _request(
                {"20240101": {"A": -0.10}},
                evaluation_dates=dates,
                market=market,
                adj=adj,
                rule_resolver=resolver,
                instrument_versions=instruments,
            )
        )


def test_native_engine_fails_closed_on_short_target_when_rule_disallows_short():
    dates = _dates()
    market, adj = _market(codes=("A",), dates=dates)
    resolver, instruments = _resolver_and_instruments(
        ("A",),
        short_allowed_by_code={"A": False},
    )

    with pytest.raises(ValueError, match="short"):
        FundRotationExecutionEngine().execute(
            _request(
                {"20240101": {"A": -0.10}},
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
    assert result.state.active_orders
    assert result.state.active_orders[0]["remaining"] == (
        result.state.active_orders[0]["requested"]
        - result.state.active_orders[0]["filled"]
    )


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
