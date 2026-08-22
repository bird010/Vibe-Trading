"""Focused behavior tests for the R34 staged re-entry."""

from __future__ import annotations

import pytest

from backtest.fund_rotation.strategies.ai_rotation_r34_staged_reentry.strategy import (
    apply_staged_reentry,
)


def test_new_target_is_half_sized_while_existing_target_is_unchanged():
    targets, cash, staged = apply_staged_reentry(
        {"HELD": 1 / 3},
        {"HELD": 1 / 3, "NEW": 1 / 3},
    )

    assert targets == {
        "HELD": pytest.approx(1 / 3),
        "NEW": pytest.approx(1 / 6),
    }
    assert cash == pytest.approx(1 / 2)
    assert staged == {"NEW"}


def test_already_held_target_is_not_staged_again():
    targets, cash, staged = apply_staged_reentry(
        {"HELD": 1 / 3},
        {"HELD": 1 / 3},
    )

    assert targets == {"HELD": pytest.approx(1 / 3)}
    assert cash == pytest.approx(2 / 3)
    assert staged == set()
