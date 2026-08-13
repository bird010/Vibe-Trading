"""Runner contract integration tests for formal execution diagnostics and PIT quality."""

import pandas as pd
import pytest
from pydantic import BaseModel

from backtest.fund_rotation.contracts import (
    DecisionKind,
    FundRotationStrategyDescriptor,
    QualityStatus,
    StrategyDataRequirements,
    StrategyDiagnostics,
    TargetWeightDecision,
)
from backtest.fund_rotation.evaluation import EvaluationContext
from backtest.fund_rotation.runner import (
    CancellationToken,
    ExecutionConfig,
    FundRotationBacktestRunner,
    SubRunStatus,
)
from backtest.fund_rotation.pit_universe import (
    FundRotationPITUniverseAdapter,
    PITQueryMode,
    UniversePolicy,
)
from src.stockpred.fund_rotation.data_snapshot import PinnedFundDataSnapshot


MARKET_DATES = pd.bdate_range("2024-01-02", "2024-01-31").strftime("%Y%m%d").tolist()


class FakeConfig(BaseModel):
    pass


class FakeSession:
    def __init__(self, first_weights: dict[str, float]):
        self._first_weights = dict(first_weights)

    def scheduled_dates(self, calendar, simulation_start_date, evaluation_end_date):
        return ("20240112",)

    def evaluate(self, context):
        return TargetWeightDecision(
            decision_id=f"decision-{context.signal_date}",
            signal_date=context.signal_date,
            action=DecisionKind.SET_TARGETS,
            target_weights=dict(self._first_weights),
            cash_weight=1.0 - sum(self._first_weights.values()),
            quality_status=QualityStatus.VALID,
        )

    def finalize(self):
        return StrategyDiagnostics()


class FakeStrategy:
    descriptor = FundRotationStrategyDescriptor(
        id="contract-fake",
        name="Contract Fake",
        description="runner contract test double",
        interface_version="1.0",
        supported_universe=("cn_etf",),
        deterministic=True,
    )
    config_model = FakeConfig

    def __init__(self, first_weights: dict[str, float]):
        self._session = FakeSession(first_weights)

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


class StaticPITResolver:
    def __init__(self, universe_codes: tuple[str, ...], quality_status: str):
        self.universe_codes = tuple(universe_codes)
        self.quality_status = quality_status
        self.calls: list[dict[str, object]] = []

    def resolve_universe(self, *, snapshot, signal_date, knowledge_cutoff, fallback_universe):
        self.calls.append(
            {
                "snapshot": snapshot,
                "signal_date": signal_date,
                "knowledge_cutoff": knowledge_cutoff,
                "fallback_universe": tuple(sorted(fallback_universe)),
            }
        )
        return {
            "universe_codes": self.universe_codes,
            "quality_status": self.quality_status,
            "diagnostics": {"resolver": "static-test"},
        }


class FormalResolverSpy:
    def __init__(self):
        self.calls = []

    def resolve(self, **kwargs):
        self.calls.append(kwargs)
        return {"universe_codes": ("A",), "quality_status": "VERIFIED"}


def test_formal_pit_adapter_forwards_universe_resolver_context():
    resolver = FormalResolverSpy()
    adapter = FundRotationPITUniverseAdapter(
        resolver,
        strategy_policy=UniversePolicy(asset_classes=frozenset({"ETF"})),
        causal_view_factory=lambda **kwargs: kwargs["signal_date"],
        snapshot_version=7,
        mode=PITQueryMode.AS_WAS_KNOWN,
    )

    result = adapter.resolve_universe(
        snapshot=object(),
        signal_date="20240112",
        knowledge_cutoff="20240112",
        fallback_universe=frozenset({"A", "B"}),
    )

    assert result["universe_codes"] == ("A",)
    assert resolver.calls[0]["strategy_policy"].asset_classes == frozenset({"ETF"})
    assert resolver.calls[0]["causal_view"] == "20240112"
    assert resolver.calls[0]["snapshot_version"] == 7
    assert resolver.calls[0]["mode"] is PITQueryMode.AS_WAS_KNOWN


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
                {"ts_code": "A", "name": "ETF Alpha", "list_date": "20200101"},
                {"ts_code": "B", "name": "ETF Beta", "list_date": "20200101"},
            ]
        ),
    )


def _snapshot():
    return PinnedFundDataSnapshot(
        fund_version=1,
        fund_adj_version=1,
        dim_version=1,
        universe_codes=("A", "B"),
        trading_dates=tuple(MARKET_DATES),
        fingerprint="contract-test",
    )


def _run(first_weights, *, pit_universe_resolver=None):
    fund_daily, fund_adj, dim_fund = _market_frames()
    return FundRotationBacktestRunner(
        fund_daily,
        fund_adj,
        dim_fund,
        pit_universe_resolver=pit_universe_resolver,
    ).run(
        strategy=FakeStrategy(first_weights),
        config=FakeConfig(),
        snapshot=_snapshot(),
        evaluation=EvaluationContext.from_range(MARKET_DATES, "20240115", "20240131"),
        execution=ExecutionConfig(initial_capital=100_000, adv_min_observations=3),
        cancellation=CancellationToken(),
    )


def test_runner_adapts_pipeline_result_to_execution_diagnostics_v2_contract():
    result = _run({"A": 1.0})

    assert result.status is SubRunStatus.SUCCEEDED
    assert result.quality_status == "RESEARCH_ONLY_UNVERIFIED_UNIVERSE"
    assert result.execution_diagnostics["metric_contract_version"] == "execution_diagnostics_v2"
    assert result.execution_diagnostics["orders"]["order_count"] == 1
    assert result.execution_diagnostics["trades"]["executed_trade_count"] >= 1
    assert "turnover" not in result.execution_diagnostics
    assert "fill_rate" not in result.execution_diagnostics
    assert result.execution_diagnostics["legacy_result"]["turnover"] > 0
    assert "diagnostics_difference" in result.execution_diagnostics
    assert result.execution_diagnostics["universe"] == {
        "quality_status": "RESEARCH_ONLY_UNVERIFIED_UNIVERSE",
        "reason_code": "PIT_MASTER_MISSING",
        "details": "snapshot does not provide PIT fund master and no PIT resolver was injected",
    }


def test_runner_uses_injected_pit_universe_resolver_for_formal_quality():
    resolver = StaticPITResolver(("A",), "VERIFIED")
    result = _run({"A": 1.0}, pit_universe_resolver=resolver)

    assert result.status is SubRunStatus.SUCCEEDED
    assert result.quality_status == "VALID"
    assert resolver.calls
    assert resolver.calls[0]["signal_date"] == "20240112"
    assert resolver.calls[0]["fallback_universe"] == ("A", "B")
    assert result.execution_diagnostics["universe"] == {
        "quality_status": "VERIFIED",
        "reason_code": "",
        "details": "",
        "resolver": "static-test",
    }


def test_runner_rejects_targets_outside_injected_pit_universe():
    resolver = StaticPITResolver(("A",), "VERIFIED")
    result = _run({"B": 1.0}, pit_universe_resolver=resolver)

    assert result.status is SubRunStatus.FAILED
    assert result.error_code == "STRATEGY_CONTRACT_VIOLATION"
    assert "not in the eligible" in result.error_message
