"""Explicit detail materialization for a completed StockPred strategy screening.

OHLCV files are written into the canonical ``artifacts/`` directory so that
all downstream consumers (``load_price_series``, ``_load_ohlcv_artifacts``)
read from a single location.

Publish protocol:
1. Read and validate screening, data snapshot, and detail manifest.
2. If completion marker is valid, return immediately (idempotent).
3. Write all ohlcv_<code>.csv to .detail.staging/ directory.
4. On success, move files to artifacts/ and atomically write detail_complete.json.
5. On failure, delete staging and do not write completion marker.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pandas as pd

from src.stockpred.contracts import StockPredDataError
from src.stockpred.gateway import StockPredDataGateway
from src.stockpred.graph.adjustment import apply_qfq


def materialize_strategy_detail(run_dir: Path, gateway: StockPredDataGateway) -> Path:
    """Publish per-symbol OHLCV into ``artifacts/`` from the pinned snapshot.

    Idempotent: if completion marker is valid, returns immediately.
    Uses staging directory for atomic publish.
    """
    root = Path(run_dir)
    detail_manifest = _detail_manifest(root)
    if gateway.manifest.model_dump(mode="json") != detail_manifest["data_snapshot"]:
        raise StockPredDataError(
            "STOCKPRED_DETAIL_SNAPSHOT_MISMATCH",
            "gateway snapshot does not match the screening detail manifest",
        )

    artifacts = root / "artifacts"
    if not artifacts.is_dir():
        raise StockPredDataError(
            "STOCKPRED_DETAIL_SCREENING_INVALID",
            "artifacts directory is missing",
        )

    codes = detail_manifest["codes"]

    # Check if completion marker is valid (idempotent)
    if _completion_marker_valid(root, codes, detail_manifest):
        return artifacts

    # Materialize with staging (UUID ensures uniqueness across concurrent calls)
    if codes:
        import uuid
        market = _detail_market(gateway, detail_manifest)
        token = uuid.uuid4().hex
        staging = root / f".detail.staging.{token}"
        try:
            # Clean up any leftover staging from previous failed attempt
            if staging.exists():
                shutil.rmtree(staging)
            staging.mkdir(parents=True)

            # Write all CSVs to staging
            _write_ohlcv(staging, market, codes)

            # All writes succeeded - move to artifacts
            for code in codes:
                src = staging / f"ohlcv_{code}.csv"
                dst = artifacts / f"ohlcv_{code}.csv"
                shutil.move(str(src), str(dst))

            # Write completion marker atomically
            _write_completion_marker(root, codes, detail_manifest)
        finally:
            # Clean up staging
            if staging.exists():
                shutil.rmtree(staging)
    else:
        _write_completion_marker(root, [], detail_manifest)

    return artifacts


def detail_publish_complete(run_dir: Path) -> bool:
    """Public gate: True if run has no detail manifest OR marker is fully valid.

    Shared by all OHLCV consumers (ui_services, API routes).
    """
    root = Path(run_dir)
    manifest_path = root / "detail_manifest.json"
    if not manifest_path.is_file():
        return True  # Not a staged run
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        codes = [str(code) for code in manifest["codes"]]
    except (KeyError, OSError, TypeError, json.JSONDecodeError):
        return False
    return _completion_marker_valid(root, codes, manifest)


def _completion_marker_valid(root: Path, codes: list[str], detail_manifest: dict[str, object]) -> bool:
    """Check if detail_complete.json is valid for the current manifest."""
    marker_path = root / "detail_complete.json"
    if not marker_path.is_file():
        return False
    try:
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False

    # Validate version
    if marker.get("version") != 1:
        return False

    # Validate codes match
    marker_codes = marker.get("codes")
    if not isinstance(marker_codes, list) or sorted(marker_codes) != sorted(codes):
        return False

    # Validate manifest digest
    manifest_digest = _manifest_digest(detail_manifest)
    if marker.get("detail_manifest_sha256") != manifest_digest:
        return False

    # Validate all CSV files exist
    artifacts = root / "artifacts"
    for code in codes:
        if not (artifacts / f"ohlcv_{code}.csv").is_file():
            return False

    return True


def _manifest_digest(detail_manifest: dict[str, object]) -> str:
    """Compute SHA-256 digest of the detail manifest."""
    canonical = json.dumps(detail_manifest, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _write_completion_marker(root: Path, codes: list[str], detail_manifest: dict[str, object]) -> None:
    """Atomically write the completion marker."""
    import uuid
    marker = {
        "version": 1,
        "codes": sorted(codes),
        "detail_manifest_sha256": _manifest_digest(detail_manifest),
    }
    marker_path = root / "detail_complete.json"
    # Write to unique temp file then rename for atomicity (avoids concurrent temp conflicts)
    temp_path = root / f".detail_complete.{uuid.uuid4().hex}.tmp"
    temp_path.write_text(json.dumps(marker, ensure_ascii=False, indent=2), encoding="utf-8")
    temp_path.replace(marker_path)


def _detail_manifest(root: Path) -> dict[str, object]:
    path = root / "detail_manifest.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise StockPredDataError(
            "STOCKPRED_DETAIL_MANIFEST_MISSING",
            "screening detail manifest is missing",
        ) from exc
    except json.JSONDecodeError as exc:
        raise StockPredDataError(
            "STOCKPRED_DETAIL_MANIFEST_INVALID",
            "screening detail manifest is invalid",
        ) from exc
    if (
        payload.get("version") != 1
        or not isinstance(payload.get("data_snapshot"), dict)
        or not isinstance(payload.get("codes"), list)
        or not all(isinstance(code, str) for code in payload["codes"])
        or not isinstance(payload.get("market_start"), str)
        or not isinstance(payload.get("market_end"), str)
    ):
        raise StockPredDataError(
            "STOCKPRED_DETAIL_MANIFEST_INVALID",
            "screening detail manifest has an unsupported shape",
        )
    _validate_successful_screening(root, payload)
    return payload


def _validate_successful_screening(root: Path, detail_manifest: dict[str, object]) -> None:
    if detail_manifest.get("run_id") != root.name:
        raise StockPredDataError(
            "STOCKPRED_DETAIL_SCREENING_INVALID",
            "detail manifest run_id does not match the screening directory",
        )
    config = _json_file(root / "config.json", "screening config")
    if config.get("comparison_key") != detail_manifest.get("comparison_key"):
        raise StockPredDataError(
            "STOCKPRED_DETAIL_SCREENING_INVALID",
            "detail manifest comparison_key does not match the screening config",
        )
    state = _json_file(root / "state.json", "screening state")
    if state.get("status") != "success":
        raise StockPredDataError(
            "STOCKPRED_DETAIL_SCREENING_INVALID",
            "strategy screening has not succeeded",
        )
    snapshot = _json_file(root / "data_snapshot.json", "screening data snapshot")
    if snapshot != detail_manifest.get("data_snapshot"):
        raise StockPredDataError(
            "STOCKPRED_DETAIL_SCREENING_INVALID",
            "detail manifest snapshot does not match the screening snapshot",
        )
    required = (
        root / "artifacts" / "metrics.csv",
        root / "artifacts" / "equity.csv",
        root / "artifacts" / "positions.csv",
        root / "artifacts" / "trades.csv",
        root / "artifacts" / "selected_signals.csv",
        root / "artifacts" / "symbol_metrics.csv",
        root / "artifacts" / "signals.parquet",
        root / "strategy_snapshot.json",
        root / "strategy_source.zip",
    )
    if not all(path.is_file() for path in required):
        raise StockPredDataError(
            "STOCKPRED_DETAIL_SCREENING_INVALID",
            "screening summary artifacts are incomplete",
        )


def _json_file(path: Path, label: str) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        raise StockPredDataError(
            "STOCKPRED_DETAIL_SCREENING_INVALID",
            f"{label} is missing or invalid",
        ) from exc
    if not isinstance(payload, dict):
        raise StockPredDataError(
            "STOCKPRED_DETAIL_SCREENING_INVALID",
            f"{label} is invalid",
        )
    return payload


def _detail_market(gateway: StockPredDataGateway, detail_manifest: dict[str, object]) -> pd.DataFrame:
    start = str(detail_manifest["market_start"])
    end = str(detail_manifest["market_end"])
    codes = list(detail_manifest["codes"])
    market = apply_qfq(
        gateway.prices(start, end, codes),
        gateway.adjustment_factors(start, end, codes),
    )
    limits = gateway.stock_limits(start, end, codes)
    return market.merge(
        limits[["ts_code", "trade_date", "up_limit", "down_limit"]],
        on=["ts_code", "trade_date"],
        how="left",
        validate="one_to_one",
    )


def _write_ohlcv(staging: Path, market: pd.DataFrame, codes: list[str]) -> None:
    groups = market.assign(ts_code=market["ts_code"].astype(str)).groupby("ts_code", sort=False)
    for code in codes:
        frame = groups.get_group(code) if code in groups.groups else market.head(0)
        frame.sort_values("trade_date", kind="stable").to_csv(
            staging / f"ohlcv_{code}.csv", index=False
        )
