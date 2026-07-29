from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from backtest.stockpred_strategy.artifacts import write_screening_artifacts
from backtest.stockpred_strategy.runner import StrategyBacktestResult
from src.stockpred.contracts import DataSnapshotManifest, ModelSnapshot, StockPredDataError
from src.stockpred.strategy_detail import materialize_strategy_detail

from tests.stockpred.test_strategy_runner import _config


def _manifest() -> DataSnapshotManifest:
    return DataSnapshotManifest(
        as_of="2025-01-07T15:00:00+08:00",
        tables={},
        model=ModelSnapshot(id="stockpred", version="v1", config_sha256="0" * 64),
    )


class _Gateway:
    def __init__(self, manifest: DataSnapshotManifest) -> None:
        self.manifest = manifest

    def prices(self, start: str, end: str, codes: list[str]) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {"ts_code": code, "trade_date": "20250102", "open": 10.0, "high": 11.0, "low": 9.0, "close": 10.0, "pct_chg": 0.0, "vol": 1000.0, "amount": 10000.0}
                for code in codes
            ]
        )

    def adjustment_factors(self, start: str, end: str, codes: list[str]) -> pd.DataFrame:
        return pd.DataFrame(
            [{"ts_code": code, "trade_date": "20250102", "adj_factor": 1.0} for code in codes]
        )

    def stock_limits(self, start: str, end: str, codes: list[str]) -> pd.DataFrame:
        return pd.DataFrame(
            [{"ts_code": code, "trade_date": "20250102", "up_limit": 20.0, "down_limit": 5.0} for code in codes]
        )


def _screening_result() -> StrategyBacktestResult:
    return StrategyBacktestResult(
        strategy_id="alpha101_1",
        eval_dates=["20250102"],
        signals=pd.DataFrame(),
        selected=pd.DataFrame({"ts_code": ["000001.SZ", "000001.SZ"]}),
        trades=pd.DataFrame(),
        positions=pd.DataFrame(),
        equity=pd.DataFrame(),
        metrics={},
    )


def _write_screening(run_dir: Path, manifest: DataSnapshotManifest) -> None:
    config = _config()
    write_screening_artifacts(run_dir, _screening_result(), manifest, config)
    (run_dir / "config.json").write_text(
        json.dumps(config.model_dump(mode="json")), encoding="utf-8"
    )
    (run_dir / "state.json").write_text(
        json.dumps({"status": "success"}), encoding="utf-8"
    )


def test_materialize_strategy_detail_writes_ohlcv_only_after_explicit_request(tmp_path: Path) -> None:
    manifest = _manifest()
    _write_screening(tmp_path, manifest)

    artifacts = materialize_strategy_detail(tmp_path, _Gateway(manifest))

    assert artifacts == tmp_path / "artifacts"
    assert (artifacts / "ohlcv_000001.SZ.csv").is_file()
    # Idempotent: second call returns same path without error
    assert materialize_strategy_detail(tmp_path, _Gateway(manifest)) == artifacts


def test_materialize_strategy_detail_rejects_another_snapshot_without_changing_screening(tmp_path: Path) -> None:
    manifest = _manifest()
    _write_screening(tmp_path, manifest)
    other_manifest = manifest.model_copy(update={"as_of": "2025-01-08T15:00:00+08:00"})

    with pytest.raises(StockPredDataError, match="snapshot"):
        materialize_strategy_detail(tmp_path, _Gateway(other_manifest))

    assert (tmp_path / "artifacts" / "metrics.csv").is_file()
    assert not list((tmp_path / "artifacts").glob("ohlcv_*.csv"))


def test_materialize_strategy_detail_requires_matching_successful_screening(tmp_path: Path) -> None:
    manifest = _manifest()
    _write_screening(tmp_path, manifest)
    config_path = tmp_path / "config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["comparison_key"] = "d" * 64
    config_path.write_text(json.dumps(config), encoding="utf-8")

    with pytest.raises(StockPredDataError, match="comparison"):
        materialize_strategy_detail(tmp_path, _Gateway(manifest))

    assert not list((tmp_path / "artifacts").glob("ohlcv_*.csv"))


# ---------------------------------------------------------------------------
# Staging and completion marker tests
# ---------------------------------------------------------------------------


class _FailingGateway:
    """Gateway that fails on second code's CSV write."""
    def __init__(self, manifest: DataSnapshotManifest, fail_code: str) -> None:
        self.manifest = manifest
        self.fail_code = fail_code
        self._call_count = 0

    def prices(self, start: str, end: str, codes: list[str]) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {"ts_code": code, "trade_date": "20250102", "open": 10.0, "high": 11.0, "low": 9.0, "close": 10.0, "pct_chg": 0.0, "vol": 1000.0, "amount": 10000.0}
                for code in codes
            ]
        )

    def adjustment_factors(self, start: str, end: str, codes: list[str]) -> pd.DataFrame:
        return pd.DataFrame(
            [{"ts_code": code, "trade_date": "20250102", "adj_factor": 1.0} for code in codes]
        )

    def stock_limits(self, start: str, end: str, codes: list[str]) -> pd.DataFrame:
        return pd.DataFrame(
            [{"ts_code": code, "trade_date": "20250102", "up_limit": 20.0, "down_limit": 5.0} for code in codes]
        )


def test_partial_write_failure_leaves_no_completion_marker(tmp_path: Path) -> None:
    """Multi-symbol materialization failure should not leave detail_complete.json."""
    manifest = _manifest()
    # Create screening with two codes
    config = _config()
    result = StrategyBacktestResult(
        strategy_id="alpha101_1",
        eval_dates=["20250102"],
        signals=pd.DataFrame(),
        selected=pd.DataFrame({"ts_code": ["000001.SZ", "000002.SZ"]}),
        trades=pd.DataFrame(),
        positions=pd.DataFrame(),
        equity=pd.DataFrame(),
        metrics={},
    )
    write_screening_artifacts(tmp_path, result, manifest, config)
    (tmp_path / "config.json").write_text(json.dumps(config.model_dump(mode="json")), encoding="utf-8")
    (tmp_path / "state.json").write_text(json.dumps({"status": "success"}), encoding="utf-8")

    # Simulate failure during write by patching _write_ohlcv
    import src.stockpred.strategy_detail as detail_module
    original_write = detail_module._write_ohlcv

    def failing_write(staging: Path, market: pd.DataFrame, codes: list[str]) -> None:
        # Write first code, fail on second
        groups = market.assign(ts_code=market["ts_code"].astype(str)).groupby("ts_code", sort=False)
        for code in codes:
            if code == "000002.SZ":
                raise OSError("simulated disk full")
            frame = groups.get_group(code) if code in groups.groups else market.head(0)
            frame.sort_values("trade_date", kind="stable").to_csv(staging / f"ohlcv_{code}.csv", index=False)

    detail_module._write_ohlcv = failing_write
    try:
        with pytest.raises(OSError, match="simulated disk full"):
            materialize_strategy_detail(tmp_path, _Gateway(manifest))
    finally:
        detail_module._write_ohlcv = original_write

    # No completion marker should exist
    assert not (tmp_path / "detail_complete.json").exists()


def test_partial_csv_without_marker_triggers_full_rebuild(tmp_path: Path) -> None:
    """Existing partial CSV without completion marker should trigger full rebuild."""
    manifest = _manifest()
    _write_screening(tmp_path, manifest)

    # Manually create a partial CSV (simulating interrupted previous run)
    artifacts = tmp_path / "artifacts"
    (artifacts / "ohlcv_000001.SZ.csv").write_text("trade_date,close\n20250102,10.0\n", encoding="utf-8")

    # Should NOT return early - should rebuild because no completion marker
    result = materialize_strategy_detail(tmp_path, _Gateway(manifest))

    assert result == artifacts
    assert (artifacts / "ohlcv_000001.SZ.csv").is_file()
    # Completion marker should now exist
    assert (tmp_path / "detail_complete.json").exists()


def test_completion_marker_mismatch_triggers_republish(tmp_path: Path) -> None:
    """Completion marker with wrong codes should be treated as incomplete."""
    manifest = _manifest()
    _write_screening(tmp_path, manifest)

    # Create a completion marker with wrong codes
    (tmp_path / "detail_complete.json").write_text(
        json.dumps({"version": 1, "codes": ["WRONG.SZ"], "detail_manifest_sha256": "x" * 64}),
        encoding="utf-8",
    )
    # Also create the CSV so old idempotent check would pass
    (tmp_path / "artifacts" / "ohlcv_000001.SZ.csv").write_text("trade_date,close\n20250102,10.0\n", encoding="utf-8")

    # Should republish because marker codes don't match manifest
    result = materialize_strategy_detail(tmp_path, _Gateway(manifest))

    assert result == tmp_path / "artifacts"
    # Marker should now have correct codes
    marker = json.loads((tmp_path / "detail_complete.json").read_text(encoding="utf-8"))
    assert marker["codes"] == ["000001.SZ"]


def test_detail_materialization_uses_unique_temporary_paths(tmp_path: Path) -> None:
    """Two consecutive materializations must use different staging and marker temp paths."""
    import src.stockpred.strategy_detail as detail_module

    manifest = _manifest()
    _write_screening(tmp_path, manifest)

    staging_paths: list[Path] = []
    original_write = detail_module._write_ohlcv

    def recording_write(staging: Path, market: pd.DataFrame, codes: list[str]) -> None:
        staging_paths.append(staging)
        original_write(staging, market, codes)

    detail_module._write_ohlcv = recording_write
    try:
        # First materialization
        materialize_strategy_detail(tmp_path, _Gateway(manifest))
        # Remove marker to force second materialization
        (tmp_path / "detail_complete.json").unlink()
        # Second materialization
        materialize_strategy_detail(tmp_path, _Gateway(manifest))
    finally:
        detail_module._write_ohlcv = original_write

    assert len(staging_paths) == 2
    assert staging_paths[0] != staging_paths[1], "staging paths must be unique per call"
