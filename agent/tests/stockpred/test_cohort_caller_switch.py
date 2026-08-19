"""Tests for caller switch: evaluation_engine routing in StrategyBatchRequest."""

from __future__ import annotations

import pytest

from src.stockpred.strategies.contracts import StrategyBatchRequest


def test_default_evaluation_engine_is_cohort():
    req = StrategyBatchRequest(start="20250101", end="20250331", select_all=True)
    assert req.evaluation_engine == "cohort"


def test_evaluation_engine_can_be_portfolio():
    req = StrategyBatchRequest(
        start="20250101", end="20250331", select_all=True, evaluation_engine="portfolio"
    )
    assert req.evaluation_engine == "portfolio"


def test_evaluation_engine_rejects_invalid():
    with pytest.raises(Exception):
        StrategyBatchRequest(
            start="20250101", end="20250331", select_all=True, evaluation_engine="invalid"
        )


def test_parity_mode_with_cohort_engine():
    req = StrategyBatchRequest(
        start="20250101", end="20250331", select_all=True,
        mode="parity", evaluation_engine="cohort",
    )
    assert req.mode == "parity"
    assert req.evaluation_engine == "cohort"
