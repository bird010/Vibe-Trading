"""Focused tests for frozen R11 champion-validation benchmarks."""

from __future__ import annotations

from backtest.fund_rotation.champion_validation.benchmark_suite import (
    build_benchmark_specs,
    compare_execution_identity,
)


def test_build_benchmark_specs_freezes_b0_through_b5_behavior() -> None:
    specs = build_benchmark_specs()

    assert tuple(spec.benchmark_id for spec in specs) == ("B0", "B1", "B2", "B3", "B4", "B5")
    by_id = {spec.benchmark_id: spec for spec in specs}
    assert by_id["B0"].implementation == "cash"
    assert by_id["B1"].implementation == "dynamic_pit_equal_weight"
    assert by_id["B2"].instrument_code == "510300.SH"
    assert by_id["B3"].selection == "top_3_m0_positive_non_cluster"
    assert by_id["B4"].selection == "top_3_m0_m1_positive_non_cluster"
    assert by_id["B5"].implementation == "dynamic_pit_inverse_vol_13w"
    assert by_id["B0"].theoretical is False
    assert by_id["B1"].theoretical is False


def test_theoretical_benchmark_is_explicitly_not_execution_comparable() -> None:
    specs = build_benchmark_specs(theoretical_equal_weight=True)
    by_id = {spec.benchmark_id: spec for spec in specs}

    assert by_id["B1"].theoretical is True
    assert by_id["B1"].comparable is False
    assert by_id["B1"].comparability_reason == "THEORETICAL_NO_COST"


def test_execution_identity_rejects_snapshot_universe_calendar_and_cost_drift() -> None:
    reference = {
        "data_snapshot": "snap-1",
        "universe_id": "pit-1",
        "evaluation_calendar": "cal-1",
        "cost_policy": "cost-1",
        "execution_identity": "exec-1",
    }
    candidate = {**reference, "data_snapshot": "snap-2", "cost_policy": "cost-2"}

    comparison = compare_execution_identity(reference, candidate)

    assert comparison.comparable is False
    assert comparison.mismatches == ("data_snapshot", "cost_policy")
    assert "IDENTITY_MISMATCH" in comparison.reason_codes


def test_execution_identity_requires_every_required_field_on_both_sides() -> None:
    reference = {
        "data_snapshot": "snap-1",
        "universe_id": "pit-1",
        "evaluation_calendar": "cal-1",
        "cost_policy": "cost-1",
        "execution_identity": "exec-1",
    }
    candidate = {**reference}
    del candidate["execution_identity"]

    comparison = compare_execution_identity(reference, candidate)

    assert comparison.comparable is False
    assert comparison.mismatches == ("execution_identity",)
    assert "MISSING_REQUIRED_IDENTITY" in comparison.reason_codes
