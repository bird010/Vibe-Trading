"""Focused behavior tests for the R31 one-week exit brake."""

from __future__ import annotations

import pytest

from backtest.fund_rotation.strategies.ai_rotation_r31_fast_exit.strategy import (
    apply_fast_exit,
)


def test_fast_exit_removes_only_already_held_negative_clusters():
    targets, cash, exited = apply_fast_exit(
        previous_weights={"HELD": 1 / 3, "OTHER": 1 / 3},
        target_weights={"HELD": 1 / 3, "NEW": 1 / 3},
        cluster_by_code={"HELD": 1, "OTHER": 2, "NEW": 3},
        one_week_cluster_returns={1: -0.01, 3: -0.20},
        top_n=3,
    )

    assert targets == {"NEW": pytest.approx(1 / 3)}
    assert cash == pytest.approx(2 / 3)
    assert exited == {1}


def test_fast_exit_keeps_held_cluster_with_positive_one_week_return():
    targets, cash, exited = apply_fast_exit(
        previous_weights={"HELD": 1 / 3},
        target_weights={"HELD": 1 / 3},
        cluster_by_code={"HELD": 1},
        one_week_cluster_returns={1: 0.01},
        top_n=3,
    )

    assert targets == {"HELD": pytest.approx(1 / 3)}
    assert cash == pytest.approx(2 / 3)
    assert exited == set()


def test_fast_exit_treats_nonfinite_one_week_return_as_unavailable():
    targets, cash, exited = apply_fast_exit(
        previous_weights={"HELD": 1 / 3},
        target_weights={"HELD": 1 / 3},
        cluster_by_code={"HELD": 1},
        one_week_cluster_returns={1: None},
        top_n=3,
    )

    assert targets == {"HELD": pytest.approx(1 / 3)}
    assert cash == pytest.approx(2 / 3)
    assert exited == set()
