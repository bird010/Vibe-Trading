"""Create immutable round records for the completed R81 combination study."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


ACTUAL = {
    "round01": {
        "batch_id": "6d6feb164db2",
        "hypothesis": "R81 dynamic representatives with the R57 three-factor signal",
        "challenger": "ai_rotation_r82_economic_role_dynamic_rep_r57_signal",
    },
    "round02": {
        "batch_id": "eb29d38020ea",
        "hypothesis": "R81/R57 with the R77 defense-pool relative-momentum layer",
        "challenger": "ai_rotation_r83_r81_r57_r77_combo",
    },
    "round03": {
        "batch_id": "934fe3522c7e",
        "hypothesis": "R81/R57 with the R62 true inverse-volatility weight layer",
        "challenger": "ai_rotation_r84_r81_r57_r62_combo",
    },
    "round04": {
        "batch_id": "f1c1e5d35cc3",
        "hypothesis": "R81 representatives with the R74 momentum-over-volatility score",
        "challenger": "ai_rotation_r85_r81_r74_combo",
    },
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--experiment", default="fund_rotation_r81_combinations_20260903_v2")
    args = parser.parse_args()
    experiment = (
        Path(__file__).resolve().parents[1]
        / "runs"
        / "fund_rotation"
        / "experiments"
        / args.experiment
    )
    for index in range(1, 31):
        name = f"round{index:02d}"
        path = experiment / name / "round_record.json"
        if path.exists():
            raise FileExistsError(path)
        if name in ACTUAL:
            record = {
                "round": name,
                "status": "COMPLETED_BATCH",
                **ACTUAL[name],
                "champion": "correlation_representative",
                "gate": "NOT_QUALIFIED",
                "artifacts": ["reports.json", "fold_metrics.json"],
            }
        else:
            record = {
                "round": name,
                "status": "NO_JUSTIFIED_HYPOTHESIS",
                "champion": "correlation_representative",
                "gate": "NOT_RUN",
                "reason": (
                    "R57, R77, R62 and R74 orthogonal directions were evaluated;"
                    " each candidate failed the fixed maximum-drawdown gate."
                    " R75's frozen 15% target offers no new falsifiable hypothesis"
                    " after the observed sub-15% risk candidates, so no additional"
                    " round is run merely to fill the campaign."
                ),
            }
        path.parent.mkdir(parents=True, exist_ok=False)
        path.write_text(
            json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    print(json.dumps({"rounds_created": 30, "actual_batches": 4}, ensure_ascii=False))


if __name__ == "__main__":
    main()
