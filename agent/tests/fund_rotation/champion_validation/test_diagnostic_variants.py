from __future__ import annotations

from backtest.fund_rotation.champion_validation.diagnostic_variants import (
    build_ablation_matrix,
)


def test_ablation_matrix_declares_a_to_e_with_one_adjacent_difference():
    matrix = build_ablation_matrix()

    assert [variant.variant_id for variant in matrix] == ["A", "B", "C", "D", "E"]
    assert all(variant.controller_diagnostic for variant in matrix)
    assert all(not variant.catalog_registered for variant in matrix)
    assert [variant.declared_difference for variant in matrix] == [
        "baseline_m0_ranking",
        "add_m0_positive_eligibility",
        "add_m1_positive_eligibility",
        "replace_m0_with_arithmetic_mean_ranking",
        "replace_arithmetic_mean_with_geometric_ranking",
    ]


def test_ablation_matrix_freezes_r11_parity_metadata_without_a_catalog_entry():
    matrix = build_ablation_matrix()

    assert matrix.parity.strategy_id == "ai_rotation_r11_persist_geom"
    assert matrix.parity.momentum_window == 4
    assert matrix.parity.top_n == 3
    assert matrix.parity.recluster_weeks == 26
    assert matrix.parity.required is True
    assert "R11" in matrix.parity.reference_label
    assert not hasattr(matrix, "winner")
    assert not hasattr(matrix, "recommended_parameter")
