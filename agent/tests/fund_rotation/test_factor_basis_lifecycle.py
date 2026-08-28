import pytest

from backtest.fund_rotation.factor_basis import (
    PositionTransition,
    classify_position_transition,
    cleanup_native_factor_basis,
    sync_position_factor_basis,
)


def test_position_transition_classifies_full_exit_and_reentry():
    assert classify_position_transition(pre_size=1000, post_size=0) is PositionTransition.CLOSE
    assert classify_position_transition(pre_size=0, post_size=1000) is PositionTransition.OPEN


def test_position_basis_is_removed_on_close_and_overwritten_on_reentry():
    basis = {"511220.SH": 1.4357}

    sync_position_factor_basis(basis, code="511220.SH", pre_size=1000, post_size=0, current_factor=14.7008)
    assert "511220.SH" not in basis

    sync_position_factor_basis(basis, code="511220.SH", pre_size=0, post_size=1000, current_factor=14.7008)
    assert basis["511220.SH"] == pytest.approx(14.7008)


def test_native_cleanup_keeps_basis_for_live_order_without_position():
    basis = {"A": 1.0, "B": 1.0}
    positions = {"A": {"size": 0}, "B": {"size": 0}}

    cleanup_native_factor_basis(
        basis,
        positions=positions,
        live_order_codes={"A"},
    )

    assert basis == {"A": 1.0}


def test_new_basis_requires_current_factor():
    basis = {}

    with pytest.raises(ValueError, match="adj_factor is required"):
        sync_position_factor_basis(
            basis,
            code="A",
            pre_size=0,
            post_size=100,
            current_factor=None,
        )
