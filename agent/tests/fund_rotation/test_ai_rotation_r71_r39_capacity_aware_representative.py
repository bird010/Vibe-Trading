from __future__ import annotations

from backtest.fund_rotation.strategies.ai_rotation_r71_r39_capacity_aware_representative.strategy import (
    AiRotationR71R39CapacityAwareRepresentativeStrategy,
    apply_capacity_overlay,
)


def _candidate(code: str, adv: float = 100_000.0) -> dict[str, object]:
    return {
        "code": code,
        "score": 1.0,
        "adv": adv,
        "tradable": True,
        "visible": True,
        "known_at": "20240105",
        "volume_date": "20240105",
        "as_of_date": "20240105",
        "cluster_id": "c1",
        "identity_key": "i1",
        "lot_size": 100,
    }


def test_overlay_is_exact_r39_fallback_without_capacity_evidence():
    result = apply_capacity_overlay(
        {"A": 0.5, "B": 0.5},
        candidates=None,
        target_quantity=1_000,
        market_observation=None,
        prior_representative="A",
    )
    assert result.target_weights == {"A": 0.5, "B": 0.5}
    assert result.cash_weight == 0.0
    assert result.diagnostics["capacity_status"] == "unavailable"


def test_overlay_unlocks_only_with_explicit_capacity_and_preserves_weight():
    result = apply_capacity_overlay(
        {"A": 1.0},
        candidates=[_candidate("A", adv=0), _candidate("B")],
        target_quantity=1_000,
        market_observation={
            "decision_cutoff": "20240106",
            "max_participation": 0.05,
            "execution_horizon": 1,
            "lot_size": 100,
            "target_cluster_id": "c1",
            "target_identity_key": "i1",
        },
        prior_representative="A",
    )
    assert result.target_weights == {"B": 1.0}
    assert result.cash_weight == 0.0
    assert result.diagnostics["capacity_status"] == "selected"
    assert result.diagnostics["capacity_reason"] == "CAPACITY_FALLBACK"


def test_overlay_cash_fallback_does_not_remove_other_r39_targets():
    result = apply_capacity_overlay(
        {"A": 0.5, "Z": 0.5},
        candidates=[_candidate("A", adv=0)],
        target_quantity=1_000,
        market_observation={
            "decision_cutoff": "20240106",
            "max_participation": 0.05,
            "execution_horizon": 1,
            "lot_size": 100,
            "target_cluster_id": "c1",
            "target_identity_key": "i1",
        },
        prior_representative="A",
    )
    assert result.target_weights == {"Z": 0.5}
    assert result.cash_weight == 0.5
    assert result.diagnostics["capacity_reason"] == "CAPACITY_CASH_FALLBACK"


def test_overlay_strategy_has_explicit_r71_identity():
    assert (
        AiRotationR71R39CapacityAwareRepresentativeStrategy.descriptor.id
        == "ai_rotation_r71_r39_capacity_aware_representative"
    )
