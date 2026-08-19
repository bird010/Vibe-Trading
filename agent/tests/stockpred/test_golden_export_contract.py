from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from tools.migration.export_stockpred_graph_golden import (
    build_parser,
    eligible_eval_dates,
    extract_metrics,
    normalize_equity,
    normalize_trades,
    run_oracle,
    select_calendar_window,
    write_golden_bundle,
)


def test_golden_manifest_records_oracle_and_writes_complete_bundle(tmp_path: Path) -> None:
    out = tmp_path / "golden"
    write_golden_bundle(
        out,
        oracle_commit="abc123",
        config={"top_n": 1, "eval_step": 5},
        details=pd.DataFrame(
            {
                "trade_date": ["20260105", "20260105"],
                "ts_code": ["000002.SZ", "000001.SZ"],
                "score": [0.8, 0.8],
            }
        ),
        trades=pd.DataFrame(
            {
                "timestamp": ["2026-01-06"],
                "code": ["000001.SZ"],
                "side": ["BUY"],
                "status": ["FILLED"],
            }
        ),
        equity=pd.DataFrame({"time": ["2026-01-06"], "equity": [1.0]}),
        metrics={"total_return": 0.0},
    )

    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    selected = pd.read_csv(out / "selected.csv", dtype={"ts_code": str})

    assert manifest["oracle_commit"] == "abc123"
    assert manifest["config"]["top_n"] == 1
    assert selected["ts_code"].tolist() == ["000001.SZ"]
    assert {path.name for path in out.iterdir()} == {
        "manifest.json",
        "details.parquet",
        "selected.csv",
        "trades.csv",
        "equity.csv",
        "metrics.json",
    }


def test_golden_bundle_refuses_to_overwrite_existing_directory(tmp_path: Path) -> None:
    out = tmp_path / "golden"
    out.mkdir()

    with pytest.raises(FileExistsError):
        write_golden_bundle(
            out,
            oracle_commit="abc123",
            config={"top_n": 1},
            details=pd.DataFrame(),
            trades=pd.DataFrame(),
            equity=pd.DataFrame(),
            metrics={},
        )


def test_select_calendar_window_anchors_requested_dates() -> None:
    dates = [
        "20241230",
        "20241231",
        "20250102",
        "20250103",
        "20250106",
        "20250107",
    ]

    window, lookback_days = select_calendar_window(
        dates,
        start="20250102",
        end="20250106",
        data_lookback_days=2,
    )

    assert window == dates[:5]
    assert lookback_days == 3


def test_select_calendar_window_rejects_missing_boundaries() -> None:
    with pytest.raises(ValueError, match="no open trading dates"):
        select_calendar_window(
            ["20250102", "20250103"],
            start="20260101",
            end="20260131",
            data_lookback_days=2,
        )


def test_run_oracle_anchors_calendar_and_restores_provider() -> None:
    def original_provider(n_days: int) -> list[str]:
        return ["original"][-n_days:]

    class FakeConfig:
        def __init__(self, **values: object) -> None:
            self.__dict__.update(values)

    captured: dict[str, object] = {}

    def fake_run(config: FakeConfig) -> SimpleNamespace:
        captured["config"] = config
        captured["calendar"] = module.get_recent_trade_dates(99)
        return SimpleNamespace(eval_dates=["20250102", "20250106"])

    module = SimpleNamespace(
        BacktestConfig=FakeConfig,
        get_recent_trade_dates=original_provider,
        run_backtest=fake_run,
    )

    result = run_oracle(
        module,
        trade_dates=["20241230", "20241231", "20250102", "20250103", "20250106"],
        start="20250102",
        end="20250106",
        top_n=50,
        eval_step=5,
        data_lookback_days=2,
    )

    config = captured["config"]
    assert result.eval_dates == ["20250102", "20250106"]
    assert config.lookback_days == 3
    assert config.n_workers == 1
    assert captured["calendar"] == [
        "20241230",
        "20241231",
        "20250102",
        "20250103",
        "20250106",
    ]
    assert module.get_recent_trade_dates is original_provider


def test_run_oracle_rejects_result_outside_requested_range() -> None:
    module = SimpleNamespace(
        BacktestConfig=lambda **values: SimpleNamespace(**values),
        get_recent_trade_dates=lambda n_days: [],
        run_backtest=lambda config: SimpleNamespace(eval_dates=["20250107"]),
    )

    with pytest.raises(RuntimeError, match="outside requested range"):
        run_oracle(
            module,
            trade_dates=["20241231", "20250102", "20250103", "20250106"],
            start="20250102",
            end="20250106",
            top_n=50,
            eval_step=5,
            data_lookback_days=1,
        )


def test_normalize_trades_keeps_rejections_and_delayed_exits() -> None:
    details = pd.DataFrame(
        {
            "trade_date": ["20250102", "20250102"],
            "ts_code": ["A", "B"],
            "score": [0.9, 0.8],
            "entry_date": ["20250103", "20250103"],
            "exit_date": ["20250110", None],
            "entry_price": [10.0, None],
            "exit_price": [11.0, None],
            "entered": [True, False],
            "entry_block_reason": [None, "limit_up"],
            "exit_delay_days": [1, 0],
        }
    )

    trades = normalize_trades(details, top_n=2)

    assert trades[["code", "side", "status", "reason"]].values.tolist() == [
        ["A", "BUY", "FILLED", None],
        ["A", "SELL", "FILLED", "exit_delayed"],
        ["B", "BUY", "REJECTED", "limit_up"],
    ]


def test_normalize_equity_aligns_dates_and_returns() -> None:
    equity = normalize_equity(
        ["20250102", "20250103"],
        [0.01, -0.02],
    )

    assert equity["time"].tolist() == ["2025-01-02", "2025-01-03"]
    assert equity["equity"].tolist() == [1.01, 0.98]


def test_eligible_eval_dates_excludes_cross_sections_smaller_than_top_n() -> None:
    details = pd.DataFrame(
        {
            "trade_date": ["20250102", "20250102", "20250103"],
            "ts_code": ["A", "B", "C"],
            "score": [0.9, 0.8, 0.7],
        }
    )

    assert eligible_eval_dates(details, top_n=2) == ["20250102"]


@dataclass
class _MetricResult:
    total_evaluated: int = 10
    sharpe_ratio: float = 1.2
    details_df: pd.DataFrame = field(default_factory=pd.DataFrame)


def test_extract_metrics_omits_dataframes() -> None:
    assert extract_metrics(_MetricResult()) == {
        "sharpe_ratio": 1.2,
        "total_evaluated": 10,
    }


def test_cli_parser_requires_root_dates_and_output() -> None:
    args = build_parser().parse_args(
        [
            "--stockpred-root",
            "../StockPred",
            "--start",
            "2025-01-02",
            "--end",
            "2025-03-31",
            "--out",
            "tmp/golden/normal",
        ]
    )

    assert args.top_n == 50
    assert args.eval_step == 5
