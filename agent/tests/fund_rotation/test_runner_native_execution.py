from __future__ import annotations

import pandas as pd
import pytest
from pydantic import BaseModel

from backtest.fund_rotation import runner as runner_module
from backtest.fund_rotation.contracts import (
    DecisionKind,
    FundRotationStrategyDescriptor,
    QualityStatus,
    StrategyDataRequirements,
    StrategyDiagnostics,
    TargetWeightDecision,
)
from backtest.fund_rotation.evaluation import EvaluationContext
from backtest.fund_rotation.execution_ledger_v2 import (
    AttemptStatus,
    ExecutedTradeRecord,
    ExecutionAttemptRecord,
    ExecutionLedger,
    OrderDirection,
    ParentOrderRecord,
    ParentOrderStatus,
)
from backtest.fund_rotation.market_rules import (
    FundInstrumentVersion,
    InMemoryPITMarketRuleSource,
    MarketRuleResolver,
)
from backtest.fund_rotation.native_execution import (
    NativeExecutionResult,
    NativeExecutionState,
)
from backtest.fund_rotation.pit_universe import PITQueryMode
from backtest.fund_rotation.runner import (
    CancellationToken,
    ExecutionConfig,
    FundRotationBacktestRunner,
    SubRunStatus,
)
from src.stockpred.fund_rotation.data_snapshot import PinnedFundDataSnapshot


MARKET_DATES = pd.bdate_range("2024-01-02", "2024-01-31").strftime("%Y%m%d").tolist()
EVALUATION_DATES = tuple(
    pd.bdate_range("2024-01-15", "2024-01-31").strftime("%Y%m%d").tolist()
)


class FakeConfig(BaseModel):
    pass


class FakeSession:
    def __init__(self, decisions: dict[str, TargetWeightDecision]):
        self._decisions = dict(decisions)

    def scheduled_dates(self, calendar, simulation_start_date, evaluation_end_date):
        return tuple(self._decisions)

    def evaluate(self, context):
        return self._decisions[context.signal_date]

    def finalize(self):
        return StrategyDiagnostics()


class FakeStrategy:
    descriptor = FundRotationStrategyDescriptor(
        id="runner-native-fake",
        name="Runner Native Fake",
        description="native runner test double",
        interface_version="1.0",
        supported_universe=("cn_etf",),
        deterministic=True,
    )
    config_model = FakeConfig

    def __init__(self, session: FakeSession):
        self._session = session

    def resolve_requirements(self, config):
        return StrategyDataRequirements(
            required_datasets=("fund", "fact_fund_adj", "dim_fund"),
            required_fields=(
                "ts_code",
                "trade_date",
                "name",
                "list_date",
                "close",
                "amount",
                "adj_factor",
            ),
            warmup_trade_days=5,
            frequency="weekly",
            needs_benchmark=False,
        )

    def create_session(self, initialization, config):
        return self._session


class SpyNativeEngine:
    def __init__(self, *, cancel_during_execute: bool = False):
        self.cancel_during_execute = cancel_during_execute
        self.calls: list[dict[str, object]] = []

    def execute(self, request, *, should_cancel=None):
        self.calls.append({"request": request, "should_cancel": should_cancel})
        dates = tuple(request.evaluation_dates)
        if self.cancel_during_execute:
            assert should_cancel is not None
            assert should_cancel() is False
            request_cancelled = should_cancel()
            assert request_cancelled is True
            dates = dates[:1]
        ledger = _ledger()
        return NativeExecutionResult(
            ledger=ledger,
            executed_equity=pd.Series([1.0 + i * 0.01 for i in range(len(dates))], index=dates),
            trade_events=[{"ts_code": "A", "trade_date": dates[0], "filled": 100}],
            orders=[{"order_id": "ORDER-A", "ts_code": "A", "rule_version": "rule-A"}],
            positions_history=[{"trade_date": date, "holdings": []} for date in dates],
            ending_cash=99_000.0,
            ending_positions={"A": {"size": 100}},
            state=NativeExecutionState(cash=99_000.0, positions={"A": {"size": 100}}),
        )


class RaisingNativeEngine:
    def __init__(self, exc: Exception):
        self.exc = exc

    def execute(self, request, *, should_cancel=None):
        raise self.exc


class DelayedToken(CancellationToken):
    def __init__(self):
        super().__init__()
        self._checks = 0

    @property
    def is_cancelled(self):
        self._checks += 1
        return self._checks > 4


def _ledger() -> ExecutionLedger:
    return ExecutionLedger(
        parent_orders=(
            ParentOrderRecord(
                order_id="ORDER-A",
                decision_id="DECISION-20240112",
                ts_code="A",
                direction=OrderDirection.BUY,
                created_date="20240115",
                original_requested_quantity=100,
                cumulative_filled_quantity=100,
                remaining_quantity=0,
                quantity_basis_id="A:shares:1",
                status=ParentOrderStatus.FILLED,
                completed_date="20240115",
            ),
        ),
        attempts=(
            ExecutionAttemptRecord(
                attempt_id="ORDER-A-A1",
                order_id="ORDER-A",
                attempt_number=1,
                trade_date="20240115",
                requested_quantity=100,
                filled_quantity=100,
                unfilled_quantity=0,
                quantity_basis_id="A:shares:1",
                raw_price=10.0,
                executed_price=10.0,
                commission=0.0,
                explicit_fee=0.0,
                slippage_cost=0.0,
                participation_rate=0.01,
                status=AttemptStatus.FILLED,
            ),
        ),
        trades=(
            ExecutedTradeRecord(
                trade_id="ORDER-A-A1-T1",
                attempt_id="ORDER-A-A1",
                order_id="ORDER-A",
                ts_code="A",
                direction=OrderDirection.BUY,
                quantity=100,
                quantity_basis_id="A:shares:1",
                price=10.0,
                notional=1_000.0,
                commission=0.0,
                explicit_fee=0.0,
                slippage_cost=0.0,
                trade_date="20240115",
            ),
        ),
    )


def _market_frames():
    rows, adj = [], []
    for code in ("A", "B"):
        for date in MARKET_DATES:
            rows.append(
                {
                    "ts_code": code,
                    "trade_date": date,
                    "open": 10.0,
                    "close": 10.0,
                    "high": 10.1,
                    "low": 9.9,
                    "pre_close": 10.0,
                    "vol": 1_000_000,
                    "amount": 10_000_000.0,
                }
            )
            adj.append({"ts_code": code, "trade_date": date, "adj_factor": 1.0})
    return (
        pd.DataFrame(rows),
        pd.DataFrame(adj),
        pd.DataFrame(
            [
                {
                    "ts_code": "A",
                    "name": "ETF Alpha",
                    "list_date": "20200101",
                    "instrument_type": "domestic_equity_etf",
                },
                {
                    "ts_code": "B",
                    "name": "ETF Beta",
                    "list_date": "20200101",
                    "instrument_type": "domestic_equity_etf",
                },
            ]
        ),
    )


def _snapshot():
    return PinnedFundDataSnapshot(
        fund_version=1,
        fund_adj_version=1,
        dim_version=7,
        universe_codes=("A", "B"),
        trading_dates=tuple(MARKET_DATES),
        fingerprint="runner-native-test",
    )


def _decision(signal_date: str = "20240112") -> TargetWeightDecision:
    return TargetWeightDecision(
        decision_id=f"DECISION-{signal_date}",
        signal_date=signal_date,
        action=DecisionKind.SET_TARGETS,
        target_weights={"A": 1.0},
        cash_weight=0.0,
        quality_status=QualityStatus.VALID,
    )


def _rules():
    resolver = MarketRuleResolver(
        InMemoryPITMarketRuleSource(
            [
                {
                    "ts_code": code,
                    "instrument_type": "domestic_equity_etf",
                    "valid_from": "20231201",
                    "valid_to": None,
                    "known_from": "20240101T000000",
                    "snapshot_version": 7,
                    "revision_id": f"{code}-r1",
                    "revision_order": 1,
                    "settlement": "T+1",
                    "lot_size": 100,
                    "tick_size": 0.001,
                    "price_limit_pct": 0.10,
                    "short_allowed": False,
                    "currency": "CNY",
                    "source_record_id": f"{code}-src",
                    "source_id": "runner-test-rules",
                    "rule_version": f"{code}-rules-v1",
                }
                for code in ("A", "B")
            ]
        )
    )
    instruments = {
        code: FundInstrumentVersion(code, "domestic_equity_etf", f"{code}-src")
        for code in ("A", "B")
    }
    return resolver, instruments


def _run(*, engine=None, token=None, market_rule_resolver=None, market_rule_instruments=None):
    fund_daily, fund_adj, dim_fund = _market_frames()
    if market_rule_resolver is None and market_rule_instruments is None:
        market_rule_resolver, market_rule_instruments = _rules()
    runner = FundRotationBacktestRunner(
        fund_daily,
        fund_adj,
        dim_fund,
        execution_engine=engine,
        market_rule_resolver=market_rule_resolver,
        market_rule_instruments=market_rule_instruments,
        market_rule_mode=PITQueryMode.AS_WAS_KNOWN,
        run_id="runner-native-run",
    )
    return runner.run(
        strategy=FakeStrategy(FakeSession({"20240112": _decision()})),
        config=FakeConfig(),
        snapshot=_snapshot(),
        evaluation=EvaluationContext.from_range(MARKET_DATES, "20240115", "20240131"),
        execution=ExecutionConfig(initial_capital=100_000, adv_min_observations=3),
        cancellation=token or CancellationToken(),
    )


def test_spy_native_engine_is_called_once_and_its_result_is_returned():
    engine = SpyNativeEngine()

    result = _run(engine=engine)

    assert result.status is SubRunStatus.SUCCEEDED
    assert len(engine.calls) == 1
    request = engine.calls[0]["request"]
    assert request.targets == {"20240112": {"A": 1.0}}
    assert request.evaluation_dates == EVALUATION_DATES
    assert request.knowledge_cutoff == "20240112"
    assert request.snapshot_version == 7
    assert request.run_id == "runner-native-run"
    assert request.decision_ids == {"20240112": "DECISION-20240112"}
    assert list(result.executed_equity.index) == list(EVALUATION_DATES)
    assert result.trade_events == [{"ts_code": "A", "trade_date": "20240115", "filled": 100}]
    assert result.orders == [{"order_id": "ORDER-A", "ts_code": "A", "rule_version": "rule-A"}]
    assert result.positions_history[0]["trade_date"] == "20240115"


def test_runner_native_formal_path_does_not_touch_legacy_loop_or_pipeline_adapter(monkeypatch):
    def forbidden(*args, **kwargs):  # pragma: no cover - assertion is the call itself
        raise AssertionError("legacy formal execution path must not be called")

    monkeypatch.setattr(runner_module, "run_execution_loop", forbidden)
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

    assert result.status is SubRunStatus.SUCCEEDED
    assert result.execution_diagnostics["orders"]["order_count"] == 1
    assert "legacy_result" not in result.execution_diagnostics


def test_missing_explicit_pit_rule_inputs_fail_closed_without_engine_call():
    engine = SpyNativeEngine()
    result = _run(
        engine=engine,
        market_rule_resolver=None,
        market_rule_instruments={},
    )

    assert result.status is SubRunStatus.FAILED
    assert result.error_code == "EXECUTION_RULES_UNAVAILABLE"
    assert "PIT market rule resolver" in result.error_message
    assert engine.calls == []


def test_native_engine_missing_rule_exception_maps_to_execution_rules_unavailable():
    resolver = MarketRuleResolver(InMemoryPITMarketRuleSource([]))
    instruments = {"A": FundInstrumentVersion("A", "domestic_equity_etf", "A-src")}

    result = _run(
        engine=None,
        market_rule_resolver=resolver,
        market_rule_instruments=instruments,
    )

    assert result.status is SubRunStatus.FAILED
    assert result.error_code == "EXECUTION_RULES_UNAVAILABLE"
    assert "UNKNOWN_EXECUTION_RULE" in result.error_message


@pytest.mark.parametrize("exc", [ValueError("bad size"), TypeError("bad request")])
def test_native_engine_general_errors_are_not_reported_as_missing_rules(exc):
    result = _run(engine=RaisingNativeEngine(exc))

    assert result.status is SubRunStatus.FAILED
    assert result.error_code == "ENGINE_EXECUTION_ERROR"
    assert str(exc) in result.error_message


def test_diagnostics_are_computed_directly_from_native_ledger():
    result = _run(engine=SpyNativeEngine())

    assert result.status is SubRunStatus.SUCCEEDED
    assert result.execution_diagnostics["metric_contract_version"] == "execution_diagnostics_v2"
    assert result.execution_diagnostics["orders"]["order_count"] == 1
    assert result.execution_diagnostics["trades"]["executed_trade_count"] == 1
    assert "legacy_result" not in result.execution_diagnostics
    assert result.execution_diagnostics["execution_identity"]["rule_versions"] == ["rule-A"]
    assert result.execution_diagnostics["universe"] == {
        "quality_status": "RESEARCH_ONLY_UNVERIFIED_UNIVERSE",
        "reason_code": "PIT_MASTER_MISSING",
        "details": "snapshot does not provide PIT fund master and no PIT resolver was injected",
    }


def test_exact_evaluation_calendar_and_cancellation_callback_are_preserved():
    engine = SpyNativeEngine(cancel_during_execute=True)
    result = _run(engine=engine, token=DelayedToken())

    assert result.status is SubRunStatus.CANCELED
    assert result.error_code == "CANCELED"
    assert engine.calls[0]["request"].evaluation_dates == EVALUATION_DATES
    assert callable(engine.calls[0]["should_cancel"])
    assert list(result.executed_equity.index) == ["20240115"]
