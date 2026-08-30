from __future__ import annotations

import pytest

from backtest.fund_rotation.contracts import DecisionKind, QualityStatus, TargetWeightDecision
from backtest.fund_rotation.strategies.ai_rotation_r78_survivor_combo.strategy import (
    AiRotationR78SurvivorComboSession,
    MechanismProvenance,
    compose_survivor_layers,
    select_survivor_layers,
)


def _candidate(
    mechanism_id: str,
    stage: str,
    *,
    promotion_allowed: bool = True,
    p0: int = 0,
    p1: int = 0,
) -> MechanismProvenance:
    return MechanismProvenance(
        mechanism_id=mechanism_id,
        stage=stage,
        source_sha256=f"hash-{mechanism_id}",
        promotion_allowed=promotion_allowed,
        review_p0=p0,
        review_p1=p1,
    )


def test_survivors_are_selected_in_fixed_composition_order():
    selected = select_survivor_layers(
        [_candidate("r74_vol", "risk"), _candidate("r73_rank", "ranking")]
    )
    assert [item.mechanism_id for item in selected] == ["r73_rank", "r74_vol"]


def test_duplicate_mechanisms_are_rejected_before_composition():
    with pytest.raises(ValueError, match="duplicate"):
        compose_survivor_layers(
            [_candidate("r73_rank", "ranking"), _candidate("r73_rank", "ranking")]
        )


def test_composition_propagates_frozen_provenance_and_hashes():
    result = compose_survivor_layers([_candidate("r73_rank", "ranking")])
    assert result["status"] == "READY"
    assert result["mechanism_ids"] == ["r73_rank"]
    assert result["source_sha256"] == {"r73_rank": "hash-r73_rank"}


def test_unpromoted_or_reviewed_mechanisms_fail_closed_to_empty_survivor_set():
    selected = select_survivor_layers(
        [
            _candidate("r73_rank", "ranking", promotion_allowed=False),
            _candidate("r74_vol", "risk", p1=1),
        ]
    )
    assert selected == ()
    assert compose_survivor_layers(selected) == {
        "status": "UNAVAILABLE_INPUTS",
        "mechanism_ids": [],
        "source_sha256": {},
    }


def test_r78_is_registered_as_a_research_only_strategy():
    from backtest.fund_rotation.strategies.registry import default_fund_rotation_strategies

    assert any(
        strategy.descriptor.id == "ai_rotation_r78_survivor_combo"
        for strategy in default_fund_rotation_strategies()
    )


def test_r78_session_is_invalid_and_all_cash_without_survivors(monkeypatch):
    baseline = TargetWeightDecision(
        decision_id="baseline",
        signal_date="2026-01-01",
        action=DecisionKind.SET_TARGETS,
        target_weights={"A": 1.0},
        cash_weight=0.0,
    )
    monkeypatch.setattr(
        "backtest.fund_rotation.strategies.ai_rotation_r39_incumbent_carry.strategy.AiRotationR39IncumbentCarrySession.evaluate",
        lambda self, context: baseline,
    )
    session = object.__new__(AiRotationR78SurvivorComboSession)
    result = session.evaluate(type("Context", (), {"signal_date": "2026-01-01"})())
    assert result.action is DecisionKind.INVALID
    assert result.target_weights == {}
    assert result.cash_weight == 1.0
    assert result.quality_status is QualityStatus.FAILED
