"""Immutable fund data snapshot — Phase 0 Task 2.

Pins the three Lance dataset versions BEFORE any business read so a backtest run
sees a fixed, reproducible data view even if new versions are appended later or
concurrently. The snapshot identity (versions + ETF pool + trading calendar) is
hashed into a stable, order-independent fingerprint that enters the run manifest.

Design references:
    §2  固定版本的数据集和交易日历快照（策略不得自行打开 Lance 最新版本）
    §11 公平比较：统一数据及交易日历版本、ETF 池规则版本
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

FUND_LANCE = "fund.lance"
DIM_FUND_LANCE = "dim_fund.lance"
FUND_ADJ_LANCE = "fact_fund_adj.lance"

_FUND_COLS = ("ts_code", "trade_date", "open", "close", "vol", "amount", "high", "low", "pre_close")
_DIM_COLS = ("ts_code", "name", "list_date")
_ADJ_COLS = ("ts_code", "trade_date", "adj_factor")


@dataclass(frozen=True)
class PinnedFundDataSnapshot:
    """Fixed Lance versions + ETF pool + trading calendar for one run."""

    fund_version: int
    fund_adj_version: int
    dim_version: int
    universe_codes: tuple[str, ...]
    trading_dates: tuple[str, ...]
    fingerprint: str


def compute_fingerprint(
    fund_version: int,
    fund_adj_version: int,
    dim_version: int,
    universe_codes,
    trading_dates,
) -> str:
    """Stable SHA-256 over the canonical (sorted) snapshot identity.

    The ordering of ``universe_codes`` / ``trading_dates`` does not affect the
    fingerprint; any change to a version, the ETF pool, or the calendar does.
    """
    canonical = json.dumps(
        {
            "fund_version": int(fund_version),
            "fund_adj_version": int(fund_adj_version),
            "dim_version": int(dim_version),
            "universe_codes": sorted(str(c) for c in universe_codes),
            "trading_dates": sorted(str(d) for d in trading_dates),
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def hash_codes(values) -> str:
    """Order-independent SHA-256 of a code/date collection (for the manifest)."""
    canonical = json.dumps(sorted(str(v) for v in values), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def resolve_pinned_snapshot(lance_dir: Path) -> PinnedFundDataSnapshot:
    """Resolve the current Lance versions and pin them before any business read.

    Opens each dataset once to read its latest version number, then re-opens at
    that pinned version to derive the ETF pool and trading calendar, so the
    identity is consistent with the version the run will actually read.
    """
    import lance

    from backtest.fund_rotation.universe import filter_etf_universe

    fund_path = lance_dir / FUND_LANCE
    dim_path = lance_dir / DIM_FUND_LANCE
    adj_path = lance_dir / FUND_ADJ_LANCE

    # 1. Resolve the version numbers to pin.
    fund_version = int(lance.dataset(str(fund_path)).version)
    dim_version = int(lance.dataset(str(dim_path)).version)
    fund_adj_version = int(lance.dataset(str(adj_path)).version)

    # 2. Re-open at the pinned versions for all identity reads.
    ds_fund = lance.dataset(str(fund_path), version=fund_version)
    ds_dim = lance.dataset(str(dim_path), version=dim_version)

    # 3. Trading calendar = sorted unique trade dates at the pinned fund version.
    fund_dates = ds_fund.to_table(columns=["trade_date"]).column("trade_date").to_pandas()
    trading_dates = tuple(sorted({str(d) for d in fund_dates}))

    # 4. ETF pool = static name filter on the pinned dim version.
    dim_cols = [c for c in _DIM_COLS if c in ds_dim.schema.names]
    dim_df = ds_dim.to_table(columns=dim_cols).to_pandas()
    universe = filter_etf_universe(dim_df)
    universe_codes = tuple(sorted({str(c) for c in universe["ts_code"]}))

    fingerprint = compute_fingerprint(
        fund_version, fund_adj_version, dim_version, universe_codes, trading_dates,
    )
    return PinnedFundDataSnapshot(
        fund_version=fund_version,
        fund_adj_version=fund_adj_version,
        dim_version=dim_version,
        universe_codes=universe_codes,
        trading_dates=trading_dates,
        fingerprint=fingerprint,
    )


def load_pinned_frames(
    snapshot: PinnedFundDataSnapshot,
    lance_dir: Path,
    *,
    data_start: str | None = None,
    data_end: str | None = None,
) -> tuple:
    """Read fund_daily / fund_adj / dim_fund at the snapshot's pinned versions.

    Date bounds only filter rows; they never reopen the latest version. Returns
    ``(fund_daily, fund_adj, dim_fund)``.
    """
    import lance

    ds_fund = lance.dataset(str(lance_dir / FUND_LANCE), version=snapshot.fund_version)
    ds_adj = lance.dataset(str(lance_dir / FUND_ADJ_LANCE), version=snapshot.fund_adj_version)
    ds_dim = lance.dataset(str(lance_dir / DIM_FUND_LANCE), version=snapshot.dim_version)

    filter_parts = []
    if data_start:
        filter_parts.append(f"trade_date >= '{data_start}'")
    if data_end:
        filter_parts.append(f"trade_date <= '{data_end}'")
    date_filter = " AND ".join(filter_parts) if filter_parts else None

    fund_cols = [c for c in _FUND_COLS if c in ds_fund.schema.names]
    fund_daily = ds_fund.to_table(columns=fund_cols, filter=date_filter).to_pandas()

    dim_cols = [c for c in _DIM_COLS if c in ds_dim.schema.names]
    dim_fund = ds_dim.to_table(columns=dim_cols).to_pandas()

    adj_cols = [c for c in _ADJ_COLS if c in ds_adj.schema.names]
    fund_adj = ds_adj.to_table(columns=adj_cols, filter=date_filter).to_pandas()

    return fund_daily, fund_adj, dim_fund
