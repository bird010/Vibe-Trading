"""Point-in-time stock universe construction for Graph backtests."""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from typing import Sequence

import pandas as pd


@dataclass(frozen=True)
class UniverseStats:
    input_count: int
    pre_list_excluded: int
    post_delist_excluded: int
    recent_listing_excluded: int
    st_excluded: int
    name_missing: int
    industry_missing: int


def _active_interval(history: pd.DataFrame | None, eval_date: str) -> pd.DataFrame:
    if history is None or history.empty:
        return pd.DataFrame()
    start = history["effective_from"].fillna("").astype(str)
    end = history["effective_to"].fillna("").astype(str)
    return history.loc[
        (start <= str(eval_date))
        & ((end == "") | (str(eval_date) < end))
    ].copy()


def _attach_name_at_date(
    stocks: pd.DataFrame,
    history: pd.DataFrame | None,
    eval_date: str,
) -> pd.DataFrame:
    result = stocks.copy()
    if history is None:
        result["pit_name"] = result.get("name", pd.Series(pd.NA, index=result.index))
        return result

    active = _active_interval(history, eval_date)
    result["pit_name"] = pd.NA
    if active.empty:
        return result
    sort_columns = [
        column
        for column in ("effective_from", "ann_date")
        if column in active.columns
    ]
    if sort_columns:
        active = active.sort_values(sort_columns, kind="stable")
    names = active.drop_duplicates("ts_code", keep="last")
    result["pit_name"] = result["ts_code"].map(
        names.set_index("ts_code")["security_name"]
    )
    return result


def _attach_industry_at_date(
    stocks: pd.DataFrame,
    history: pd.DataFrame | None,
    eval_date: str,
) -> pd.DataFrame:
    result = stocks.copy()
    if history is None:
        if "industry_code" not in result.columns:
            result["industry_code"] = pd.NA
        return result

    result = result.drop(columns=["industry", "industry_code"], errors="ignore")
    active = _active_interval(history, eval_date)
    if active.empty:
        result["industry"] = pd.NA
        result["industry_code"] = pd.NA
        return result
    active = active.sort_values("effective_from", kind="stable").drop_duplicates(
        "ts_code",
        keep="last",
    )
    return result.merge(
        active[["ts_code", "industry_code", "industry_name"]].rename(
            columns={"industry_name": "industry"}
        ),
        on="ts_code",
        how="left",
        sort=False,
    )


def build_pit_universe(
    stocks: pd.DataFrame,
    *,
    eval_date: str,
    trade_dates: Sequence[str],
    min_listed_trade_days: int,
    name_history: pd.DataFrame | None = None,
    industry_history: pd.DataFrame | None = None,
    exclude_st: bool = True,
) -> tuple[pd.DataFrame, UniverseStats]:
    """Return securities visible and eligible on one historical date."""
    if stocks.empty:
        return stocks.copy(), UniverseStats(0, 0, 0, 0, 0, 0, 0)

    work = stocks.copy()
    work["list_date"] = work["list_date"].fillna("").astype(str)
    work["delist_date"] = work["delist_date"].fillna("").astype(str)

    pre_list = (work["list_date"] == "") | (work["list_date"] > str(eval_date))
    post_delist = (work["delist_date"] != "") & (
        work["delist_date"] <= str(eval_date)
    )
    eligible = work.loc[~pre_list & ~post_delist].copy()

    ordered_dates = sorted(
        str(date) for date in trade_dates if str(date) <= str(eval_date)
    )
    listed_days = eligible["list_date"].map(
        lambda date: bisect_right(ordered_dates, str(eval_date))
        - bisect_left(ordered_dates, str(date))
    )
    recent = listed_days < int(min_listed_trade_days)
    recent_count = int(recent.sum())
    eligible = eligible.loc[~recent].copy()

    eligible = _attach_name_at_date(eligible, name_history, str(eval_date))
    name_missing = int(eligible["pit_name"].isna().sum())
    if exclude_st:
        # Preserve the frozen StockPred oracle pattern byte-for-byte for parity.
        st_mask = eligible["pit_name"].fillna("").str.contains(
            r"^(?:S\*?ST|\*?ST)|退市|退$",
            case=False,
            regex=True,
        )
    else:
        st_mask = pd.Series(False, index=eligible.index)
    st_count = int(st_mask.sum())
    eligible = eligible.loc[~st_mask].copy()

    eligible = _attach_industry_at_date(
        eligible,
        industry_history,
        str(eval_date),
    )
    industry_missing = int(eligible["industry"].isna().sum())
    stats = UniverseStats(
        input_count=len(work),
        pre_list_excluded=int(pre_list.sum()),
        post_delist_excluded=int(post_delist.sum()),
        recent_listing_excluded=recent_count,
        st_excluded=st_count,
        name_missing=name_missing,
        industry_missing=industry_missing,
    )
    return eligible.reset_index(drop=True), stats
