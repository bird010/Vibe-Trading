from __future__ import annotations

from datetime import datetime
from dataclasses import replace
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
    ShadowFill,
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
    def scheduled_dates(self, calendar, decision_start_date, evaluation_end_date):
        return tuple(date for date in calendar if date <= evaluation_end_date)


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


def test_production_provider_evaluates_formal_session_without_store_signal() -> None:
    strategy = _Strategy()
    provider = ProductionFrozenStrategyDecisionProvider(
        strategy_binding=_binding(strategy),
        data_view_factory=lambda signal_date, as_of_time: SimpleNamespace(
            signal_date=pd.Timestamp(signal_date), snapshot_fingerprint="pit-1"
        ),
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
    assert strategy.session.evaluated == ["20260105"]

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
        "sv-1", datetime(2026, 1, 5, 10, 0)
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
    assert facts.execution_state is native_state
    assert account.execution_state is native_state
    assert account.positions == (("ETF_A", 10.0),)


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
