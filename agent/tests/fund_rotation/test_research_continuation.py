from __future__ import annotations

import json
from pathlib import Path

import pytest

from experiments.fund_rotation_research_validity.research_chain import (
    build_chain_manifest,
    build_numeric_stage_record,
    _comparison,
    _validate_real_batch_evidence,
    resolve_stockpred_root,
    select_research_range,
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


def test_resolve_stockpred_root_prefers_configured_data_root(monkeypatch, tmp_path):
    data_root = tmp_path / "stockpred"
    (data_root / "data" / "lance" / "market_core").mkdir(parents=True)
    for dataset in ("fund.lance", "fact_fund_adj.lance", "dim_fund.lance"):
        (data_root / "data" / "lance" / "market_core" / dataset).mkdir()
    monkeypatch.setenv("STOCKPRED_ROOT", str(tmp_path / "wrong"))
    monkeypatch.setenv("STOCKPRED_DATA_ROOT", str(data_root))

    assert resolve_stockpred_root() == data_root


def test_select_research_range_uses_u0_calendar_and_keeps_warmup_boundary():
    result = select_research_range(
        ["20190101", "20191227", "20200102", "20200103", "20200106", "20260801"],
        requested_start="20200101",
        requested_end="20260815",
        warmup_trade_days=2,
    )

    assert result == {
        "evaluation_start_date": "20200102",
        "evaluation_end_date": "20260801",
        "data_start_date": "20190101",
    }


def test_select_research_range_rejects_empty_range_after_warmup_adjustment():
    with pytest.raises(ValueError, match="warmup-adjusted research range is empty"):
        select_research_range(
            ["20200101", "20200102", "20200103"],
            requested_start="20200101",
            requested_end="20200102",
            warmup_trade_days=2,
        )


def test_numeric_stage_record_preserves_metrics_and_research_only_gate():
    record = build_numeric_stage_record(
        "batch_3a",
        {
            "status": "SUCCEEDED",
            "quality_status": "RESEARCH_ONLY_UNVERIFIED_UNIVERSE",
            "metrics": {"annual_return": 0.12, "sharpe": 1.1, "max_drawdown": -0.08},
            "comparison": {"winner": "challenger"},
        },
        reason="real U0-derived paired run",
    )

    assert record["execution_status"] == "COMPLETED_RESEARCH_ONLY"
    assert record["result_status"] == "NUMERIC_RESULT"
    assert record["metrics"]["annual_return"] == 0.12
    assert record["comparison"]["winner"] == "challenger"
    assert record["research_execution_allowed"] is True
    assert record["promotion_allowed"] is False


def test_comparison_does_not_fill_missing_metrics_with_zero():
    result = _comparison(
        {"strategy_id": "r39", "annual_return": 0.1},
        {"strategy_id": "r72"},
    )

    assert result["deltas"]["annual_return"] is None
    assert result["deltas"]["sharpe"] is None


def test_real_batch_evidence_validation_requires_contract_and_all_variant_outcomes(tmp_path):
    batch_dir = tmp_path / "batch"
    batch_dir.mkdir()
    fingerprint = "a" * 64
    range_info = {
        "evaluation_start_date": "20250102",
        "evaluation_end_date": "20250630",
    }
    (batch_dir / "state.json").write_text(json.dumps({"stage": "SUCCEEDED"}), encoding="utf-8")
    (batch_dir / "data_snapshot.json").write_text(json.dumps({"fingerprint": fingerprint}), encoding="utf-8")
    (batch_dir / "manifest.json").write_text(json.dumps({"data_snapshot_fingerprint": fingerprint}), encoding="utf-8")
    (batch_dir / "resolved_batch.json").write_text(
        json.dumps({"plan": range_info}), encoding="utf-8"
    )
    (batch_dir / "reports.json").write_text(
        json.dumps({
            "contract": {"components": {"data_snapshot_fingerprint": fingerprint}},
            "ranking": [{"strategy_id": "r39"}],
            "metrics": {"r39@config": {}},
            "excluded": [{"variant_key": "r78@config"}],
        }),
        encoding="utf-8",
    )

    reports = _validate_real_batch_evidence(
        batch_dir,
        expected_ids={"r39", "r78"},
        snapshot_fingerprint=fingerprint,
        range_info=range_info,
    )

    assert reports["metrics"]["r39@config"] == {}
