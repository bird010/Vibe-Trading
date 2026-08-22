"""Focused behavior tests for the R33 quality-rejection fallback."""

from __future__ import annotations

import pytest

from backtest.fund_rotation.strategies.ai_rotation_r33_quality_fallback.strategy import (
    apply_quality_fallback,
)


def test_quality_reject_and_positive_benchmark_fill_one_slot_only():
    targets, cash, used = apply_quality_fallback(
        {"A": 1 / 3},
        2 / 3,
        quality_rejected=True,
        benchmark_return=0.02,
        benchmark_code="510300.SH",
        top_n=3,
    )

    assert targets == {"A": pytest.approx(1 / 3), "510300.SH": pytest.approx(1 / 3)}
    assert cash == pytest.approx(1 / 3)
    assert used is True


def test_quality_reject_without_positive_benchmark_keeps_cash():
    targets, cash, used = apply_quality_fallback(
        {"A": 1 / 3},
        2 / 3,
        quality_rejected=True,
        benchmark_return=0.0,
        benchmark_code="510300.SH",
        top_n=3,
    )

    assert targets == {"A": pytest.approx(1 / 3)}
    assert cash == pytest.approx(2 / 3)
    assert used is False
