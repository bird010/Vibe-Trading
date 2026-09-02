"""Append one completed combination batch to the research ledger."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-id", required=True)
    parser.add_argument("--experiment", default="fund_rotation_r81_combinations_20260903_v2")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1] / "runs" / "fund_rotation"
    batch_dir = root / "strategy_batches" / args.batch_id
    report = json.loads((batch_dir / "reports.json").read_text(encoding="utf-8"))
    state_path = batch_dir / "state.json"
    state = (
        json.loads(state_path.read_text(encoding="utf-8"))
        if state_path.exists()
        else {}
    )
    metrics = report.get("metrics", {})
    row = {
        "event": "combination_batch_completed",
        "batch_id": args.batch_id,
        "status": state.get("stage", report.get("status")),
        "comparison_available": report.get("comparison_available"),
        "comparable_variant_count": report.get("comparable_variant_count"),
        "metrics": metrics,
        "excluded": report.get("excluded", []),
    }
    ledger = root / "experiments" / args.experiment / "ledger.jsonl"
    with ledger.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    print(json.dumps(row, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
