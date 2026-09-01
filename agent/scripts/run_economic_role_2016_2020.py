"""Run the frozen v4.3 Economic Role Phase-A comparison batch."""

from __future__ import annotations

import json
import os
from pathlib import Path

from src.stockpred.fund_rotation.batch_models import (
    RESEARCH_ONLY,
    BatchVariantRequest,
    StrategyBatchRequest,
)
from src.stockpred.fund_rotation.batch_service import BatchService
from src.stockpred.fund_rotation.data_snapshot import (
    load_pinned_frames,
    resolve_pinned_snapshot,
)


def main() -> None:
    stockpred_root = Path(os.environ["STOCKPRED_DATA_ROOT"])
    lance_dir = stockpred_root / "data" / "lance" / "market_core"
    output_root = Path(__file__).resolve().parents[2] / "runs" / "fund_rotation"
    batches_dir = output_root / "strategy_batches_economic_role_2016_2020_v4"
    runs_root = output_root / "economic_role_2016_2020_v4"
    snapshot = resolve_pinned_snapshot(lance_dir)

    def frames_loader(pinned_snapshot, data_start: str, data_end: str):
        return load_pinned_frames(
            pinned_snapshot,
            lance_dir,
            data_start=data_start,
            data_end=data_end,
        )

    service = BatchService(
        batches_dir,
        runs_root=runs_root,
        metadata_loader=lambda: snapshot,
        frames_loader=frames_loader,
        auto_start=False,
    )
    request = StrategyBatchRequest(
        schema_version="1",
        idempotency_key="economic-role-v43-phase-a-2016-2020-v4",
        mode=RESEARCH_ONLY,
        evaluation_start_date="20160101",
        evaluation_end_date="20201231",
        variants=[
            BatchVariantRequest(
                strategy_id="ai_rotation_r76_fixed_short_bond",
                label="G0 baseline",
                params={},
            ),
            BatchVariantRequest(
                strategy_id="ai_rotation_r79_economic_role_members",
                label="G1b frozen Role Members",
                params={},
            ),
            BatchVariantRequest(
                strategy_id="ai_rotation_r80_economic_role_fixed_rep",
                label="G1 fixed representative",
                params={},
            ),
            BatchVariantRequest(
                strategy_id="ai_rotation_r81_economic_role_dynamic_rep",
                label="G2 dynamic representative",
                params={},
            ),
        ],
    )
    submitted = service.submit_batch(request)
    batch_id = submitted["batch_id"]
    if submitted.get("status") != "EXISTING":
        service.run_batch_sync(batch_id)
    state = json.loads(
        (service.persistence.batch_dir(batch_id) / "state.json").read_text(
            encoding="utf-8"
        )
    )
    print(json.dumps({
        "batch_id": batch_id,
        "status": state.get("stage"),
        "snapshot": {
            "fingerprint": snapshot.fingerprint,
            "fund_version": snapshot.fund_version,
            "dim_version": snapshot.dim_version,
            "fund_adj_version": snapshot.fund_adj_version,
            "data_min": snapshot.trading_dates[0] if snapshot.trading_dates else None,
            "data_max": snapshot.trading_dates[-1] if snapshot.trading_dates else None,
        },
        "batches_dir": str(batches_dir),
        "runs_root": str(runs_root),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
