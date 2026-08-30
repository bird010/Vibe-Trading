from __future__ import annotations

from pathlib import Path

import pytest

from experiments.fund_rotation_research_validity.research_chain import (
    build_chain_manifest,
    stage_record,
    u1_basis_from_snapshot,
)


def test_unavailable_stage_is_recorded_as_completed_inconclusive_research():
    record = stage_record(
        "batch_3a",
        {
            "status": "UNAVAILABLE_INPUTS",
            "promotion_allowed": False,
        },
        reason="missing frozen U1 and paired control",
    )

    assert record["recorded_to_completion"] is True
    assert record["execution_status"] == "COMPLETED_RESEARCH_ONLY"
    assert record["result_status"] == "INCONCLUSIVE"
    assert record["research_execution_allowed"] is True
    assert record["promotion_allowed"] is False
    assert record["deployment_allowed"] is False
    assert record["reason"] == "missing frozen U1 and paired control"


def test_successful_stage_retains_success_without_opening_promotion_gate():
    record = stage_record(
        "batch_0",
        {
            "status": "SUCCEEDED",
            "promotion_allowed": False,
        },
        reason="summary repaired",
    )

    assert record["recorded_to_completion"] is True
    assert record["execution_status"] == "COMPLETED"
    assert record["result_status"] == "SUCCEEDED"
    assert record["research_execution_allowed"] is True
    assert record["promotion_allowed"] is False
    assert record["deployment_allowed"] is False


def test_u1_basis_derives_same_result_set_without_fabricating_identity():
    source = (
        Path(__file__).resolve().parents[3]
        / "agent/runs/fund_rotation/1a8eb8560998/data_snapshot.json"
    )

    basis = u1_basis_from_snapshot(source)

    assert basis["derivation"] == "U1_FROM_U0"
    assert basis["u0_count"] == basis["u1_count"] > 0
    assert basis["u1_equals_u0"] is True
    assert basis["identity_evidence_status"] == "UNAVAILABLE"
    assert basis["promotion_allowed"] is False


def test_chain_manifest_keeps_inconclusive_stages_in_the_completed_chain():
    manifest = build_chain_manifest(
        {"derivation": "U1_FROM_U0", "u1_equals_u0": True},
        [
            {"stage": "batch_1", "result_status": "INCONCLUSIVE", "recorded_to_completion": True},
            {"stage": "shadow_a", "result_status": "INCONCLUSIVE", "recorded_to_completion": True},
        ],
    )

    assert manifest["chain_status"] == "INCONCLUSIVE"
    assert manifest["recorded_to_completion"] is True
    assert manifest["research_execution_allowed"] is True
    assert manifest["promotion_allowed"] is False
    assert manifest["deployment_allowed"] is False
    assert [stage["stage"] for stage in manifest["stages"]] == ["batch_1", "shadow_a"]


@pytest.mark.parametrize("status", ["invalid", "PIT_INVALID", "FAILED", "error"])
def test_core_failure_is_not_relaxed_to_research_only(status):
    record = stage_record(
        "batch_1",
        {"status": status, "promotion_allowed": False},
        reason="invalid query timezone",
    )

    assert record["execution_status"] == "FAILED_CORE"
    assert record["result_status"] == status
    assert record["recorded_to_completion"] is True
    assert record["research_execution_allowed"] is False
    assert record["promotion_allowed"] is False
    assert record["deployment_allowed"] is False


def test_chain_manifest_preserves_core_failure_as_failed_closed():
    manifest = build_chain_manifest(
        {"derivation": "U1_FROM_U0", "u1_equals_u0": True},
        [
            {
                "stage": "batch_1",
                "execution_status": "FAILED_CORE",
                "result_status": "PIT_INVALID",
                "recorded_to_completion": True,
            }
        ],
    )

    assert manifest["chain_status"] == "FAILED_CORE"
    assert manifest["research_execution_allowed"] is False
    assert manifest["promotion_allowed"] is False
    assert manifest["deployment_allowed"] is False
