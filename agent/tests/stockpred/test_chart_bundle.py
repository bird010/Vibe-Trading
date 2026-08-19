"""Tests for chart bundle publisher."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest
from backtest.stockpred.cohort.chart_bundle import publish_chart_bundle


def _market(codes: list[str], days: int = 30) -> pd.DataFrame:
    dates = [f"202501{d:02d}" for d in range(1, days + 1)]
    rows = []
    for code in codes:
        for i, date in enumerate(dates):
            price = 10.0 * (1.01 ** i)
            rows.append({
                "ts_code": code, "trade_date": date,
                "open": price, "high": price * 1.02, "low": price * 0.98,
                "close": price * 1.01, "vol": 100000.0, "amount": 5000.0,
            })
    return pd.DataFrame(rows)


def _orders() -> pd.DataFrame:
    return pd.DataFrame([
        {"cohort_id": "c1", "code": "A", "trade_date": "20250111", "side": "BUY", "price": 10.0, "quantity": 1000},
        {"cohort_id": "c1", "code": "A", "trade_date": "20250116", "side": "SELL", "price": 11.0, "quantity": 1000},
        {"cohort_id": "c2", "code": "B", "trade_date": "20250112", "side": "BUY", "price": 20.0, "quantity": 500},
    ])


class TestChartBundle:
    def test_chart_bundle_contains_all_signal_codes_and_extended_exit_dates(self, tmp_path: Path):
        mkt = _market(["SELECTED", "SIGNAL_ONLY"], days=30)

        publish_chart_bundle(
            staging_dir=tmp_path,
            market=mkt,
            codes=["SELECTED", "SIGNAL_ONLY"],
            orders=_orders(),
            start_date="20250101",
            end_date="20250130",
        )

        manifest = json.loads((tmp_path / "chart_bundle_manifest.json").read_text(encoding="utf-8"))
        assert {entry["code"] for entry in manifest["entries"]} == {"SELECTED", "SIGNAL_ONLY"}
        assert {entry["end_date"] for entry in manifest["entries"]} == {"20250130"}

    def test_creates_parquet_per_code(self, tmp_path: Path):
        mkt = _market(["A", "B"])
        publish_chart_bundle(
            staging_dir=tmp_path, market=mkt, codes=["A", "B"],
            orders=_orders(), start_date="20250101", end_date="20250130",
        )

        assert (tmp_path / "charts" / "ohlcv_A.parquet").is_file()
        assert (tmp_path / "charts" / "ohlcv_B.parquet").is_file()

    def test_manifest_json_created(self, tmp_path: Path):
        mkt = _market(["A"])
        publish_chart_bundle(
            staging_dir=tmp_path, market=mkt, codes=["A"],
            orders=_orders(), start_date="20250101", end_date="20250130",
        )

        manifest_path = tmp_path / "chart_bundle_manifest.json"
        assert manifest_path.is_file()
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert len(data["entries"]) == 1
        entry = data["entries"][0]
        assert entry["code"] == "A"
        assert entry["row_count"] == 30
        assert "sha256" in entry

    def test_manifest_hash_matches_file(self, tmp_path: Path):
        mkt = _market(["A"])
        publish_chart_bundle(
            staging_dir=tmp_path, market=mkt, codes=["A"],
            orders=_orders(), start_date="20250101", end_date="20250130",
        )

        manifest = json.loads((tmp_path / "chart_bundle_manifest.json").read_text(encoding="utf-8"))
        entry = manifest["entries"][0]
        file_bytes = (tmp_path / entry["relative_path"]).read_bytes()
        actual_hash = hashlib.sha256(file_bytes).hexdigest()
        assert entry["sha256"] == actual_hash

    def test_orders_csv_includes_cohort_id(self, tmp_path: Path):
        mkt = _market(["A", "B"])
        publish_chart_bundle(
            staging_dir=tmp_path, market=mkt, codes=["A", "B"],
            orders=_orders(), start_date="20250101", end_date="20250130",
        )

        orders_path = tmp_path / "cohort_orders.csv"
        assert orders_path.is_file()
        df = pd.read_csv(orders_path)
        assert "cohort_id" in df.columns
        assert len(df) == 3

    def test_nonempty_orders_normalize_requested_quantity_known_without_mutating_input(self, tmp_path: Path):
        orders = _orders()
        explicit_false = _orders()
        explicit_false["requested_quantity_known"] = False

        publish_chart_bundle(
            staging_dir=tmp_path / "missing", market=_market(["A", "B"]), codes=["A", "B"],
            orders=orders, start_date="20250101", end_date="20250130",
        )
        publish_chart_bundle(
            staging_dir=tmp_path / "explicit", market=_market(["A", "B"]), codes=["A", "B"],
            orders=explicit_false, start_date="20250101", end_date="20250130",
        )

        missing_column = pd.read_csv(tmp_path / "missing" / "cohort_orders.csv")
        preserved_false = pd.read_csv(tmp_path / "explicit" / "cohort_orders.csv")

        assert "requested_quantity_known" not in orders.columns
        assert missing_column["requested_quantity_known"].tolist() == [True, True, True]
        assert preserved_false["requested_quantity_known"].tolist() == [False, False, False]

    def test_empty_codes_produces_empty_manifest(self, tmp_path: Path):
        mkt = _market(["A"])
        publish_chart_bundle(
            staging_dir=tmp_path, market=mkt, codes=[],
            orders=pd.DataFrame(), start_date="20250101", end_date="20250130",
        )

        manifest_path = tmp_path / "chart_bundle_manifest.json"
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert data["entries"] == []

    def test_empty_orders_csv_includes_requested_quantity_known(self, tmp_path: Path):
        publish_chart_bundle(
            staging_dir=tmp_path, market=_market(["A"]), codes=[],
            orders=pd.DataFrame(), start_date="20250101", end_date="20250130",
        )

        orders = pd.read_csv(tmp_path / "cohort_orders.csv")

        assert "requested_quantity_known" in orders.columns

    def test_missing_requested_code_fails_closed(self, tmp_path: Path):
        mkt = _market(["A"])
        with pytest.raises(ValueError, match="CHART_BUNDLE_INCOMPLETE"):
            publish_chart_bundle(
                staging_dir=tmp_path, market=mkt, codes=["A", "UNKNOWN"],
                orders=_orders(), start_date="20250101", end_date="20250130",
            )
        assert not (tmp_path / "chart_bundle_manifest.json").exists()

    def test_manifest_byte_size_correct(self, tmp_path: Path):
        mkt = _market(["A"])
        publish_chart_bundle(
            staging_dir=tmp_path, market=mkt, codes=["A"],
            orders=_orders(), start_date="20250101", end_date="20250130",
        )

        data = json.loads((tmp_path / "chart_bundle_manifest.json").read_text(encoding="utf-8"))
        entry = data["entries"][0]
        file_path = tmp_path / entry["relative_path"]
        assert entry["byte_size"] == file_path.stat().st_size
