"""Phase 0 Task 2 — immutable pinned data snapshot tests.

Verifies:
    * fingerprint is order-independent but sensitive to every identity component;
    * a snapshot pins Lance versions: appending a new version later does NOT
      change what a pinned read returns;
    * resolve_pinned_snapshot derives the ETF pool (static name filter) and the
      trading calendar from the pinned versions.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

lance = pytest.importorskip("lance")

from src.stockpred.fund_rotation.data_snapshot import (  # noqa: E402
    PinnedFundDataSnapshot,
    compute_fingerprint,
    hash_codes,
    load_pinned_frames,
    resolve_pinned_snapshot,
)

ETF_CODES = ["510300.SH", "510010.SH", "510020.SH"]
DATES = ["20220103", "20220104"]


def _fund_rows(codes=ETF_CODES, dates=DATES, close=3.0):
    rows = []
    for d in dates:
        for c in codes:
            rows.append({
                "ts_code": c, "trade_date": d,
                "open": close, "high": close * 1.01, "low": close * 0.99,
                "close": close, "pre_close": close,
                "vol": 1_000_000, "amount": close * 1_000_000,
            })
    return rows


def _dim_rows(codes=ETF_CODES, extra=None):
    rows = [{"ts_code": c, "name": f"测试ETF{i}", "list_date": "20200101"}
            for i, c in enumerate(codes)]
    # A non-ETF (LOF) that must be filtered out of the pool.
    rows.append({"ts_code": "999999.SH", "name": "某LOF基金", "list_date": "20200101"})
    if extra:
        rows.extend(extra)
    return rows


def _adj_rows(codes=ETF_CODES, dates=DATES):
    return [{"ts_code": c, "trade_date": d, "adj_factor": 1.0}
            for d in dates for c in codes]


def _create_datasets(lance_dir: Path, *, codes=ETF_CODES, dates=DATES, dim_extra=None):
    lance_dir.mkdir(parents=True, exist_ok=True)
    lance.write_dataset(pd.DataFrame(_fund_rows(codes, dates)), str(lance_dir / "fund.lance"), mode="create")
    lance.write_dataset(pd.DataFrame(_dim_rows(codes, dim_extra)), str(lance_dir / "dim_fund.lance"), mode="create")
    lance.write_dataset(pd.DataFrame(_adj_rows(codes, dates)), str(lance_dir / "fact_fund_adj.lance"), mode="create")
    return lance_dir


# ── fingerprint purity ──

def test_fingerprint_is_order_independent():
    fp_sorted = compute_fingerprint(1, 2, 3, ["A", "B", "C"], ["20220103", "20220104"])
    fp_shuffled = compute_fingerprint(1, 2, 3, ["C", "A", "B"], ["20220104", "20220103"])
    assert fp_sorted == fp_shuffled


def test_fingerprint_sensitive_to_each_component():
    base = compute_fingerprint(1, 2, 3, ["A", "B"], ["20220103"])
    assert compute_fingerprint(9, 2, 3, ["A", "B"], ["20220103"]) != base  # fund version
    assert compute_fingerprint(1, 9, 3, ["A", "B"], ["20220103"]) != base  # adj version
    assert compute_fingerprint(1, 2, 9, ["A", "B"], ["20220103"]) != base  # dim version
    assert compute_fingerprint(1, 2, 3, ["A", "X"], ["20220103"]) != base  # universe
    assert compute_fingerprint(1, 2, 3, ["A", "B"], ["20220999"]) != base  # calendar


def test_hash_codes_order_independent():
    assert hash_codes(["A", "B", "C"]) == hash_codes(["C", "A", "B"])


# ── resolve + pinning against real Lance datasets ──

def test_resolve_derives_pool_and_calendar(tmp_path):
    _create_datasets(tmp_path)
    snapshot = resolve_pinned_snapshot(tmp_path)

    # LOF (999999.SH) is excluded; the three ETFs remain.
    assert snapshot.universe_codes == tuple(sorted(ETF_CODES))
    assert "999999.SH" not in snapshot.universe_codes
    assert snapshot.trading_dates == tuple(sorted(DATES))
    assert snapshot.fund_version == 1
    assert snapshot.fund_adj_version == 1
    assert snapshot.dim_version == 1
    assert snapshot.fingerprint == compute_fingerprint(
        1, 1, 1, snapshot.universe_codes, snapshot.trading_dates,
    )


def test_pinned_read_ignores_later_versions(tmp_path):
    _create_datasets(tmp_path)
    snapshot_v1 = resolve_pinned_snapshot(tmp_path)
    assert snapshot_v1.fund_version == 1

    fund_daily_v1, _, _ = load_pinned_frames(snapshot_v1, tmp_path)
    assert set(fund_daily_v1["trade_date"].astype(str)) == set(DATES)

    # Append a NEW trading date -> fund.lance becomes version 2.
    new_rows = _fund_rows(codes=ETF_CODES, dates=["20220105"])
    lance.write_dataset(pd.DataFrame(new_rows), str(tmp_path / "fund.lance"), mode="append")
    assert lance.dataset(str(tmp_path / "fund.lance")).version == 2

    # The pinned snapshot still reads version 1 only.
    fund_daily_pinned, _, _ = load_pinned_frames(snapshot_v1, tmp_path)
    assert set(fund_daily_pinned["trade_date"].astype(str)) == set(DATES)
    assert "20220105" not in set(fund_daily_pinned["trade_date"].astype(str))

    # A freshly resolved snapshot sees version 2 and a different fingerprint.
    snapshot_v2 = resolve_pinned_snapshot(tmp_path)
    assert snapshot_v2.fund_version == 2
    assert "20220105" in snapshot_v2.trading_dates
    assert snapshot_v2.fingerprint != snapshot_v1.fingerprint


def test_etf_pool_change_changes_fingerprint(tmp_path):
    _create_datasets(tmp_path)
    snapshot_before = resolve_pinned_snapshot(tmp_path)

    # Append a new ETF to dim_fund.lance -> new dim version, larger pool.
    extra_dim = pd.DataFrame([{"ts_code": "510030.SH", "name": "测试ETF新", "list_date": "20200101"}])
    lance.write_dataset(extra_dim, str(tmp_path / "dim_fund.lance"), mode="append")

    snapshot_after = resolve_pinned_snapshot(tmp_path)
    assert "510030.SH" in snapshot_after.universe_codes
    assert snapshot_after.dim_version == 2
    assert snapshot_after.fingerprint != snapshot_before.fingerprint


def test_load_pinned_frames_respects_date_bounds(tmp_path):
    _create_datasets(tmp_path, dates=["20220103", "20220104", "20220105"])
    snapshot = resolve_pinned_snapshot(tmp_path)
    fund_daily, fund_adj, dim_fund = load_pinned_frames(
        snapshot, tmp_path, data_start="20220104", data_end="20220105",
    )
    assert set(fund_daily["trade_date"].astype(str)) == {"20220104", "20220105"}
    assert set(fund_adj["trade_date"].astype(str)) == {"20220104", "20220105"}
    # dim_fund has no trade_date filter
    assert set(dim_fund["ts_code"].astype(str)) >= set(ETF_CODES)


def test_snapshot_is_immutable_value():
    snapshot = PinnedFundDataSnapshot(
        fund_version=1, fund_adj_version=1, dim_version=1,
        universe_codes=("A",), trading_dates=("20220103",), fingerprint="x",
    )
    with pytest.raises(Exception):
        snapshot.fund_version = 2  # frozen dataclass
