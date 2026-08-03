"""Fund rotation API routes — §15.1.

Mounted by api_server.py via register_fund_rotation_routes(app, ...).
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
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
    catalog = FundRotationStrategyCatalog(
        list(default_fund_rotation_strategies())
    )
    for entry in catalog.list():
        catalog.resolve(entry.strategy_id, {})
    catalog_version = hashlib.sha256(
        json.dumps(
            [
                {
                    "strategy_id": entry.strategy_id,
                    "interface_version": entry.interface_version,
                    "implementation_hash": entry.implementation_hash,
                }
                for entry in catalog.list()
            ],
            sort_keys=True,
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()

    def _published_manifest(run_id: str) -> tuple[Path, dict[str, Any]]:
        """Read a checksum-bound terminal child manifest.

        Failed/canceled v2 runs are readable for audit but remain explicitly
        non-comparable in their manifests. Historical readers without a valid
        manifest continue to fail closed.
        """
        run_dir = runs_dir / "fund_rotation" / run_id
        from src.stockpred.fund_rotation.artifact_publisher import (
            read_valid_manifest,
        )

        manifest = read_valid_manifest(
            run_dir,
            identity_field="run_id",
            expected_identity=run_id,
            allowed_statuses={"SUCCEEDED", "FAILED", "CANCELED"},
        )
        if manifest is None:
            raise HTTPException(
                status_code=404,
                detail="Run has no published terminal result",
            )
        return run_dir, manifest

    def _published_batch_manifest(
        batch_dir: Path,
        batch_id: str,
    ) -> dict[str, Any]:
        from src.stockpred.fund_rotation.artifact_publisher import (
            read_valid_manifest,
        )

        manifest = read_valid_manifest(
            batch_dir,
            identity_field="batch_id",
            expected_identity=batch_id,
            allowed_statuses={"SUCCEEDED", "PARTIAL_SUCCEEDED"},
        )
        if manifest is None:
            raise HTTPException(
                status_code=409,
                detail="Batch publication is inconsistent",
            )
        return manifest

    def _validated_artifact(
        run_dir: Path,
        manifest: dict[str, Any],
        name: str,
    ) -> Path | None:
        if name not in set(manifest.get("files", [])):
            return None
        path = run_dir / name
        expected = (
            manifest.get("file_details", {})
            .get(name, {})
            .get("checksum")
        )
        if not path.is_file() or not expected:
            raise HTTPException(
                status_code=409,
                detail=f"Published artifact invalid: {name}",
            )
        from src.stockpred.fund_rotation.artifacts import compute_file_checksum

        if compute_file_checksum(path) != expected:
            raise HTTPException(
                status_code=409,
                detail=f"Artifact checksum mismatch: {name}",
            )
        return path

    def _last_event_sequence(request: Request) -> int:
        """Resolve browser header or explicit query-param replay cursor."""
        raw = request.headers.get("Last-Event-ID")
        if raw is None:
            raw = request.query_params.get("last_event_id")
        if raw is None:
            return 0
        try:
            value = int(raw)
        except ValueError:
            return 0
        return max(value, 0)

    def _batch_created_at(batch_dir: Path, resolved: dict[str, Any]) -> str:
        explicit = resolved.get("created_at")
        if isinstance(explicit, str) and explicit:
            return explicit
        request_path = batch_dir / "request.json"
        source = request_path if request_path.exists() else batch_dir
        return datetime.fromtimestamp(
            source.stat().st_mtime,
            timezone.utc,
        ).isoformat()

    service.recover_interrupted()

    fund_rotation_root = runs_dir / "fund_rotation"
    batches_dir = fund_rotation_root / "strategy_batches"
    batch_service = None
    if stockpred_root is not None:
        from src.stockpred.fund_rotation.batch_service import BatchService

        lance_dir = stockpred_root / "data" / "lance" / "market_core"

        def _batch_metadata_loader():
            from src.stockpred.fund_rotation.data_snapshot import (
                resolve_pinned_snapshot,
            )

            return resolve_pinned_snapshot(lance_dir)

        def _batch_frames_loader(
            snapshot,
            data_start: str,
            data_end: str,
        ) -> tuple:
            from src.stockpred.fund_rotation.data_snapshot import (
                load_pinned_frames,
            )

            return load_pinned_frames(
                snapshot,
                lance_dir,
                data_start=data_start,
                data_end=data_end,
            )

        batch_service = BatchService(
            batches_dir,
            runs_root=fund_rotation_root,
            catalog=catalog,
            metadata_loader=_batch_metadata_loader,
            frames_loader=_batch_frames_loader,
            auto_start=True,
        )
        batch_service.recover_interrupted()

    @app.get(
        "/stockpred/fund-rotation/strategies",
        dependencies=[Depends(require_auth)],
    )
    def list_strategies() -> dict[str, Any]:
        strategies: list[StrategySummary] = []
        for entry in catalog.list():
            binding = catalog.resolve(entry.strategy_id, {})
            requirements = binding.spec.resolved_requirements
            strategies.append(
                StrategySummary(
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
                )
            )
        return StrategyListResponse(
            catalog_version=catalog_version,
            strategies=strategies,
        ).model_dump(mode="json")

    @app.get(
        "/stockpred/fund-rotation/strategies/{strategy_id}",
        dependencies=[Depends(require_auth)],
    )
    def strategy_detail(
        strategy_id: str,
        request: Request,
        response: Response,
    ) -> Any:
        try:
            binding = catalog.resolve(strategy_id, {})
        except CatalogError as exc:
            status = (
                404
                if exc.code == "FUND_ROTATION_STRATEGY_NOT_FOUND"
                else 422
            )
            raise HTTPException(
                status_code=status,
                detail={"code": exc.code, "message": exc.message},
            ) from exc

        entry = catalog.require(strategy_id)
        spec = binding.spec
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
            implementation_hash=(
                entry.implementation_snapshot.implementation_hash
            ),
            supported_universe=entry.descriptor.supported_universe,
            warmup_trade_days=spec.resolved_requirements.warmup_trade_days,
            required_datasets=tuple(
                spec.resolved_requirements.required_datasets
            ),
            required_fields=tuple(
                spec.resolved_requirements.required_fields
            ),
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
        response.headers["ETag"] = etag
        return detail.model_dump(mode="json")

    POST_BATCH_PATH = "/stockpred/fund-rotation/strategy-batches"

    @app.post(
        POST_BATCH_PATH,
        status_code=202,
        dependencies=[Depends(require_auth)],
    )
    async def submit_strategy_batch(request: Request) -> Any:
        try:
            body = await request.json()
        except Exception as exc:
            raise HTTPException(
                status_code=400,
                detail="Invalid JSON body",
            ) from exc

        from src.stockpred.fund_rotation.batch_models import StrategyBatchRequest

        try:
            batch_request = StrategyBatchRequest.model_validate(body)
        except Exception as exc:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "FUND_ROTATION_BATCH_INVALID",
                    "message": str(exc),
                },
            ) from exc

        if batch_service is None:
            raise HTTPException(
                status_code=503,
                detail="Batch service not available",
            )

        from src.stockpred.fund_rotation.batch_persistence import (
            BatchIdempotencyError,
        )
        from src.stockpred.fund_rotation.batch_service import BatchPlanningError

        try:
            result = batch_service.submit_batch(batch_request)
        except BatchIdempotencyError as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": exc.code, "message": exc.message},
            ) from exc
        except BatchPlanningError as exc:
            raise HTTPException(
                status_code=422,
                detail={"code": exc.code, "message": exc.message},
            ) from exc

        from fastapi.responses import JSONResponse

        status_code = 200 if result.get("status") == "EXISTING" else 202
        return JSONResponse(status_code=status_code, content=result)

    @app.get(POST_BATCH_PATH, dependencies=[Depends(require_auth)])
    def list_strategy_batches(limit: int = 50) -> list[dict[str, Any]]:
        if batch_service is None:
            return []
        batches_root = batch_service.persistence.batches_dir
        if not batches_root.exists():
            return []
        results: list[dict[str, Any]] = []
        for batch_dir in batches_root.iterdir():
            if batch_dir.name == "idempotency" or not batch_dir.is_dir():
                continue
            state_path = batch_dir / "state.json"
            if not state_path.exists():
                continue
            state = json.loads(state_path.read_text(encoding="utf-8"))
            if state.get("stage") in {"SUCCEEDED", "PARTIAL_SUCCEEDED"}:
                try:
                    _published_batch_manifest(batch_dir, batch_dir.name)
                except HTTPException:
                    state = {**state, "stage": "WRITING_RESULTS"}
            resolved: dict[str, Any] = {}
            resolved_path = batch_dir / "resolved_batch.json"
            if resolved_path.exists():
                resolved = json.loads(
                    resolved_path.read_text(encoding="utf-8")
                )
            results.append(
                {
                    "batch_id": batch_dir.name,
                    "status": state.get("stage", "UNKNOWN"),
                    "mode": state.get("mode", "RESEARCH_ONLY"),
                    "variant_count": len(resolved.get("variants", [])),
                    "created_at": _batch_created_at(batch_dir, resolved),
                }
            )
        results.sort(
            key=lambda item: str(item.get("created_at", "")),
            reverse=True,
        )
        return results[: max(limit, 0)]

    @app.get(
        POST_BATCH_PATH + "/{batch_id}",
        dependencies=[Depends(require_auth)],
    )
    def get_strategy_batch(batch_id: str) -> dict[str, Any]:
        if batch_service is None:
            raise HTTPException(
                status_code=503,
                detail="Batch service not available",
            )
        batch_dir = batch_service.persistence.batch_dir(batch_id)
        if not batch_dir.exists():
            raise HTTPException(status_code=404, detail="Batch not found")
        state_path = batch_dir / "state.json"
        resolved_path = batch_dir / "resolved_batch.json"
        if not state_path.exists():
            raise HTTPException(
                status_code=404,
                detail="Batch has no state",
            )
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if state.get("stage") in {"SUCCEEDED", "PARTIAL_SUCCEEDED"}:
            try:
                _published_batch_manifest(batch_dir, batch_id)
            except HTTPException:
                state = {**state, "stage": "WRITING_RESULTS"}
        resolved: dict[str, Any] = {}
        if resolved_path.exists():
            resolved = json.loads(resolved_path.read_text(encoding="utf-8"))
        child_runs: list[dict[str, Any]] = []
        for variant in resolved.get("variants", []):
            run_id = variant.get("run_id")
            child_state_path = (
                batch_service.runs_root / str(run_id) / "state.json"
            )
            if run_id and child_state_path.exists():
                child_runs.append(
                    json.loads(
                        child_state_path.read_text(encoding="utf-8")
                    )
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
            raise HTTPException(
                status_code=503,
                detail="Batch service not available",
            )
        cancelled = batch_service.cancel_batch(batch_id)
        if not cancelled:
            batch_dir = batch_service.persistence.batch_dir(batch_id)
            if batch_dir.exists():
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "BATCH_NOT_CANCELLABLE",
                        "message": f"Batch {batch_id} is already terminal",
                    },
                )
            raise HTTPException(
                status_code=404,
                detail={
                    "code": "BATCH_NOT_FOUND",
                    "message": (
                        f"Batch {batch_id} not found or not cancellable"
                    ),
                },
            )
        return {"batch_id": batch_id, "cancelled": True}

    @app.get(
        POST_BATCH_PATH + "/{batch_id}/events",
        dependencies=[Depends(require_event_stream_auth)],
    )
    async def stream_batch_events(
        batch_id: str,
        request: Request,
    ) -> StreamingResponse:
        if batch_service is None:
            raise HTTPException(
                status_code=503,
                detail="Batch service not available",
            )
        batch_dir = batch_service.persistence.batch_dir(batch_id)
        events_path = batch_dir / "events.jsonl"
        if not batch_dir.exists():
            raise HTTPException(status_code=404, detail="Batch not found")

        async def event_generator():
            last_seq = _last_event_sequence(request)
            import asyncio

            while True:
                if await request.is_disconnected():
                    return
                if events_path.exists():
                    lines = (
                        events_path.read_text(encoding="utf-8")
                        .strip()
                        .split("\n")
                    )
                    for line in lines:
                        if not line.strip():
                            continue
                        try:
                            event = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        seq = event.get("seq", 0)
                        if seq <= last_seq:
                            continue
                        event_type = event.get("event_type", "")
                        if (
                            event_type == "TERMINAL"
                            and event.get("scope") == "BATCH"
                        ):
                            message = event.get("message", "")
                            if message in ("SUCCEEDED", "PARTIAL_SUCCEEDED"):
                                try:
                                    _published_batch_manifest(batch_dir, batch_id)
                                except HTTPException:
                                    continue
                                last_seq = seq
                                yield (
                                    f"id: {seq}\nevent: done\ndata: "
                                    f"{json.dumps(event, ensure_ascii=False)}\n\n"
                                )
                                return
                            if message in (
                                "FAILED",
                                "CANCELED",
                                "FAILED_INTERRUPTED",
                            ):
                                last_seq = seq
                                yield (
                                    f"id: {seq}\nevent: done\ndata: "
                                    f"{json.dumps(event, ensure_ascii=False)}\n\n"
                                )
                                return
                        last_seq = seq
                        yield (
                            f"id: {seq}\nevent: progress\ndata: "
                            f"{json.dumps(event, ensure_ascii=False)}\n\n"
                        )
                state_path = batch_dir / "state.json"
                if state_path.exists():
                    state = json.loads(
                        state_path.read_text(encoding="utf-8")
                    )
                    if state.get("stage") in (
                        "SUCCEEDED",
                        "PARTIAL_SUCCEEDED",
                        "FAILED",
                        "CANCELED",
                        "FAILED_INTERRUPTED",
                    ):
                        return
                await asyncio.sleep(0.5)

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
        )

    @app.get(
        POST_BATCH_PATH + "/{batch_id}/artifacts/{artifact_id:path}",
        dependencies=[Depends(require_auth)],
    )
    def get_batch_artifact(batch_id: str, artifact_id: str) -> Any:
        if batch_service is None:
            raise HTTPException(
                status_code=503,
                detail="Batch service not available",
            )
        batch_dir = batch_service.persistence.batch_dir(batch_id)
        if not (batch_dir / "manifest.json").exists():
            raise HTTPException(
                status_code=404,
                detail="Batch has no published manifest",
            )
        manifest = _published_batch_manifest(batch_dir, batch_id)
        allowed = set(manifest.get("files", []))
        if artifact_id not in allowed:
            raise HTTPException(
                status_code=404,
                detail=f"Artifact '{artifact_id}' not in manifest",
            )
        artifact_path = (batch_dir / artifact_id).resolve()
        try:
            artifact_path.relative_to(batch_dir.resolve())
        except ValueError as exc:
            raise HTTPException(
                status_code=403,
                detail="Path traversal detected",
            ) from exc
        from fastapi.responses import FileResponse

        return FileResponse(artifact_path)

    @app.get(
        "/stockpred/fund-rotation/backtests",
        dependencies=[Depends(require_auth)],
    )
    def list_backtests(limit: int = 20) -> list[dict]:
        return service.list_backtests(limit=limit)

    @app.get(
        "/stockpred/fund-rotation/backtests/{run_id}",
        dependencies=[Depends(require_auth)],
    )
    def get_backtest(run_id: str) -> dict:
        result = service.get_backtest(run_id)
        if result is None:
            raise HTTPException(status_code=404, detail="Run not found")
        return result

    @app.get(
        "/stockpred/fund-rotation/backtests/{run_id}/events",
        dependencies=[Depends(require_event_stream_auth)],
    )
    async def stream_events(
        run_id: str,
        request: Request,
    ) -> StreamingResponse:
        run_dir = runs_dir / "fund_rotation" / run_id
        events_path = run_dir / "events.jsonl"
        if not run_dir.exists():
            raise HTTPException(status_code=404, detail="Run not found")

        async def event_generator():
            last_seq = _last_event_sequence(request)
            import asyncio

            while True:
                if await request.is_disconnected():
                    return
                if events_path.exists():
                    lines = (
                        events_path.read_text(encoding="utf-8")
                        .strip()
                        .split("\n")
                    )
                    for line in lines:
                        if not line.strip():
                            continue
                        try:
                            event = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        seq = event.get("seq", 0)
                        if seq <= last_seq:
                            continue
                        terminal_stage = str(
                            event.get("stage") or event.get("message") or ""
                        )
                        is_terminal = event.get("event_type") == "TERMINAL"
                        if is_terminal and terminal_stage in (
                            "SUCCEEDED",
                            "FAILED",
                            "FAILED_INTERRUPTED",
                            "CANCELED",
                        ):
                            last_seq = seq
                            yield (
                                f"id: {seq}\nevent: done\ndata: "
                                f"{json.dumps(event, ensure_ascii=False)}\n\n"
                            )
                            return
                        last_seq = seq
                        yield (
                            f"id: {seq}\nevent: progress\ndata: "
                            f"{json.dumps(event, ensure_ascii=False)}\n\n"
                        )
                state_path = run_dir / "state.json"
                if state_path.exists():
                    state = json.loads(
                        state_path.read_text(encoding="utf-8")
                    )
                    if state.get("stage") in (
                        "SUCCEEDED",
                        "FAILED",
                        "FAILED_INTERRUPTED",
                        "CANCELED",
                    ):
                        return
                await asyncio.sleep(0.5)

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
        )

    @app.get(
        "/stockpred/fund-rotation/backtests/{run_id}/artifacts/{artifact_name}",
        dependencies=[Depends(require_auth)],
    )
    def get_artifact(run_id: str, artifact_name: str) -> Any:
        run_dir, manifest = _published_manifest(run_id)
        if artifact_name not in set(manifest.get("files", [])):
            raise HTTPException(
                status_code=404,
                detail=f"Artifact '{artifact_name}' not in manifest",
            )
        artifact_path = (run_dir / artifact_name).resolve()
        try:
            artifact_path.relative_to(run_dir.resolve())
        except ValueError as exc:
            raise HTTPException(
                status_code=403,
                detail="Path traversal detected",
            ) from exc
        artifact_path = _validated_artifact(
            run_dir,
            manifest,
            artifact_name,
        )
        if artifact_path is None:
            raise HTTPException(
                status_code=404,
                detail="Artifact file missing",
            )
        from fastapi.responses import FileResponse

        return FileResponse(artifact_path)

    @app.get(
        "/stockpred/fund-rotation/backtests/{run_id}/instruments/{ts_code}/chart",
        dependencies=[Depends(require_auth)],
    )
    def get_instrument_chart(
        run_id: str,
        ts_code: str,
        limit: int = 500,
    ) -> dict:
        """Return pinned OHLCV, targets, executions, positions and orders."""
        run_dir, manifest = _published_manifest(run_id)
        targets_path = _validated_artifact(
            run_dir,
            manifest,
            "targets.csv",
        )
        trade_events_path = _validated_artifact(
            run_dir,
            manifest,
            "trade_events.csv",
        )

        signals: list[dict[str, Any]] = []
        if targets_path is not None:
            import pandas as pd

            targets_df = pd.read_csv(targets_path)
            if "ts_code" in targets_df.columns:
                signals = targets_df[
                    targets_df["ts_code"].astype(str) == ts_code
                ].to_dict(orient="records")

        trades: list[dict[str, Any]] = []
        if trade_events_path is not None:
            import pandas as pd

            trades_df = pd.read_csv(trade_events_path)
            code_column = (
                "ts_code"
                if "ts_code" in trades_df.columns
                else "code"
                if "code" in trades_df.columns
                else None
            )
            if code_column is not None:
                trades = trades_df[
                    trades_df[code_column].astype(str) == ts_code
                ].to_dict(orient="records")

        ohlcv: list[dict[str, Any]] = []
        snapshot_path = _validated_artifact(
            run_dir,
            manifest,
            "data_snapshot.json",
        )
        if snapshot_path is not None and stockpred_root:
            try:
                with open(snapshot_path, encoding="utf-8") as handle:
                    snapshot = json.load(handle)
                if "fund_version" in snapshot:
                    fund_lance_path = str(
                        stockpred_root
                        / "data"
                        / "lance"
                        / "market_core"
                        / "fund.lance"
                    )
                    fund_version = snapshot.get("fund_version")
                else:
                    fund_meta = (
                        snapshot.get("datasets", {}).get("fund.lance", {})
                    )
                    fund_lance_path = fund_meta.get("path")
                    fund_version = fund_meta.get("version")
                if fund_lance_path and fund_version is not None:
                    import lance

                    dataset = lance.dataset(
                        fund_lance_path,
                        version=fund_version,
                    )
                    table = dataset.to_table(
                        filter=f"ts_code = '{ts_code}'"
                    )
                    frame = table.to_pandas()
                    if not frame.empty:
                        frame = frame.sort_values("trade_date").tail(limit)
                        ohlcv = frame[
                            [
                                "trade_date",
                                "open",
                                "high",
                                "low",
                                "close",
                                "vol",
                            ]
                        ].to_dict(orient="records")
            except Exception:
                # OHLCV is optional; the immutable run evidence remains usable
                # even when the local Lance store is temporarily unavailable.
                pass

        positions: list[dict[str, Any]] = []
        positions_path = _validated_artifact(
            run_dir,
            manifest,
            "positions.csv",
        )
        if positions_path is not None:
            import pandas as pd

            positions_df = pd.read_csv(positions_path)
            if "ts_code" in positions_df.columns:
                positions = positions_df[
                    positions_df["ts_code"].astype(str) == ts_code
                ].to_dict(orient="records")

        orders: list[dict[str, Any]] = []
        orders_path = _validated_artifact(
            run_dir,
            manifest,
            "orders.csv",
        )
        if orders_path is not None:
            import pandas as pd

            orders_df = pd.read_csv(orders_path)
            if "ts_code" in orders_df.columns:
                orders = orders_df[
                    orders_df["ts_code"].astype(str) == ts_code
                ].to_dict(orient="records")

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
