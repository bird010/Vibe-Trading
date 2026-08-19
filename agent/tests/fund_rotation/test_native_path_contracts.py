from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest

from backtest.fund_rotation import runner as runner_module
from backtest.fund_rotation.market_rules import MarketRuleResolver
from src.stockpred.fund_rotation.forward_validation import (
    ACCOUNTING_CONTRACT_VERSION,
    InMemoryForwardValidationStore,
    ScheduledSignal,
    ShadowAccountState,
)
from src.stockpred.fund_rotation.production_adapters import (
    build_production_shadow_execution_service,
)


def test_runner_formal_path_never_calls_legacy_loop_or_old_diagnostics(monkeypatch):
    from tests.fund_rotation.test_runner_native_execution import (
        SpyNativeEngine,
        _run,
    )

    def forbidden(*args, **kwargs):  # pragma: no cover - call is the failure
        raise AssertionError("legacy execution source must not be used formally")

    assert not hasattr(runner_module, "run_execution_loop")
    monkeypatch.setattr(
        runner_module,
        "build_execution_ledger_from_pipeline_result",
        forbidden,
        raising=False,
    )
    monkeypatch.setattr(
        runner_module,
        "compute_pipeline_execution_diagnostics_v2",
        forbidden,
        raising=False,
    )

    result = _run(engine=SpyNativeEngine())

    assert result.status.value == "SUCCEEDED"
    assert result.execution_diagnostics["metric_contract_version"] == (
        "execution_diagnostics_v2"
    )


class _ExplicitProvider:
    def __init__(self) -> None:
        self.calls: list[tuple[str, datetime]] = []

    def next_signal(self, *, store, strategy_version_id, as_of_time):
        self.calls.append((strategy_version_id, as_of_time))
        return ScheduledSignal(
            strategy_version_id=strategy_version_id,
            signal_date="20260105",
            data_available_at=as_of_time,
            snapshot_fingerprint="formal-snapshot",
            raw_signal={"source": "formal-provider"},
            selected_clusters=(),
            target_weights=(),
            target_change_reasons=(),
            expected_execution_date="20260106",
            cash_weight=1.0,
        )


def _shadow_store() -> InMemoryForwardValidationStore:
    state = ShadowAccountState(
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
    store = InMemoryForwardValidationStore(account_states={"sv-1": state})
    store.strategy_versions["sv-1"] = SimpleNamespace(
        accounting_contract_version=ACCOUNTING_CONTRACT_VERSION
    )
    return store


def test_production_shadow_uses_explicit_provider_and_has_no_deterministic_defaults():
    provider = _ExplicitProvider()
    store = _shadow_store()
    service = build_production_shadow_execution_service(
        store,
        strategy_provider=provider,
        execution_adapter=None,
        accounting_adapter=None,
        strategy_identity="strategy-1",
        rule_identity="rule-1",
    )

    with pytest.raises(ValueError, match="NOT_CONFIGURED"):
        service.seal_scheduled_decision("sv-1", datetime(2026, 1, 5, 10, 0))

    assert service.decision_provider is None
    assert provider.calls == []

    unconfigured = build_production_shadow_execution_service(
        _shadow_store(),
        strategy_provider=None,
        execution_adapter=None,
        accounting_adapter=None,
        strategy_identity="strategy-1",
        rule_identity="rule-1",
    )
    assert unconfigured.decision_provider is None
    assert unconfigured.execution_adapter is None
    assert unconfigured.accounting_adapter is None


def test_market_rule_resolver_requires_explicit_source_at_construction():
    with pytest.raises((TypeError, ValueError)):
        MarketRuleResolver(None)
