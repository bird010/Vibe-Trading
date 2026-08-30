from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest

from src.stockpred.fund_rotation.forward_validation import EXPECTED_ARTIFACT_NAMES
from experiments.fund_rotation_research_validity.start_r40_shadow import (
    R40_CEILING,
    R40_SOURCE_PATH,
    R40_STRATEGY_ID,
    INTEGRITY_ARTIFACT_NAME,
    _pit_stable_hash,
    start_shadow_a,
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_start_shadow_a_freezes_r40_and_records_insufficient_forward_evidence(
    tmp_path: Path,
) -> None:
    result = start_shadow_a(
        output_dir=tmp_path,
        report_path=tmp_path / "shadow_a_report.md",
    )

    assert result["strategy_id"] == R40_STRATEGY_ID
    assert result["ceiling"] == pytest.approx(R40_CEILING)
    assert result["shadow_status"] == "STARTED"
    assert result["qualification_status"] == "INSUFFICIENT_FORWARD_EVIDENCE"
    assert result["promotion_allowed"] is False

    frozen = json.loads(
        (tmp_path / "frozen_strategy_manifest.json").read_text(encoding="utf-8")
    )
    assert frozen["strategy_id"] == R40_STRATEGY_ID
    assert frozen["config"]["single_name_ceiling"] == pytest.approx(0.5)
    assert frozen["config"]["r40_source_sha256"] == _sha256(R40_SOURCE_PATH)
    assert frozen["lifecycle"] == "FROZEN"

    shadow_manifest = json.loads(
        (tmp_path / "shadow_manifest.json").read_text(encoding="utf-8")
    )
    assert shadow_manifest["shadow_service_binding"]["runtime_binding_status"] == "BOOTSTRAP_ONLY"
    integrity = json.loads(
        (tmp_path / INTEGRITY_ARTIFACT_NAME).read_text(encoding="utf-8")
    )
    assert integrity["artifact_hashes"]["shadow_manifest.json"] == _sha256(
        tmp_path / "shadow_manifest.json"
    )
    assert integrity["artifact_hashes"]["shadow_a_report.md"] == _sha256(
        tmp_path / "shadow_a_report.md"
    )

    evidence = json.loads(
        (tmp_path / "qualification_evidence.json").read_text(encoding="utf-8")
    )
    assert evidence["status"] == "unavailable"
    assert evidence["reason_code"] == "INSUFFICIENT_FORWARD_EVIDENCE"
    assert evidence["metrics"]["forward_observation_weeks"] == "unavailable"

    policy = json.loads(
        (tmp_path / "qualification_policy.json").read_text(encoding="utf-8")
    )
    cycles_gate = next(
        gate for gate in policy["hard_gates"]
        if gate["gate_id"] == "min-completed-rebalance-cycles"
    )
    assert cycles_gate["threshold"] == 6

    assert set(EXPECTED_ARTIFACT_NAMES).issubset(
        {path.name for path in tmp_path.iterdir()}
    )


def test_start_shadow_a_is_idempotent_and_does_not_overwrite_frozen_artifacts(
    tmp_path: Path,
) -> None:
    first = start_shadow_a(
        output_dir=tmp_path,
        report_path=tmp_path / "shadow_a_report.md",
    )
    first_hashes = {
        name: _sha256(tmp_path / name)
        for name in ("frozen_strategy_manifest.json", "qualification_policy.json")
    }

    second = start_shadow_a(
        output_dir=tmp_path,
        report_path=tmp_path / "shadow_a_report.md",
    )

    assert second["strategy_version_id"] == first["strategy_version_id"]
    assert {
        name: _sha256(tmp_path / name)
        for name in first_hashes
    } == first_hashes

    with pytest.raises(ValueError, match="identity inputs"):
        start_shadow_a(
            output_dir=tmp_path,
            report_path=tmp_path / "shadow_a_report.md",
            r39_control_manifest=tmp_path / "new_control.json",
        )


def test_start_shadow_a_rejects_present_but_invalid_identity_inputs(tmp_path: Path) -> None:
    invalid_u1 = tmp_path / "invalid_u1.json"
    invalid_u1.write_text(
        json.dumps(
            {
                "schema_version": "fund_rotation_pit_identity_v1",
                "snapshot_status": {"status": "available", "date_count": 0, "invalid_dates": []},
                "snapshots": [],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="fail-closed"):
        start_shadow_a(output_dir=tmp_path / "u1", frozen_u1_manifest=invalid_u1)

    malformed_available_u1 = tmp_path / "malformed_available_u1.json"
    malformed_available_u1.write_text(
        json.dumps(
            {
                "schema_version": "fund_rotation_pit_identity_v1",
                "snapshot_status": {"status": "available", "date_count": 1, "invalid_dates": []},
                "snapshots": [{"signal_date": "2020-99-99", "u1": {"identity_hash": "0" * 64}}],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="fail-closed"):
        start_shadow_a(output_dir=tmp_path / "malformed_u1", frozen_u1_manifest=malformed_available_u1)

    invalid_r39 = tmp_path / "invalid_r39.json"
    invalid_r39.write_text(json.dumps({"strategy_id": {"not": "a string"}}), encoding="utf-8")
    with pytest.raises(ValueError, match="fail-closed"):
        start_shadow_a(output_dir=tmp_path / "r39", r39_control_manifest=invalid_r39)

    wrong_r39 = tmp_path / "wrong_r39.json"
    wrong_r39.write_text(
        json.dumps({"strategy_id": "not-r39", "implementation_hash": "0" * 64}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="fail-closed"):
        start_shadow_a(output_dir=tmp_path / "wrong_r39", r39_control_manifest=wrong_r39)

    wrong_hash_r39 = tmp_path / "wrong_hash_r39.json"
    wrong_hash_r39.write_text(
        json.dumps(
            {
                "strategy_id": "ai_rotation_r39_incumbent_carry",
                "implementation_hash": "0" * 64,
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="fail-closed"):
        start_shadow_a(output_dir=tmp_path / "wrong_hash_r39", r39_control_manifest=wrong_hash_r39)


def test_start_shadow_a_rejects_tampered_report_via_integrity_sidecar(tmp_path: Path) -> None:
    report_path = tmp_path / "shadow_a_report.md"
    start_shadow_a(output_dir=tmp_path, report_path=report_path)
    report_path.write_text(report_path.read_text(encoding="utf-8") + "tampered\n", encoding="utf-8")
    with pytest.raises(ValueError, match="integrity hash mismatch: report"):
        start_shadow_a(output_dir=tmp_path, report_path=report_path)


def _reseal_pit_snapshot(snapshot: dict) -> None:
    identity_mapping = snapshot["identity_mapping"]
    identity_payload: dict[str, object] = {
        "layer": snapshot["layer"],
        "identity_mapping": sorted(identity_mapping.items()),
    }
    if snapshot["layer"] == "U1":
        representatives = {
            identity: min(
                code
                for code in snapshot["eligible_codes"]
                if identity_mapping.get(code) == identity
            )
            for identity in {identity_mapping.get(code) for code in snapshot["eligible_codes"]}
            if identity is not None
        }
        identity_payload["representatives"] = sorted(representatives.items())
    snapshot["identity_hash"] = _pit_stable_hash(identity_payload)
    snapshot["snapshot_fingerprint"] = _pit_stable_hash(
        {
            "layer": snapshot["layer"],
            "signal_date": snapshot["signal_date"],
            "knowledge_cutoff": snapshot["knowledge_cutoff"],
            "source_snapshot_version": snapshot["source_snapshot_version"],
            "eligible_codes": sorted(snapshot["eligible_codes"]),
            "membership": snapshot["membership"],
            "identity_hash": snapshot["identity_hash"],
            "quality_status": snapshot["quality_status"],
            "coverage_diagnostics": dict(sorted(snapshot["coverage_diagnostics"].items())),
        }
    )


def _canonical_u1_manifest() -> dict:
    identity_mapping = {"A": "same-a", "B": "same-b"}
    u0_membership = [
        {"ts_code": "A", "included": True, "reason_code": "U0_ELIGIBLE", "identity_key": "same-a", "layer": "U0"},
        {"ts_code": "B", "included": True, "reason_code": "U0_ELIGIBLE", "identity_key": "same-b", "layer": "U0"},
    ]
    u0 = {
        "layer": "U0",
        "signal_date": "2020-01-03",
        "knowledge_cutoff": "2020-01-03T15:00:00",
        "source_snapshot_version": 1,
        "eligible_codes": ["A", "B"],
        "membership": u0_membership,
        "identity_mapping": identity_mapping,
        "identity_hash": _pit_stable_hash({"layer": "U0", "identity_mapping": sorted(identity_mapping.items())}),
        "snapshot_fingerprint": "0" * 64,
        "coverage_diagnostics": {
            "identity_validation_status": "VERIFIED",
            "pit_evidence_status": "VERIFIED",
            "research_execution_allowed": True,
            "promotion_allowed": True,
            "deployment_allowed": True,
        },
        "quality_status": "VERIFIED",
    }
    u1_membership = [
        {"ts_code": "A", "included": True, "reason_code": "U1_DERIVED_FROM_U0", "identity_key": "same-a", "layer": "U1"},
        {"ts_code": "B", "included": True, "reason_code": "U1_DERIVED_FROM_U0", "identity_key": "same-b", "layer": "U1"},
    ]
    u1 = {
        **u0,
        "layer": "U1",
        "eligible_codes": ["A", "B"],
        "membership": u1_membership,
        "identity_hash": "0" * 64,
        "coverage_diagnostics": {
            "u1_equals_u0": True,
            "identity_validation_status": "VERIFIED",
            "pit_evidence_status": "VERIFIED",
            "research_execution_allowed": True,
            "promotion_allowed": True,
            "deployment_allowed": True,
        },
    }
    _reseal_pit_snapshot(u0)
    _reseal_pit_snapshot(u1)
    return {
        "schema_version": "fund_rotation_pit_identity_v1",
        "snapshot_status": {"status": "available", "date_count": 1, "invalid_dates": []},
        "snapshots": [{"signal_date": "2020-01-03", "u0": u0, "u1": u1}],
    }


def _write_u1_manifest(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_start_shadow_a_accepts_canonical_u0_to_u1_derivation(tmp_path: Path) -> None:
    manifest = _write_u1_manifest(tmp_path / "u1.json", _canonical_u1_manifest())
    result = start_shadow_a(
        output_dir=tmp_path / "shadow",
        frozen_u1_manifest=manifest,
        report_path=tmp_path / "shadow_report.md",
    )
    assert result["qualification_status"] == "INSUFFICIENT_FORWARD_EVIDENCE"


def test_start_shadow_a_rejects_research_only_same_set_envelope(tmp_path: Path) -> None:
    payload = deepcopy(_canonical_u1_manifest())
    u0 = payload["snapshots"][0]["u0"]
    u1 = payload["snapshots"][0]["u1"]
    u0["quality_status"] = "RESEARCH_ONLY_UNVERIFIED_UNIVERSE"
    u1["quality_status"] = "RESEARCH_ONLY_UNVERIFIED_UNIVERSE"
    u1["coverage_diagnostics"].update(
        {
            "pit_evidence_status": "PARTIAL",
            "research_execution_allowed": True,
            "promotion_allowed": False,
            "deployment_allowed": False,
        }
    )
    _reseal_pit_snapshot(u0)
    _reseal_pit_snapshot(u1)
    manifest = _write_u1_manifest(tmp_path / "research_only.json", payload)

    with pytest.raises(ValueError, match="fail-closed"):
        start_shadow_a(
            output_dir=tmp_path / "research_only",
            frozen_u1_manifest=manifest,
            report_path=tmp_path / "research_only_report.md",
        )


@pytest.mark.parametrize(
    "variant",
    [
        "representative_only",
        "u1_equals_u0_false",
        "missing_eligible_code",
        "added_eligible_code",
        "identity_key_replaced",
        "wrong_membership_reason",
        "duplicate_identity",
        "missing_identity",
        "identity_hash_tampered",
        "snapshot_fingerprint_tampered",
    ],
)
def test_start_shadow_a_rejects_noncanonical_u1_variants(
    tmp_path: Path, variant: str
) -> None:
    payload = deepcopy(_canonical_u1_manifest())
    u0 = payload["snapshots"][0]["u0"]
    u1 = payload["snapshots"][0]["u1"]

    if variant == "representative_only":
        u0["eligible_codes"].append("C")
        u0["identity_mapping"]["C"] = "same-a"
        u0["membership"].append(
            {
                "ts_code": "C",
                "included": True,
                "reason_code": "U0_ELIGIBLE",
                "identity_key": "same-a",
                "layer": "U0",
            }
        )
        for item in u1["membership"]:
            if item["ts_code"] in {"A", "B"}:
                item["reason_code"] = "U1_REPRESENTATIVE"
        u1["identity_mapping"]["C"] = "same-a"
        u1["membership"].append(
            {
                "ts_code": "C",
                "included": False,
                "reason_code": "DUPLICATE_IDENTITY",
                "identity_key": "same-a",
                "layer": "U1",
            }
        )
    elif variant == "u1_equals_u0_false":
        u1["coverage_diagnostics"]["u1_equals_u0"] = False
    elif variant == "missing_eligible_code":
        u1["eligible_codes"] = ["A"]
        u1["membership"][1]["included"] = False
    elif variant == "added_eligible_code":
        u1["eligible_codes"].append("C")
        u1["identity_mapping"]["C"] = "same-c"
        u1["membership"].append(
            {
                "ts_code": "C",
                "included": True,
                "reason_code": "U1_DERIVED_FROM_U0",
                "identity_key": "same-c",
                "layer": "U1",
            }
        )
    elif variant == "identity_key_replaced":
        u1["identity_mapping"]["B"] = "replacement-b"
        u1["membership"][1]["identity_key"] = "replacement-b"
    elif variant == "wrong_membership_reason":
        u1["membership"][0]["reason_code"] = "WRONG_REASON"
    elif variant == "duplicate_identity":
        u1["identity_mapping"]["B"] = "same-a"
        u1["membership"][1]["identity_key"] = "same-a"
    elif variant == "missing_identity":
        u1["identity_mapping"]["B"] = None
        u1["membership"][1]["identity_key"] = None
    elif variant == "identity_hash_tampered":
        u1["identity_hash"] = "f" * 64
    elif variant == "snapshot_fingerprint_tampered":
        u1["snapshot_fingerprint"] = "e" * 64
    else:
        raise AssertionError(f"unknown variant: {variant}")

    if variant not in {"identity_hash_tampered", "snapshot_fingerprint_tampered"}:
        _reseal_pit_snapshot(u0)
        _reseal_pit_snapshot(u1)
    manifest = _write_u1_manifest(tmp_path / f"{variant}.json", payload)

    with pytest.raises(ValueError, match="fail-closed"):
        start_shadow_a(
            output_dir=tmp_path / variant,
            frozen_u1_manifest=manifest,
            report_path=tmp_path / f"{variant}_report.md",
        )


def test_start_shadow_a_manifest_keeps_started_separate_from_qualified(
    tmp_path: Path,
) -> None:
    result = start_shadow_a(
        output_dir=tmp_path,
        report_path=tmp_path / "shadow_a_report.md",
    )
    manifest = json.loads(
        (tmp_path / "shadow_manifest.json").read_text(encoding="utf-8")
    )
    assessment = json.loads(
        (tmp_path / "qualification_assessments.json").read_text(encoding="utf-8")
    )

    assert manifest["status"] == "STARTED"
    assert manifest["deployment_status"] == "RUNNING"
    assert manifest["qualification_status"] == "INSUFFICIENT_FORWARD_EVIDENCE"
    assert assessment["decision"] == "INELIGIBLE"
    assert result["qualified"] is False
