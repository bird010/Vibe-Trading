"""Regression coverage for strict decision diagnostics and e50dd681833f."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
from pydantic import BaseModel

from backtest.fund_rotation.contracts import (
    DecisionKind,
    FundRotationStrategyDescriptor,
    QualityStatus,
    StrategyArtifact,
    StrategyContractViolation,
    StrategyDataRequirements,
    StrategyDiagnostics,
    TargetWeightDecision,
    validate_diagnostics,
    validate_target_decision,
)
from backtest.fund_rotation.evaluation import EvaluationContext
from backtest.fund_rotation.momentum import (
    compute_cluster_momentum,
    select_top_clusters,
)
from backtest.fund_rotation.runner import (
    CancellationToken,
    ExecutionConfig,
    FundRotationBacktestRunner,
    SubRunStatus,
)
from backtest.fund_rotation.strategies.correlation_representative.strategy import (
    _momentum_diagnostics,
)
from src.stockpred.fund_rotation import batch_child_runtime as child_runtime_module
from src.stockpred.fund_rotation.batch_child_runtime import BatchChildRuntime
from src.stockpred.fund_rotation.data_snapshot import PinnedFundDataSnapshot


def test_cluster_with_missing_week_keeps_internal_nan_sentinel():
    weekly_returns = pd.DataFrame(
        {
            "A": [0.02, np.nan],
            "B": [0.01, np.nan],
        },
        index=["20240105", "20240112"],
    )

    momentum = compute_cluster_momentum(
        weekly_returns,
        {"A": 8, "B": 8},
        momentum_window=2,
    )

    assert np.isnan(momentum[8])


def test_momentum_diagnostics_maps_non_finite_to_null_with_string_keys():
    values, unavailable = _momentum_diagnostics(
        {1: 0.05, 8: float("nan")}
    )

    assert values == {"1": 0.05, "8": None}
    assert unavailable == [8]
    assert json.loads(json.dumps(values, allow_nan=False))["8"] is None


def test_select_top_clusters_excludes_all_non_finite_values():
    selected = select_top_clusters(
        {
            1: 0.05,
            2: float("nan"),
            3: float("inf"),
            4: float("-inf"),
        },
        top_n=3,
        threshold=0.0,
    )

    assert selected == [1]


def test_select_top_clusters_rejects_non_numeric_values():
    with pytest.raises(TypeError, match="cluster 8 must be numeric"):
        select_top_clusters(  # type: ignore[arg-type]
            {1: 0.05, 8: None},
            top_n=3,
            threshold=0.0,
        )


def test_validate_diagnostics_rejects_non_string_nested_keys():
    with pytest.raises(
        StrategyContractViolation,
        match="non-string mapping key 8",
    ):
        validate_diagnostics({"momentum": {8: 0.1}})  # type: ignore[dict-item]


def test_invalid_decision_diagnostics_are_checked_before_early_return():
    decision = TargetWeightDecision(
        decision_id="invalid-nan",
        signal_date="20240112",
        action=DecisionKind.INVALID,
        reason_code="TEST_INVALID",
        diagnostics={"momentum": {"8": float("nan")}},
    )

    with pytest.raises(
        StrategyContractViolation,
        match=r"diagnostics\.momentum\.8 contains a non-finite float",
    ):
        validate_target_decision(decision, frozenset(), frozenset())


def test_hold_decision_diagnostics_are_checked_before_early_return():
    decision = TargetWeightDecision(
        decision_id="hold-nan",
        signal_date="20240112",
        action=DecisionKind.HOLD_TARGETS,
        diagnostics={"nested": [{"value": float("inf")}]},
    )

    with pytest.raises(
        StrategyContractViolation,
        match=r"diagnostics\.nested\[0\]\.value",
    ):
        validate_target_decision(decision, frozenset(), frozenset())


class _RunnerConfig(BaseModel):
    pass


class _NanInvalidSession:
    def scheduled_dates(self, calendar, decision_start_date, evaluation_end_date):
        return (decision_start_date,)

    def evaluate(self, context):
        return TargetWeightDecision(
            decision_id="runner-invalid-nan",
            signal_date=context.signal_date,
            action=DecisionKind.INVALID,
            reason_code="SHOULD_NOT_WIN",
            diagnostics={"momentum": {"8": float("nan")}},
        )

    def finalize(self):
        raise AssertionError("finalize must not run after a contract violation")


class _NanInvalidStrategy:
    descriptor = FundRotationStrategyDescriptor(
        id="nan-invalid",
        name="NaN invalid",
        description="test",
        interface_version="1.0",
        supported_universe=("etf",),
        deterministic=True,
    )
    config_model = _RunnerConfig

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
        return _NanInvalidSession()


def _runner_market_frames():
    dates = (
        pd.bdate_range("2024-01-02", "2024-02-02")
        .strftime("%Y%m%d")
        .tolist()
    )
    rows: list[dict] = []
    adjustments: list[dict] = []
    for code in ("A", "B"):
        for date in dates:
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
            adjustments.append(
                {"ts_code": code, "trade_date": date, "adj_factor": 1.0}
            )
    dimensions = pd.DataFrame(
        [
            {"ts_code": "A", "name": "ETF A", "list_date": "20200101"},
            {"ts_code": "B", "name": "ETF B", "list_date": "20200101"},
        ]
    )
    return dates, pd.DataFrame(rows), pd.DataFrame(adjustments), dimensions


def test_runner_fails_before_execution_for_invalid_nan_diagnostics():
    dates, fund_daily, fund_adj, dim_fund = _runner_market_frames()
    runner = FundRotationBacktestRunner(fund_daily, fund_adj, dim_fund)
    result = runner.run(
        strategy=_NanInvalidStrategy(),
        config=_RunnerConfig(),
        snapshot=PinnedFundDataSnapshot(
            fund_version=1,
            fund_adj_version=1,
            dim_version=1,
            universe_codes=("A", "B"),
            trading_dates=tuple(dates),
            fingerprint="strict-diagnostics",
        ),
        evaluation=EvaluationContext.from_range(
            dates,
            "20240115",
            "20240131",
        ),
        execution=ExecutionConfig(
            initial_capital=100_000,
            adv_min_observations=3,
        ),
        cancellation=CancellationToken(),
        decision_start_date="20240112",
    )

    assert result.status is SubRunStatus.FAILED
    assert result.error_code == StrategyContractViolation.code
    assert "diagnostics.momentum.8" in result.error_message
    assert result.decisions == ()
    assert result.executed_equity.empty
    assert result.trade_events == []


class _CapturingPublisher:
    def __init__(self) -> None:
        self.artifacts: dict[str, tuple[object, str]] = {}

    def publish(self, artifact, *, producer="common"):
        self.artifacts[artifact.role] = (artifact.payload, producer)
        return Path(artifact.role)

    def index_external(self, role):
        return Path(role)

    def artifact_index(self):
        return {}

    def finalize(self, **kwargs):
        return kwargs


def _publication_inputs(diagnostics: dict[str, object]):
    decision = TargetWeightDecision(
        decision_id="publish-decision",
        signal_date="20240112",
        action=DecisionKind.HOLD_TARGETS,
        diagnostics=diagnostics,
    )
    result = SimpleNamespace(
        decisions=(decision,),
        weekly_targets={},
        orders=[],
        trade_events=[],
        positions_history=[],
        executed_equity=pd.Series([1.0], index=["20240115"]),
        benchmark_equity={},
        strategy_metrics={},
        benchmark_metrics={},
        execution_diagnostics={},
        quality_status=QualityStatus.VALID.value,
        diagnostics=StrategyDiagnostics(
            artifacts=(
                StrategyArtifact(
                    role="decisions",
                    media_type="application/json",
                    payload=[{"diagnostics": diagnostics}],
                ),
            )
        ),
        error_code="",
        error_message="",
    )
    identity = SimpleNamespace(
        variant_key="variant-a",
        strategy_id="correlation_representative",
        implementation_hash="strategy-hash",
        resolved_config_hash="config-hash",
        resolved_requirements_hash="requirements-hash",
        resolved_config={},
        resolved_requirements={},
        label="",
    )
    request = SimpleNamespace(
        schema_version="1",
        mode="RESEARCH_ONLY",
        evaluation_start_date="20240115",
        evaluation_end_date="20240115",
        execution=SimpleNamespace(model_dump=lambda **kwargs: {}),
    )
    plan = {
        "identity": identity,
        "run_id": "strict-publication",
        "data_start": "20240102",
        "decision_start_date": "20240112",
        "anchor_decision_date": "20240112",
    }
    snapshot = PinnedFundDataSnapshot(
        fund_version=1,
        fund_adj_version=1,
        dim_version=1,
        universe_codes=("A",),
        trading_dates=("20240115",),
        fingerprint="snapshot-hash",
    )
    evaluation = SimpleNamespace(
        trading_dates=(pd.Timestamp("2024-01-15"),)
    )
    return result, identity, request, plan, snapshot, evaluation


def _runtime(tmp_path, publisher, monkeypatch):
    descriptor = FundRotationStrategyDescriptor(
        id="correlation_representative",
        name="Representative",
        description="test",
        interface_version="1.0",
        supported_universe=("etf",),
        deterministic=True,
    )
    registered = SimpleNamespace(
        descriptor=descriptor,
        implementation_snapshot=SimpleNamespace(
            source_files=(),
            file_hashes={},
        ),
    )
    catalog = SimpleNamespace(require=lambda strategy_id: registered)
    runtime = BatchChildRuntime(tmp_path, catalog, "framework-hash")
    monkeypatch.setattr(
        child_runtime_module,
        "ArtifactPublisher",
        lambda run_dir: publisher,
    )
    monkeypatch.setattr(
        child_runtime_module,
        "compute_file_checksum",
        lambda path: "checksum",
    )
    monkeypatch.setattr(runtime, "record_stage", lambda **kwargs: None)
    return runtime


def test_publication_writes_null_to_csv_and_strategy_json(tmp_path, monkeypatch):
    publisher = _CapturingPublisher()
    runtime = _runtime(tmp_path, publisher, monkeypatch)
    result, _, request, plan, snapshot, evaluation = _publication_inputs(
        {"momentum": {"8": None}}
    )

    runtime.publish_result(
        batch_id="batch-a",
        request=request,
        plan=plan,
        snapshot=snapshot,
        evaluation=evaluation,
        result=result,
    )

    decisions_frame = publisher.artifacts["target_decisions"][0]
    assert isinstance(decisions_frame, pd.DataFrame)
    serialized = decisions_frame.iloc[0]["diagnostics"]
    assert json.loads(serialized) == {"momentum": {"8": None}}

    strategy_payload, producer = publisher.artifacts["decisions"]
    assert producer == "correlation_representative"
    assert strategy_payload == [
        {"diagnostics": {"momentum": {"8": None}}}
    ]


def test_publication_rejects_nan_even_if_contract_was_bypassed(
    tmp_path,
    monkeypatch,
):
    publisher = _CapturingPublisher()
    runtime = _runtime(tmp_path, publisher, monkeypatch)
    result, _, request, plan, snapshot, evaluation = _publication_inputs(
        {"momentum": {"8": float("nan")}}
    )

    with pytest.raises(ValueError, match="Out of range float values"):
        runtime.publish_result(
            batch_id="batch-a",
            request=request,
            plan=plan,
            snapshot=snapshot,
            evaluation=evaluation,
            result=result,
        )
