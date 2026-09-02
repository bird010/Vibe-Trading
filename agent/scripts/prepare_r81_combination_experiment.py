"""Freeze the R81-combination research contract and rolling-fold manifest."""

from __future__ import annotations

import json
import os
from pathlib import Path

from backtest.fund_rotation.evaluation import iso_week_endings
from backtest.fund_rotation.oos_validation import RollingWalkForwardPolicy
from src.stockpred.fund_rotation.batch_models import (
    RESEARCH_ONLY,
    BatchVariantRequest,
    StrategyBatchRequest,
)
from src.stockpred.fund_rotation.data_snapshot import resolve_pinned_snapshot


RESEARCH_START = "20130329"
RESEARCH_END = "20220729"
EXPERIMENT_NAME = "fund_rotation_r81_combinations_20260903_v2"


def _write_json(path: Path, payload: object) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    stockpred_root = Path(os.environ["STOCKPRED_DATA_ROOT"])
    lance_dir = stockpred_root / "data" / "lance" / "market_core"
    snapshot = resolve_pinned_snapshot(lance_dir)

    all_dates = tuple(
        date
        for date in snapshot.trading_dates
        if RESEARCH_START <= date <= RESEARCH_END
    )
    weeks = tuple(iso_week_endings(all_dates))
    folds = RollingWalkForwardPolicy().generate_folds(weeks)
    if len(folds) < 3:
        raise RuntimeError(f"expected at least 3 folds, got {len(folds)}")

    root = repo_root / "agent" / "runs" / "fund_rotation" / "experiments" / EXPERIMENT_NAME
    root.mkdir(parents=True, exist_ok=True)
    spec = {
        "experiment_name": EXPERIMENT_NAME,
        "status": "FROZEN_RESEARCH_ONLY",
        "objective": "R81 dynamic economic-role representative combined with existing strategy signals",
        "priority_hypothesis": {
            "strategy_id": "ai_rotation_r82_economic_role_dynamic_rep_r57_signal",
            "champion_anchor": "ai_rotation_r81_economic_role_dynamic_rep",
            "signal_source": "R57 three-factor signal from R58",
            "signal_window_trading_days": 49,
            "signal_weights": {"bias": 0.3, "slope": 0.3, "efficiency": 0.4},
            "minimum_complete_candidates": 2,
            "representative_selection_is_unchanged": True,
        },
        "seed": {
            "required_seed": "e33b00bd5689",
            "reproducibility_status": "SEED_REPRO_PROBE_REQUIRED",
            "historical_reference_only": "f988e415122d",
        },
        "snapshot": {
            "fingerprint": snapshot.fingerprint,
            "fund_version": snapshot.fund_version,
            "fund_adj_version": snapshot.fund_adj_version,
            "dim_version": snapshot.dim_version,
            "role_universe_count": len(snapshot.role_universe_codes),
            "universe_count": len(snapshot.universe_codes),
        },
        "interval": {
            "research_start": RESEARCH_START,
            "research_end": RESEARCH_END,
            "confirmation_interval_excluded": "20220801..20260801",
        },
        "walk_forward": RollingWalkForwardPolicy().to_identity_dict(),
        "execution": {
            "initial_capital": 1_000_000.0,
            "commission_rate": 0.00025,
            "commission_min": 5.0,
            "other_fee_rate": 0.0,
            "max_participation_rate": 0.05,
            "adv_lookback": 20,
            "adv_min_observations": 10,
            "base_slippage_bps": 5.0,
            "max_slippage_bps": 30.0,
            "lot_size": 100,
        },
        "mode": RESEARCH_ONLY,
        "final_state_allowed": "FROZEN_RESEARCH_CANDIDATE",
        "deployment_allowed": False,
    }
    manifest = {
        "experiment_name": EXPERIMENT_NAME,
        "snapshot_fingerprint": snapshot.fingerprint,
        "research_start": RESEARCH_START,
        "research_end": RESEARCH_END,
        "weeks_count": len(weeks),
        "fold_count": len(folds),
        "folds": [
            {
                "fold_index": fold.fold_index,
                "train": [fold.train_weeks[0], fold.train_weeks[-1]],
                "validation": [fold.validation_weeks[0], fold.validation_weeks[-1]],
                "test": [fold.test_weeks[0], fold.test_weeks[-1]],
                "frozen_parameter_cutoff": fold.frozen_parameter_cutoff,
            }
            for fold in folds
        ],
    }
    anchor_request = StrategyBatchRequest(
        schema_version="1",
        idempotency_key="r81-combination-20260903-round00-r81-anchor",
        mode=RESEARCH_ONLY,
        evaluation_start_date=RESEARCH_START,
        evaluation_end_date=RESEARCH_END,
        variants=[
            BatchVariantRequest(
                strategy_id="ai_rotation_r81_economic_role_dynamic_rep",
                label="R81 anchor",
                params={},
            )
        ],
    )
    _write_json(root / "experiment_spec.json", spec)
    _write_json(root / "fold_manifest.json", manifest)
    _write_json(root / "r81_anchor_request.json", anchor_request.model_dump(mode="json"))
    (root / "ledger.jsonl").write_text(
        json.dumps(
            {
                "event": "snapshot_frozen",
                "status": "RECORDED",
                "snapshot_fingerprint": snapshot.fingerprint,
                "fund_version": snapshot.fund_version,
                "fund_adj_version": snapshot.fund_adj_version,
                "dim_version": snapshot.dim_version,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        + "\n"
        + json.dumps(
            {
                "event": "seed_repro_probe",
                "status": "SEED_REPRO_PROBE_REQUIRED",
                "required_seed": "e33b00bd5689",
                "historical_reference_only": "f988e415122d",
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"root": str(root), "snapshot": snapshot.fingerprint, "fold_count": len(folds)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
