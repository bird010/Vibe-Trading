"""FundRotationBacktestService — legacy v1 run read-only access (§15)."""

from __future__ import annotations

import logging
from pathlib import Path

from src.stockpred.fund_rotation.persistence import (
    RunDirectory,
    IdempotencyGuard,
    atomic_write_json,
)
from src.stockpred.fund_rotation.state_machine import TaskStateMachine

logger = logging.getLogger(__name__)


class FundRotationBacktestService:
    """Legacy v1 read-only access for historical fund rotation backtest runs.

    Write operations (POST /backtests, GET /defaults) were removed in Phase 6.
    New runs must use the strategy batch API (POST /strategy-batches).
    """

    def __init__(self, runs_dir: Path, stockpred_root: Path | None = None) -> None:
        self.runs_dir = runs_dir
        self.stockpred_root = stockpred_root
        self.idempotency = IdempotencyGuard(runs_dir)

    def list_backtests(self, limit: int = 20) -> list[dict]:
        """List completed v1 runs."""
        fund_dir = self.runs_dir / "fund_rotation"
        if not fund_dir.exists():
            return []

        runs = []
        for d in sorted(fund_dir.iterdir(), reverse=True):
            if not d.is_dir():
                continue
            state_path = d / "state.json"
            if state_path.exists():
                import json

                with open(state_path, encoding="utf-8") as f:
                    state = json.load(f)
                if state.get("stage") == "SUCCEEDED" and not self._is_published(d, state):
                    state["stage"] = "WRITING_RESULTS"
                runs.append(state)
            if len(runs) >= limit:
                break
        return runs

    def get_backtest(self, run_id: str) -> dict | None:
        """Get v1 run detail."""
        run_dir = RunDirectory(self.runs_dir, run_id)
        state = run_dir.read_state()
        if state is None:
            return None
        if state.get("stage") == "SUCCEEDED" and not self._is_published(run_dir.path, state):
            return {**state, "stage": "WRITING_RESULTS", "result_published": False}

        summary_path = run_dir.path / "summary.json"
        if state.get("stage") == "SUCCEEDED" and summary_path.exists():
            import json

            from src.stockpred.fund_rotation.artifacts import compute_file_checksum

            manifest = json.loads((run_dir.path / "manifest.json").read_text(encoding="utf-8"))
            expected = manifest.get("file_details", {}).get("summary.json", {}).get("checksum")
            if expected and compute_file_checksum(summary_path) == expected:
                with open(summary_path, encoding="utf-8") as f:
                    state["summary"] = json.load(f)

        return state

    @staticmethod
    def _is_published(run_path: Path, state: dict) -> bool:
        manifest_path = run_path / "manifest.json"
        if not manifest_path.exists():
            return False
        try:
            import json

            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        from src.stockpred.fund_rotation.artifacts import compute_file_checksum

        return bool(
            manifest.get("status") == "SUCCEEDED"
            and manifest.get("run_id") == state.get("run_id")
            and manifest.get("params_fingerprint") == state.get("params_fingerprint")
            and manifest.get("state_checksum") == compute_file_checksum(run_path / "state.json")
        )

    def recover_interrupted(self) -> int:
        """On startup, mark orphaned running v1 states as FAILED_INTERRUPTED."""
        fund_dir = self.runs_dir / "fund_rotation"
        if not fund_dir.exists():
            return 0

        recovered = 0
        for d in fund_dir.iterdir():
            if not d.is_dir():
                continue
            state_path = d / "state.json"
            if not state_path.exists():
                continue
            import json

            with open(state_path, encoding="utf-8") as f:
                state = json.load(f)
            if TaskStateMachine.detect_interrupted(state):
                updated = TaskStateMachine.mark_interrupted(state)
                atomic_write_json(state_path, updated)
                recovered += 1
                logger.info("Recovered interrupted run: %s", d.name)

        return recovered
