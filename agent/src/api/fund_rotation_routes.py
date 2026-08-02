"""Fund rotation API routes — §15.1.

Mounted by api_server.py via register_fund_rotation_routes(app, ...).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse

from src.stockpred.fund_rotation.service import FundRotationBacktestService, StructuredError


def register_fund_rotation_routes(
    app: FastAPI,
    runs_dir: Path,
    require_auth: Callable,
    require_event_stream_auth: Callable,
    stockpred_root: Path | None = None,
) -> None:
    """Register fund rotation routes on the FastAPI app."""

    service = FundRotationBacktestService(runs_dir, stockpred_root)

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

    @app.get("/stockpred/fund-rotation/defaults", dependencies=[Depends(require_auth)])
    def get_defaults() -> dict[str, Any]:
        return service.get_defaults()

    @app.post("/stockpred/fund-rotation/backtests", status_code=202, dependencies=[Depends(require_auth)])
    async def create_backtest(request: Request) -> Any:
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid JSON body")

        params = body.get("params", {})
        idempotency_key = body.get("idempotency_key", "")
        if not idempotency_key:
            raise HTTPException(status_code=400, detail="idempotency_key required")

        try:
            run_id, status_code = service.submit_backtest(params, idempotency_key)
        except StructuredError as e:
            raise HTTPException(status_code=422, detail=e.to_dict())
        except ValueError as e:
            raise HTTPException(status_code=409, detail=str(e))

        from fastapi.responses import JSONResponse
        return JSONResponse(
            status_code=status_code,
            content={"run_id": run_id, "status": "QUEUED" if status_code == 202 else "EXISTING"},
        )

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
