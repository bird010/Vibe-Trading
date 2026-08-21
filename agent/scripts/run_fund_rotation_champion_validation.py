from __future__ import annotations

import argparse
import json
from dataclasses import fields
from pathlib import Path
from typing import Any, Mapping

from backtest.fund_rotation.champion_validation.controller import ChampionValidationController
from backtest.fund_rotation.champion_validation.contracts import DateInterval, ValidationContract
from backtest.fund_rotation.champion_validation.historical_handlers import (
    build_historical_stage_handlers,
    historical_identity,
)


_TUPLE_FIELDS = {
    "benchmark_ids",
    "ablation_variants",
    "momentum_windows",
    "top_n_values",
    "recluster_weeks",
    "stress_scenarios",
}


def _load_contract(path: str | None) -> ValidationContract:
    if path is None:
        return ValidationContract()
    contract_path = Path(path)
    try:
        payload = json.loads(contract_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError, TypeError) as exc:
        raise ValueError(f"invalid --contract file: {contract_path}") from exc
    if not isinstance(payload, Mapping):
        raise ValueError("invalid --contract file: expected a JSON object")
    allowed = {field.name for field in fields(ValidationContract)}
    unknown = set(payload) - allowed
    if unknown:
        raise ValueError(f"invalid --contract fields: {sorted(unknown)}")
    values: dict[str, Any] = dict(payload)
    for field_name in ("research_interval", "consumed_confirmation_interval"):
        if field_name in values:
            value = values[field_name]
            if not isinstance(value, Mapping) or set(value) != {"start", "end"}:
                raise ValueError(f"invalid --contract {field_name}")
            values[field_name] = DateInterval(value["start"], value["end"])
    for field_name in _TUPLE_FIELDS:
        if field_name in values:
            value = values[field_name]
            if not isinstance(value, list):
                raise ValueError(f"invalid --contract {field_name}")
            values[field_name] = tuple(value)
    try:
        return ValidationContract(**values)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid --contract: {exc}") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="运行 R11 Champion 可信度验证")
    parser.add_argument("--experiment-dir", required=True)
    parser.add_argument("--contract", help="JSON 文件形式的冻结 ValidationContract")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--idempotency-key", default="")
    parser.add_argument("--source-dir", help="Round 11 frozen artifact directory")
    args = parser.parse_args(argv)
    contract = _load_contract(args.contract)
    ChampionValidationController(
        Path(args.experiment_dir),
        contract=contract,
        identity=historical_identity(args.source_dir),
        stage_handlers=build_historical_stage_handlers(args.source_dir),
    ).run(
        resume=args.resume,
        idempotency_key=args.idempotency_key,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
