from __future__ import annotations

import importlib
import os
from collections import Counter
from contextlib import contextmanager

import pandas as pd
import pytest

import src.stockpred.batch_screening as batch_screening
from backtest.stockpred_strategy.runner import StockPredStrategyBacktestRunner
from backtest.stockpred.cohort.engine import CohortRunResult
from backtest.stockpred.cohort.engine import cohort_config_from_strategy_config
from src.stockpred.batch_screening import AlphaBatchScreeningCoordinator
from src.stockpred.strategies.adapters import AlphaZooStrategyAdapter
from src.stockpred.strategies.contracts import (
    StrategyBacktestConfig,
    StrategyDescriptor,
    StrategySnapshot,
    StrategySourceFile,
)
from src.stockpred.strategies.panel import StockPredPanelBuilder


def test_default_process_worker_limit_is_eight(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("STOCKPRED_BATCH_WORKERS", raising=False)

    reloaded = importlib.reload(batch_screening)

    assert reloaded._EVAL_PROCESS_WORKERS == min(8, os.cpu_count() or 4)


class _Gateway:
    def __init__(self) -> None:
        self.dates = ["20250102", "20250103", "20250106", "20250107", "20250108"]
        self.price_ends: list[str] = []
        self.trade_dates_calls = 0

    def trade_dates(self, start: str, end: str) -> list[str]:
        self.trade_dates_calls += 1
        return [date for date in self.dates if start <= date <= end]

    def stock_dimension(self) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "ts_code": "000001.SZ",
                    "name": "Ping An",
                    "industry": "Bank",
                    "list_date": "20200101",
                    "delist_date": "",
                    "list_status": "L",
                    "exchange": "SZSE",
                    "market": "main",
                },
                {
                    "ts_code": "000002.SZ",
                    "name": "Vanke",
                    "industry": "Property",
                    "list_date": "20200101",
                    "delist_date": "",
                    "list_status": "L",
                    "exchange": "SZSE",
                    "market": "main",
                },
            ]
        )

    def name_history(self) -> pd.DataFrame:
        return pd.DataFrame(columns=["ts_code", "security_name", "effective_from", "effective_to", "ann_date", "change_reason"])

    def industry_history(self) -> pd.DataFrame:
        return pd.DataFrame(columns=["ts_code", "industry_code", "industry_name", "level", "effective_from", "effective_to", "source"])

    def prices(self, start: str, end: str, codes: list[str]) -> pd.DataFrame:
        self.price_ends.append(end)
        return pd.DataFrame(
            [
                {
                    "ts_code": code,
                    "trade_date": date,
                    "open": 10.0 + index,
                    "high": 11.0 + index,
                    "low": 9.0 + index,
                    "close": 10.0 + index + (1.0 if code == "000001.SZ" else 0.0),
                    "pct_chg": 0.0,
                    "vol": 100_000.0,
                    "amount": 1_000_000.0,
                }
                for index, date in enumerate(self.dates)
                if start <= date <= end
                for code in codes
            ]
        )

    def adjustment_factors(self, start: str, end: str, codes: list[str]) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {"ts_code": code, "trade_date": date, "adj_factor": 1.0}
                for date in self.dates
                if start <= date <= end
                for code in codes
            ]
        )

    def stock_limits(self, start: str, end: str, codes: list[str]) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {"ts_code": code, "trade_date": date, "up_limit": 100.0, "down_limit": 0.01}
                for date in self.dates
                if start <= date <= end
                for code in codes
            ]
        )


class _Registry:
    def compute(self, alpha_id: str, panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
        if alpha_id == "alpha101_1":
            return panel["close"]
        return panel["close"] * 2.0


class _RecordingTimer:
    def __init__(self) -> None:
        self.phases: list[str] = []

    @contextmanager
    def phase(self, name: str):
        self.phases.append(name)
        yield


def _descriptor(strategy_id: str, warmup: int) -> StrategyDescriptor:
    return StrategyDescriptor(
        id=strategy_id,
        name=strategy_id,
        kind="alpha_zoo",
        zoo="alpha101",
        columns_required=("close",),
        min_warmup_bars=warmup,
    )


def _config(descriptor: StrategyDescriptor) -> StrategyBacktestConfig:
    snapshot = StrategySnapshot(
        descriptor=descriptor,
        source_files=(StrategySourceFile(path="alpha.py", sha256="a" * 64, content="x"),),
        strategy_version="b" * 64,
        python_version="3.11",
    )
    return StrategyBacktestConfig(
        start="20250102",
        end="20250108",
        batch_id="batch_1",
        comparison_key="c" * 64,
        strategy_snapshot=snapshot,
        top_n=2,
        eval_step=1,
        forward_days=1,
        data_lookback_days=3,
    )


def _individual(gateway: _Gateway, configs: list[StrategyBacktestConfig]) -> dict[str, object]:
    registry = _Registry()
    return {
        config.strategy_snapshot.descriptor.id: StockPredStrategyBacktestRunner(
            gateway,
            AlphaZooStrategyAdapter(
                registry,
                StockPredPanelBuilder(gateway, data_lookback_days=config.data_lookback_days),
                config.strategy_snapshot.descriptor,
            ),
        ).run(config)
        for config in configs
    }


def _canonical(result: object) -> dict[str, object]:
    return {
        name: getattr(result, name).sort_values(list(getattr(result, name).columns), kind="stable").reset_index(drop=True)
        for name in ("signals", "selected", "trades", "positions", "equity")
    }


def test_shared_date_screening_matches_individual_runner_and_reads_one_panel_per_day() -> None:
    configs = [_config(_descriptor("alpha101_1", 2)), _config(_descriptor("alpha101_2", 4))]
    expected = _individual(_Gateway(), configs)
    gateway = _Gateway()

    actual = AlphaBatchScreeningCoordinator(
        gateway,
        _Registry(),
        configs,
        snapshot_digest="a" * 64,
    ).run()

    for strategy_id in expected:
        for name, frame in _canonical(expected[strategy_id]).items():
            pd.testing.assert_frame_equal(_canonical(actual[strategy_id])[name], frame, check_exact=False, atol=1e-12, rtol=0)
        assert actual[strategy_id].metrics == pytest.approx(expected[strategy_id].metrics, abs=1e-12)
    assert gateway.trade_dates_calls == 1
    assert Counter(end for end in gateway.price_ends if end in gateway.dates) == Counter({date: 1 for date in gateway.dates})


def test_shared_timer_records_panel_and_execution_without_factor_evaluation() -> None:
    configs = [_config(_descriptor("alpha101_1", 2)), _config(_descriptor("alpha101_2", 4))]
    timer = _RecordingTimer()

    AlphaBatchScreeningCoordinator(
        _Gateway(),
        _Registry(),
        configs,
        snapshot_digest="a" * 64,
        phase_timer=timer,
    ).run()

    assert timer.phases.count("panel_build") == 5
    assert timer.phases.count("execution") in {0, 2}  # 0 when process pool, 2 with threads
    assert "factor_compute" not in timer.phases


def test_in_process_cohort_request_uses_cohort_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    config = _config(_descriptor("alpha101_1", 2))

    def run_cohort(self, config):
        return CohortRunResult(metrics={"engine": "cohort"})

    monkeypatch.setattr("backtest.stockpred.cohort.engine.CohortRunner.run", run_cohort)
    result = AlphaBatchScreeningCoordinator(
        _Gateway(), _Registry(), [config], snapshot_digest="a" * 64,
        evaluation_engine="cohort",
    ).run()

    assert result["alpha101_1"].metrics == {"engine": "cohort"}


def test_shared_cohort_config_preserves_snapshot_and_benchmark() -> None:
    config = _config(_descriptor("alpha101_1", 2)).model_copy(update={"benchmark_code": "000905.SH"})
    cohort = cohort_config_from_strategy_config(config, run_dir="out", data_snapshot_id="snapshot-x")
    assert cohort.data_snapshot_id == "snapshot-x"
    assert cohort.benchmark_code == "000905.SH"
    assert cohort.strategy_version == config.strategy_snapshot.strategy_version


def test_in_process_cohort_patches_req_json_atomically(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    config = _config(_descriptor("alpha101_1", 2))
    (tmp_path / "req.json").write_text('{"context": {}}', encoding="utf-8")

    def run_cohort(self, config):
        return CohortRunResult(metrics={"engine": "cohort"})

    monkeypatch.setattr("backtest.stockpred.cohort.engine.CohortRunner.run", run_cohort)
    AlphaBatchScreeningCoordinator(
        _Gateway(), _Registry(), [config], snapshot_digest="a" * 64,
        evaluation_engine="cohort", run_id_by_strategy={"alpha101_1": str(tmp_path)},
    ).run()

    import json
    assert json.loads((tmp_path / "req.json").read_text(encoding="utf-8"))["context"]["metric_schema_version"] == "signal_cohort_v1"
