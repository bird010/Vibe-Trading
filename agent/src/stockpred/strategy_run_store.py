"""Persistent state and provenance for one unified StockPred strategy report."""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.stockpred.run_store import atomic_json
from src.stockpred.strategies.contracts import StrategyBacktestConfig


_RUN_ID = re.compile(r"^strategy_[A-Za-z0-9_-]+$")
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


class StrategyRunStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def create(self, config: StrategyBacktestConfig, *, parent: str | None = None) -> Path:
        base = self.root / parent if parent else self.root
        base.mkdir(parents=True, exist_ok=True)
        run_id = "strategy_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "_" + uuid.uuid4().hex[:8]
        run_dir = base / run_id
        run_dir.mkdir()
        atomic_json(run_dir / "config.json", config.model_dump(mode="json"))
        atomic_json(run_dir / "req.json", {"prompt": f"StockPred strategy backtest: {config.strategy_snapshot.descriptor.name}", "context": self._context(config)})
        created_at = _now()
        atomic_json(run_dir / "state.json", {"status": "queued", "phase": "QUEUED", "created_at": created_at, "updated_at": created_at, "error_code": None, "reason": None})
        return run_dir

    def require(self, run_id: str) -> Path:
        if not _RUN_ID.fullmatch(str(run_id)):
            raise KeyError(f"invalid strategy run id: {run_id}")
        run_dir = self.root / str(run_id)
        if run_dir.is_dir():
            return run_dir
        for candidate in self.root.rglob(str(run_id)):
            if candidate.is_dir():
                return candidate
        raise KeyError(f"strategy run not found: {run_id}")

    def load_config(self, run_dir: Path) -> StrategyBacktestConfig:
        return StrategyBacktestConfig.model_validate(self._read(Path(run_dir) / "config.json"))

    def request_context(self, run_dir: Path) -> dict[str, Any]:
        return dict(self._read(Path(run_dir) / "req.json")["context"])

    def read(self, run_dir: Path) -> dict[str, Any]:
        return self._read(Path(run_dir) / "state.json")

    def transition(self, run_dir: Path, phase: str) -> None:
        normalized = str(phase).upper()
        if normalized not in _STATUS_BY_PHASE:
            raise ValueError(f"unsupported strategy run phase: {phase}")
        state = self.read(run_dir)
        state.update({"phase": normalized, "status": _STATUS_BY_PHASE[normalized], "updated_at": _now()})
        atomic_json(Path(run_dir) / "state.json", state)

    def fail(self, run_dir: Path, *, error_code: str, reason: str) -> None:
        state = self.read(run_dir)
        state.update({"phase": "FAILED", "status": "failed", "updated_at": _now(), "error_code": str(error_code), "reason": str(reason)})
        atomic_json(Path(run_dir) / "state.json", state)

    def progress(self, run_dir: Path, *, done: int, total: int, eval_date: str) -> None:
        state = self.read(run_dir)
        state.update({"updated_at": _now(), "progress": {"done": int(done), "total": int(total), "eval_date": str(eval_date)}})
        atomic_json(Path(run_dir) / "state.json", state)

    @staticmethod
    def _context(config: StrategyBacktestConfig) -> dict[str, Any]:
        return {
            "strategy_type": "stockpred_strategy",
            "strategy_id": config.strategy_snapshot.descriptor.id,
            "strategy_version": config.strategy_snapshot.strategy_version,
            "batch_id": config.batch_id,
            "comparison_key": config.comparison_key,
            "mode": config.mode,
            "start_date": config.start,
            "end_date": config.end,
        }

    @staticmethod
    def _read(path: Path) -> dict[str, Any]:
        return json.loads(path.read_text(encoding="utf-8"))
