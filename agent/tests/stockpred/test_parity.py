from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

from src.stockpred.parity import compare_backtest_bundle, compare_signal_frames


def test_parity_fails_on_selected_symbol_difference() -> None:
    expected = pd.DataFrame(
        {"trade_date": ["20260105"], "ts_code": ["A"], "score": [0.5]}
    )
    actual = pd.DataFrame(
        {"trade_date": ["20260105"], "ts_code": ["B"], "score": [0.5]}
    )

    report = compare_signal_frames(
        expected,
        actual,
        keys=("trade_date", "ts_code"),
        numeric_columns=("score",),
    )

    assert not report.passed
    assert report.missing_keys == [("20260105", "A")]
    assert report.extra_keys == [("20260105", "B")]


def test_signal_frame_can_tolerate_one_diagnostic_column_rounding() -> None:
    expected = pd.DataFrame(
        {
            "trade_date": ["20260105"],
            "ts_code": ["A"],
            "score": [1.0],
            "f_reversal": [9.71],
        }
    )
    actual = expected.assign(f_reversal=[9.70])

    report = compare_signal_frames(
        expected,
        actual,
        keys=("trade_date", "ts_code"),
        numeric_columns=("score", "f_reversal"),
        column_atol={"f_reversal": 0.011},
    )

    assert report.passed

    score_report = compare_signal_frames(
        expected,
        actual.assign(score=[1.01]),
        keys=("trade_date", "ts_code"),
        numeric_columns=("score", "f_reversal"),
        column_atol={"f_reversal": 0.011},
    )

    assert not score_report.passed
    assert score_report.mismatched_columns == ["score"]


def _golden_bundle(root: Path) -> SimpleNamespace:
    signals = pd.DataFrame(
        {"trade_date": ["20260105"], "ts_code": ["A"], "score": [0.5]}
    )
    selected = signals.copy()
    trades = pd.DataFrame(
        {
            "timestamp": ["2026-01-06"],
            "code": ["A"],
            "side": ["BUY"],
            "price": [10.124],
            "status": ["FILLED"],
            "reason": [None],
            "signal_date": ["2026-01-05"],
            "exit_delay_days": [0],
        }
    )
    equity = pd.DataFrame({"time": ["2026-01-06"], "equity": [1.01]})
    metrics = {"sharpe_ratio": 1.2}
    signals.to_parquet(root / "details.parquet", index=False)
    selected.to_csv(root / "selected.csv", index=False)
    trades.to_csv(root / "trades.csv", index=False)
    equity.to_csv(root / "equity.csv", index=False)
    (root / "metrics.json").write_text(json.dumps(metrics), encoding="utf-8")
    return SimpleNamespace(
        signals=signals,
        selected=selected,
        trades=trades.assign(price=10.123),
        equity=equity,
        metrics=metrics,
    )


def test_backtest_bundle_compares_signals_selection_trades_and_nav(tmp_path: Path) -> None:
    result = _golden_bundle(tmp_path)

    report = compare_backtest_bundle(tmp_path, result)

    assert report.passed
    assert set(report.layers) == {"signals", "selected", "trades", "equity", "metrics"}


def test_backtest_bundle_prefers_explicit_parity_view(tmp_path: Path) -> None:
    result = _golden_bundle(tmp_path)
    parity_signals = result.signals.copy()
    parity_selected = result.selected.copy()
    parity_trades = result.trades.copy()
    parity_equity = result.equity.copy()
    parity_metrics = dict(result.metrics)

    result.signals = pd.concat(
        [
            result.signals,
            pd.DataFrame(
                {"trade_date": ["20260106"], "ts_code": ["Z"], "score": [9.0]}
            ),
        ],
        ignore_index=True,
    )
    result.selected = result.signals.copy()
    result.trades = pd.concat(
        [
            result.trades,
            pd.DataFrame(
                {
                    "timestamp": ["2026-01-07"],
                    "code": ["Z"],
                    "side": ["BUY"],
                    "price": [1.0],
                    "status": ["FILLED"],
                    "reason": [None],
                    "signal_date": ["2026-01-06"],
                    "exit_delay_days": [0],
                }
            ),
        ],
        ignore_index=True,
    )
    result.equity = pd.DataFrame({"time": ["2026-01-07"], "equity": [2.0]})
    result.metrics = {"scheduled_evaluations": 2.0}
    result.parity_signals = parity_signals
    result.parity_selected = parity_selected
    result.parity_trades = parity_trades
    result.parity_equity = parity_equity
    result.parity_metrics = parity_metrics

    report = compare_backtest_bundle(tmp_path, result)

    assert report.passed


def test_backtest_bundle_fails_closed_when_required_file_is_missing(tmp_path: Path) -> None:
    result = _golden_bundle(tmp_path)
    (tmp_path / "equity.csv").unlink()

    report = compare_backtest_bundle(tmp_path, result)

    assert not report.passed
    assert not report.layers["equity"].passed
