"""Fund rotation API routes — §15.1.

Mounted by api_server.py via register_fund_rotation_routes(app, ...).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Callable

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import StreamingResponse

from backtest.fund_rotation.catalog import CatalogError, FundRotationStrategyCatalog
from backtest.fund_rotation.strategies.registry import (
    default_fund_rotation_strategies,
)
from src.stockpred.fund_rotation.api_models import (
    StrategyDetail,
    StrategyListResponse,
    StrategySummary,
)
from src.stockpred.fund_rotation.service import FundRotationBacktestService


def register_fund_rotation_routes(
    app: FastAPI,
    runs_dir: Path,
    require_auth: Callable,
    require_event_stream_auth: Callable,
    stockpred_root: Path | None = None,
) -> None:
    """Register fund rotation routes on the FastAPI app."""

    service = FundRotationBacktestService(runs_dir, stockpred_root)

    # Build the strategy catalog once at registration time. A corrupted
    # whitelist (duplicate id / incompatible interface) fails here — before
    # any background task can be created — never serving a half-built list
    # (§16.3).
    catalog = FundRotationStrategyCatalog(list(default_fund_rotation_strategies()))
    # Fail fast on any entry whose DEFAULT config cannot resolve — a broken
    # catalog must never surface as a half-built list or a runtime 500 (§16.3).
    for _entry in catalog.list():
        catalog.resolve(_entry.strategy_id, {})
    catalog_version = hashlib.sha256(
        json.dumps(
            [
                {
                    "strategy_id": e.strategy_id,
                    "interface_version": e.interface_version,
                    "implementation_hash": e.implementation_hash,
                }
                for e in catalog.list()
            ],
            sort_keys=True,
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()

    def _published_manifest(run_id: str) -> tuple[Path, dict[str, Any]]:
        """Fail closed unless state and the uniquely-bound manifest agree."""
        run_dir = runs_dir / "fund_rotation" / run_id
        state_path = run_dir / "state.json"
        manifest_path = run_dir / "manifest.json"
        if not state_path.exists() or not manifest_path.exists():
            raise HTTPException(status_code=404, detail="Run has no published result")
        state = json.loads(state_path.read_text(encoding="utf-8"))
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        from src.stockpred.fund_rotation.artifacts import compute_file_checksum
        if (
            state.get("stage") != "SUCCEEDED"
            or manifest.get("status") != "SUCCEEDED"
            or manifest.get("run_id") != run_id
            or manifest.get("params_fingerprint") != state.get("params_fingerprint")
            or manifest.get("state_checksum") != compute_file_checksum(state_path)
        ):
            raise HTTPException(status_code=409, detail="Run publication is inconsistent")
        return run_dir, manifest

    def _validated_artifact(run_dir: Path, manifest: dict[str, Any], name: str) -> Path | None:
        """Return one checksum-verified declared artifact, or None if absent."""
        if name not in set(manifest.get("files", [])):
            return None
        path = run_dir / name
        expected = manifest.get("file_details", {}).get(name, {}).get("checksum")
        if not path.is_file() or not expected:
            raise HTTPException(status_code=409, detail=f"Published artifact invalid: {name}")
        from src.stockpred.fund_rotation.artifacts import compute_file_checksum
        if compute_file_checksum(path) != expected:
            raise HTTPException(status_code=409, detail=f"Artifact checksum mismatch: {name}")
        return path

    # Recover interrupted runs on startup
    service.recover_interrupted()

    # ── Batch service (§21/Phase 4) ──
    batches_dir = runs_dir / "fund_rotation_batches"
    batch_service = None
    if stockpred_root is not None:
        from src.stockpred.fund_rotation.batch_service import BatchService

        lance_dir = stockpred_root / "data" / "lance" / "market_core"

        def _batch_metadata_loader() -> dict[str, Any]:
            from src.stockpred.fund_rotation.data_snapshot import (
                resolve_pinned_snapshot,
            )
            snap = resolve_pinned_snapshot(lance_dir)
            return {
                "trading_dates": list(snap.trading_dates),
                "fingerprint": snap.fingerprint,
            }

        def _batch_frames_loader(
            data_start: str, data_end: str,
        ) -> tuple:
            from src.stockpred.fund_rotation.data_snapshot import (
                load_pinned_frames,
                resolve_pinned_snapshot,
            )
            snap = resolve_pinned_snapshot(lance_dir)
            return load_pinned_frames(
                snap, lance_dir, data_start=data_start, data_end=data_end,
            )

        batch_service = BatchService(
            batches_dir,
            catalog=catalog,
            metadata_loader=_batch_metadata_loader,
            frames_loader=_batch_frames_loader,
            auto_start=True,
        )
        batch_service.recover_interrupted()

    # ── Strategy catalog read endpoints (§16/§18) ──

    @app.get("/stockpred/fund-rotation/strategies", dependencies=[Depends(require_auth)])
    def list_strategies() -> dict[str, Any]:
        strategies: list[StrategySummary] = []
        for entry in catalog.list():
            binding = catalog.resolve(entry.strategy_id, {})
            requirements = binding.spec.resolved_requirements
            strategies.append(StrategySummary(
                strategy_id=entry.strategy_id,
                name=entry.name,
                description=entry.description,
                interface_version=entry.interface_version,
                implementation_hash=entry.implementation_hash,
                supported_universe=entry.supported_universe,
                warmup_trade_days=requirements.warmup_trade_days,
                required_datasets=tuple(requirements.required_datasets),
                required_fields=tuple(requirements.required_fields),
                frequency=requirements.frequency,
            ))
        return StrategyListResponse(
            catalog_version=catalog_version, strategies=strategies,
        ).model_dump(mode="json")

    @app.get(
        "/stockpred/fund-rotation/strategies/{strategy_id}",
        dependencies=[Depends(require_auth)],
    )
    def strategy_detail(strategy_id: str, request: Request, response: Response) -> Any:
        try:
            binding = catalog.resolve(strategy_id, {})
        except CatalogError as exc:
            status = 404 if exc.code == "FUND_ROTATION_STRATEGY_NOT_FOUND" else 422
            raise HTTPException(
                status_code=status,
                detail={"code": exc.code, "message": exc.message},
            ) from exc

        entry = catalog.require(strategy_id)
        spec = binding.spec
        # RFC 7232 entity-tag; honor If-None-Match with 304.
        etag = f'"{spec.config_schema_hash}"'
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers={"ETag": etag})
        schema = binding.registered.config_model.model_json_schema()
        descriptions = {
            name: str(prop.get("description", ""))
            for name, prop in schema.get("properties", {}).items()
        }
        detail = StrategyDetail(
            strategy_id=strategy_id,
            name=entry.descriptor.name,
            description=entry.descriptor.description,
            interface_version=entry.descriptor.interface_version,
            implementation_hash=entry.implementation_snapshot.implementation_hash,
            supported_universe=entry.descriptor.supported_universe,
            warmup_trade_days=spec.resolved_requirements.warmup_trade_days,
            required_datasets=tuple(spec.resolved_requirements.required_datasets),
            required_fields=tuple(spec.resolved_requirements.required_fields),
            frequency=spec.resolved_requirements.frequency,
            config_schema=schema,
            config_schema_version=spec.config_schema_version,
            config_schema_hash=spec.config_schema_hash,
            default_config=dict(spec.resolved_config),
            parameter_descriptions=descriptions,
            artifact_roles=list(
                getattr(binding.strategy, "artifact_roles", ())
            ),
        )
        # Schema content hash for frontend caching.
        response.headers["ETag"] = etag
        return detail.model_dump(mode="json")

    # ── Strategy batch endpoints (Phase 4 Task 6, §21) ──

    POST_BATCH_PATH = "/stockpred/fund-rotation/strategy-batches"

    @app.post(POST_BATCH_PATH, status_code=202, dependencies=[Depends(require_auth)])
    async def submit_strategy_batch(request: Request) -> Any:
        # Validate request body before checking backend availability.
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid JSON body")

        from src.stockpred.fund_rotation.batch_models import StrategyBatchRequest

        try:
            batch_request = StrategyBatchRequest.model_validate(body)
        except Exception as exc:
            raise HTTPException(
                status_code=422,
                detail={"code": "FUND_ROTATION_BATCH_INVALID", "message": str(exc)},
            ) from exc

        if batch_service is None:
            raise HTTPException(status_code=503, detail="Batch service not available")

        from src.stockpred.fund_rotation.batch_service import BatchPlanningError

        try:
            result = batch_service.submit_batch(batch_request)
        except BatchPlanningError as exc:
            raise HTTPException(
                status_code=422,
                detail={"code": exc.code, "message": exc.message},
            ) from exc

        from fastapi.responses import JSONResponse

        return JSONResponse(status_code=202, content=result)

    @app.get(POST_BATCH_PATH, dependencies=[Depends(require_auth)])
    def list_strategy_batches(limit: int = 50) -> list[dict[str, Any]]:
        if batch_service is None:
            return []
        batches_dir = batch_service.persistence.batches_dir
        if not batches_dir.exists():
            return []
        results: list[dict[str, Any]] = []
        for d in sorted(batches_dir.iterdir(), reverse=True):
            if d.name == "idempotency" or not d.is_dir():
                continue
            state_path = d / "state.json"
            if not state_path.exists():
                continue
            state = json.loads(state_path.read_text(encoding="utf-8"))
            resolved = {}
            resolved_path = d / "resolved_batch.json"
            if resolved_path.exists():
                resolved = json.loads(resolved_path.read_text(encoding="utf-8"))
            results.append({
                "batch_id": d.name,
                "status": state.get("stage", "UNKNOWN"),
                "mode": state.get("mode", "RESEARCH_ONLY"),
                "variant_count": len(resolved.get("variants", [])),
                "created_at": resolved.get("plan", {}).get(
                    "evaluation_start_date", "",
                ),
            })
            if len(results) >= limit:
                break
        return results

    @app.get(
        POST_BATCH_PATH + "/{batch_id}",
        dependencies=[Depends(require_auth)],
    )
    def get_strategy_batch(batch_id: str) -> dict[str, Any]:
        if batch_service is None:
            raise HTTPException(status_code=503, detail="Batch service not available")
        batch_dir = batch_service.persistence.batch_dir(batch_id)
        if not batch_dir.exists():
            raise HTTPException(status_code=404, detail="Batch not found")
        state_path = batch_dir / "state.json"
        resolved_path = batch_dir / "resolved_batch.json"
        if not state_path.exists():
            raise HTTPException(status_code=404, detail="Batch has no state")
        state = json.loads(state_path.read_text(encoding="utf-8"))
        resolved = {}
        if resolved_path.exists():
            resolved = json.loads(resolved_path.read_text(encoding="utf-8"))
        # Collect child run states
        runs_dir = batch_dir / "runs"
        child_runs: list[dict[str, Any]] = []
        if runs_dir.exists():
            for child in sorted(runs_dir.iterdir()):
                child_state_path = child / "state.json"
                if child_state_path.exists():
                    child_runs.append(
                        json.loads(child_state_path.read_text(encoding="utf-8")),
                    )
        return {
            "batch_id": batch_id,
            "state": state,
            "resolved": resolved,
            "child_runs": child_runs,
            "mode": "RESEARCH_ONLY",
        }

    @app.post(
        POST_BATCH_PATH + "/{batch_id}/cancel",
        dependencies=[Depends(require_auth)],
    )
    def cancel_strategy_batch(batch_id: str) -> dict[str, Any]:
        if batch_service is None:
            raise HTTPException(status_code=503, detail="Batch service not available")
        cancelled = batch_service.cancel_batch(batch_id)
        if not cancelled:
            raise HTTPException(
                status_code=404,
                detail={"code": "BATCH_NOT_FOUND", "message": f"Batch {batch_id} not found or not cancellable"},
            )
        return {"batch_id": batch_id, "cancelled": True}

    @app.get(
        POST_BATCH_PATH + "/{batch_id}/events",
        dependencies=[Depends(require_event_stream_auth)],
    )
    async def stream_batch_events(batch_id: str, request: Request) -> StreamingResponse:
        if batch_service is None:
            raise HTTPException(status_code=503, detail="Batch service not available")
        batch_dir = batch_service.persistence.batch_dir(batch_id)
        events_path = batch_dir / "events.jsonl"
        if not batch_dir.exists():
            raise HTTPException(status_code=404, detail="Batch not found")

        async def event_generator():
            last_seq = 0
            last_event_id = request.headers.get("Last-Event-ID")
            if last_event_id:
                try:
                    last_seq = int(last_event_id)
                except ValueError:
                    pass
            import asyncio

            while True:
                if await request.is_disconnected():
                    return
                if events_path.exists():
                    lines = events_path.read_text(encoding="utf-8").strip().split("\n")
                    for line in lines:
                        if not line.strip():
                            continue
                        try:
                            event = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        seq = event.get("seq", 0)
                        if seq > last_seq:
                            last_seq = seq
                            yield f"id: {seq}\nevent: progress\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
                            etype = event.get("event_type", "")
                            if etype == "TERMINAL" and event.get("scope") == "BATCH":
                                msg = event.get("message", "")
                                if msg in ("SUCCEEDED", "PARTIAL_SUCCEEDED", "FAILED",
                                           "CANCELED", "FAILED_INTERRUPTED"):
                                    yield f"id: {seq}\nevent: done\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
                                    return
                # Check if manifest signals completion
                manifest_path = batch_dir / "manifest.json"
                if manifest_path.exists():
                    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                    status = manifest.get("status", "")
                    if status in ("SUCCEEDED", "PARTIAL_SUCCEEDED", "FAILED"):
                        terminal = {
                            "seq": last_seq + 1,
                            "event_type": "TERMINAL",
                            "scope": "BATCH",
                            "message": status,
                            "source": "manifest",
                        }
                        yield f"id: {last_seq + 1}\nevent: done\ndata: {json.dumps(terminal, ensure_ascii=False)}\n\n"
                        return
                await asyncio.sleep(0.5)

        return StreamingResponse(event_generator(), media_type="text/event-stream")

    @app.get(
        POST_BATCH_PATH + "/{batch_id}/artifacts/{artifact_id:path}",
        dependencies=[Depends(require_auth)],
    )
    def get_batch_artifact(batch_id: str, artifact_id: str) -> Any:
        if batch_service is None:
            raise HTTPException(status_code=503, detail="Batch service not available")
        batch_dir = batch_service.persistence.batch_dir(batch_id)
        manifest_path = batch_dir / "manifest.json"
        if not manifest_path.exists():
            raise HTTPException(status_code=404, detail="Batch has no published manifest")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        allowed = set(manifest.get("files", []))
        if artifact_id not in allowed:
            raise HTTPException(
                status_code=404,
                detail=f"Artifact '{artifact_id}' not in manifest",
            )
        artifact_path = (batch_dir / artifact_id).resolve()
        if not str(artifact_path).startswith(str(batch_dir.resolve())):
            raise HTTPException(status_code=403, detail="Path traversal detected")
        if not artifact_path.is_file():
            raise HTTPException(status_code=404, detail="Artifact file missing")
        from fastapi.responses import FileResponse

        return FileResponse(artifact_path)

    # ── Legacy v1 read-only backtest endpoints (kept for historical access) ──

    @app.get("/stockpred/fund-rotation/backtests", dependencies=[Depends(require_auth)])
    def list_backtests(limit: int = 20) -> list[dict]:
        return service.list_backtests(limit=limit)

    @app.get("/stockpred/fund-rotation/backtests/{run_id}", dependencies=[Depends(require_auth)])
    def get_backtest(run_id: str) -> dict:
        result = service.get_backtest(run_id)
        if result is None:
            raise HTTPException(status_code=404, detail="Run not found")
        return result

    @app.get("/stockpred/fund-rotation/backtests/{run_id}/events", dependencies=[Depends(require_event_stream_auth)])
    async def stream_events(run_id: str, request: Request) -> StreamingResponse:
        run_dir = runs_dir / "fund_rotation" / run_id
        events_path = run_dir / "events.jsonl"
        if not run_dir.exists():
            raise HTTPException(status_code=404, detail="Run not found")

        async def event_generator():
            last_seq = 0
            # Support Last-Event-ID for reconnection
            last_event_id = request.headers.get("Last-Event-ID")
            if last_event_id:
                try:
                    last_seq = int(last_event_id)
                except ValueError:
                    pass

            while True:
                if await request.is_disconnected():
                    return
                if events_path.exists():
                    lines = events_path.read_text(encoding="utf-8").strip().split("\n")
                    for line in lines:
                        if not line.strip():
                            continue
                        event = json.loads(line)
                        seq = event.get("seq", 0)
                        if seq > last_seq:
                            last_seq = seq
                            yield f"id: {seq}\nevent: progress\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
                            # Check if terminal
                            stage = event.get("stage", "")
                            if stage == "SUCCEEDED":
                                try:
                                    _published_manifest(run_id)
                                except HTTPException:
                                    continue
                                yield f"id: {seq}\nevent: done\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
                                return
                            if stage in ("FAILED", "FAILED_INTERRUPTED"):
                                yield f"id: {seq}\nevent: done\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
                                return
                # A successful publication remains terminal even when the
                # best-effort events.jsonl append failed after manifest publish.
                try:
                    _, manifest = _published_manifest(run_id)
                except HTTPException:
                    manifest = None
                if manifest is not None:
                    seq = int(manifest.get("terminal_event_seq") or (last_seq + 1))
                    terminal = {
                        "seq": seq, "stage": "SUCCEEDED", "source": "state_manifest",
                    }
                    yield f"id: {seq}\nevent: done\ndata: {json.dumps(terminal, ensure_ascii=False)}\n\n"
                    return
                import asyncio
                await asyncio.sleep(0.5)

        return StreamingResponse(event_generator(), media_type="text/event-stream")

    @app.get("/stockpred/fund-rotation/backtests/{run_id}/artifacts/{artifact_name}", dependencies=[Depends(require_auth)])
    def get_artifact(run_id: str, artifact_name: str) -> Any:
        """Serve artifact files, restricted to manifest-declared names."""
        run_dir, manifest = _published_manifest(run_id)

        allowed_files = set(manifest.get("files", []))
        if artifact_name not in allowed_files:
            raise HTTPException(status_code=404, detail=f"Artifact '{artifact_name}' not in manifest")

        # Path traversal protection
        artifact_path = (run_dir / artifact_name).resolve()
        if not str(artifact_path).startswith(str(run_dir.resolve())):
            raise HTTPException(status_code=403, detail="Path traversal detected")

        artifact_path = _validated_artifact(run_dir, manifest, artifact_name)
        if artifact_path is None:
            raise HTTPException(status_code=404, detail="Artifact file missing")

        from fastapi.responses import FileResponse
        return FileResponse(artifact_path)

    @app.get("/stockpred/fund-rotation/backtests/{run_id}/instruments/{ts_code}/chart", dependencies=[Depends(require_auth)])
    def get_instrument_chart(run_id: str, ts_code: str, limit: int = 500) -> dict:
        """§15.1 — K-line chart data with signal and trade markers for one ETF.

        Returns OHLCV from the fixed Lance version recorded in data_snapshot.json,
        plus signal targets and actual trade events for this instrument.
        """
        run_dir, manifest = _published_manifest(run_id)

        # Read targets for this instrument
        targets_path = _validated_artifact(run_dir, manifest, "targets.csv")
        trade_events_path = _validated_artifact(run_dir, manifest, "trade_events.csv")

        signals = []
        if targets_path is not None:
            import pandas as pd
            targets_df = pd.read_csv(targets_path)
            inst_targets = targets_df[targets_df["ts_code"] == ts_code]
            signals = inst_targets.to_dict(orient="records")

        trades = []
        if trade_events_path is not None:
            import pandas as pd
            trades_df = pd.read_csv(trade_events_path)
            inst_trades = trades_df[trades_df["ts_code"] == ts_code] if "ts_code" in trades_df.columns else pd.DataFrame()
            trades = inst_trades.to_dict(orient="records")

        # §6: Load OHLCV from fixed Lance version (data_snapshot.json)
        ohlcv = []
        snapshot_path = _validated_artifact(run_dir, manifest, "data_snapshot.json")
        if snapshot_path is not None and stockpred_root:
            try:
                with open(snapshot_path, encoding="utf-8") as f:
                    snapshot = json.load(f)
                fund_meta = snapshot.get("datasets", {}).get("fund.lance", {})
                fund_lance_path = fund_meta.get("path")
                fund_version = fund_meta.get("version")
                if fund_lance_path and fund_version:
                    import lance
                    ds = lance.dataset(fund_lance_path, version=fund_version)
                    import pandas as pd
                    # Filter to this instrument
                    tbl = ds.to_table(filter=f"ts_code = '{ts_code}'")
                    df = tbl.to_pandas()
                    if not df.empty:
                        df = df.sort_values("trade_date").tail(limit)
                        ohlcv = df[["trade_date", "open", "high", "low", "close", "vol"]].to_dict(orient="records")
            except Exception:
                pass  # OHLCV is optional; don't fail the request

        # §6: Load positions for this instrument
        positions = []
        positions_path = _validated_artifact(run_dir, manifest, "positions.csv")
        if positions_path is not None:
            import pandas as pd
            pos_df = pd.read_csv(positions_path)
            inst_pos = pos_df[pos_df["ts_code"] == ts_code] if "ts_code" in pos_df.columns else pd.DataFrame()
            positions = inst_pos.to_dict(orient="records")

        orders = []
        orders_path = _validated_artifact(run_dir, manifest, "orders.csv")
        if orders_path is not None:
            import pandas as pd
            orders_df = pd.read_csv(orders_path)
            inst_orders = orders_df[orders_df["ts_code"] == ts_code] if "ts_code" in orders_df.columns else pd.DataFrame()
            orders = inst_orders.to_dict(orient="records")

        return {
            "ts_code": ts_code,
            "run_id": run_id,
            "signals": signals,
            "trades": trades,
            "ohlcv": ohlcv,
            "positions": positions,
            "orders": orders,
            "mode": "RESEARCH_ONLY",
        }
