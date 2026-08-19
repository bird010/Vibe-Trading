"""Deterministic Graph signal ranking and target portfolio construction."""

from __future__ import annotations

import numpy as np
import pandas as pd


def rank_signals(signals: pd.DataFrame) -> pd.DataFrame:
    """Rank signals by score, using stock code as the deterministic tie-breaker."""
    ranked = signals.sort_values(
        ["score", "ts_code"],
        ascending=[False, True],
        kind="stable",
    ).reset_index(drop=True)
    ranked["rank"] = np.arange(1, len(ranked) + 1)
    return ranked


def select_buffered_portfolio(
    ranked_codes: list[str],
    *,
    previous_holdings: set[str],
    target_size: int,
    retain_rank: int,
) -> list[str]:
    """Retain eligible holdings, then fill the portfolio in current rank order."""
    if target_size <= 0:
        return []

    unique_ranked = list(dict.fromkeys(ranked_codes))
    retainable = set(unique_ranked[: max(retain_rank, 0)])
    ordered_retained = [
        code
        for code in unique_ranked
        if code in previous_holdings and code in retainable
    ]
    selected = set(ordered_retained[:target_size])

    for code in unique_ranked:
        if len(selected) >= target_size:
            break
        selected.add(code)

    return [code for code in unique_ranked if code in selected][:target_size]


def build_equal_weight_targets(
    signals: pd.DataFrame,
    *,
    top_n: int,
    previous_holdings: set[str],
    retain_rank: int,
) -> pd.DataFrame:
    """Build ranked equal-weight targets with the frozen buffer behavior."""
    ranked = rank_signals(signals)
    codes = select_buffered_portfolio(
        ranked["ts_code"].tolist(),
        previous_holdings=previous_holdings,
        target_size=top_n,
        retain_rank=retain_rank,
    )
    selected = ranked[ranked["ts_code"].isin(codes)].copy()
    selected["target_weight"] = 1.0 / len(selected) if len(selected) else 0.0
    return selected.sort_values("rank", kind="stable").reset_index(drop=True)
