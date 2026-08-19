"""Persistent StockPred Graph status, backtest, and SSE routes."""

from __future__ import annotations

import asyncio
from io import BytesIO
import json
import math
import re
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, ValidationError

from src.stockpred.cli_handlers import (
    build_service as _build_service,
    probe_stockpred_status,
)
from src.stockpred.graph.backtest_config import GraphBacktestConfig
from src.stockpred.strategies.catalog import StrategyCatalog
from src.stockpred.strategies.contracts import StrategyBatchRequest


AuthDependency = Callable[..., Awaitable[Any] | Any]
_RUN_ID = re.compile(r"^graph_[A-Za-z0-9_-]+$")
_BATCH_ID = re.compile(r"^batch_[A-Za-z0-9_-]+$")
_RUNNING_TASKS: set[asyncio.Task[Any]] = set()
_BATCH_HEARTBEAT_STALE_SECONDS = 300.0


async def _execute_claimed_batch(
    service: Any, batch_id: str, idempotency_key: str, lease_token: str
) -> None:
    """Execute only while this task still owns its idempotency lease."""
    if not service.store.confirm_execution(idempotency_key, lease_token):
        return
    await asyncio.to_thread(service.execute, batch_id)


def _schedule_task(coro: Any) -> asyncio.Task[Any]:
    return asyncio.create_task(coro)


def build_service(runs_dir: Path):  # noqa: ANN201
    return _build_service(runs_dir)


def build_catalog() -> StrategyCatalog:
    return StrategyCatalog()


def build_batch_service(runs_dir: Path):  # noqa: ANN201
    from src.stockpred.batch_service import StockPredStrategyBatchService
    from src.stockpred.batch_store import StockPredBatchStore
    from src.stockpred.snapshot import resolve_stockpred_root
    from src.stockpred.strategy_execution import StrategyReportExecutor

    return StockPredStrategyBatchService(
        StockPredBatchStore(Path(runs_dir) / "strategy_batches"),
        build_catalog(),
        StrategyReportExecutor(Path(runs_dir), resolve_stockpred_root()),
    )


class GraphBacktestRequest(BaseModel):
    start: date
    end: date
    mode: Literal["parity", "research"] = "parity"
    top_n: int | None = Field(None, ge=1, le=500)
    eval_step: int | None = Field(None, ge=1, le=60)

    def to_config(self) -> GraphBacktestConfig:
        values: dict[str, object] = {
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "mode": self.mode,
        }
        if self.top_n is not None:
            values["top_n"] = self.top_n
        if self.eval_step is not None:
            values["eval_step"] = self.eval_step
        return GraphBacktestConfig.model_validate(values)


class StrategyBatchCreateRequest(BaseModel):
    idempotency_key: uuid.UUID
    start: str
    end: str
    strategy_ids: list[str] = []
    select_all: bool = False
    mode: str = "parity"
    top_n: int = 50
    eval_step: int = 5
    forward_days: int = 5
    portfolio_capital: float = 10_000_000.0
    max_participation: float = 0.05


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _sanitize_json(value: Any) -> Any:
    """Convert every nested non-finite number into JSON's explicit null."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {str(key): _sanitize_json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_sanitize_json(item) for item in value]
    return value


def _iter_run_dirs(root: Path) -> list[Path]:
    """Collect run directories from root and nested strategy_batches/<batch>/ dirs."""
    dirs: list[Path] = []
    if not root.is_dir():
        return dirs
    for entry in root.iterdir():
        if not entry.is_dir():
            continue
        if entry.name == "strategy_batches":
            for batch_dir in entry.iterdir():
                if batch_dir.is_dir():
                    dirs.extend(p for p in batch_dir.iterdir() if p.is_dir() and p.name.startswith("strategy_"))
        elif entry.name.startswith("strategy_") or entry.name.startswith("graph_"):
            dirs.append(entry)
    return dirs


def list_graph_run_summaries(runs_dir: Path, *, limit: int) -> list[dict[str, Any]]:
    root = Path(runs_dir)
    rows: list[dict[str, Any]] = []
    directories = sorted(
        _iter_run_dirs(root),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for run_dir in directories:
        request = _read_json(run_dir / "req.json")
        context = request.get("context") if isinstance(request.get("context"), dict) else None
        if context is None:
            continue
        strategy_type = context.get("strategy_type", "")
        if strategy_type not in ("stockpred_graph", "stockpred_strategy"):
            continue
        state = _read_json(run_dir / "state.json")
        summary: dict[str, Any] = {
            "run_id": run_dir.name,
            "status": state.get("status", "unknown"),
            "phase": state.get("phase"),
            "created_at": state.get("created_at", ""),
            "start": context.get("start_date", ""),
            "end": context.get("end_date", ""),
            "mode": context.get("mode", "parity"),
        }
        if strategy_type == "stockpred_strategy":
            summary["strategy_id"] = context.get("strategy_id", "")
            summary["strategy_name"] = context.get("strategy_name", context.get("strategy_id", ""))
        rows.append(summary)
        if len(rows) >= limit:
            break
    return rows


def _sse(event: str, payload: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


async def iter_strategy_batch_events(
    state_path: Path,
    request: Request,
    *,
    poll_seconds: float = 0.5,
) -> AsyncIterator[str]:
    previous = ""
    while True:
        if await request.is_disconnected():
            return
        state = _read_json(state_path)
        encoded = json.dumps(state, ensure_ascii=False, sort_keys=True)
        if encoded != previous:
            yield _sse("progress", state)
            previous = encoded
        status = state.get("status")
        if status in {"completed", "completed_with_failures"}:
            yield _sse("done", state)
            return
        if status == "stalled":
            yield _sse("batch_error", state)
            return
        await asyncio.sleep(poll_seconds)


def register_stockpred_routes(
    app: FastAPI,
    *,
    runs_dir: Path,
    require_auth: AuthDependency,
    require_event_stream_auth: AuthDependency,
) -> None:
    root = Path(runs_dir)

    def scan_stalled_batches() -> None:
        # Startup recovery is deliberately read/write state only; it never resumes work.
        from src.stockpred.batch_store import StockPredBatchStore

        StockPredBatchStore(root / "strategy_batches").mark_expired_stalled(
            now=datetime.now(timezone.utc),
            stale_after_seconds=_BATCH_HEARTBEAT_STALE_SECONDS,
        )

    app.router.on_startup.append(scan_stalled_batches)

    @app.get("/stockpred/status", dependencies=[Depends(require_auth)])
    def get_status() -> dict[str, object]:
        return probe_stockpred_status()

    @app.get("/stockpred/strategies", dependencies=[Depends(require_auth)])
    def list_strategies() -> dict[str, list[dict[str, object]]]:
        return {"strategies": [item.model_dump(mode="json") for item in build_catalog().list()]}

    @app.get("/stockpred/strategy-batches", dependencies=[Depends(require_auth)])
    def list_unfinished_strategy_batches() -> list[dict[str, Any]]:
        return build_batch_service(root).store.list_unfinished()

    @app.get("/stockpred/strategy-batches/recent", dependencies=[Depends(require_auth)])
    def list_recent_strategy_batches(limit: int = Query(20, ge=1, le=100)) -> list[dict[str, Any]]:
        return build_batch_service(root).store.list_recent(limit=limit)

    @app.post("/stockpred/strategy-batches", status_code=202, dependencies=[Depends(require_auth)])
    async def create_strategy_batch(body: StrategyBatchCreateRequest) -> dict[str, str]:
        idempotency_key = str(body.idempotency_key)
        request = StrategyBatchRequest.model_validate(body.model_dump(exclude={"idempotency_key"}))
        service = build_batch_service(root)
        batch_id, _created = service.reserve_idempotent(request, idempotency_key=idempotency_key)
        lease_token = service.store.try_claim_execution(idempotency_key)
        if lease_token:
            async def _run_execution(token: str = lease_token) -> None:
                # Confirm only once the task is actually running; a crash before
                # this point leaves an expiring lease a same-key retry can take over.
                # The token fences off a superseded owner from confirming/releasing.
                await _execute_claimed_batch(service, batch_id, idempotency_key, token)

            try:
                task = _schedule_task(_run_execution())
            except BaseException:
                service.store.release_execution(idempotency_key, lease_token)
                raise
            _RUNNING_TASKS.add(task)
            task.add_done_callback(_RUNNING_TASKS.discard)
        return {"batch_id": batch_id, "events_url": f"/stockpred/strategy-batches/{batch_id}/events"}

    @app.get("/stockpred/strategy-batches/{batch_id}", dependencies=[Depends(require_auth)])
    def get_strategy_batch(batch_id: str, sort_by: str = "sharpe", descending: bool = True) -> dict[str, Any]:
        try:
            return build_batch_service(root).store.summary(batch_id, sort_by=sort_by, descending=descending)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="strategy batch not found") from exc

    @app.get("/stockpred/strategy-batches/{batch_id}/events", dependencies=[Depends(require_event_stream_auth)])
    async def stream_strategy_batch(batch_id: str, request: Request) -> StreamingResponse:
        if not _BATCH_ID.fullmatch(batch_id):
            raise HTTPException(status_code=404, detail="strategy batch not found")
        state_path = root / "strategy_batches" / batch_id / "state.json"
        if not state_path.is_file():
            raise HTTPException(status_code=404, detail="strategy batch not found")

        return StreamingResponse(iter_strategy_batch_events(state_path, request), media_type="text/event-stream")

    @app.get("/stockpred/graph/defaults", dependencies=[Depends(require_auth)])
    def get_defaults() -> dict[str, object]:
        config = GraphBacktestConfig(start="2000-01-01", end="2000-01-02")
        return {
            "mode": "parity",
            "benchmark_code": config.benchmark_code,
            "top_n": config.top_n,
            "eval_step": config.eval_step,
            "forward_days": config.forward_days,
            "locked_fields": [
                "top_n",
                "eval_step",
                "forward_days",
                "benchmark_code",
            ],
        }

    @app.get("/stockpred/graph/backtests", dependencies=[Depends(require_auth)])
    def list_backtests(
        limit: int = Query(20, ge=1, le=100),
    ) -> list[dict[str, Any]]:
        return list_graph_run_summaries(root, limit=limit)

    @app.post(
        "/stockpred/graph/backtests",
        status_code=202,
        dependencies=[Depends(require_auth)],
    )
    async def create_backtest(body: GraphBacktestRequest) -> dict[str, str]:
        try:
            config = body.to_config()
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=exc.errors()) from exc
        service = build_service(root)
        run_id = service.reserve(config)
        task = asyncio.create_task(asyncio.to_thread(service.execute, run_id))
        _RUNNING_TASKS.add(task)
        task.add_done_callback(_RUNNING_TASKS.discard)
        return {
            "run_id": run_id,
            "events_url": f"/stockpred/graph/backtests/{run_id}/events",
        }

    @app.get(
        "/stockpred/graph/backtests/{run_id}/events",
        dependencies=[Depends(require_event_stream_auth)],
    )
    async def stream_backtest(run_id: str, request: Request) -> StreamingResponse:
        if not _RUN_ID.fullmatch(run_id):
            raise HTTPException(status_code=404, detail="run not found")
        run_dir = root / run_id
        state_path = run_dir / "state.json"
        if not state_path.is_file():
            raise HTTPException(status_code=404, detail="run not found")

        async def events():  # noqa: ANN202
            last_payload = ""
            while True:
                if await request.is_disconnected():
                    return
                state = _read_json(state_path)
                encoded = json.dumps(state, ensure_ascii=False, sort_keys=True)
                if encoded != last_payload:
                    yield _sse("progress", state)
                    last_payload = encoded
                if state.get("status") == "success":
                    yield _sse("done", state)
                    return
                if state.get("status") == "failed":
                    yield _sse("error", state)
                    return
                await asyncio.sleep(0.5)

        return StreamingResponse(events(), media_type="text/event-stream")

    # -----------------------------------------------------------------------
    # Leaderboard endpoints
    # -----------------------------------------------------------------------
    _register_leaderboard_routes(app, root, require_auth)

    # -----------------------------------------------------------------------
    # Cohort data endpoints (signal_cohort_v1)
    # -----------------------------------------------------------------------

    @app.get("/stockpred/runs/{run_id}/cohort/metrics", dependencies=[Depends(require_auth)])
    def get_cohort_metrics(run_id: str) -> dict[str, Any]:
        from src.stockpred.artifact_resolver import ArtifactsMissingError, RunArtifactResolver, artifact_bytes

        run_dir = _resolve_run_dir(root, run_id)
        try:
            resolved = RunArtifactResolver.resolve(run_dir)
        except ArtifactsMissingError:
            raise HTTPException(status_code=404, detail="artifacts not found")
        if resolved.is_legacy or resolved.metrics_path is None:
            raise HTTPException(status_code=404, detail="not a cohort run")
        try:
            return _sanitize_json(json.loads(artifact_bytes(resolved, "aggregate_metrics.json")))
        except ArtifactsMissingError:
            raise HTTPException(status_code=404, detail="artifacts not found")

    @app.get("/stockpred/runs/{run_id}/cohort/returns", dependencies=[Depends(require_auth)])
    def get_cohort_returns(run_id: str) -> list[dict[str, Any]]:
        import pandas as pd
        from src.stockpred.artifact_resolver import ArtifactsMissingError, RunArtifactResolver, artifact_bytes

        run_dir = _resolve_run_dir(root, run_id)
        try:
            resolved = RunArtifactResolver.resolve(run_dir)
        except ArtifactsMissingError:
            raise HTTPException(status_code=404, detail="artifacts not found")
        if resolved.is_legacy or resolved.returns_path is None:
            raise HTTPException(status_code=404, detail="not a cohort run")
        try:
            df = pd.read_csv(BytesIO(artifact_bytes(resolved, "cohort_returns.csv")))
        except ArtifactsMissingError:
            raise HTTPException(status_code=404, detail="artifacts not found")
        records = df.to_dict(orient="records")
        return _sanitize_json(records)

    @app.get("/stockpred/runs/{run_id}/cohort/quality", dependencies=[Depends(require_auth)])
    def get_cohort_quality(run_id: str) -> dict[str, Any]:
        from src.stockpred.artifact_resolver import ArtifactsMissingError, RunArtifactResolver, artifact_bytes

        run_dir = _resolve_run_dir(root, run_id)
        try:
            resolved = RunArtifactResolver.resolve(run_dir)
        except ArtifactsMissingError:
            raise HTTPException(status_code=404, detail="artifacts not found")
        if resolved.is_legacy or resolved.quality_path is None:
            raise HTTPException(status_code=404, detail="not a cohort run")
        try:
            return _sanitize_json(json.loads(artifact_bytes(resolved, "quality_report.json")))
        except ArtifactsMissingError:
            raise HTTPException(status_code=404, detail="artifacts not found")

    @app.get("/stockpred/runs/{run_id}/cohort/symbols", dependencies=[Depends(require_auth)])
    def get_cohort_symbols(run_id: str) -> dict[str, list[str]]:
        from src.stockpred.artifact_resolver import (
            ArtifactsMissingError,
            RunArtifactResolver,
            load_chart_manifest,
        )

        try:
            resolved = RunArtifactResolver.resolve(_resolve_run_dir(root, run_id))
            if resolved.is_legacy:
                raise ArtifactsMissingError(resolved.version_dir, "not a cohort run")
            manifest = load_chart_manifest(resolved)
        except ArtifactsMissingError:
            raise HTTPException(status_code=404, detail="artifacts not found")
        return {"symbols": sorted(entry["code"] for entry in manifest["entries"])}

    @app.get("/stockpred/runs/{run_id}/cohort/period-breakdown", dependencies=[Depends(require_auth)])
    def get_cohort_period_breakdown(run_id: str) -> list[dict[str, Any]]:
        import pandas as pd
        from src.stockpred.artifact_resolver import ArtifactsMissingError, RunArtifactResolver, artifact_bytes

        try:
            resolved = RunArtifactResolver.resolve(_resolve_run_dir(root, run_id))
        except ArtifactsMissingError:
            raise HTTPException(status_code=404, detail="artifacts not found")
        if resolved.is_legacy or resolved.period_breakdown_path is None:
            raise HTTPException(status_code=404, detail="no period breakdown")
        try:
            return _sanitize_json(pd.read_csv(BytesIO(artifact_bytes(resolved, "period_breakdown.csv"))).to_dict(orient="records"))
        except ArtifactsMissingError:
            raise HTTPException(status_code=404, detail="artifacts not found")

    @app.get("/stockpred/runs/{run_id}/cohort/chart/{code}", dependencies=[Depends(require_auth)])
    def get_cohort_chart(run_id: str, code: str) -> dict[str, Any]:
        import pandas as pd
        from src.stockpred.artifact_resolver import (
            ArtifactsMissingError,
            RunArtifactResolver,
            artifact_bytes,
            has_artifact,
            load_chart_manifest,
        )

        run_dir = _resolve_run_dir(root, run_id)
        try:
            resolved = RunArtifactResolver.resolve(run_dir)
        except ArtifactsMissingError:
            raise HTTPException(status_code=404, detail="artifacts not found")
        if resolved.is_legacy or resolved.chart_manifest_path is None:
            raise HTTPException(status_code=404, detail="no chart bundle")

        try:
            manifest = load_chart_manifest(resolved)
        except ArtifactsMissingError:
            raise HTTPException(status_code=404, detail="invalid chart bundle")
        entry = next((e for e in manifest["entries"] if e["code"] == code), None)
        if entry is None:
            raise HTTPException(status_code=404, detail=f"no chart for {code}")

        try:
            df = pd.read_parquet(BytesIO(artifact_bytes(resolved, entry["relative_path"])))
            ohlcv = df.to_dict(orient="records")

            # Load order markers for this code
            orders: list[dict[str, Any]] = []
            if has_artifact(resolved, "cohort_orders.csv"):
                orders_df = pd.read_csv(BytesIO(artifact_bytes(resolved, "cohort_orders.csv")))
                if "code" in orders_df.columns:
                    code_orders = orders_df[orders_df["code"].astype(str) == code]
                    orders = code_orders.to_dict(orient="records")
        except ArtifactsMissingError:
            raise HTTPException(status_code=404, detail="invalid chart bundle")

        return _sanitize_json({"code": code, "ohlcv": ohlcv, "orders": orders})


def _resolve_run_dir(root: Path, run_id: str) -> Path:
    """Resolve run directory from run_id, checking batch sub-directories."""
    # Validate format to prevent path traversal
    if not re.fullmatch(r"^[A-Za-z0-9_-]+$", run_id):
        raise HTTPException(status_code=404, detail="run not found")
    # Direct run directory
    direct = root / run_id
    if direct.is_dir():
        return direct
    # Search in strategy_batches
    batches_dir = root / "strategy_batches"
    if batches_dir.is_dir():
        for batch_dir in batches_dir.iterdir():
            candidate = batch_dir / run_id
            if candidate.is_dir():
                return candidate
    raise HTTPException(status_code=404, detail="run not found")


def _register_leaderboard_routes(app: FastAPI, root: Path, require_auth: AuthDependency) -> None:
    """Register leaderboard endpoints (called from register_stockpred_routes)."""
    from src.stockpred.leaderboard import LeaderboardService

    @app.get("/stockpred/leaderboard", dependencies=[Depends(require_auth)])
    def get_leaderboard(
        protocol_key: str = Query(..., min_length=1),
        sort_by: str = Query("mean_return"),
        limit: int = Query(50, ge=1, le=200),
    ) -> dict[str, Any]:
        service = LeaderboardService(root)
        result = service.get_leaderboard(protocol_key, sort_by=sort_by, limit=limit)  # type: ignore[arg-type]
        return {
            "protocol_key": result.protocol_key,
            "total": result.total,
            "entries": [
                {
                    "run_id": e.run_id,
                    "strategy_id": e.strategy_id,
                    "mean_return": e.mean_return,
                    "win_rate": e.win_rate,
                    "valid_cohort_count": e.valid_cohort_count,
                    "pit_assurance": e.pit_assurance,
                }
                for e in result.entries
            ],
        }

    @app.get("/stockpred/leaderboard/protocols", dependencies=[Depends(require_auth)])
    def list_leaderboard_protocols() -> dict[str, list[str]]:
        service = LeaderboardService(root)
        return {"protocols": service.list_protocols()}
