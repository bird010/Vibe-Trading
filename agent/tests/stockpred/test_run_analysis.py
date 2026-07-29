from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from src.ui_services import (
    build_run_analysis,
    build_trade_markers,
    load_graph_signal_series,
    load_run_context,
    load_symbol_metrics,
)


@pytest.fixture
def graph_run_dir(tmp_path: Path) -> Path:
    run_dir = tmp_path / "graph_123"
    artifacts = run_dir / "artifacts"
    artifacts.mkdir(parents=True)
    (run_dir / "req.json").write_text(
        json.dumps(
            {
                "context": {
                    "strategy_type": "stockpred_graph",
                    "start_date": "20250101",
                    "end_date": "20250131",
                }
            }
        ),
        encoding="utf-8",
    )
    with (artifacts / "selected_signals.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["trade_date", "ts_code", "score", "rank", "risk_adjustment"],
        )
        writer.writeheader()
        writer.writerows(
            [
                {
                    "trade_date": "20250103",
                    "ts_code": "000001.SZ",
                    "score": "1.25",
                    "rank": "7",
                    "risk_adjustment": "-0.1",
                },
                {
                    "trade_date": "20250103",
                    "ts_code": "000002.SZ",
                    "score": "0.75",
                    "rank": "8",
                    "risk_adjustment": "",
                },
            ]
        )
    return run_dir


@pytest.fixture
def normal_run_dir(tmp_path: Path) -> Path:
    run_dir = tmp_path / "normal_123"
    run_dir.mkdir()
    (run_dir / "req.json").write_text(
        json.dumps({"context": {"strategy_type": "generated_strategy"}}),
        encoding="utf-8",
    )
    return run_dir


def test_graph_signal_series_filters_selected_symbol(graph_run_dir: Path) -> None:
    result = load_graph_signal_series(graph_run_dir, symbols=["000001.SZ"])

    assert set(result) == {"000001.SZ"}
    assert result["000001.SZ"][0]["time"] == "2025-01-03"
    assert result["000001.SZ"][0]["rank"] == 7


def test_normal_run_has_no_graph_signal_series(normal_run_dir: Path) -> None:
    assert load_graph_signal_series(normal_run_dir) == {}


def test_summary_analysis_omits_graph_signal_rows(graph_run_dir: Path) -> None:
    result = build_run_analysis(graph_run_dir, include_payload=False)

    assert result["graph_signal_series"] == {}


def test_analysis_filters_graph_signals_with_chart_symbol(graph_run_dir: Path) -> None:
    result = build_run_analysis(graph_run_dir, symbols=["000002.SZ"])

    assert set(result["graph_signal_series"]) == {"000002.SZ"}


def test_run_context_exposes_strategy_type(graph_run_dir: Path) -> None:
    assert load_run_context(graph_run_dir)["strategy_type"] == "stockpred_graph"


def test_missing_schema_legacy_stockpred_run_is_inferred_but_non_stockpred_is_not(tmp_path: Path) -> None:
    legacy = tmp_path / "legacy"
    (legacy / "artifacts").mkdir(parents=True)
    (legacy / "req.json").write_text(
        json.dumps({"context": {"strategy_type": "stockpred_strategy"}}), encoding="utf-8"
    )
    other = tmp_path / "other"
    (other / "artifacts").mkdir(parents=True)
    (other / "req.json").write_text(
        json.dumps({"context": {"strategy_type": "generated_strategy"}}), encoding="utf-8"
    )

    assert load_run_context(legacy)["metric_schema_version"] == "legacy_portfolio_like_v1"
    assert load_run_context(other)["metric_schema_version"] is None


def test_run_analysis_exposes_strategy_snapshot(normal_run_dir: Path) -> None:
    (normal_run_dir / "strategy_snapshot.json").write_text(
        json.dumps({"strategy_version": "a" * 64, "descriptor": {"id": "alpha101_1"}}),
        encoding="utf-8",
    )

    result = build_run_analysis(normal_run_dir, include_payload=False)

    assert result["strategy_snapshot"]["strategy_version"] == "a" * 64


def test_load_symbol_metrics_prefers_persisted_artifact(graph_run_dir: Path) -> None:
    (graph_run_dir / "artifacts" / "symbol_metrics.csv").write_text(
        "symbol,total_return,trade_count,profit_loss_ratio\n"
        "000001.SZ,0.12,3,\n",
        encoding="utf-8",
    )

    assert load_symbol_metrics(graph_run_dir) == [
        {"symbol": "000001.SZ", "total_return": 0.12, "trade_count": 3.0}
    ]


def test_load_symbol_metrics_supports_unified_strategy_reports(normal_run_dir: Path) -> None:
    (normal_run_dir / "req.json").write_text(
        json.dumps({"context": {"strategy_type": "stockpred_strategy"}}),
        encoding="utf-8",
    )
    artifacts = normal_run_dir / "artifacts"
    artifacts.mkdir()
    (artifacts / "symbol_metrics.csv").write_text(
        "symbol,total_return,trade_count\n000001.SZ,0.12,3\n",
        encoding="utf-8",
    )

    assert load_symbol_metrics(normal_run_dir) == [
        {"symbol": "000001.SZ", "total_return": 0.12, "trade_count": 3.0}
    ]


def test_load_symbol_metrics_rebuilds_legacy_graph_artifacts(graph_run_dir: Path) -> None:
    artifacts = graph_run_dir / "artifacts"
    (artifacts / "trades.csv").write_text(
        "timestamp,code,side,executed_value,qty,cost_bps,status\n"
        "2025-01-01,000001.SZ,BUY,100,10,0,FILLED\n"
        "2025-01-02,000001.SZ,SELL,120,10,0,FILLED\n",
        encoding="utf-8",
    )
    (artifacts / "ohlcv_000001.SZ.csv").write_text(
        "trade_date,close\n20250101,10\n20250102,12\n",
        encoding="utf-8",
    )

    metrics = load_symbol_metrics(graph_run_dir)

    assert metrics[0]["symbol"] == "000001.SZ"
    assert metrics[0]["total_return"] == pytest.approx(0.2)


def test_load_symbol_metrics_omits_non_finite_persisted_values(graph_run_dir: Path) -> None:
    (graph_run_dir / "artifacts" / "symbol_metrics.csv").write_text(
        "symbol,total_return,sharpe,label\n000001.SZ,nan,inf,ignored\n",
        encoding="utf-8",
    )

    assert load_symbol_metrics(graph_run_dir) == [{"symbol": "000001.SZ"}]

def test_empty_persisted_symbol_metrics_is_authoritative(graph_run_dir: Path) -> None:
    artifacts = graph_run_dir / "artifacts"
    (artifacts / "symbol_metrics.csv").write_text("symbol,total_return\n", encoding="utf-8")
    (artifacts / "trades.csv").write_text(
        "timestamp,code,side,executed_value,qty,cost_bps,status\n"
        "2025-01-01,000001.SZ,BUY,100,10,0,FILLED\n"
        "2025-01-02,000001.SZ,SELL,120,10,0,FILLED\n",
        encoding="utf-8",
    )
    (artifacts / "ohlcv_000001.SZ.csv").write_text(
        "trade_date,close\n20250101,10\n20250102,12\n",
        encoding="utf-8",
    )

    assert load_symbol_metrics(graph_run_dir) == []

def test_trade_marker_preserves_execution_status() -> None:
    marker = build_trade_markers(
        [
            {
                "timestamp": "2025-01-06",
                "code": "000001.SZ",
                "side": "BUY",
                "price": "10.0",
                "qty": "0",
                "status": "REJECTED",
                "reason": "limit_up",
                "exit_delay_days": "2",
            }
        ]
    )[0]

    assert marker["status"] == "REJECTED"
    assert marker["exit_delay_days"] == 2


# ---------------------------------------------------------------------------
# Completion marker gate for OHLCV reads
# ---------------------------------------------------------------------------


def test_load_price_series_skips_partial_csv_when_detail_manifest_exists(tmp_path: Path) -> None:
    """With detail_manifest.json but no valid completion marker, partial CSVs must not be served."""
    from src.ui_services import load_price_series

    run_dir = tmp_path / "strategy_test"
    artifacts = run_dir / "artifacts"
    artifacts.mkdir(parents=True)
    # A detail manifest marks this as a staged run
    (run_dir / "detail_manifest.json").write_text(json.dumps({"version": 1, "codes": ["A", "B"]}), encoding="utf-8")
    # Partial CSV from interrupted publish (only 1 of 2 codes)
    (artifacts / "ohlcv_A.csv").write_text("trade_date,open,high,low,close,volume\n20250102,10,11,9,10,1000\n", encoding="utf-8")
    # No detail_complete.json → incomplete

    result = load_price_series(run_dir)

    # Must NOT return partial data
    assert result == []


def test_load_price_series_serves_csv_when_completion_marker_valid(tmp_path: Path) -> None:
    """With valid completion marker, OHLCV data is served normally."""
    from src.ui_services import load_price_series

    run_dir = tmp_path / "strategy_test"
    artifacts = run_dir / "artifacts"
    artifacts.mkdir(parents=True)
    (run_dir / "detail_manifest.json").write_text(json.dumps({"version": 1, "codes": ["A"]}), encoding="utf-8")
    (artifacts / "ohlcv_A.csv").write_text("trade_date,open,high,low,close,volume\n20250102,10,11,9,10,1000\n", encoding="utf-8")
    # Valid completion marker
    import hashlib
    manifest = {"version": 1, "codes": ["A"]}
    digest = hashlib.sha256(json.dumps(manifest, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    (run_dir / "detail_complete.json").write_text(
        json.dumps({"version": 1, "codes": ["A"], "detail_manifest_sha256": digest}), encoding="utf-8"
    )

    result = load_price_series(run_dir)

    assert len(result) == 1
    assert result[0]["code"] == "A"


def test_load_chart_symbols_uses_manifest_codes_while_publish_incomplete(tmp_path: Path) -> None:
    """Incomplete publish: load_chart_symbols returns manifest codes, not partial CSV scan."""
    from src.ui_services import load_chart_symbols

    run_dir = tmp_path / "strategy_test"
    artifacts = run_dir / "artifacts"
    artifacts.mkdir(parents=True)
    (run_dir / "detail_manifest.json").write_text(
        json.dumps({"version": 1, "codes": ["A", "B"]}), encoding="utf-8"
    )
    # Only partial CSV exists (A but not B)
    (artifacts / "ohlcv_A.csv").write_text("trade_date,close\n20250102,10\n", encoding="utf-8")

    # Must return full manifest codes, not just ["A"] from partial scan
    assert load_chart_symbols(run_dir, {"codes": ["A", "B"]}) == ["A", "B"]


def test_load_chart_symbols_invalid_marker_digest_treated_incomplete(tmp_path: Path) -> None:
    """Wrong digest in marker: symbols must come from manifest, not CSVs."""
    from src.ui_services import load_chart_symbols

    run_dir = tmp_path / "strategy_test"
    artifacts = run_dir / "artifacts"
    artifacts.mkdir(parents=True)
    (run_dir / "detail_manifest.json").write_text(
        json.dumps({"version": 1, "codes": ["X", "Y", "Z"]}), encoding="utf-8"
    )
    (artifacts / "ohlcv_X.csv").write_text("trade_date,close\n20250102,10\n", encoding="utf-8")
    # Marker with wrong digest
    (run_dir / "detail_complete.json").write_text(
        json.dumps({"version": 1, "codes": ["X", "Y", "Z"], "detail_manifest_sha256": "bad"}), encoding="utf-8"
    )

    assert load_chart_symbols(run_dir, {"codes": ["X", "Y", "Z"]}) == ["X", "Y", "Z"]
