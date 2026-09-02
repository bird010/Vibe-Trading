"""Submit and synchronously run one frozen two-variant research batch."""

from __future__ import annotations

import argparse
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
from backtest.fund_rotation.market_rules import (
    build_research_static_execution_rule_context,
)


RESEARCH_START = "20130329"
RESEARCH_END = "20220729"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--idempotency-key", required=True)
    parser.add_argument("--champion", required=True)
    parser.add_argument("--challenger", required=True)
    parser.add_argument("--champion-label", default="Champion")
    parser.add_argument("--challenger-label", default="Challenger")
    return parser


def main() -> None:
    args = _parser().parse_args()
    repo_root = Path(__file__).resolve().parents[2]
    stockpred_root = Path(os.environ["STOCKPRED_DATA_ROOT"])
    lance_dir = stockpred_root / "data" / "lance" / "market_core"
    snapshot = resolve_pinned_snapshot(lance_dir)

    def frames_loader(pinned_snapshot, data_start: str, data_end: str):
        return load_pinned_frames(
            pinned_snapshot,
            lance_dir,
            data_start=data_start,
            data_end=data_end,
        )

    def execution_rule_loader(*, request, snapshot, dim_fund):
        role_prefixes = (
            "ai_rotation_r79_economic_role",
            "ai_rotation_r80_economic_role",
            "ai_rotation_r81_economic_role",
            "ai_rotation_r82_economic_role",
            "ai_rotation_r83_r81_r57_r77_combo",
            "ai_rotation_r84_r81_r57_r62_combo",
            "ai_rotation_r85_r81_r74_combo",
        )
        use_role_pool = any(
            str(variant.strategy_id).startswith(role_prefixes)
            for variant in request.variants
        )
        codes = (
            snapshot.role_universe_codes
            if use_role_pool
            else snapshot.universe_codes
        )
        return build_research_static_execution_rule_context(
            dim_fund=dim_fund,
            universe_codes=codes,
            evaluation_start_date=request.evaluation_start_date,
            evaluation_end_date=request.evaluation_end_date,
            snapshot_version=snapshot.dim_version,
        )

    output_root = repo_root / "agent" / "runs" / "fund_rotation"
    service = BatchService(
        output_root / "strategy_batches",
        runs_root=output_root,
        metadata_loader=lambda: snapshot,
        frames_loader=frames_loader,
        execution_rule_context_loader=execution_rule_loader,
        auto_start=False,
    )
    request = StrategyBatchRequest(
        schema_version="1",
        idempotency_key=args.idempotency_key,
        mode=RESEARCH_ONLY,
        evaluation_start_date=RESEARCH_START,
        evaluation_end_date=RESEARCH_END,
        variants=[
            BatchVariantRequest(
                strategy_id=args.champion,
                label=args.champion_label,
                params={},
            ),
            BatchVariantRequest(
                strategy_id=args.challenger,
                label=args.challenger_label,
                params={},
            ),
        ],
    )
    submitted = service.submit_batch(request)
    batch_id = submitted["batch_id"]
    if submitted.get("status") != "EXISTING":
        service.run_batch_sync(batch_id)
    state_path = service.persistence.batch_dir(batch_id) / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    print(
        json.dumps(
            {
                "batch_id": batch_id,
                "status": state.get("stage"),
                "snapshot_fingerprint": snapshot.fingerprint,
                "strategy_ids": [args.champion, args.challenger],
                "evaluation_interval": [RESEARCH_START, RESEARCH_END],
                "batch_dir": str(service.persistence.batch_dir(batch_id)),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
