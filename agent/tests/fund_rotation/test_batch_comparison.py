"""Phase 4 Task 5 — strict common-calendar comparisons (design §27/§22).

Only technically successful sub-runs whose equity index EXACTLY equals the
shared evaluation calendar enter the comparison; no date-intersection
shortening. Metrics are recomputed from raw equity with the common
initial_nav. Research quality is four-valued: only VALID/DEGRADED rank;
INVALID quality displays NAV with a warning; FAILED never ranks (no
zero-return entry). The contract fingerprint has exactly eight components and
excludes strategy implementation/config identities.
"""

from __future__ import annotations

import json

import pandas as pd
import pytest

from backtest.fund_rotation.metrics import compute_performance_metrics
from src.stockpred.fund_rotation.comparison import (
    CONTRACT_COMPONENT_KEYS,
    VariantComparisonInput,
    build_comparison,
    comparison_contract_fingerprint,
    evaluation_calendar_hash,
)

CALENDAR = [f"202401{d:02d}" for d in range(1, 11)]


def _equity(values, index=None):
    return pd.Series(values, index=index or CALENDAR, name="executed_strategy")


def _inputs():
    return [
        VariantComparisonInput(
            variant_key="s@aaaa", strategy_id="s", run_id="r1",
            status="SUCCEEDED", equity=_equity([1.0 + i * 0.01 for i in range(10)]),
            decision_quality="VALID",
        ),
        VariantComparisonInput(
            variant_key="s@bbbb", strategy_id="s", run_id="r2",
            status="SUCCEEDED", equity=_equity([1.0 + i * 0.02 for i in range(10)]),
            decision_quality="DEGRADED",
        ),
    ]


FP_KW = dict(
    framework_implementation_hash="fw-hash",
    data_snapshot_fingerprint="snap-hash",
)


class TestEligibility:
    def test_only_succeeded_calendar_exact_enters(self):
        inputs = _inputs() + [
            VariantComparisonInput(
                variant_key="s@cccc", strategy_id="s", run_id="r3",
                status="SUCCEEDED",
                equity=_equity([1.0] * 9, index=CALENDAR[:9]),  # missing day
                decision_quality="VALID",
            ),
            VariantComparisonInput(
                variant_key="s@dddd", strategy_id="s", run_id="r4",
                status="FAILED", equity=_equity([1.0] * 10),
                decision_quality="FAILED",
            ),
            VariantComparisonInput(
                variant_key="s@eeee", strategy_id="s", run_id="r5",
                status="CANCELED", equity=pd.Series(dtype=float),
                decision_quality="VALID",
            ),
        ]
        outcome = build_comparison(
            inputs, evaluation_calendar=CALENDAR, **FP_KW,
        )
        ranked_keys = [r["variant_key"] for r in outcome.ranking]
        assert ranked_keys == ["s@bbbb", "s@aaaa"]  # annual_return desc
        excluded = {e["variant_key"]: e["reason"] for e in outcome.excluded}
        assert excluded["s@cccc"] == "CALENDAR_MISMATCH"
        assert excluded["s@dddd"] == "TECHNICAL_FAILURE"
        assert excluded["s@eeee"] == "CANCELED"

    def test_extra_dates_also_rejected_no_intersection_shortening(self):
        inputs = [
            VariantComparisonInput(
                variant_key="s@aaaa", strategy_id="s", run_id="r1",
                status="SUCCEEDED",
                equity=_equity([1.0] * 11, index=CALENDAR + ["20240111"]),
                decision_quality="VALID",
            ),
        ]
        outcome = build_comparison(
            inputs, evaluation_calendar=CALENDAR, **FP_KW,
        )
        assert outcome.ranking == []
        assert outcome.excluded[0]["reason"] == "CALENDAR_MISMATCH"

    def test_decision_action_invalid_excluded(self):
        inputs = [
            VariantComparisonInput(
                variant_key="s@aaaa", strategy_id="s", run_id="r1",
                status="SUCCEEDED", equity=_equity([1.0] * 10),
                decision_quality="INVALID", has_invalid_action=True,
            ),
        ]
        outcome = build_comparison(
            inputs, evaluation_calendar=CALENDAR, **FP_KW,
        )
        assert outcome.excluded[0]["reason"] == "DECISION_INVALID"
        assert outcome.ranking == []

    def test_quality_invalid_displayed_but_not_ranked(self):
        """A gate-rejected run stays technically successful: full NAV and
        metrics are preserved and shown with a warning — never ranked."""
        inputs = _inputs() + [
            VariantComparisonInput(
                variant_key="s@cccc", strategy_id="s", run_id="r3",
                status="SUCCEEDED", equity=_equity([1.0] * 10),
                decision_quality="INVALID",
            ),
        ]
        outcome = build_comparison(
            inputs, evaluation_calendar=CALENDAR, **FP_KW,
        )
        ranked_keys = [r["variant_key"] for r in outcome.ranking]
        assert "s@cccc" not in ranked_keys
        assert "s@cccc" in outcome.equity_frame.columns
        assert "s@cccc" in outcome.metrics
        warnings = {w["variant_key"] for w in outcome.quality_warnings}
        assert warnings == {"s@cccc"}


class TestMetrics:
    def test_metrics_recomputed_from_raw_equity(self):
        outcome = build_comparison(
            _inputs(), evaluation_calendar=CALENDAR, **FP_KW,
        )
        for variant in _inputs():
            expected = compute_performance_metrics(
                variant.equity, periods_per_year=244, initial_nav=1.0,
            )
            actual = outcome.metrics[variant.variant_key]
            assert set(actual) == set(expected)
            for key in expected:
                assert actual[key] == pytest.approx(expected[key])


class TestContractFingerprint:
    def test_eight_components_and_no_strategy_identity(self):
        components, fingerprint = comparison_contract_fingerprint(
            evaluation_calendar=CALENDAR, **FP_KW,
        )
        assert set(components) == CONTRACT_COMPONENT_KEYS
        assert len(CONTRACT_COMPONENT_KEYS) == 8
        assert components["framework_implementation_hash"] == "fw-hash"
        assert components["data_snapshot_fingerprint"] == "snap-hash"
        assert components["evaluation_calendar_hash"] == evaluation_calendar_hash(
            CALENDAR,
        )
        # Strategy implementation/config identities never enter the
        # comparison contract.
        assert "strategy" not in json.dumps(components).lower()

    def test_calendar_order_independent_hash(self):
        assert evaluation_calendar_hash(CALENDAR) == evaluation_calendar_hash(
            list(reversed(CALENDAR)),
        )

    def test_fingerprint_stable_across_variant_configs(self):
        _, fp1 = comparison_contract_fingerprint(
            evaluation_calendar=CALENDAR, **FP_KW,
        )
        _, fp2 = comparison_contract_fingerprint(
            evaluation_calendar=CALENDAR, **FP_KW,
        )
        assert fp1 == fp2
        outcome = build_comparison(
            _inputs(), evaluation_calendar=CALENDAR, **FP_KW,
        )
        assert outcome.contract_fingerprint == fp1
        # Variant identities are carried separately, not mixed in.
        assert outcome.contract_fingerprint not in (
            "s@aaaa", "s@bbbb",
        )


# ── orchestration integration ──

class TestBatchComparisonIntegration:
    def _run_two_variant_batch(self, tmp_path):
        from tests.fund_rotation.test_batch_service import (
            CALENDAR as SVC_CALENDAR,
            FakeBatchStrategy,
            _calendar_metadata,
            _frames_loader,
            _request,
        )
        from backtest.fund_rotation.catalog import FundRotationStrategyCatalog
        from src.stockpred.fund_rotation.batch_service import BatchService

        service = BatchService(
            tmp_path,
            catalog=FundRotationStrategyCatalog([FakeBatchStrategy]),
            metadata_loader=_calendar_metadata,
            frames_loader=_frames_loader,
            auto_start=False,
        )
        request = _request([
            {"strategy_id": "fake_batch", "params": {"lookback_days": 30}},
            {"strategy_id": "fake_batch", "params": {"lookback_days": 40}},
        ])
        outcome = service.submit_batch(request)
        service.run_batch_sync(outcome["batch_id"])
        return service, outcome["batch_id"]

    def test_batch_writes_comparison_artifacts(self, tmp_path):
        service, batch_id = self._run_two_variant_batch(tmp_path)
        batch_dir = service.persistence.batch_dir(batch_id)

        for name in ("reports.json", "comparison_equity.csv",
                     "comparison_metrics.csv", "data_snapshot.json"):
            assert (batch_dir / name).exists(), f"missing {name}"

        reports = json.loads((batch_dir / "reports.json").read_text(encoding="utf-8"))
        assert len(reports["ranking"]) == 2
        assert reports["contract"]["fingerprint"]
        assert set(reports["contract"]["components"]) == CONTRACT_COMPONENT_KEYS
        assert reports["excluded"] == []

        equity = pd.read_csv(batch_dir / "comparison_equity.csv", index_col=0)
        assert len(equity.columns) == 2
        metrics = pd.read_csv(batch_dir / "comparison_metrics.csv", index_col=0)
        assert len(metrics.index) == 2

    def test_canceled_batch_publishes_no_comparison(self, tmp_path):
        from tests.fund_rotation.test_batch_service import (
            FakeBatchStrategy,
            _calendar_metadata,
            _frames_loader,
            _request,
        )
        from backtest.fund_rotation.catalog import FundRotationStrategyCatalog
        from src.stockpred.fund_rotation.batch_service import BatchService

        service = BatchService(
            tmp_path,
            catalog=FundRotationStrategyCatalog([FakeBatchStrategy]),
            metadata_loader=_calendar_metadata,
            frames_loader=_frames_loader,
            auto_start=False,
        )
        request = _request(
            [{"strategy_id": "fake_batch", "params": {"lookback_days": 30}}],
        )
        outcome = service.submit_batch(request)
        batch_id = outcome["batch_id"]
        assert service.cancel_batch(batch_id) is True
        service.run_batch_sync(batch_id)
        batch_dir = service.persistence.batch_dir(batch_id)
        assert not (batch_dir / "reports.json").exists()
        assert not (batch_dir / "comparison_equity.csv").exists()
        assert not (batch_dir / "manifest.json").exists()
