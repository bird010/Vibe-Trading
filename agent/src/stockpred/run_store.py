"""Persistent state machine for StockPred Graph runs."""

from __future__ import annotations

import json
import re
import time
import uuid
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path

from src.stockpred.graph.backtest_config import GraphBacktestConfig


_RUN_ID = re.compile(r"^graph_[A-Za-z0-9_-]+$")
_STATUS_BY_PHASE = {
    "QUEUED": "queued",
    "VALIDATING": "running",
    "RUNNING": "running",
    "FINALIZING": "running",
    "SUCCEEDED": "success",
    "FAILED": "failed",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def atomic_json(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str)
        + "\n",
        encoding="utf-8",
    )
    for attempt in range(5):
        try:
            temporary.replace(path)
            return
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(0.05 * (attempt + 1))


class StockPredRunStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def create(self, config: GraphBacktestConfig) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        run_id = (
            "graph_"
            + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
            + "_"
            + uuid.uuid4().hex[:8]
        )
        run_dir = self.root / run_id
        run_dir.mkdir()
        atomic_json(run_dir / "config.json", config.model_dump(mode="json"))
        atomic_json(
            run_dir / "req.json",
            {
                "prompt": "StockPred Graph backtest",
                "context": {
                    "strategy_type": "stockpred_graph",
                    "strategy_id": "stockpred_graph",
                    "strategy_kind": "graph",
                    "start_date": config.start,
                    "end_date": config.end,
                    "mode": config.mode,
                    "benchmark_code": config.benchmark_code,
                    "codes": [],
                },
            },
        )
        created_at = _now()
        atomic_json(
            run_dir / "state.json",
            {
                "status": "queued",
                "phase": "QUEUED",
                "created_at": created_at,
                "updated_at": created_at,
                "error_code": None,
                "reason": None,
            },
        )
        return run_dir

    def require(self, run_id: str) -> Path:
        if not _RUN_ID.fullmatch(str(run_id)):
            raise KeyError(f"invalid StockPred run id: {run_id}")
        run_dir = self.root / str(run_id)
        if not run_dir.is_dir():
            raise KeyError(f"StockPred run not found: {run_id}")
        return run_dir

    def load_config(self, run_dir: Path) -> GraphBacktestConfig:
        payload = json.loads((Path(run_dir) / "config.json").read_text(encoding="utf-8"))
        return GraphBacktestConfig.model_validate(payload)

    def read(self, run_dir: Path) -> dict[str, object]:
        return json.loads((Path(run_dir) / "state.json").read_text(encoding="utf-8"))

    def transition(self, run_dir: Path, phase: str) -> None:
        normalized = str(phase).upper()
        if normalized not in _STATUS_BY_PHASE:
            raise ValueError(f"unsupported StockPred run phase: {phase}")
        state = self.read(run_dir)
        state.update(
            {
                "phase": normalized,
                "status": _STATUS_BY_PHASE[normalized],
                "updated_at": _now(),
            }
        )
        atomic_json(Path(run_dir) / "state.json", state)

    def fail(self, run_dir: Path, *, error_code: str, reason: str) -> None:
        state = self.read(run_dir)
        state.update(
            {
                "phase": "FAILED",
                "status": "failed",
                "updated_at": _now(),
                "error_code": str(error_code),
                "reason": str(reason),
            }
        )
        atomic_json(Path(run_dir) / "state.json", state)

    def progress(
        self,
        run_dir: Path,
        *,
        done: int,
        total: int,
        eval_date: str,
    ) -> None:
        state = self.read(run_dir)
        state.update(
            {
                "updated_at": _now(),
                "progress": {
                    "done": int(done),
                    "total": int(total),
                    "eval_date": str(eval_date),
                },
            }
        )
        atomic_json(Path(run_dir) / "state.json", state)
