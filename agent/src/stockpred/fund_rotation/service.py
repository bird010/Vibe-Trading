"""FundRotationBacktestService — §15. Orchestrates backtest runs."""

from __future__ import annotations

import logging
import threading
import time
import traceback
from pathlib import Path

from backtest.fund_rotation.config import FundRotationConfig
from src.stockpred.fund_rotation.persistence import (
    RunDirectory,
    IdempotencyGuard,
    atomic_write_json,
    request_fingerprint,
)
from src.stockpred.fund_rotation.state_machine import (
    TaskStage,
    TaskStateMachine,
)
from src.stockpred.fund_rotation.artifacts import (
    publish_manifest,
    write_debug_json,
    write_run_artifacts,
)

logger = logging.getLogger(__name__)


class StructuredError(Exception):
    """§18 — Structured error with code, stage, message, details, action_hint."""

    def __init__(self, code: str, stage: str, message: str, details: str = "", action_hint: str = ""):
        super().__init__(message)
        self.code = code
        self.stage = stage
        self.message = message
        self.details = details
        self.action_hint = action_hint

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "stage": self.stage,
            "message": self.message,
            "details": self.details,
            "action_hint": self.action_hint,
        }


class FundRotationBacktestService:
    """§15 — Manages fund rotation backtest lifecycle.

    File-based persistence, no database. Each run gets its own directory.
    """

    def __init__(self, runs_dir: Path, stockpred_root: Path | None = None) -> None:
        self.runs_dir = runs_dir
        self.stockpred_root = stockpred_root
        self.idempotency = IdempotencyGuard(runs_dir)

    def get_defaults(self) -> dict:
        """GET /stockpred/fund-rotation/defaults — parameter schema and defaults."""
        cfg = FundRotationConfig()
        return {
            "params": {
                "k": cfg.k,
                "top_n": cfg.top_n,
                "momentum_window_weeks": cfg.momentum_window_weeks,
                "recluster_interval_weeks": cfg.recluster_interval_weeks,
                "correlation_lookback_weeks": cfg.correlation_lookback_weeks,
                "min_training_weeks": cfg.min_training_weeks,
                "min_valid_weeks": cfg.min_valid_weeks,
                "min_pairwise_weeks": cfg.min_pairwise_weeks,
                "momentum_threshold": cfg.momentum_threshold,
                "initial_capital": cfg.initial_capital,
                "commission_rate": cfg.commission_rate,
                "commission_min": cfg.commission_min,
                "other_fee_rate": cfg.other_fee_rate,
                "max_participation_rate": cfg.max_participation_rate,
                "adv_lookback": cfg.adv_lookback,
                "base_slippage_bps": cfg.base_slippage_bps,
                "max_slippage_bps": cfg.max_slippage_bps,
            },
            "schema_version": "v1",
            "mode": "RESEARCH_ONLY",
        }

    def submit_backtest(self, params: dict, idempotency_key: str) -> tuple[str, int]:
        """POST /stockpred/fund-rotation/backtests — create and queue a run.

        Returns:
            (run_id, http_status) — 202 for new, 200 for existing.

        Raises:
            ValueError: If idempotency conflict (409).
            StructuredError: If params invalid.
        """
        # Validate params
        try:
            config = FundRotationConfig(**{k: v for k, v in params.items() if k != "idempotency_key"})
        except (ValueError, TypeError) as e:
            raise StructuredError(
                code="INVALID_PARAMS",
                stage="VALIDATION",
                message=str(e),
                action_hint="Check parameter values against defaults endpoint",
            ) from e

        # Idempotency check
        run_id, existing_status = self.idempotency.check(idempotency_key, params)
        if existing_status is not None:
            return run_id, 200  # Already exists

        # Create run directory and initial state
        run_dir = RunDirectory(self.runs_dir, run_id)
        run_dir.ensure()
        run_dir.write_request(params)

        sm = TaskStateMachine()
        run_dir.write_state({
            "stage": sm.stage.value,
            "run_id": run_id,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "params_fingerprint": request_fingerprint(params),
        })
        run_dir.append_event({"seq": 0, "stage": "QUEUED", "ts": time.time()})

        # Launch background execution
        thread = threading.Thread(
            target=self._execute_run,
            args=(run_id, config, params),
            daemon=True,
            name=f"fund-rotation-{run_id}",
        )
        thread.start()

        return run_id, 202

    def _execute_run(self, run_id: str, config: FundRotationConfig, params: dict) -> None:
        """Background thread: advance state machine and run pipeline."""
        run_dir = RunDirectory(self.runs_dir, run_id)
        sm = TaskStateMachine()
        stage_times: dict[str, float] = {}

        def advance(target: TaskStage, *, emit_event: bool = True) -> int:
            seq = sm.transition(target)
            stage_times[target.value] = time.time()
            run_dir.write_state({
                "stage": sm.stage.value,
                "run_id": run_id,
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "params_fingerprint": request_fingerprint(params),
                "stage_times": stage_times,
            })
            if emit_event:
                run_dir.append_event({"seq": seq, "stage": target.value, "ts": time.time()})
            return seq

        try:
            # VALIDATING_DATA
            advance(TaskStage.VALIDATING_DATA)
            fund_daily, fund_adj, dim_fund, data_snapshot = self._load_data(config)

            # §5: Write data_snapshot.json (task-local, not shared)
            atomic_write_json(run_dir.path / "data_snapshot.json", data_snapshot)

            # PREPARING_RETURNS — advanced via stage_callback from pipeline
            # Stage callback maps pipeline stage names to state machine transitions
            stage_map = {
                "PREPARING_RETURNS": TaskStage.PREPARING_RETURNS,
                "CLUSTERING": TaskStage.CLUSTERING,
                "GENERATING_TARGETS": TaskStage.GENERATING_TARGETS,
                "EXECUTING": TaskStage.EXECUTING,
                "COMPUTING_BENCHMARKS": TaskStage.COMPUTING_BENCHMARKS,
            }

            def _stage_callback(stage_name: str) -> None:
                target = stage_map.get(stage_name)
                if target and sm.stage != target:
                    advance(target)

            from backtest.fund_rotation.pipeline import run_signal_pipeline
            result = run_signal_pipeline(
                config, fund_daily, fund_adj, dim_fund,
                stage_callback=_stage_callback,
            )

            # WRITING_RESULTS
            advance(TaskStage.WRITING_RESULTS)
            exclusions_dicts = [
                {"ts_code": e.ts_code, "reason": e.reason.value, "details": e.details, "signal_date": e.signal_date}
                for e in result.exclusions
            ]
            manifest = write_run_artifacts(
                run_dir.path,
                weekly_targets=result.weekly_targets,
                cluster_history=result.cluster_history,
                exclusions=exclusions_dicts,
                strategy_cumulative=result.strategy_cumulative,
                equal_weight_benchmark=result.equal_weight_benchmark,
                buy_hold_benchmark=result.buy_hold_benchmark,
                cash_benchmark=result.cash_benchmark,
                strategy_metrics=result.strategy_metrics,
                benchmark_metrics=result.benchmark_metrics,
                config_params=params,
                num_weeks=result.num_weeks,
                num_reclusters=result.num_reclusters,
                num_etfs_used=result.num_etfs_used,
                trade_events=result.trade_events,
                positions_history=result.positions_history,
                executed_equity=result.executed_equity,
                robustness=result.robustness,
                orders=result.orders,
                data_snapshot=data_snapshot,
            )

            # Persist success state first; manifest is the final atomic
            # publication boundary, and the success event is emitted only
            # after readers can validate both.
            success_seq = advance(TaskStage.SUCCEEDED, emit_event=False)
            publish_manifest(
                run_dir.path,
                manifest,
                run_id=run_id,
                params_fingerprint=request_fingerprint(params),
                terminal_event_seq=success_seq,
            )
            try:
                run_dir.append_event({"seq": success_seq, "stage": "SUCCEEDED", "ts": time.time()})
            except OSError:
                logger.warning("Run %s published but success event append failed", run_id, exc_info=True)
            logger.info("Run %s succeeded", run_id)

        except Exception as exc:
            logger.error("Run %s failed at %s: %s", run_id, sm.stage.value, exc)
            # §18: Preserve structured error code; traceback only in debug.json
            if isinstance(exc, StructuredError):
                error_dict = exc.to_dict()
                error_dict["stage"] = sm.stage.value
            else:
                error_dict = {
                    "code": "RUNTIME_ERROR",
                    "stage": sm.stage.value,
                    "message": str(exc),
                    "details": "",
                    "action_hint": "Check data availability and parameters",
                }
            # Full traceback goes to debug.json only (local diagnostics)
            write_debug_json(run_dir.path, {**error_dict, "traceback": traceback.format_exc()}, sm.stage.value)
            # Transition to FAILED if possible
            try:
                sm.transition(TaskStage.FAILED)
            except Exception:
                pass
            # state.json gets the structured error WITHOUT traceback
            run_dir.write_state({
                "stage": TaskStage.FAILED.value,
                "run_id": run_id,
                "error": error_dict,
                "params_fingerprint": request_fingerprint(params),
            })
            run_dir.append_event({"seq": sm.next_event_seq(), "stage": "FAILED", "ts": time.time(), "error": error_dict.get("message", str(exc))})

    def _load_data(self, config: FundRotationConfig):
        """Load data from StockPred Lance datasets at PINNED versions.

        Returns (fund_daily, fund_adj, dim_fund, data_snapshot) tuple.
        data_snapshot is a task-local dict (NOT stored on self) for concurrency safety.
        Raises StructuredError if data unavailable.
        """
        if self.stockpred_root is None:
            raise StructuredError(
                code="NO_DATA_ROOT",
                stage="VALIDATING_DATA",
                message="stockpred_root not configured",
                action_hint="Set stockpred_root to StockPred project path",
            )

        lance_dir = self.stockpred_root / "data" / "lance" / "market_core"

        try:
            import lance  # noqa: F401
        except ImportError as e:
            raise StructuredError(
                code="LANCE_UNAVAILABLE",
                stage="VALIDATING_DATA",
                message="pylance not installed",
                action_hint="pip install pylance",
            ) from e

        # Fail fast with structured codes before pinning versions.
        for name, code in (
            ("fund.lance", "FUND_LANCE_MISSING"),
            ("dim_fund.lance", "DIM_FUND_MISSING"),
            ("fact_fund_adj.lance", "FUND_ADJ_MISSING"),
        ):
            if not (lance_dir / name).exists():
                raise StructuredError(
                    code=code,
                    stage="VALIDATING_DATA",
                    message=f"{name} not found at {lance_dir / name}",
                    action_hint="Run StockPred data update first",
                )

        # Compute date bounds BEFORE reading (training window lookback).
        data_start: str | None = None
        data_end: str | None = config.end_date or None
        if config.start_date:
            import datetime
            try:
                sd = datetime.datetime.strptime(config.start_date, "%Y%m%d")
                training_days = (config.min_training_weeks + config.momentum_window_weeks + 4) * 7
                data_start = (sd - datetime.timedelta(days=training_days)).strftime("%Y%m%d")
            except ValueError:
                data_start = config.start_date

        # Pin the three Lance versions BEFORE any business read, then read at
        # those versions only — never reopen the latest version (§2/§11).
        from src.stockpred.fund_rotation.data_snapshot import (
            hash_codes,
            load_pinned_frames,
            resolve_pinned_snapshot,
        )
        snapshot = resolve_pinned_snapshot(lance_dir)
        fund_daily, fund_adj, dim_fund = load_pinned_frames(
            snapshot, lance_dir, data_start=data_start, data_end=data_end,
        )

        # §5/§11: immutable snapshot identity for the manifest — three versions,
        # ETF pool hash, trading calendar hash, and the total fingerprint.
        data_snapshot = {
            "datasets": {
                "fund.lance": {
                    "version": snapshot.fund_version,
                    "path": str(lance_dir / "fund.lance"),
                },
                "dim_fund.lance": {
                    "version": snapshot.dim_version,
                    "path": str(lance_dir / "dim_fund.lance"),
                },
                "fact_fund_adj.lance": {
                    "version": snapshot.fund_adj_version,
                    "path": str(lance_dir / "fact_fund_adj.lance"),
                },
            },
            "universe_codes_hash": hash_codes(snapshot.universe_codes),
            "trading_dates_hash": hash_codes(snapshot.trading_dates),
            "universe_count": len(snapshot.universe_codes),
            "trading_dates_count": len(snapshot.trading_dates),
            "fingerprint": snapshot.fingerprint,
            "load_range": {
                "data_start": data_start,
                "data_end": data_end,
            },
            "loaded_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }

        return fund_daily, fund_adj, dim_fund, data_snapshot

    def list_backtests(self, limit: int = 20) -> list[dict]:
        """GET /stockpred/fund-rotation/backtests — list runs."""
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
        """GET /stockpred/fund-rotation/backtests/{run_id} — run detail."""
        run_dir = RunDirectory(self.runs_dir, run_id)
        state = run_dir.read_state()
        if state is None:
            return None
        if state.get("stage") == "SUCCEEDED" and not self._is_published(run_dir.path, state):
            return {**state, "stage": "WRITING_RESULTS", "result_published": False}

        # Add summary only from the checksum-verified published result.
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
        """§15.2 — On startup, mark orphaned running states as FAILED_INTERRUPTED."""
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
