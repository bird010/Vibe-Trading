"""Regression test for Python bool/int ambiguity in momentum selection."""

from __future__ import annotations

import pytest

from backtest.fund_rotation.momentum import select_top_clusters


def test_select_top_clusters_rejects_bool() -> None:
    """Boolean momentum must not be accepted as the numeric value 1.0."""
    with pytest.raises(
        TypeError,
        match=r"momentum for cluster 8 must be numeric, got bool",
    ):
        select_top_clusters(  # type: ignore[arg-type]
            {1: 0.05, 8: True},
            top_n=3,
            threshold=0.0,
        )
