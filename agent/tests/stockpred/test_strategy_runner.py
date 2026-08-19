from __future__ import annotations

import pandas as pd
import pytest
from types import SimpleNamespace

from backtest.stockpred_strategy.runner import StockPredStrategyBacktestRunner, StrategyScreeningSession
from src.stockpred.contracts import StockPredDataError
from src.stockpred import strategy_execution
from src.stockpred.strategy_execution import StrategyReportExecutor
from src.stockpred.strategies.contracts import (
    StrategyBatchRequest,
    StrategyBacktestConfig,
    StrategyDescriptor,
    StrategyScore,
    StrategySnapshot,
    StrategySourceFile,
)


class _Gateway:
    dates = ["20250102", "20250103", "20250106", "20250107"]

    def trade_dates(self, start: str, end: str) -> list[str]:
        return [date for date in self.dates if start <= date <= end]

    def prices(self, start: str, end: str, codes: list[str]) -> pd.DataFrame:
        return pd.DataFrame(
            [{"ts_code": code, "trade_date": date, "open": 10.0, "high": 11.0, "low": 9.0, "close": 10.0, "pct_chg": 0.0, "vol": 100_000.0, "amount": 1_000_000.0} for code in codes for date in self.dates if start <= date <= end]
        )

    def adjustment_factors(self, start: str, end: str, codes: list[str]) -> pd.DataFrame:
        return self.prices(start, end, codes)[["ts_code", "trade_date"]].assign(adj_factor=1.0)

    def stock_limits(self, start: str, end: str, codes: list[str]) -> pd.DataFrame:
        return self.prices(start, end, codes)[["ts_code", "trade_date"]].assign(up_limit=100.0, down_limit=0.01)


class _Strategy:
    def evaluate(self, eval_date: str) -> StrategyScore:
        return StrategyScore(pd.DataFrame({"ts_code": ["000001.SZ", "000002.SZ"], "score": [2.0, 1.0], "trade_date": [eval_date, eval_date]}))


def _config() -> StrategyBacktestConfig:
    snapshot = StrategySnapshot(
        descriptor=StrategyDescriptor(id="alpha101_1", name="Alpha", kind="alpha_zoo", zoo="alpha101"),
        source_files=(StrategySourceFile(path="alpha.py", sha256="a" * 64, content="x"),),
        strategy_version="b" * 64,
        python_version="3.11",
    )
    return StrategyBacktestConfig(start="2025-01-02", end="2025-01-07", batch_id="batch_1", comparison_key="c" * 64, strategy_snapshot=snapshot, top_n=2, eval_step=1, forward_days=1)


def test_strategy_config_defaults_to_total_return_benchmark() -> None:
    assert _config().benchmark_code == "H00300.CSI"


def test_runner_executes_any_score_adapter_with_shared_execution() -> None:
    result = StockPredStrategyBacktestRunner(_Gateway(), _Strategy()).run(_config())

    assert result.strategy_id == "alpha101_1"
    assert result.metrics["scheduled_evaluations"] == 4.0
    assert result.metrics["valid_evaluations"] == 4.0
    assert not result.trades.empty
    assert not result.equity.empty
    assert result.ohlcv == {}


def test_screening_session_finalizes_like_runner() -> None:
    gateway = _Gateway()
    runner = StockPredStrategyBacktestRunner(gateway, _Strategy())
    session = StrategyScreeningSession(runner, _config())

    for date in gateway.dates:
        session.evaluate(date)

    result = session.finalize()

    assert result.metrics == pytest.approx(runner.run(_config()).metrics, abs=1e-12)


def test_strategy_report_executor_maps_timeout_to_transient_io(tmp_path, monkeypatch) -> None:
    executor = StrategyReportExecutor(tmp_path / "runs", tmp_path)
    descriptor = _config().strategy_snapshot.descriptor
    request = StrategyBatchRequest(start="2025-01-02", end="2025-01-07", strategy_ids=(descriptor.id,))
    monkeypatch.setattr(executor, "_manifest", lambda *_: object())
    monkeypatch.setattr(executor, "_sources", lambda *_: {})
    monkeypatch.setattr(strategy_execution, "snapshot_strategy", lambda *_args, **_kwargs: _config().strategy_snapshot)

    def timeout_gateway(*_args, **_kwargs):  # noqa: ANN002
        raise TimeoutError("upstream timed out")

    monkeypatch.setattr(strategy_execution, "StockPredDataGateway", timeout_gateway)

    with pytest.raises(StockPredDataError) as error:
        executor(descriptor, request, "batch_1", "c" * 64)

    assert error.value.code == "STOCKPRED_TRANSIENT_IO"


def test_strategy_report_executor_uses_alpha_batch_coordinator(tmp_path, monkeypatch) -> None:
    executor = StrategyReportExecutor(tmp_path / "runs", tmp_path)
    request = StrategyBatchRequest(start="2025-01-02", end="2025-01-07", strategy_ids=("alpha101_1", "alpha101_2"))
    descriptors = [
        StrategyDescriptor(id="alpha101_1", name="Alpha 1", kind="alpha_zoo", zoo="alpha101"),
        StrategyDescriptor(id="alpha101_2", name="Alpha 2", kind="alpha_zoo", zoo="alpha101"),
    ]
    calls: list[tuple[str, ...]] = []

    def snapshot(descriptor, *_args, **_kwargs):  # noqa: ANN001
        return StrategySnapshot(
            descriptor=descriptor,
            source_files=(StrategySourceFile(path="alpha.py", sha256="a" * 64, content="x"),),
            strategy_version="b" * 64,
            python_version="3.11",
        )

    class _Coordinator:
        def __init__(self, _gateway, _registry, configs, **_kwargs):  # noqa: ANN001
            calls.append(tuple(config.strategy_snapshot.descriptor.id for config in configs))
            self.context = SimpleNamespace(cache_metrics=lambda: {"cache_hits": 3, "cache_misses": 2})

        def run(self):
            return {strategy_id: SimpleNamespace(metrics={"sharpe": 1.0}) for strategy_id in calls[-1]}

    monkeypatch.setattr(executor, "_manifest", lambda *_: object())
    monkeypatch.setattr(executor, "_sources", lambda *_: {})
    monkeypatch.setattr(strategy_execution, "snapshot_strategy", snapshot)
    monkeypatch.setattr(strategy_execution, "StockPredDataGateway", lambda *_: SimpleNamespace(read_metrics=lambda: {"rows_read": 42}))
    monkeypatch.setattr(strategy_execution, "AlphaBatchScreeningCoordinator", _Coordinator)
    monkeypatch.setattr(strategy_execution, "write_screening_artifacts", lambda *_: None)

    results = executor.run_alpha_batch(descriptors, request, "batch_1", "c" * 64, object())

    assert calls == [("alpha101_1", "alpha101_2")]
    assert set(results) == {"alpha101_1", "alpha101_2"}
    # phase_metrics.json is now written by worker processes (not the main process).
    # The mock coordinator does not spawn real workers, so we only verify results.
    for strategy_id in ("alpha101_1", "alpha101_2"):
        run_name, metrics = results[strategy_id]
        assert metrics == {"sharpe": 1.0}


def test_strategy_report_executor_isolates_alpha_prepare_failure(tmp_path, monkeypatch) -> None:
    executor = StrategyReportExecutor(tmp_path / "runs", tmp_path)
    request = StrategyBatchRequest(start="2025-01-02", end="2025-01-07", strategy_ids=("alpha101_1", "alpha101_2"))
    descriptors = [
        StrategyDescriptor(id="alpha101_1", name="Alpha 1", kind="alpha_zoo", zoo="alpha101"),
        StrategyDescriptor(id="alpha101_2", name="Alpha 2", kind="alpha_zoo", zoo="alpha101"),
    ]

    def snapshot(descriptor, *_args, **_kwargs):  # noqa: ANN001
        return StrategySnapshot(
            descriptor=descriptor,
            source_files=(StrategySourceFile(path="alpha.py", sha256="a" * 64, content="x"),),
            strategy_version="b" * 64,
            python_version="3.11",
        )

    class _Coordinator:
        def __init__(self, _gateway, _registry, configs, **_kwargs):  # noqa: ANN001
            self.strategy_ids = [config.strategy_snapshot.descriptor.id for config in configs]

        def run(self):
            return {strategy_id: SimpleNamespace(metrics={"sharpe": 1.0}) for strategy_id in self.strategy_ids}

    monkeypatch.setattr(executor, "_manifest", lambda *_: object())
    monkeypatch.setattr(executor, "_sources", lambda *_: {})
    monkeypatch.setattr(strategy_execution, "snapshot_strategy", snapshot)
    monkeypatch.setattr(strategy_execution, "StockPredDataGateway", lambda *_: object())
    monkeypatch.setattr(strategy_execution, "AlphaBatchScreeningCoordinator", _Coordinator)
    monkeypatch.setattr(strategy_execution, "write_screening_artifacts", lambda *_: None)
    original_transition = executor.runs.transition
    failed_once = False

    def transition(run_dir, phase):  # noqa: ANN001
        nonlocal failed_once
        if phase == "RUNNING" and not failed_once:
            failed_once = True
            raise ValueError("broken run transition")
        original_transition(run_dir, phase)

    monkeypatch.setattr(executor.runs, "transition", transition)

    results = executor.run_alpha_batch(descriptors, request, "batch_1", "c" * 64, object())

    assert isinstance(results["alpha101_1"], ValueError)
    assert results["alpha101_2"][1] == {"sharpe": 1.0}
    states = [executor.runs.read(run_dir) for run_dir in sorted(d for d in (tmp_path / "runs").glob("**/strategy_????????T??????_*") if d.is_dir())]
    assert {state["phase"] for state in states} == {"FAILED", "SUCCEEDED"}


# ---------------------------------------------------------------------------
# Mode contract tests
# ---------------------------------------------------------------------------


def test_batch_request_mode_defaults_to_parity() -> None:
    request = StrategyBatchRequest(start="2025-01-01", end="2025-03-31", strategy_ids=("alpha101_1",))
    assert request.mode == "parity"


def test_batch_request_accepts_research_mode() -> None:
    request = StrategyBatchRequest(start="2025-01-01", end="2025-03-31", strategy_ids=("alpha101_1",), mode="research")
    assert request.mode == "research"


def test_backtest_config_mode_defaults_to_parity() -> None:
    config = _config()
    assert config.mode == "parity"


def test_backtest_config_accepts_research_mode() -> None:
    snapshot = StrategySnapshot(
        descriptor=StrategyDescriptor(id="alpha101_1", name="Alpha", kind="alpha_zoo", zoo="alpha101"),
        source_files=(StrategySourceFile(path="alpha.py", sha256="a" * 64, content="x"),),
        strategy_version="b" * 64,
        python_version="3.11",
    )
    config = StrategyBacktestConfig(
        start="2025-01-02", end="2025-01-07", batch_id="batch_1",
        comparison_key="c" * 64, strategy_snapshot=snapshot,
        top_n=2, eval_step=1, forward_days=1, mode="research",
    )
    assert config.mode == "research"
