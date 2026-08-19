"""Chart bundle publisher: OHLCV Parquet files + manifest + cohort orders.

Implements design §16. All eligibility-filtered signal codes get charts.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

_SAFE_CODE = re.compile(r"^[A-Za-z0-9_.-]+$")
ORDER_COLUMNS = [
    "order_id", "cohort_id", "trade_date", "code", "side",
    "requested_quantity", "requested_quantity_known", "requested_value", "executed_quantity", "executed_value",
    "price", "remaining_quantity", "status", "reason_code",
    "commission", "stamp_duty", "transfer_fee", "slippage", "market_impact", "total_fees",
]


@dataclass(frozen=True)
class ChartBundleManifest:
    """Chart bundle manifest with per-code entries."""

    entries: list[dict[str, Any]] = field(default_factory=list)
    total_codes: int = 0
    total_bytes: int = 0


def publish_chart_bundle(
    *,
    staging_dir: Path,
    market: pd.DataFrame,
    codes: list[str],
    orders: pd.DataFrame,
    start_date: str,
    end_date: str,
) -> ChartBundleManifest:
    """Publish chart bundle into staging directory.

    Writes:
    - charts/ohlcv_<code>.parquet for each code with market data
    - chart_bundle_manifest.json with per-code metadata
    - cohort_orders.csv with cohort_id column
    """
    staging_dir = Path(staging_dir)
    charts_dir = staging_dir / "charts"
    charts_dir.mkdir(parents=True, exist_ok=True)

    entries: list[dict[str, Any]] = []
    total_bytes = 0

    mkt = market.copy()
    mkt["ts_code"] = mkt["ts_code"].astype(str)
    mkt["trade_date"] = mkt["trade_date"].astype(str)

    requested_codes = sorted(set(codes))
    chart_rows: dict[str, pd.DataFrame] = {}
    for code in requested_codes:
        if not _SAFE_CODE.fullmatch(code):
            raise ValueError(f"CHART_BUNDLE_INCOMPLETE: invalid code {code}")
        stock_data = mkt[mkt["ts_code"] == code].sort_values("trade_date")
        stock_data = stock_data[
            (stock_data["trade_date"] >= start_date) & (stock_data["trade_date"] <= end_date)
        ]
        if stock_data.empty:
            raise ValueError(f"CHART_BUNDLE_INCOMPLETE: missing OHLCV for {code}")
        chart_rows[code] = stock_data

    for code in requested_codes:
        stock_data = chart_rows[code]

        # Write Parquet
        relative_path = f"charts/ohlcv_{code}.parquet"
        file_path = staging_dir / relative_path
        stock_data.to_parquet(file_path, index=False)

        # Compute metadata
        file_bytes = file_path.read_bytes()
        sha256 = hashlib.sha256(file_bytes).hexdigest()
        byte_size = len(file_bytes)
        total_bytes += byte_size

        entries.append({
            "code": code,
            "relative_path": relative_path,
            "start_date": str(stock_data["trade_date"].iloc[0]),
            "end_date": str(stock_data["trade_date"].iloc[-1]),
            "row_count": len(stock_data),
            "columns": list(stock_data.columns),
            "sha256": sha256,
            "byte_size": byte_size,
        })

    # Write manifest
    manifest_data = {
        "version": 1,
        "entries": entries,
        "total_codes": len(entries),
        "total_bytes": total_bytes,
    }
    manifest_path = staging_dir / "chart_bundle_manifest.json"
    manifest_path.write_text(json.dumps(manifest_data, ensure_ascii=False, indent=2), encoding="utf-8")

    # Write cohort orders CSV
    orders_path = staging_dir / "cohort_orders.csv"
    if orders is not None and not orders.empty:
        output_orders = orders.copy()
        if "requested_quantity_known" not in output_orders.columns:
            output_orders["requested_quantity_known"] = True
        output_orders.to_csv(orders_path, index=False)
    else:
        pd.DataFrame(columns=ORDER_COLUMNS).to_csv(
            orders_path, index=False
        )

    return ChartBundleManifest(entries=entries, total_codes=len(entries), total_bytes=total_bytes)
