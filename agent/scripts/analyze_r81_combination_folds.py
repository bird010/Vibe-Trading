"""Write validation/test fold metrics for a completed combination batch."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from backtest.fund_rotation.metrics import compute_performance_metrics


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-id", required=True)
    parser.add_argument("--experiment", default="fund_rotation_r81_combinations_20260903_v2")
    parser.add_argument("--output-root", default=None)
    parser.add_argument("--experiment-root", default=None)
    args = parser.parse_args()
    root = (
        Path(args.output_root)
        if args.output_root
        else Path(__file__).resolve().parents[1] / "runs" / "fund_rotation"
    )
    batch_dir = root / "strategy_batches" / args.batch_id
    experiment_root = (
        Path(args.experiment_root)
        if args.experiment_root
        else root / "experiments" / args.experiment
    )
    manifest = json.loads((experiment_root / "fold_manifest.json").read_text(encoding="utf-8"))
    events = [
        json.loads(line)
        for line in (batch_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    run_ids = {
        event["strategy_id"]: event["run_id"]
        for event in events
        if event.get("event_type") == "TERMINAL"
        and event.get("scope") == "VARIANT"
        and event.get("stage") == "SUCCEEDED"
    }
    result = {"batch_id": args.batch_id, "folds": {}}
    for strategy_id, run_id in sorted(run_ids.items()):
        equity = pd.read_csv(root / run_id / "equity.csv", index_col=0)
        equity.index = equity.index.astype(str)
        series = equity["strategy"].astype(float)
        rows = []
        for fold in manifest["folds"]:
            for slice_name in ("validation", "test"):
                start, end = fold[slice_name]
                dates = [date for date in series.index if start <= date <= end]
                position = series.index.get_loc(dates[0])
                initial = float(series.iloc[position - 1]) if position else 1.0
                metrics = compute_performance_metrics(
                    series.loc[dates], periods_per_year=244, initial_nav=initial
                )
                rows.append(
                    {
                        "fold_index": fold["fold_index"],
                        "slice": slice_name,
                        "start": start,
                        "end": end,
                        "annual_return": metrics["annual_return"],
                        "sharpe": metrics["sharpe"],
                        "max_drawdown": metrics["max_drawdown"],
                        "total_return": metrics["total_return"],
                        "num_periods": metrics["num_periods"],
                    }
                )
        result["folds"][strategy_id] = rows
    (batch_dir / "fold_metrics.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
