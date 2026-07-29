"""Dedicated CLI adapter for StockPred data and Graph backtests."""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from backtest.stockpred_graph.runner import GraphBacktestRunner
from src.stockpred.backtest_service import GraphBacktestService
from src.stockpred.contracts import ModelSnapshot, StockPredDataError
from src.stockpred.gateway import StockPredDataGateway
from src.stockpred.graph.backtest_config import GraphBacktestConfig
from src.stockpred.graph.service import GraphSignalService
from src.stockpred.snapshot import build_snapshot, resolve_stockpred_root


def add_subparser(subparsers: Any) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        "stockpred",
        help="StockPred data and Graph backtests",
    )
    commands = parser.add_subparsers(dest="stockpred_command")
    status = commands.add_parser("status", help="Validate StockPred data contract")
    status.add_argument("--json", action="store_true", dest="stockpred_json")
    backtest = commands.add_parser(
        "graph-backtest",
        help="Run StockPred Graph backtest",
    )
    backtest.add_argument("--start", required=True)
    backtest.add_argument("--end", required=True)
    backtest.add_argument("--mode", choices=("parity", "research"), default="parity")
    backtest.add_argument("--top-n", type=int, default=None)
    backtest.add_argument("--eval-step", type=int, default=None)
    backtest.add_argument("--parity-golden", default=None)
    backtest.add_argument("--json", action="store_true", dest="stockpred_json")
    return parser


def _model() -> ModelSnapshot:
    return ModelSnapshot(
        id="stockpred-graph",
        version="graph-v1",
        config_sha256="0" * 64,
    )


def probe_stockpred_status() -> dict[str, object]:
    try:
        root = resolve_stockpred_root()
        manifest = build_snapshot(
            root,
            as_of=datetime.now(ZoneInfo("Asia/Taipei")),
            model=_model(),
        )
        return {
            "ready": True,
            "contract": manifest.contract,
            "root": str(root),
            "tables": [
                {
                    "name": name,
                    "version": snapshot.version,
                    "max_date": snapshot.max_date,
                }
                for name, snapshot in sorted(manifest.tables.items())
            ],
            "error_code": None,
            "message": "ready",
        }
    except StockPredDataError as exc:
        return {
            "ready": False,
            "contract": "stockpred-data/v1",
            "root": None,
            "tables": [],
            "error_code": exc.code,
            "message": str(exc),
        }


def build_service(runs_root: Path | None = None) -> GraphBacktestService:
    root = resolve_stockpred_root()
    agent_dir = Path(__file__).resolve().parents[2]
    resolved_runs_root = runs_root or Path(
        os.getenv("VIBE_TRADING_RUNS_DIR", agent_dir / "runs")
    )

    def snapshot_factory(config: GraphBacktestConfig):  # noqa: ANN202
        as_of = datetime.strptime(config.end, "%Y%m%d").replace(
            hour=15,
            tzinfo=ZoneInfo("Asia/Taipei"),
        )
        return build_snapshot(root, as_of=as_of, model=_model())

    def runner_factory(manifest):  # noqa: ANN001, ANN202
        gateway = StockPredDataGateway(root, manifest)
        return GraphBacktestRunner(gateway, GraphSignalService(gateway))

    return GraphBacktestService(resolved_runs_root, runner_factory, snapshot_factory)


def _emit(payload: dict[str, object], *, json_mode: bool) -> None:
    if json_mode:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return
    if payload.get("status") == "success" or payload.get("ready") is True:
        print(payload.get("message") or f"run completed: {payload.get('run_id')}")
    else:
        print(payload.get("reason") or payload.get("message") or "StockPred command failed")


def _config_from_args(args: argparse.Namespace) -> GraphBacktestConfig:
    values: dict[str, object] = {
        "start": args.start,
        "end": args.end,
        "mode": args.mode,
    }
    if args.top_n is not None:
        values["top_n"] = args.top_n
    if args.eval_step is not None:
        values["eval_step"] = args.eval_step
    if args.parity_golden:
        golden = Path(args.parity_golden).expanduser().resolve()
        if not golden.is_dir():
            raise StockPredDataError(
                "STOCKPRED_GOLDEN_MISSING",
                f"parity golden directory does not exist: {golden}",
            )
        values["parity_reference"] = str(golden)
    return GraphBacktestConfig.model_validate(values)


def dispatch(args: argparse.Namespace) -> int:
    json_mode = bool(getattr(args, "stockpred_json", False))
    if args.stockpred_command == "status":
        status = probe_stockpred_status()
        _emit(status, json_mode=json_mode)
        return 0 if status["ready"] else 1
    if args.stockpred_command != "graph-backtest":
        _emit(
            {
                "status": "failed",
                "error_code": "STOCKPRED_COMMAND_REQUIRED",
                "reason": "stockpred requires a subcommand",
            },
            json_mode=json_mode,
        )
        return 2
    try:
        config = _config_from_args(args)
        service = build_service()
        run_id = service.run(config)
        state = service.store.read(service.store.require(run_id))
    except ValidationError as exc:
        _emit(
            {
                "status": "failed",
                "error_code": "STOCKPRED_CONFIG_INVALID",
                "reason": str(exc),
            },
            json_mode=json_mode,
        )
        return 2
    except StockPredDataError as exc:
        _emit(
            {"status": "failed", "error_code": exc.code, "reason": str(exc)},
            json_mode=json_mode,
        )
        return 2
    payload = {
        "status": state.get("status"),
        "run_id": run_id,
        "error_code": state.get("error_code"),
        "reason": state.get("reason"),
    }
    _emit(payload, json_mode=json_mode)
    return 0 if state.get("status") == "success" else 1
