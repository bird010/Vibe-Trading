from __future__ import annotations

import json

import pytest
from experiments.fund_rotation_research_validity import batch_4_ablation

from backtest.fund_rotation.ablation import (
    AblationArm,
    apply_ablation_arm,
    fixed_ablation_arms,
)


def test_fixed_arms_disable_exactly_one_mechanism_and_keep_identity_dedup():
    arms = fixed_ablation_arms()
    assert [(arm.arm_id, arm.cluster, arm.carry, arm.identity_dedup) for arm in arms] == [
        ("M0", False, False, True),
        ("M1", True, False, True),
        ("M2", True, True, True),
    ]
    assert all(arm.momentum for arm in arms)


def test_identity_dedup_is_shared_by_momentum_only_arm():
    result = apply_ablation_arm(
        AblationArm("M0", momentum=True, cluster=False, carry=False, identity_dedup=True),
        momentum_scores={"A": 0.9, "B": 0.8, "C": 0.7},
        cluster_by_code={"A": 1, "B": 2, "C": 3},
        identity_by_code={"A": "IDX-1", "B": "IDX-1", "C": "IDX-2"},
        representatives={1: "A", 2: "B", 3: "C"},
        top_n=2,
        previous_weights={},
    )
    assert result.selected_codes == ("A", "C")
    assert result.diagnostics["identity_dedup"] is True


def test_identity_dedup_uses_fixed_u1_minimum_code_before_momentum_ranking():
    result = apply_ablation_arm(
        AblationArm("M0", momentum=True, cluster=False, carry=False, identity_dedup=True),
        momentum_scores={"A": 0.8, "B": 0.9, "C": 0.7},
        cluster_by_code={"A": 1, "B": 1, "C": 2},
        identity_by_code={"A": "IDX-1", "B": "IDX-1", "C": "IDX-2"},
        representatives={1: "A", 2: "C"},
        top_n=2,
        previous_weights={},
    )
    assert result.selected_codes == ("A", "C")


def test_cluster_tie_break_uses_minimum_member_code_not_representative():
    result = apply_ablation_arm(
        AblationArm("M1", momentum=True, cluster=True, carry=False, identity_dedup=True),
        momentum_scores={"A": 0.8, "Z": 0.8, "B": 0.8},
        cluster_by_code={"A": 1, "Z": 1, "B": 2},
        identity_by_code={"A": "IDX-1", "Z": "IDX-2", "B": "IDX-3"},
        representatives={1: "Z", 2: "B"},
        top_n=1,
        previous_weights={},
    )
    assert result.selected_codes == ("Z",)
    assert result.diagnostics["selected_clusters"] == [1]


def test_cluster_tie_break_includes_u1_members_without_valid_momentum_score():
    result = apply_ablation_arm(
        AblationArm("M1", momentum=True, cluster=True, carry=False, identity_dedup=True),
        momentum_scores={"Z": 0.8, "B": 0.8},
        cluster_by_code={"A": 1, "Z": 1, "B": 2},
        identity_by_code={"A": "IDX-1", "Z": "IDX-2", "B": "IDX-3"},
        representatives={1: "Z", 2: "B"},
        top_n=1,
        previous_weights={},
    )
    assert result.selected_codes == ("Z",)
    assert result.diagnostics["selected_clusters"] == [1]


def test_cluster_arm_uses_representatives_and_carry_arm_only_adds_r39_carry():
    inputs = {
        "momentum_scores": {"A": 0.9, "B": 0.8, "C": 0.7},
        "cluster_by_code": {"A": 1, "B": 1, "C": 2},
        "identity_by_code": {"A": "IDX-1", "B": "IDX-2", "C": "IDX-3"},
        "representatives": {1: "B", 2: "C"},
        "top_n": 2,
        "previous_weights": {"B": 0.5},
    }
    m1 = apply_ablation_arm(
        AblationArm("M1", momentum=True, cluster=True, carry=False, identity_dedup=True),
        **inputs,
    )
    m2 = apply_ablation_arm(
        AblationArm("M2", momentum=True, cluster=True, carry=True, identity_dedup=True),
        **inputs,
    )
    assert m1.selected_codes == ("B", "C")
    assert m1.target_weights == {"B": pytest.approx(0.5), "C": pytest.approx(0.5)}
    assert m2.target_weights["B"] > m1.target_weights["B"]
    assert m2.diagnostics["carry"] is True
    assert m1.diagnostics["carry"] is False


def test_identity_dedup_cannot_be_disabled_by_an_arm():
    with pytest.raises(ValueError, match="identity_dedup"):
        apply_ablation_arm(
            AblationArm("M0", momentum=True, cluster=False, carry=False, identity_dedup=False),
            momentum_scores={"A": 0.9},
            cluster_by_code={"A": 1},
            identity_by_code={"A": "IDX-1"},
            representatives={1: "A"},
            top_n=1,
            previous_weights={},
        )


def test_batch_4_registration_is_stable_and_fail_closed(tmp_path):
    report_path = tmp_path / "batch_4_report.md"
    first = batch_4_ablation.run(output_dir=tmp_path / "batch_4", report_path=report_path)
    manifest_path = tmp_path / "batch_4" / "manifest.json"
    first_manifest = manifest_path.read_text(encoding="utf-8")
    first_report = report_path.read_text(encoding="utf-8")

    second = batch_4_ablation.run(output_dir=tmp_path / "batch_4", report_path=report_path)

    assert first == second
    assert first["status"] == "UNAVAILABLE_INPUTS"
    assert first["promotion_allowed"] is False
    assert first_manifest == manifest_path.read_text(encoding="utf-8")
    assert first_report == report_path.read_text(encoding="utf-8")
    manifest = json.loads(first_manifest)
    assert manifest["source_identity"]["experiment_script_sha256"] == batch_4_ablation._sha256(
        batch_4_ablation.Path(batch_4_ablation.__file__).resolve()
    )
