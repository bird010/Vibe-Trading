from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from dataclasses import replace
import json
from types import SimpleNamespace

import pandas as pd
import pytest

from backtest.fund_rotation.attribution import compute_accounting_day
from backtest.fund_rotation.execution_ledger_v2 import (
    AttemptStatus,
    ExecutedTradeRecord,
    ExecutionAttemptRecord,
    ExecutionLedger,
    OrderDirection,
    ParentOrderRecord,
    ParentOrderStatus,
)
from backtest.fund_rotation.native_execution import NativeExecutionState
from backtest.fund_rotation.contracts import (
    DecisionKind,
    FundRotationStrategyDescriptor,
    StrategyDataRequirements,
    TargetWeightDecision,
)
from src.stockpred.fund_rotation.forward_validation import (
    ACCOUNTING_CONTRACT_VERSION,
    InMemoryForwardValidationStore,
    MarketDataForExecution,
    ShadowAccountState,
    ShadowDecision,
    ShadowExecutionAttempt,
    ShadowExecutionService,
    ShadowFill,
    ShadowOrder,
)
from src.stockpred.fund_rotation.production_adapters import (
    ProductionAdapterError,
    ProductionFrozenStrategyDecisionProvider,
    ProductionShadowAccountingAdapter,
    ProductionShadowExecutionAdapter,
    build_production_shadow_execution_service,
)


def _state() -> ShadowAccountState:
    return ShadowAccountState(
        strategy_version_id="sv-1",
        as_of_time=datetime(2026, 1, 5, 10, 0),
        cash=1000.0,
        positions=(),
        target_weights=(),
        residual_orders=(),
        shadow_ideal_nav=1000.0,
        shadow_executable_nav=1000.0,
        accounting_contract_version=ACCOUNTING_CONTRACT_VERSION,
        completed_rebalance_cycles=0,
        cash_weight=1.0,
    )


def _decision() -> ShadowDecision:
    return ShadowDecision(
        shadow_decision_id="sd-1",
        strategy_version_id="sv-1",
        generated_at=datetime(2026, 1, 5, 10, 0),
        signal_date="20260105",
        as_of_time=datetime(2026, 1, 5, 10, 0),
        snapshot_fingerprint="snapshot-1",
        previous_targets=(),
        new_targets=(("ETF_A", 0.1),),
        previous_cash=1000.0,
        previous_nav=1000.0,
        raw_signal={},
        selected_clusters=(),
        target_change_reasons=(),
        expected_execution_date="20260106",
        status="SEALED",
        reason_codes=("SEALED",),
        decision_idempotency_key="decision:sv-1:20260105",
        accounting_contract_version=ACCOUNTING_CONTRACT_VERSION,
        new_cash_weight=0.9,
    )


class _Session:
    def __init__(self) -> None:
        self.evaluated: list[str] = []

    def scheduled_dates(self, calendar, decision_start_date, evaluation_end_date):
        return ("20260105",)

    def evaluate(self, context):
        self.evaluated.append(context.signal_date)
        return TargetWeightDecision(
            decision_id="formal-decision-1",
            signal_date=context.signal_date,
            action=DecisionKind.SET_TARGETS,
            target_weights={"ETF_A": 0.1},
            cash_weight=0.9,
            reason_code="FORMAL_RULE",
            diagnostics={"snapshot": "pit-1"},
        )

    def finalize(self):
        return SimpleNamespace(artifacts=())


class _StatefulSession(_Session):
    def __init__(self) -> None:
        super().__init__()
        self.evaluation_calls = 0

    def scheduled_dates(self, calendar, decision_start_date, evaluation_end_date):
        return tuple(date for date in calendar if date <= evaluation_end_date)

    def evaluate(self, context):
        self.evaluation_calls += 1
        return super().evaluate(context)

    def to_snapshot(self):
        return {"evaluated": tuple(self.evaluated)}

    def restore_snapshot(self, snapshot):
        self.evaluated = list(snapshot.get("evaluated", ()))


class _Strategy:
    descriptor = FundRotationStrategyDescriptor(
        id="fund-rotation",
        name="Fund rotation",
        description="test",
        interface_version="1.0",
        supported_universe=("ETF_A",),
        deterministic=True,
    )
    config_model = None

    def __init__(self):
        self.session = _Session()

    def create_session(self, initialization, config):
        self.initialization = initialization
        self.config = config
        return self.session


class _StatefulStrategy(_Strategy):
    def __init__(self):
        self.session = _StatefulSession()
        self.create_count = 0

    def create_session(self, initialization, config):
        self.create_count += 1
        return self.session

    def resolve_requirements(self, config):
        return StrategyDataRequirements((), (), 0, "daily", False)


def _binding(strategy: _Strategy):
    return SimpleNamespace(
        strategy=strategy,
        registered=SimpleNamespace(config_model=None),
        spec=SimpleNamespace(
            strategy_id="fund-rotation",
            implementation_hash="impl-1",
            resolved_config={},
            resolved_config_hash="config-1",
        ),
    )


def _scheduled_data_cutoff(signal_date: str) -> datetime:
    return datetime.strptime(str(signal_date), "%Y%m%d").replace(hour=16)


def test_production_provider_evaluates_formal_session_without_store_signal() -> None:
    strategy = _Strategy()
    cutoffs = []

    def data_view_factory(signal_date, as_of_time):
        cutoffs.append((signal_date, as_of_time))
        return SimpleNamespace(
            signal_date=pd.Timestamp(signal_date), snapshot_fingerprint="pit-1"
        )

    provider = ProductionFrozenStrategyDecisionProvider(
        strategy_binding=_binding(strategy),
        data_view_factory=data_view_factory,
        data_availability_cutoff_factory=_scheduled_data_cutoff,
        calendar_factory=lambda as_of_time: ("20260105", "20260106"),
        run_id="shadow-run-1",
    )
    store = InMemoryForwardValidationStore(account_states={"sv-1": _state()})
    store.strategy_versions["sv-1"] = SimpleNamespace(
        accounting_contract_version=ACCOUNTING_CONTRACT_VERSION
    )
    store.next_signal = lambda *_args, **_kwargs: pytest.fail("store signal must not be read")

    signal = provider.next_signal(
        store=store,
        strategy_version_id="sv-1",
        as_of_time=datetime(2026, 1, 5, 10, 0),
    )

    assert signal is not None
    assert signal.raw_signal["decision_id"] == "formal-decision-1"
    assert signal.target_weights == (("ETF_A", 0.1),)
    assert signal.data_available_at == datetime(2026, 1, 5, 16, 0)
    assert strategy.session.evaluated == ["20260105"]
    assert cutoffs == [("20260105", datetime(2026, 1, 5, 16, 0))]

    service = build_production_shadow_execution_service(
        store,
        strategy_provider=provider,
        execution_adapter=ProductionShadowExecutionAdapter(
            engine=object(),
            request_factory=lambda **_: None,
            strategy_identity="strategy-1",
            rule_identity="rule-1",
        ),
        accounting_adapter=ProductionShadowAccountingAdapter(),
        strategy_identity="strategy-1",
        rule_identity="rule-1",
    )
    result = service.seal_scheduled_decision(
        "sv-1", datetime(2026, 1, 5, 17, 0)
    )
    assert service.decision_provider is provider
    assert result.decision.status.value == "SEALED"


def test_production_provider_reuses_strategy_session_across_shadow_cycles() -> None:
    strategy = _StatefulStrategy()
    provider = ProductionFrozenStrategyDecisionProvider(
        strategy_binding=_binding(strategy),
        data_view_factory=lambda signal_date, as_of_time: SimpleNamespace(
            signal_date=pd.Timestamp(signal_date), snapshot_fingerprint="pit-1"
        ),
        data_availability_cutoff_factory=_scheduled_data_cutoff,
        calendar_factory=lambda as_of_time: ("20260105", "20260106"),
        run_id="shadow-run-1",
    )
    store = InMemoryForwardValidationStore(account_states={"sv-1": _state()})

    first = provider.next_signal(
        store=store,
        strategy_version_id="sv-1",
        as_of_time=datetime(2026, 1, 5, 10, 0),
    )
    second = provider.next_signal(
        store=store,
        strategy_version_id="sv-1",
        as_of_time=datetime(2026, 1, 6, 10, 0),
    )

    assert first is not None and second is not None
    assert strategy.create_count == 1
    assert strategy.session.evaluated == ["20260105", "20260106"]


def test_production_provider_restores_evaluated_dates_after_process_restart() -> None:
    store = InMemoryForwardValidationStore(account_states={"sv-1": _state()})
    first_strategy = _StatefulStrategy()
    first_provider = ProductionFrozenStrategyDecisionProvider(
        strategy_binding=_binding(first_strategy),
        data_view_factory=lambda signal_date, as_of_time: SimpleNamespace(
            signal_date=pd.Timestamp(signal_date), snapshot_fingerprint="pit-1"
        ),
        data_availability_cutoff_factory=_scheduled_data_cutoff,
        calendar_factory=lambda as_of_time: ("20260105", "20260106"),
        run_id="shadow-run-1",
    )
    first_provider.next_signal(
        store=store, strategy_version_id="sv-1", as_of_time=datetime(2026, 1, 6, 10, 0)
    )

    persisted_runtime_state = json.loads(
        json.dumps(store.export_strategy_runtime_state("sv-1"))
    )
    restarted_store = InMemoryForwardValidationStore(
        account_states=deepcopy(store.account_states),
    )
    restarted_store.import_strategy_runtime_state("sv-1", persisted_runtime_state)

    restarted_strategy = _StatefulStrategy()
    restarted_provider = ProductionFrozenStrategyDecisionProvider(
        strategy_binding=_binding(restarted_strategy),
        data_view_factory=lambda signal_date, as_of_time: SimpleNamespace(
            signal_date=pd.Timestamp(signal_date), snapshot_fingerprint="pit-1"
        ),
        data_availability_cutoff_factory=_scheduled_data_cutoff,
        calendar_factory=lambda as_of_time: ("20260105", "20260106"),
        run_id="shadow-run-1",
    )
    restarted_signal = restarted_provider.next_signal(
        store=restarted_store,
        strategy_version_id="sv-1",
        as_of_time=datetime(2026, 1, 6, 10, 0),
    )

    assert restarted_signal is not None
    assert restarted_strategy.session.evaluated == ["20260105", "20260106"]
    assert restarted_strategy.session.evaluation_calls == 0


def test_production_execution_adapter_preserves_native_ledger_ids() -> None:
    parent = ParentOrderRecord(
        order_id="parent-1", decision_id="formal-decision-1", ts_code="ETF_A",
        direction=OrderDirection.BUY, created_date="20260106",
        original_requested_quantity=10, cumulative_filled_quantity=10,
        remaining_quantity=0, quantity_basis_id="ETF_A:shares:1",
        status=ParentOrderStatus.FILLED,
    )
    attempt = ExecutionAttemptRecord(
        attempt_id="attempt-1", order_id="parent-1", attempt_number=1,
        trade_date="20260106", requested_quantity=10, filled_quantity=10,
        unfilled_quantity=0, quantity_basis_id="ETF_A:shares:1", raw_price=10,
        executed_price=10, commission=0, explicit_fee=0, slippage_cost=0,
        participation_rate=1, status=AttemptStatus.FILLED,
    )
    trade = ExecutedTradeRecord(
        trade_id="trade-1", attempt_id="attempt-1", order_id="parent-1",
        ts_code="ETF_A", direction=OrderDirection.BUY, quantity=10,
        quantity_basis_id="ETF_A:shares:1", price=10, notional=100,
        commission=0, explicit_fee=0, slippage_cost=0, trade_date="20260106",
    )
    native = SimpleNamespace(
        ledger=ExecutionLedger((parent,), (attempt,), (trade,), ()),
    )
    calls = []
    adapter = ProductionShadowExecutionAdapter(
        engine=SimpleNamespace(execute=lambda request: calls.append(request) or native),
        request_factory=lambda **kwargs: kwargs,
        strategy_identity="strategy-1",
        rule_identity="rule-1",
    )
    orders = (SimpleNamespace(symbol="ETF_A", target_weight=0.1),)

    attempts, fills = adapter.execute(
        decision=_decision(),
        orders=orders,
        market_data=MarketDataForExecution(
            execution_date="20260106", available_at=datetime(2026, 1, 6, 9, 31),
            prices=(("ETF_A", 10.0),), ideal_nav=1000.0, executable_nav=1000.0,
        ),
        execution_as_of_time=datetime(2026, 1, 6, 9, 31),
    )

    assert len(calls) == 1
    assert attempts == (ShadowExecutionAttempt("attempt-1", "sd-1", "ETF_A", 0.1, datetime(2026, 1, 6, 9, 31)),)
    assert fills == (ShadowFill("trade-1", "attempt-1", "ETF_A", 10.0, 10.0, 0.0),)


def test_production_execution_adapter_emits_only_native_ledger_delta() -> None:
    old_parent = ParentOrderRecord(
        order_id="parent-old", decision_id="formal-decision-0", ts_code="ETF_A",
        direction=OrderDirection.BUY, created_date="20260105",
        original_requested_quantity=20, cumulative_filled_quantity=20,
        remaining_quantity=0, quantity_basis_id="ETF_A:shares:1",
        status=ParentOrderStatus.FILLED,
    )
    new_parent = ParentOrderRecord(
        order_id="parent-new", decision_id="formal-decision-1", ts_code="ETF_B",
        direction=OrderDirection.BUY, created_date="20260106",
        original_requested_quantity=20, cumulative_filled_quantity=20,
        remaining_quantity=0, quantity_basis_id="ETF_B:shares:1",
        status=ParentOrderStatus.FILLED,
    )
    old_attempt = ExecutionAttemptRecord(
        attempt_id="attempt-old", order_id="parent-old", attempt_number=1,
        trade_date="20260105", requested_quantity=10, filled_quantity=10,
        unfilled_quantity=0, quantity_basis_id="ETF_A:shares:1", raw_price=10,
        executed_price=10, commission=0, explicit_fee=0, slippage_cost=0,
        participation_rate=1, status=AttemptStatus.FILLED,
    )
    new_attempt = ExecutionAttemptRecord(
        attempt_id="attempt-new", order_id="parent-new", attempt_number=1,
        trade_date="20260106", requested_quantity=20, filled_quantity=20,
        unfilled_quantity=0, quantity_basis_id="ETF_B:shares:1", raw_price=10,
        executed_price=10, commission=0, explicit_fee=0, slippage_cost=0,
        participation_rate=1, status=AttemptStatus.FILLED,
    )
    retry_attempt = ExecutionAttemptRecord(
        attempt_id="attempt-retry", order_id="parent-old", attempt_number=2,
        trade_date="20260106", requested_quantity=10, filled_quantity=10,
        unfilled_quantity=0, quantity_basis_id="ETF_A:shares:1", raw_price=10,
        executed_price=10, commission=0, explicit_fee=0, slippage_cost=0,
        participation_rate=1, status=AttemptStatus.FILLED,
    )
    old_trade = ExecutedTradeRecord(
        trade_id="trade-old", attempt_id="attempt-old", order_id="parent-old",
        ts_code="ETF_A", direction=OrderDirection.BUY, quantity=10,
        quantity_basis_id="ETF_A:shares:1", price=10, notional=100,
        commission=0, explicit_fee=0, slippage_cost=0, trade_date="20260105",
    )
    new_trade = ExecutedTradeRecord(
        trade_id="trade-new", attempt_id="attempt-new", order_id="parent-new",
        ts_code="ETF_B", direction=OrderDirection.BUY, quantity=20,
        quantity_basis_id="ETF_B:shares:1", price=10, notional=200,
        commission=0, explicit_fee=0, slippage_cost=0, trade_date="20260106",
    )
    retry_trade = ExecutedTradeRecord(
        trade_id="trade-retry", attempt_id="attempt-retry", order_id="parent-old",
        ts_code="ETF_A", direction=OrderDirection.BUY, quantity=10,
        quantity_basis_id="ETF_A:shares:1", price=10, notional=100,
        commission=0, explicit_fee=0, slippage_cost=0, trade_date="20260106",
    )
    native_result = SimpleNamespace(
        ledger=ExecutionLedger(
            (old_parent, new_parent), (old_attempt, retry_attempt, new_attempt),
            (old_trade, retry_trade, new_trade), (),
        ),
        state=SimpleNamespace(),
    )
    adapter = ProductionShadowExecutionAdapter(
        engine=SimpleNamespace(execute=lambda request: native_result),
        request_factory=lambda **kwargs: kwargs,
        strategy_identity="strategy-1",
        rule_identity="rule-1",
    )
    previous = replace(
        _state(),
        target_weights=(("ETF_B", 0.2),),
        residual_orders=(("ETF_A", 10.0),),
        execution_state=SimpleNamespace(
            parent_orders=({"ts_code": "ETF_A", "target_weight": 0.1},),
            attempts=({"attempt_id": "attempt-old"},),
            trades=({"trade_id": "trade-old"},),
        ),
    )

    facts = adapter.execute_formal(
        decision=_decision(),
        orders=(SimpleNamespace(symbol="ETF_B", target_weight=0.2),),
        previous_state=previous,
        market_data=MarketDataForExecution(
            execution_date="20260106", available_at=datetime(2026, 1, 6, 9, 31),
            prices=(("ETF_B", 10.0),), ideal_nav=1000.0, executable_nav=1000.0,
        ),
        execution_as_of_time=datetime(2026, 1, 6, 9, 31),
    )

    assert tuple(attempt.attempt_id for attempt in facts.attempts) == (
        "attempt-retry", "attempt-new"
    )
    assert tuple(fill.fill_id for fill in facts.fills) == ("trade-retry", "trade-new")


def test_production_adapters_carry_native_state_into_next_accounting_cycle() -> None:
    native_state = SimpleNamespace(
        cash=900.0,
        positions={"ETF_A": {"size": 10, "entry_date": "20260106"}},
        active_orders=(),
    )
    parent = ParentOrderRecord(
        order_id="parent-1", decision_id="formal-decision-1", ts_code="ETF_A",
        direction=OrderDirection.BUY, created_date="20260106",
        original_requested_quantity=10, cumulative_filled_quantity=10,
        remaining_quantity=0, quantity_basis_id="ETF_A:shares:1",
        status=ParentOrderStatus.FILLED,
    )
    attempt = ExecutionAttemptRecord(
        attempt_id="attempt-1", order_id="parent-1", attempt_number=1,
        trade_date="20260106", requested_quantity=10, filled_quantity=10,
        unfilled_quantity=0, quantity_basis_id="ETF_A:shares:1", raw_price=10,
        executed_price=10, commission=0, explicit_fee=0, slippage_cost=0,
        participation_rate=1, status=AttemptStatus.FILLED,
    )
    trade = ExecutedTradeRecord(
        trade_id="trade-1", attempt_id="attempt-1", order_id="parent-1",
        ts_code="ETF_A", direction=OrderDirection.BUY, quantity=10,
        quantity_basis_id="ETF_A:shares:1", price=10, notional=100,
        commission=0, explicit_fee=0, slippage_cost=0, trade_date="20260106",
    )
    native_result = SimpleNamespace(
        ledger=ExecutionLedger((parent,), (attempt,), (trade,), ()),
        state=native_state,
    )
    seen = {}
    adapter = ProductionShadowExecutionAdapter(
        engine=SimpleNamespace(execute=lambda request: native_result),
        request_factory=lambda **kwargs: seen.update(kwargs) or kwargs,
        strategy_identity="strategy-1",
        rule_identity="rule-1",
    )
    previous = _state()
    previous = replace(previous, execution_state=SimpleNamespace(marker="prior"))
    orders = (SimpleNamespace(symbol="ETF_A", target_weight=0.1),)
    market_data = MarketDataForExecution(
        execution_date="20260106", available_at=datetime(2026, 1, 6, 9, 31),
        prices=(("ETF_A", 10.0),), open_prices=(("ETF_A", 10.0),),
        ideal_nav=1000.0, executable_nav=1000.0,
    )

    facts = adapter.execute_formal(
        decision=_decision(),
        orders=orders,
        previous_state=previous,
        market_data=market_data,
        execution_as_of_time=datetime(2026, 1, 6, 9, 31),
    )
    account = ProductionShadowAccountingAdapter().apply_formal(
        decision=_decision(),
        previous_state=previous,
        fills=facts.fills,
        execution_state=facts.execution_state,
        market_data=market_data,
        execution_as_of_time=datetime(2026, 1, 6, 9, 31),
    )

    assert seen["initial_state"] is previous.execution_state
    assert seen["execution_mode"] == "NEW_TARGET"
    assert facts.execution_state is native_state
    assert account.execution_state is native_state
    assert account.positions == (("ETF_A", 10.0),)


def test_production_execution_adapter_marks_residual_retry_and_drops_new_orders() -> None:
    native_state = NativeExecutionState(cash=700.0, positions={"ETF_A": {"size": 30}})
    seen = {}
    adapter = ProductionShadowExecutionAdapter(
        engine=SimpleNamespace(
            execute=lambda request: SimpleNamespace(
                ledger=ExecutionLedger((), (), (), ()),
                state=native_state,
            )
        ),
        request_factory=lambda **kwargs: seen.update(kwargs) or kwargs,
        strategy_identity="strategy-1",
        rule_identity="rule-1",
    )
    previous = replace(
        _state(),
        residual_orders=(("ETF_A", 70.0),),
        execution_state=native_state,
    )

    adapter.execute_formal(
        decision=_decision(),
        orders=(SimpleNamespace(symbol="ETF_A", target_weight=0.1),),
        previous_state=previous,
        market_data=MarketDataForExecution(
            execution_date="20260107",
            available_at=datetime(2026, 1, 7, 9, 31),
            prices=(("ETF_A", 10.0),),
            ideal_nav=1000.0,
            executable_nav=1000.0,
        ),
        execution_as_of_time=datetime(2026, 1, 7, 9, 31),
        execution_mode="RESIDUAL_RETRY",
    )

    assert seen["execution_mode"] == "RESIDUAL_RETRY"
    assert seen["orders"] == ()


def test_production_shadow_service_persists_partial_cash_and_retries_native_parent() -> None:
    parent_open = ParentOrderRecord(
        order_id="parent-1", decision_id="sd-1", ts_code="ETF_A",
        direction=OrderDirection.BUY, created_date="20260106",
        original_requested_quantity=100, cumulative_filled_quantity=30,
        remaining_quantity=70, quantity_basis_id="ETF_A:shares:1",
        status=ParentOrderStatus.OPEN,
    )
    parent_filled = replace(
        parent_open,
        cumulative_filled_quantity=100,
        remaining_quantity=0,
        status=ParentOrderStatus.FILLED,
    )
    attempt_partial = ExecutionAttemptRecord(
        attempt_id="attempt-1", order_id="parent-1", attempt_number=1,
        trade_date="20260106", requested_quantity=100, filled_quantity=30,
        unfilled_quantity=70, quantity_basis_id="ETF_A:shares:1", raw_price=10,
        executed_price=10, commission=0, explicit_fee=0, slippage_cost=0,
        participation_rate=0.3, status=AttemptStatus.PARTIALLY_FILLED,
    )
    attempt_retry = replace(
        attempt_partial,
        attempt_id="attempt-2",
        attempt_number=2,
        trade_date="20260107",
        requested_quantity=70,
        filled_quantity=70,
        unfilled_quantity=0,
        participation_rate=1.0,
        status=AttemptStatus.FILLED,
    )
    trade_partial = ExecutedTradeRecord(
        trade_id="trade-1", attempt_id="attempt-1", order_id="parent-1",
        ts_code="ETF_A", direction=OrderDirection.BUY, quantity=30,
        quantity_basis_id="ETF_A:shares:1", price=10, notional=300,
        commission=0, explicit_fee=0, slippage_cost=0, trade_date="20260106",
    )
    trade_retry = replace(
        trade_partial,
        trade_id="trade-2",
        attempt_id="attempt-2",
        quantity=70,
        notional=700,
        trade_date="20260107",
    )

    class PartialFillEngine:
        def __init__(self):
            self.calls = []

        def execute(self, request):
            self.calls.append(request)
            if request["execution_mode"] == "NEW_TARGET":
                return SimpleNamespace(
                    ledger=ExecutionLedger(
                        (parent_open,), (attempt_partial,), (trade_partial,), ()
                    ),
                    state=NativeExecutionState(
                        cash=700.0,
                        positions={"ETF_A": {"size": 30}},
                        active_orders=({
                            "ts_code": "ETF_A",
                            "parent_order_id": "parent-1",
                            "remaining": 70,
                        },),
                        parent_orders=((
                            {"order_id": "parent-1", "ts_code": "ETF_A", "target_weight": 1.0}
                        ),),
                        attempts=(({"attempt_id": "attempt-1"}),),
                        trades=(({"trade_id": "trade-1"}),),
                    ),
                )
            return SimpleNamespace(
                ledger=ExecutionLedger(
                    (parent_filled,),
                    (attempt_partial, attempt_retry),
                    (trade_partial, trade_retry),
                    (),
                ),
                state=NativeExecutionState(
                    cash=0.0,
                    positions={"ETF_A": {"size": 100}},
                    active_orders=(),
                    parent_orders=((
                        {"order_id": "parent-1", "ts_code": "ETF_A", "target_weight": 1.0}
                    ),),
                    attempts=(({"attempt_id": "attempt-1"}), {"attempt_id": "attempt-2"}),
                    trades=(({"trade_id": "trade-1"}), {"trade_id": "trade-2"}),
                ),
            )

    decision = replace(
        _decision(),
        new_targets=(("ETF_A", 1.0),),
        new_cash_weight=0.0,
        expected_execution_date="20260106",
    )
    store = InMemoryForwardValidationStore(
        account_states={"sv-1": _state()},
        decisions=[decision],
        orders=[ShadowOrder("so-1", "sd-1", "ETF_A", 1.0, "20260106")],
        market_data={
            "20260106": MarketDataForExecution(
                execution_date="20260106",
                available_at=datetime(2026, 1, 6, 9, 31),
                prices=(("ETF_A", 10.0),),
                open_prices=(("ETF_A", 10.0),),
                ideal_nav=1000.0,
                executable_nav=1000.0,
            ),
            "20260107": MarketDataForExecution(
                execution_date="20260107",
                available_at=datetime(2026, 1, 7, 9, 31),
                prices=(("ETF_A", 10.0),),
                prior_close_prices=(("ETF_A", 10.0),),
                open_prices=(("ETF_A", 10.0),),
                ideal_nav=1000.0,
                executable_nav=1000.0,
            ),
        },
    )
    engine = PartialFillEngine()
    service = ShadowExecutionService(
        store,
        execution_adapter=ProductionShadowExecutionAdapter(
            engine=engine,
            request_factory=lambda **kwargs: kwargs,
            strategy_identity="strategy-1",
            rule_identity="rule-1",
        ),
        accounting_adapter=ProductionShadowAccountingAdapter(),
    )

    first = service.execute_due_orders("sd-1", datetime(2026, 1, 6, 9, 31))
    retry = service.execute_due_orders("sd-1", datetime(2026, 1, 7, 9, 31))

    assert first.status == "EXECUTED"
    assert first.account_state.cash_weight == pytest.approx(0.7)
    assert first.account_state.residual_orders == (("ETF_A", 70.0),)
    assert retry.status == "EXECUTED"
    assert retry.account_state.cash_weight == pytest.approx(0.0)
    assert retry.account_state.residual_orders == ()
    assert [call["execution_mode"] for call in engine.calls] == [
        "NEW_TARGET", "RESIDUAL_RETRY"
    ]
    assert engine.calls[1]["orders"] == ()
    assert engine.calls[1]["initial_state"] is first.account_state.execution_state
    assert retry.attempts[0].attempt_id == "attempt-2"


def test_production_accounting_persists_actual_cash_after_partial_fill() -> None:
    decision = replace(
        _decision(),
        new_targets=(("ETF_A", 1.0),),
        new_cash_weight=0.0,
    )
    previous = replace(_state(), target_weights=(), cash=1000.0)
    native_state = SimpleNamespace(
        cash=700.0,
        positions={"ETF_A": {"size": 30}},
        active_orders=({"ts_code": "ETF_A", "remaining": 70},),
    )

    state = ProductionShadowAccountingAdapter().apply_formal(
        decision=decision,
        previous_state=previous,
        fills=(ShadowFill("trade-1", "attempt-1", "ETF_A", 30.0, 10.0, 0.0),),
        execution_state=native_state,
        market_data=MarketDataForExecution(
            execution_date="20260106",
            available_at=datetime(2026, 1, 6, 9, 31),
            prices=(("ETF_A", 10.0),),
            open_prices=(("ETF_A", 10.0),),
            ideal_nav=1000.0,
            executable_nav=1000.0,
        ),
        execution_as_of_time=datetime(2026, 1, 6, 9, 31),
    )

    assert state.cash == pytest.approx(700.0)
    assert state.cash_weight == pytest.approx(0.7)
    assert state.residual_orders == (("ETF_A", 70.0),)


def test_production_execution_adapter_restores_persisted_native_snapshot() -> None:
    native_state = NativeExecutionState(cash=900.0, positions={"ETF_A": {"size": 10}})
    seen = {}
    ledger = ExecutionLedger((), (), (), ())
    adapter = ProductionShadowExecutionAdapter(
        engine=SimpleNamespace(
            execute=lambda request: SimpleNamespace(
                ledger=ledger,
                state=native_state,
            )
        ),
        request_factory=lambda **kwargs: seen.update(kwargs) or kwargs,
        strategy_identity="strategy-1",
        rule_identity="rule-1",
    )
    previous = replace(
        _state(),
        execution_state=None,
        execution_state_snapshot=native_state.to_snapshot(),
    )
    adapter.execute_formal(
        decision=_decision(),
        orders=(),
        previous_state=previous,
        market_data=MarketDataForExecution(
            execution_date="20260106",
            available_at=datetime(2026, 1, 6, 9, 31),
            prices=(),
            ideal_nav=1000.0,
            executable_nav=1000.0,
        ),
        execution_as_of_time=datetime(2026, 1, 6, 9, 31),
    )
    assert isinstance(seen["initial_state"], NativeExecutionState)
    assert seen["initial_state"].cash == 900.0


def test_formal_production_accounting_fails_closed_without_open_price() -> None:
    with pytest.raises(ProductionAdapterError, match="open price is required"):
        ProductionShadowAccountingAdapter().apply_formal(
            decision=_decision(),
            previous_state=_state(),
            fills=(ShadowFill("trade-1", "attempt-1", "ETF_A", 10.0, 10.0, 0.0),),
            execution_state=None,
            market_data=MarketDataForExecution(
                execution_date="20260106",
                available_at=datetime(2026, 1, 6, 9, 31),
                prices=(("ETF_A", 10.0),),
                ideal_nav=1000.0,
                executable_nav=1000.0,
            ),
            execution_as_of_time=datetime(2026, 1, 6, 9, 31),
        )


def test_formal_production_accounting_passes_native_corporate_actions_to_shared_accounting(
    monkeypatch,
) -> None:
    seen = []
    original = compute_accounting_day
    monkeypatch.setattr(
        "src.stockpred.fund_rotation.production_adapters.compute_accounting_day",
        lambda day: seen.append(day) or original(day),
    )
    previous = replace(
        _state(),
        cash=0.0,
        positions=(("ETF_A", 100.0),),
        valuation_prices=(("ETF_A", 10.0),),
    )
    native_state = SimpleNamespace(
        cash=0.0,
        positions={"ETF_A": {"size": 100}},
        active_orders=(),
        corporate_actions=(
            {
                "corporate_action_id": "CA-20260106-ETF_A",
                "ts_code": "ETF_A",
                "old_quantity": 100,
                "new_quantity": 100,
                "old_cost_basis": 10.0,
                "new_cost_basis": 10.0,
                "cash_in_lieu": 0.0,
            },
        ),
    )
    result = ProductionShadowAccountingAdapter().apply_formal(
        decision=_decision(),
        previous_state=previous,
        fills=(),
        execution_state=native_state,
        market_data=MarketDataForExecution(
            execution_date="20260106",
            available_at=datetime(2026, 1, 6, 9, 31),
            prices=(("ETF_A", 10.0),),
            open_prices=(("ETF_A", 10.0),),
            ideal_nav=1000.0,
            executable_nav=1000.0,
        ),
        execution_as_of_time=datetime(2026, 1, 6, 9, 31),
    )
    assert result.shadow_executable_nav == pytest.approx(1000.0)
    assert len(seen) == 1
    assert seen[0].corporate_actions[0].symbol == "ETF_A"


def test_production_accounting_adapter_delegates_to_shared_accounting_day(monkeypatch) -> None:
    calls = []
    original = compute_accounting_day

    def spy(day):
        calls.append(day)
        return original(day)

    monkeypatch.setattr(
        "src.stockpred.fund_rotation.production_adapters.compute_accounting_day", spy
    )
    state = ProductionShadowAccountingAdapter().apply(
        decision=_decision(),
        previous_state=_state(),
        fills=(ShadowFill("trade-1", "attempt-1", "ETF_A", 10.0, 10.0, 0.0),),
        market_data=MarketDataForExecution(
            execution_date="20260106", available_at=datetime(2026, 1, 6, 9, 31),
            prices=(("ETF_A", 10.0),), ideal_nav=1000.0, executable_nav=1000.0,
        ),
        execution_as_of_time=datetime(2026, 1, 6, 9, 31),
    )

    assert calls
    assert state.cash == 900.0
    assert state.shadow_executable_nav == 1000.0
    assert state.positions == (("ETF_A", 10.0),)
    assert state.completed_rebalance_cycles == 1
    assert state.cash_weight == 0.9


def test_production_accounting_uses_previous_valuation_for_beginning_nav(monkeypatch) -> None:
    calls = []
    original = compute_accounting_day

    def spy(day):
        calls.append(day)
        return original(day)

    monkeypatch.setattr(
        "src.stockpred.fund_rotation.production_adapters.compute_accounting_day", spy
    )
    previous = replace(
        _state(),
        cash=0.0,
        positions=(("ETF_A", 100.0),),
        shadow_executable_nav=1000.0,
        cash_weight=0.0,
        valuation_prices=(("ETF_A", 10.0),),
    )
    result = ProductionShadowAccountingAdapter().apply(
        decision=_decision(),
        previous_state=previous,
        fills=(),
        market_data=MarketDataForExecution(
            execution_date="20260106",
            available_at=datetime(2026, 1, 6, 9, 31),
            prices=(("ETF_A", 11.0),),
            ideal_nav=1100.0,
            executable_nav=1100.0,
        ),
        execution_as_of_time=datetime(2026, 1, 6, 9, 31),
    )

    assert calls[0].begin_positions[0].valuation_price == 10.0
    assert calls[0].prices["ETF_A"].prior_close == 10.0
    assert result.shadow_executable_nav == pytest.approx(1100.0)


@pytest.mark.parametrize(
    ("previous_state", "fills", "missing_symbol"),
    [
        (replace(_state(), positions=(("ETF_MISSING", 10.0),)), (), "ETF_MISSING"),
        (_state(), (ShadowFill("trade-1", "attempt-1", "ETF_MISSING", 10.0, 10.0, 0.0),), "ETF_MISSING"),
    ],
)
def test_production_accounting_adapter_fails_closed_on_missing_accounting_price(
    previous_state, fills, missing_symbol
) -> None:
    with pytest.raises(
        ProductionAdapterError,
        match=f"market data price is required for accounting symbols: {missing_symbol}",
    ):
        ProductionShadowAccountingAdapter().apply(
            decision=_decision(),
            previous_state=previous_state,
            fills=fills,
            market_data=MarketDataForExecution(
                execution_date="20260106",
                available_at=datetime(2026, 1, 6, 9, 31),
                prices=(("ETF_A", 10.0),),
                ideal_nav=1000.0,
                executable_nav=1000.0,
            ),
            execution_as_of_time=datetime(2026, 1, 6, 9, 31),
        )


def test_production_wiring_missing_formal_components_is_not_configured() -> None:
    previous_state = _state()
    store = InMemoryForwardValidationStore(account_states={"sv-1": previous_state})
    decision = _decision()
    store.decisions.append(decision)
    store.orders.append(
        SimpleNamespace(
            shadow_order_id="so-1",
            shadow_decision_id=decision.shadow_decision_id,
            symbol="ETF_A",
            target_weight=0.1,
            expected_execution_date="20260106",
        )
    )
    store.add_market_data(
        MarketDataForExecution(
            execution_date="20260106",
            available_at=datetime(2026, 1, 6, 9, 31),
            prices=(("ETF_A", 10.0),),
            ideal_nav=1000.0,
            executable_nav=1000.0,
        )
    )
    service = build_production_shadow_execution_service(
        store,
        strategy_provider=None,
        execution_adapter=None,
        accounting_adapter=None,
        strategy_identity="strategy-1",
        rule_identity="rule-1",
    )

    assert service.execution_adapter is None
    assert service.accounting_adapter is None
    result = service.execute_due_orders("sd-1", datetime(2026, 1, 6, 9, 31))
    assert result.status == "NOT_CONFIGURED"
    assert store.execution_attempts == []
    assert store.fills == []
    assert store.execution_results == {}
    assert store.account_states["sv-1"] is previous_state


def test_production_wiring_blank_identity_is_not_configured() -> None:
    service = build_production_shadow_execution_service(
        InMemoryForwardValidationStore(),
        strategy_provider=object(),
        execution_adapter=object(),
        accounting_adapter=object(),
        strategy_identity="",
        rule_identity="rule-1",
    )

    assert service.execution_adapter is None
    assert service.accounting_adapter is None


def test_production_wiring_invalid_adapter_objects_are_not_configured() -> None:
    service = build_production_shadow_execution_service(
        InMemoryForwardValidationStore(),
        strategy_provider=object(),
        execution_adapter=object(),
        accounting_adapter=object(),
        strategy_identity="strategy-1",
        rule_identity="rule-1",
    )

    assert service.execution_adapter is None
    assert service.accounting_adapter is None


class _WrongSignatureProvider:
    def next_signal(self):
        return None


class _WrongSignatureExecutionAdapter:
    def execute(self):
        return (), ()


class _WrongSignatureAccountingAdapter:
    def apply(self):
        return None


def test_production_wiring_wrong_adapter_signatures_are_not_configured() -> None:
    service = build_production_shadow_execution_service(
        InMemoryForwardValidationStore(),
        strategy_provider=_WrongSignatureProvider(),
        execution_adapter=_WrongSignatureExecutionAdapter(),
        accounting_adapter=_WrongSignatureAccountingAdapter(),
        strategy_identity="strategy-1",
        rule_identity="rule-1",
    )

    assert service.decision_provider is None
    assert service.execution_adapter is None
    assert service.accounting_adapter is None
