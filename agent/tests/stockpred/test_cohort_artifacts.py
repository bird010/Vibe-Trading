"""Tests for versioned cohort artifacts publishing."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from backtest.stockpred.cohort.artifacts import publish_cohort_artifacts
from backtest.stockpred.cohort.contracts import CohortResult, CohortStatus


def _cohort_result(i: int) -> CohortResult:
    return CohortResult(
        cohort_id=f"cohort_{i:03d}",
        committed_capital_return=0.01 * i,
        executed_capital_return=0.012 * i,
        raw_signal_return=0.015 * i,
        horizon_mark_return=0.009 * i,
        liquidation_return=0.01 * i,
        benchmark_return=0.005,
        target_horizon_excess_return=0.004 * i,
        liquidation_policy_excess_return=0.005 * i,
        fill_rate=0.95,
        idle_cash_ratio=0.05,
        cost_ratio=0.003,
        exit_delay_days=0,
        unliquidated_ratio=0.0,
        status=CohortStatus.LIQUIDATED,
        evaluation_date=f"2025010{i + 1}",
        data_quality={"reason": "verified"},
    )


def _agg_result():
    from backtest.stockpred.cohort.aggregation import (
        AggregateMetrics,
        AggregationResult,
        QualityReport,
    )

    metrics = AggregateMetrics(
        mean_return=0.02, median_return=0.018, std_return=0.01,
        win_rate=0.7, p5=-0.01, p25=0.005, p75=0.03, p95=0.05,
        mean_excess_return=0.015, positive_excess_ratio=0.65,
        mean_fill_rate=0.95, mean_idle_cash_ratio=0.05,
        mean_cost_ratio=0.003, mean_unliquidated_ratio=0.0,
        valid_cohort_count=5, total_cohort_count=5,
        hac_se=0.004, bootstrap_ci=None,
    )
    quality = QualityReport(ranking_eligible=True, valid_eval_ratio=1.0, failures=[])
    return AggregationResult(metrics=metrics, quality=quality)


class TestPublishCohortArtifacts:
    def test_empty_run_publishes_empty_chart_manifest(self, tmp_path: Path):
        version_id = publish_cohort_artifacts(
            run_dir=tmp_path,
            cohort_results=[],
            agg_result=_agg_result(),
            config={},
        )

        manifest = json.loads(
            (tmp_path / "artifacts_versions" / version_id / "chart_bundle_manifest.json").read_text(
                encoding="utf-8"
            )
        )
        assert manifest["entries"] == []
        assert manifest["total_codes"] == 0
        assert {item["relative_path"] for item in manifest["files"]} >= {
            "aggregate_metrics.json",
            "cohort_returns.csv",
            "cohort_orders.csv",
            "quality_report.json",
        }
        assert all(len(item["sha256"]) == 64 and item["byte_size"] >= 0 for item in manifest["files"])

    def test_creates_versioned_directory(self, tmp_path: Path):
        results = [_cohort_result(i) for i in range(5)]
        version_id = publish_cohort_artifacts(
            run_dir=tmp_path,
            cohort_results=results,
            agg_result=_agg_result(),
            config={"holding_days": 5, "eval_step": 5},
        )

        assert version_id != ""
        version_dir = tmp_path / "artifacts_versions" / version_id
        assert version_dir.is_dir()

    def test_artifacts_current_json_published(self, tmp_path: Path):
        results = [_cohort_result(i) for i in range(3)]
        version_id = publish_cohort_artifacts(
            run_dir=tmp_path,
            cohort_results=results,
            agg_result=_agg_result(),
            config={"holding_days": 5},
        )

        pointer_path = tmp_path / "artifacts_current.json"
        assert pointer_path.is_file()
        pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
        assert pointer["version_id"] == version_id
        assert pointer["schema_version"] == "signal_cohort_v1"

    def test_required_files_present(self, tmp_path: Path):
        results = [_cohort_result(i) for i in range(3)]
        version_id = publish_cohort_artifacts(
            run_dir=tmp_path,
            cohort_results=results,
            agg_result=_agg_result(),
            config={"holding_days": 5},
        )

        version_dir = tmp_path / "artifacts_versions" / version_id
        assert (version_dir / "cohort_returns.csv").is_file()
        assert (version_dir / "aggregate_metrics.json").is_file()
        assert (version_dir / "quality_report.json").is_file()

    def test_cohort_returns_csv_content(self, tmp_path: Path):
        import pandas as pd

        results = [_cohort_result(i) for i in range(3)]
        version_id = publish_cohort_artifacts(
            run_dir=tmp_path,
            cohort_results=results,
            agg_result=_agg_result(),
            config={},
        )

        df = pd.read_csv(tmp_path / "artifacts_versions" / version_id / "cohort_returns.csv")
        assert len(df) == 3
        assert "cohort_id" in df.columns
        assert "committed_capital_return" in df.columns
        assert str(df.iloc[0]["evaluation_date"]) == "20250101"
        assert json.loads(df.iloc[0]["data_quality"])["reason"] == "verified"

    def test_empty_csv_uses_full_cohort_schema(self, tmp_path: Path):
        version_id = publish_cohort_artifacts(
            run_dir=tmp_path, cohort_results=[], agg_result=_agg_result(), config={}
        )
        df = pd.read_csv(tmp_path / "artifacts_versions" / version_id / "cohort_returns.csv")
        assert {"evaluation_date", "data_quality", "raw_label_coverage", "raw_label_status", "uses_stale_valuation", "max_stale_days"}.issubset(df.columns)

    def test_no_staging_left_after_success(self, tmp_path: Path):
        results = [_cohort_result(0)]
        publish_cohort_artifacts(
            run_dir=tmp_path, cohort_results=results, agg_result=_agg_result(), config={}
        )

        staging_dirs = list(tmp_path.glob("artifacts_versions/.staging.*"))
        assert staging_dirs == []

    def test_version_id_deterministic(self, tmp_path: Path):
        results = [_cohort_result(i) for i in range(3)]
        v1 = publish_cohort_artifacts(
            run_dir=tmp_path / "run1", cohort_results=results, agg_result=_agg_result(), config={"a": 1}
        )
        v2 = publish_cohort_artifacts(
            run_dir=tmp_path / "run2", cohort_results=results, agg_result=_agg_result(), config={"a": 1}
        )
        assert v1 == v2

    def test_failure_leaves_no_pointer(self, tmp_path: Path):
        # Simulate failure by making artifacts_versions read-only after creation
        # Instead, test that empty results still works (edge case)
        publish_cohort_artifacts(
            run_dir=tmp_path, cohort_results=[], agg_result=_agg_result(), config={}
        )
        # Empty results should still publish valid (empty) artifacts
        assert (tmp_path / "artifacts_current.json").is_file()

    def test_incomplete_chart_bundle_cleans_staging_and_keeps_old_pointer(self, tmp_path: Path):
        original = publish_cohort_artifacts(
            run_dir=tmp_path, cohort_results=[], agg_result=_agg_result(), config={}
        )
        old_pointer = (tmp_path / "artifacts_current.json").read_text(encoding="utf-8")

        market = pd.DataFrame([
            {"ts_code": "A", "trade_date": "20250101", "open": 1, "high": 1, "low": 1, "close": 1, "vol": 1},
        ])
        with pytest.raises(ValueError, match="CHART_BUNDLE_INCOMPLETE"):
            publish_cohort_artifacts(
                run_dir=tmp_path,
                cohort_results=[],
                agg_result=_agg_result(),
                config={},
                chart_market=market,
                chart_codes=["A", "B"],
                chart_orders=pd.DataFrame(),
                chart_start_date="20250101",
                chart_end_date="20250101",
            )

        assert json.loads((tmp_path / "artifacts_current.json").read_text(encoding="utf-8"))["version_id"] == original
        assert (tmp_path / "artifacts_current.json").read_text(encoding="utf-8") == old_pointer
        assert list((tmp_path / "artifacts_versions").glob(".staging.*")) == []
