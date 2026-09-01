"""ETF universe filtering and historical eligibility — §8.

Static name filter (§8.1) and per-signal-date eligibility (§8.2).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import pandas as pd


class ExclusionReason(str, Enum):
    """Structured exclusion reasons for universe filtering."""

    NOT_ETF_NAME = "not_etf_name"
    QDII = "qdii"
    LOF = "lof"
    FEEDER = "feeder"
    NOT_YET_LISTED = "not_yet_listed"
    INVALID_LIST_DATE = "invalid_list_date"
    INSUFFICIENT_ADJ_COVERAGE = "insufficient_adj_coverage"
    INSUFFICIENT_VALID_WEEKS = "insufficient_valid_weeks"
    PAIRWISE_EXCLUSION = "pairwise_exclusion"
    NO_VALID_CLOSE = "no_valid_close"


@dataclass(frozen=True)
class ExclusionRecord:
    """One ETF exclusion event with structured reason."""

    ts_code: str
    reason: ExclusionReason
    details: str = ""
    signal_date: str = ""


# ── Static name filter keywords ──

_EXCLUDE_KEYWORDS = ("QDII", "LOF", "联接")


def filter_etf_universe(
    dim_fund: pd.DataFrame,
    *,
    include_qdii: bool = False,
) -> pd.DataFrame:
    """§8.1 — Static ETF name filter.

    Keeps rows where:
    - name contains 'ETF'
    - name does NOT contain 'QDII', 'LOF', or '联接'

    Args:
        dim_fund: DataFrame with at least columns [ts_code, name].

    Returns:
        Filtered DataFrame (subset of input rows).
    """
    if dim_fund.empty:
        return dim_fund

    name = dim_fund["name"].astype(str)
    mask = name.str.contains("ETF", case=True, na=False)
    excluded_keywords = tuple(
        kw for kw in _EXCLUDE_KEYWORDS if not (include_qdii and kw == "QDII")
    )
    for kw in excluded_keywords:
        mask &= ~name.str.contains(kw, case=True, na=False)

    return dim_fund[mask].reset_index(drop=True)


def check_historical_eligibility(
    dim_fund: pd.DataFrame,
    signal_date: str,
) -> tuple[list[str], list[ExclusionRecord]]:
    """§8.2 — Per-signal-date historical eligibility check.

    Assumes dim_fund has already passed static name filter.

    Args:
        dim_fund: DataFrame with columns [ts_code, name, list_date].
        signal_date: Signal date in YYYYMMDD format.

    Returns:
        (eligible_codes, exclusion_records)
    """
    eligible: list[str] = []
    excluded: list[ExclusionRecord] = []

    for _, row in dim_fund.iterrows():
        ts_code = str(row["ts_code"])
        list_date_raw = row.get("list_date")

        # Validate list_date
        parsed = _parse_date(list_date_raw)
        if parsed is None:
            excluded.append(ExclusionRecord(
                ts_code=ts_code,
                reason=ExclusionReason.INVALID_LIST_DATE,
                details=f"list_date={list_date_raw!r}",
                signal_date=signal_date,
            ))
            continue

        # list_date <= signal_date
        if parsed > signal_date:
            excluded.append(ExclusionRecord(
                ts_code=ts_code,
                reason=ExclusionReason.NOT_YET_LISTED,
                details=f"list_date={parsed} > signal_date={signal_date}",
                signal_date=signal_date,
            ))
            continue

        eligible.append(ts_code)

    return eligible, excluded


def _parse_date(value: object) -> str | None:
    """Parse a date value to YYYYMMDD string, or None if invalid."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        if pd.isna(value):
            return None
        value = str(int(value))
    s = str(value).strip()
    if len(s) == 8 and s.isdigit():
        # Basic sanity: year 1900-2100, month 01-12, day 01-31
        year, month, day = int(s[:4]), int(s[4:6]), int(s[6:8])
        if 1900 <= year <= 2100 and 1 <= month <= 12 and 1 <= day <= 31:
            return s
        return None
    # Try pandas parsing for other formats
    try:
        ts = pd.Timestamp(s)
        if pd.isna(ts):
            return None
        return ts.strftime("%Y%m%d")
    except (ValueError, TypeError):
        return None
