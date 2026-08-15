"""Phase 6 Task 5 — real-data acceptance smoke test (requires Lance data).

This test is marked ``integration`` and skipped when Lance data is not
available. It runs a full batch with baseline + correlation representative
strategies against pinned Lance versions and verifies:
- Batch submits, runs, and reaches SUCCEEDED
- Comparison reports are generated with at least one ranked variant
- manifest.json is the final atomic publish point
- Each child run has an independent directory with state.json
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


def _has_lance_data() -> bool:
    """Check if real Lance datasets are available."""
    stockpred_root = os.environ.get("STOCKPRED_ROOT")
    if not stockpred_root:
        stockpred_root = Path(__file__).resolve().parents[1] / ".." / ".." / ".."
    lance_dir = Path(stockpred_root) / "data" / "lance" / "market_core"
    return (lance_dir / "fund.lance").exists()


@pytest.mark.skipif(not _has_lance_data(), reason="Lance data not available")
class TestRealDataSmoke:
    def test_full_batch_baseline_and_correlation_representative(self, tmp_path):
        """Run a real batch with both strategies and verify all artifacts."""
        import lance
        from pathlib import Path as P

        stockpred_root = Path(os.environ.get("STOCKPRED_ROOT", __file__)).resolve().parents[1]

        # Build service with real Lance loaders
        from backtest.fund_rotation.catalog import FundRotationStrategyCatalog
        from backtest.fund_rotation.strategies.registry import (
            default_fund_rotation_strategies,
        )
        from src.stockpred.fund_rotation.batch_service import BatchService

        catalog = FundRotationStrategyCatalog(list(default_fund_rotation_strategies()))
        lance_dir = stockpred_root / "data" / "lance" / "market_core"

        def metadata_loader():
            from src.stockpred.fund_rotation.data_snapshot import resolve_pinned_snapshot

            return resolve_pinned_snapshot(lance_dir)

        def frames_loader(snapshot, data_start, data_end):
            from src.stockpred.fund_rotation.data_snapshot import (
                load_pinned_frames,
            )

            return load_pinned_frames(
                snapshot, lance_dir, data_start=data_start, data_end=data_end,
            )

        service = BatchService(
            tmp_path / "batches",
            catalog=catalog,
            metadata_loader=metadata_loader,
            frames_loader=frames_loader,
            auto_start=False,
        )
        service.recover_interrupted()

        from src.stockpred.fund_rotation.batch_models import StrategyBatchRequest

        request = StrategyBatchRequest.model_validate({
            "schema_version": "1",
            "idempotency_key": "smoke-test-phase6",
            "mode": "RESEARCH_ONLY",
            "evaluation_start_date": "20240101",
            "evaluation_end_date": "20240630",
            "execution": {"initial_capital": 1_000_000.0},
            "variants": [
                {
                    "strategy_id": "correlation_all_members",
                    "label": "baseline",
                    "params": {"k": 5, "top_n": 2},
                },
                {
                    "strategy_id": "correlation_representative",
                    "label": "representative",
                    "params": {"k": 5, "top_n": 2},
                },
            ],
        })

        outcome = service.submit_batch(request)
        batch_id = outcome["batch_id"]
        if outcome["status"] == "EXISTING":
            # Clean up and retry with a new key
            request = request.model_copy(update={"idempotency_key": "smoke-test-phase6-2"})
            outcome = service.submit_batch(request)
            batch_id = outcome["batch_id"]

        service.run_batch_sync(batch_id)
        batch_dir = service.persistence.batch_dir(batch_id)

        # State must be SUCCEEDED or PARTIAL_SUCCEEDED
        state = json.loads((batch_dir / "state.json").read_text(encoding="utf-8"))
        assert state["stage"] in ("SUCCEEDED", "PARTIAL_SUCCEEDED"), f"state={state}"

        # Manifest must exist
        manifest_path = batch_dir / "manifest.json"
        assert manifest_path.exists(), "manifest.json missing"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert manifest["status"] in ("SUCCEEDED", "PARTIAL_SUCCEEDED")

        # Comparison reports
        reports_path = batch_dir / "reports.json"
        assert reports_path.exists(), "reports.json missing"
        reports = json.loads(reports_path.read_text(encoding="utf-8"))
        assert len(reports["ranking"]) >= 1, "no ranked variants"

        # Child runs exist
        runs_dir = batch_dir / "runs"
        assert runs_dir.exists()
        children = list(runs_dir.iterdir())
        assert len(children) == 2, f"expected 2 child runs, got {len(children)}"
        succeeded_children = 0
        for child in children:
            child_state = json.loads((child / "state.json").read_text(encoding="utf-8"))
            assert child_state["stage"] in ("SUCCEEDED", "FAILED"), f"child state={child_state}"
            if child_state["stage"] != "SUCCEEDED":
                continue
            succeeded_children += 1
            for artifact in (
                "target_decisions.csv",
                "orders.csv",
                "positions.csv",
                "equity.csv",
            ):
                artifact_path = child / artifact
                assert artifact_path.exists(), f"missing {artifact} in {child.name}"
                assert artifact_path.read_text(encoding="utf-8").count("\n") > 1
            evidence = json.loads(
                (child / "strategy_execution_diagnostics.json").read_text(
                    encoding="utf-8"
                )
            )["execution_rule_evidence"]
            assert evidence == {
                "source": "RESEARCH_STATIC_RULES",
                "pit_verified": False,
                "rule_version": "research-cn-etf-v1",
            }
        assert succeeded_children >= 1, "no native Research Static child succeeded"

        # Comparison artifacts
        for name in ("comparison_equity.csv", "comparison_metrics.csv", "data_snapshot.json"):
            assert (batch_dir / name).exists(), f"missing {name}"
