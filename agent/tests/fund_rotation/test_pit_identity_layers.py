"""TDD coverage for the PIT U0/U1 identity layers."""

from __future__ import annotations

import math
import json

import pytest

from backtest.fund_rotation.pit_universe import (
    ExclusionReasonCode,
    PITFundMaster,
    PITQueryMode,
    PITQualityStatus,
    UniversePolicy,
    UniverseResolver,
)
from experiments.fund_rotation_research_validity.pit_identity import (
    StrictTradabilityView,
    generate,
)
from tests.fund_rotation.test_pit_universe import DictCausalView


def _identity_row(ts_code: str, **overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "ts_code": ts_code,
        "valid_from": "2020-01-01",
        "valid_to": None,
        "known_from": "2020-01-02T00:00:00",
        "revision_id": "r1",
        "source_id": "identity-fixture",
        "source_record_id": f"src-{ts_code}-r1",
        "source_published_at": None,
        "ingested_at": "2020-01-02T01:00:00",
        "list_date": "2020-01-01",
        "delist_date": "2099-12-31",
        "fund_status": "ACTIVE",
        "name": f"{ts_code} ETF",
        "fund_type": "ETF",
        "asset_class": "equity",
        "tracking_index": "000300.SH",
        "underlying_index": "000300.SH",
        "region": "CN",
        "currency": "CNY",
        "leveraged_or_inverse": False,
        "share_class_or_feeder_relationship": "standalone",
        "exchange": "SH",
        "revision_chain_verified": True,
        "independent_source_verified": True,
    }
    row.update(overrides)
    return row


def _resolver(rows: list[dict[str, object]]) -> UniverseResolver:
    return UniverseResolver(PITFundMaster(rows))


def test_identity_layers_include_known_before_cutoff_and_exclude_future_known() -> None:
    resolver = _resolver(
        [
            _identity_row("known.SH", underlying_index="000301.SH", tracking_index="000301.SH"),
            _identity_row(
                "boundary.SH",
                known_from="2020-03-01T15:00:00",
                underlying_index="000300.SH",
                tracking_index="000300.SH",
            ),
            _identity_row("future.SH", known_from="2020-03-01T15:00:01"),
            _identity_row("not_yet.SH", list_date="2020-03-02"),
            _identity_row("delisted.SH", delist_date="2020-03-01"),
        ]
    )

    layers = resolver.resolve_identity_layers(
        signal_date="2020-03-01",
        knowledge_cutoff="2020-03-01T15:00:00",
        strategy_policy=UniversePolicy(),
        causal_view=DictCausalView(),
        snapshot_version=7,
        mode=PITQueryMode.AS_WAS_KNOWN,
    )

    assert layers.u0.eligible_codes == ("boundary.SH", "known.SH")
    assert layers.u1.eligible_codes == layers.u0.eligible_codes
    assert layers.u1.coverage_diagnostics["u1_equals_u0"] is True
    u1_eligible_membership = [
        item for item in layers.u1.membership if item.ts_code in layers.u0.eligible_codes
    ]
    assert {item.ts_code for item in u1_eligible_membership} == set(layers.u0.eligible_codes)
    assert all(
        item.included and item.reason_code == "U1_DERIVED_FROM_U0"
        for item in layers.u1.membership
        if item.ts_code in layers.u0.eligible_codes
    )
    reasons = {item.ts_code: item.reason_code for item in layers.u0.membership}
    assert reasons["future.SH"] == ExclusionReasonCode.KNOWN_AFTER_CUTOFF.value
    assert reasons["not_yet.SH"] == ExclusionReasonCode.NOT_YET_LISTED.value
    assert reasons["delisted.SH"] == ExclusionReasonCode.DELISTED.value
    assert layers.u0.coverage_diagnostics["known_before_cutoff_count"] == 2


def test_u1_membership_preserves_u0_exclusion_reasons() -> None:
    layers = _resolver(
        [
            _identity_row("known.SH"),
            _identity_row("future.SH", known_from="2020-03-01T15:00:01"),
        ]
    ).resolve_identity_layers(
        signal_date="2020-03-01",
        knowledge_cutoff="2020-03-01T15:00:00",
        strategy_policy=UniversePolicy(),
        causal_view=DictCausalView(),
        snapshot_version=7,
        mode=PITQueryMode.AS_WAS_KNOWN,
    )

    reasons = {item.ts_code: item.reason_code for item in layers.u1.membership}
    assert reasons["future.SH"] == ExclusionReasonCode.KNOWN_AFTER_CUTOFF.value


def test_u1_preserves_duplicate_identity_members_and_exposes_duplicate() -> None:
    rows = [
        _identity_row("510300.SH"),
        _identity_row("510301.SH"),
    ]

    layers = _resolver(rows).resolve_identity_layers(
        signal_date="2020-03-01",
        knowledge_cutoff="2020-03-01T15:00:00",
        strategy_policy=UniversePolicy(),
        causal_view=DictCausalView(),
        snapshot_version=7,
        mode=PITQueryMode.AS_WAS_KNOWN,
    )

    assert layers.u1.eligible_codes == layers.u0.eligible_codes
    assert layers.u1.coverage_diagnostics["u1_equals_u0"] is True
    assert layers.u1.coverage_diagnostics["identity_validation_status"] == "CONFLICT"
    assert layers.u1.coverage_diagnostics["research_execution_allowed"] is True
    assert layers.u1.coverage_diagnostics["promotion_allowed"] is False
    assert layers.u1.coverage_diagnostics["deployment_allowed"] is False
    assert {
        item.ts_code for item in layers.u1.membership if item.included
    } == set(layers.u0.eligible_codes)
    assert layers.u1.coverage_diagnostics["duplicate_identity_count"] == 1
    assert layers.u1.coverage_diagnostics["duplicate_identity_ratio"] == 0.5


def test_u1_missing_identity_allows_research_only_execution() -> None:
    layers = _resolver(
        [_identity_row("missing.SH", underlying_index=None, tracking_index=None)]
    ).resolve_identity_layers(
        signal_date="2020-03-01",
        knowledge_cutoff="2020-03-01T15:00:00",
        strategy_policy=UniversePolicy(),
        causal_view=DictCausalView(),
        snapshot_version=7,
        mode=PITQueryMode.AS_WAS_KNOWN,
    )

    assert layers.u0.eligible_codes == ("missing.SH",)
    assert layers.u1.eligible_codes == layers.u0.eligible_codes
    assert layers.u1.coverage_diagnostics["u1_equals_u0"] is True
    assert layers.u1.coverage_diagnostics["identity_validation_status"] == "UNAVAILABLE"
    assert layers.u1.coverage_diagnostics["pit_evidence_status"] in {"UNAVAILABLE", "PARTIAL"}
    assert layers.u1.coverage_diagnostics["research_execution_allowed"] is True
    assert layers.u1.coverage_diagnostics["promotion_allowed"] is False
    assert layers.u1.coverage_diagnostics["deployment_allowed"] is False
    assert layers.u1.quality_status is PITQualityStatus.RESEARCH_ONLY_UNVERIFIED_UNIVERSE
    assert layers.u1.membership[0].included is True
    assert layers.u1.identity_mapping["missing.SH"] is None
    assert layers.u1.coverage_diagnostics["missing_identity_count"] == 1


def test_identity_and_snapshot_hashes_are_stable_under_input_order() -> None:
    rows = [_identity_row("b.SH"), _identity_row("a.SH")]
    kwargs = dict(
        signal_date="2020-03-01",
        knowledge_cutoff="2020-03-01T15:00:00",
        strategy_policy=UniversePolicy(),
        causal_view=DictCausalView(),
        snapshot_version=7,
        mode=PITQueryMode.AS_WAS_KNOWN,
    )

    first = _resolver(rows).resolve_identity_layers(**kwargs)
    second = _resolver(list(reversed(rows))).resolve_identity_layers(**kwargs)

    assert first.u0.identity_hash == second.u0.identity_hash
    assert first.u0.snapshot_fingerprint == second.u0.snapshot_fingerprint
    assert first.u1.identity_hash == second.u1.identity_hash
    assert first.u1.snapshot_fingerprint == second.u1.snapshot_fingerprint


@pytest.mark.parametrize(
    "field,value",
    [
        ("revision_chain_verified", math.nan),
        ("independent_source_verified", "false"),
    ],
)
def test_invalid_evidence_flags_never_claim_verified(
    field: str,
    value: object,
) -> None:
    resolution = _resolver([_identity_row("bad-evidence.SH", **{field: value})]).resolve(
        signal_date="2020-03-01",
        knowledge_cutoff="2020-03-01T15:00:00",
        strategy_policy=UniversePolicy(),
        causal_view=DictCausalView(),
        snapshot_version=7,
        mode=PITQueryMode.AS_WAS_KNOWN,
    )

    assert resolution.quality_status is not PITQualityStatus.VERIFIED


def test_missing_decision_date_tradability_is_fail_closed() -> None:
    resolution = _resolver([_identity_row("unknown-tradability.SH")]).resolve(
        signal_date="2020-03-01",
        knowledge_cutoff="2020-03-01T15:00:00",
        strategy_policy=UniversePolicy(),
        causal_view=None,
        snapshot_version=7,
        mode=PITQueryMode.AS_WAS_KNOWN,
    )

    assert resolution.eligible == ()
    assert resolution.quality_status is PITQualityStatus.PIT_INVALID
    assert resolution.tradable_exclusions[0].details == "TRADABILITY_UNAVAILABLE"


def test_invalid_query_keeps_u1_fail_closed() -> None:
    layers = _resolver(
        [_identity_row("mixed-timezone.SH", known_from="2020-03-01T15:00:00Z")]
    ).resolve_identity_layers(
        signal_date="2020-03-01",
        knowledge_cutoff="2020-03-01T15:00:00",
        strategy_policy=UniversePolicy(),
        causal_view=DictCausalView(),
        snapshot_version=7,
        mode=PITQueryMode.AS_WAS_KNOWN,
    )

    assert layers.resolution.eligible == ()
    assert layers.resolution.quality_status is PITQualityStatus.PIT_INVALID
    assert layers.u1.quality_status is PITQualityStatus.PIT_INVALID
    assert layers.u1.coverage_diagnostics["research_execution_allowed"] is False


def test_distinct_revisions_at_same_knowledge_time_are_ambiguous() -> None:
    rows = [
        _identity_row("same-time.SH", revision_id="r10"),
        _identity_row("same-time.SH", revision_id="r2"),
    ]

    resolution = _resolver(rows).resolve(
        signal_date="2020-03-01",
        knowledge_cutoff="2020-03-01T15:00:00",
        strategy_policy=UniversePolicy(),
        causal_view=DictCausalView(),
        snapshot_version=7,
        mode=PITQueryMode.AS_WAS_KNOWN,
    )

    assert resolution.eligible == ()
    assert resolution.quality_status is PITQualityStatus.PIT_INVALID
    assert resolution.master_exclusions[0].reason_code is ExclusionReasonCode.OVERLAPPING_VALID_RANGE


def test_mixed_missing_identity_keeps_u0_set_for_research_only() -> None:
    layers = _resolver(
        [
            _identity_row("valid.SH"),
            _identity_row("missing.SH", underlying_index=None, tracking_index=None),
        ]
    ).resolve_identity_layers(
        signal_date="2020-03-01",
        knowledge_cutoff="2020-03-01T15:00:00",
        strategy_policy=UniversePolicy(),
        causal_view=DictCausalView(),
        snapshot_version=7,
        mode=PITQueryMode.AS_WAS_KNOWN,
    )

    assert layers.u1.eligible_codes == layers.u0.eligible_codes
    assert layers.u1.coverage_diagnostics["u1_equals_u0"] is True
    assert layers.u1.coverage_diagnostics["identity_validation_status"] == "PARTIAL"
    assert layers.u1.coverage_diagnostics["research_execution_allowed"] is True
    assert layers.u1.coverage_diagnostics["promotion_allowed"] is False
    assert layers.u1.coverage_diagnostics["deployment_allowed"] is False
    assert layers.u1.quality_status is PITQualityStatus.RESEARCH_ONLY_UNVERIFIED_UNIVERSE
    assert {
        item.ts_code for item in layers.u1.membership if item.included
    } == set(layers.u0.eligible_codes)
    assert layers.u1.identity_mapping["missing.SH"] is None


def test_tradability_conflict_is_order_independent_and_fail_closed() -> None:
    rows = [
        {"signal_date": "2020-03-01", "ts_code": "A", "tradable": True},
        {"signal_date": "2020-03-01", "ts_code": "A", "tradable": False},
    ]
    first = StrictTradabilityView(rows)
    second = StrictTradabilityView(list(reversed(rows)))

    assert first.tradable_status("A", "2020-03-01") == (
        False,
        "TRADABILITY_CONFLICT",
    )
    assert second.tradable_status("A", "2020-03-01") == first.tradable_status(
        "A", "2020-03-01"
    )


class _InvalidTradabilityView:
    def __init__(self, value: object):
        self.value = value

    def tradable_status(self, ts_code: str, signal_date: str) -> object:
        return self.value


class _ExplodingTradabilityView:
    def tradable_status(self, ts_code: str, signal_date: str) -> object:
        raise RuntimeError("data source unavailable")


@pytest.mark.parametrize("value", [None, "false", math.nan])
def test_invalid_tradability_values_are_fail_closed(value: object) -> None:
    resolution = _resolver([_identity_row("invalid-tradability.SH")]).resolve(
        signal_date="2020-03-01",
        knowledge_cutoff="2020-03-01T15:00:00",
        strategy_policy=UniversePolicy(),
        causal_view=_InvalidTradabilityView(value),
        snapshot_version=7,
        mode=PITQueryMode.AS_WAS_KNOWN,
    )

    assert resolution.eligible == ()
    assert resolution.quality_status is PITQualityStatus.PIT_INVALID
    assert resolution.tradable_exclusions[0].details == "TRADABILITY_UNAVAILABLE"


def test_tradability_runtime_errors_are_fail_closed() -> None:
    resolution = _resolver([_identity_row("runtime-error.SH")]).resolve(
        signal_date="2020-03-01",
        knowledge_cutoff="2020-03-01T15:00:00",
        strategy_policy=UniversePolicy(),
        causal_view=_ExplodingTradabilityView(),
        snapshot_version=7,
        mode=PITQueryMode.AS_WAS_KNOWN,
    )

    assert resolution.eligible == ()
    assert resolution.quality_status is PITQualityStatus.PIT_INVALID
    assert resolution.tradable_exclusions[0].details == "TRADABILITY_UNAVAILABLE"


@pytest.mark.parametrize("value", [(True, "ok", "unexpected"), (True, 123)])
def test_malformed_tradability_tuples_are_fail_closed(value: object) -> None:
    resolution = _resolver([_identity_row("malformed-tradability.SH")]).resolve(
        signal_date="2020-03-01",
        knowledge_cutoff="2020-03-01T15:00:00",
        strategy_policy=UniversePolicy(),
        causal_view=_InvalidTradabilityView(value),
        snapshot_version=7,
        mode=PITQueryMode.AS_WAS_KNOWN,
    )

    assert resolution.eligible == ()
    assert resolution.quality_status is PITQualityStatus.PIT_INVALID
    assert resolution.tradable_exclusions[0].details == "TRADABILITY_UNAVAILABLE"


@pytest.mark.parametrize(
    "field", ["valid_from", "valid_to", "known_from", "list_date", "delist_date"]
)
def test_malformed_pit_dates_fail_closed_with_stable_reason(field: str) -> None:
    resolution = _resolver(
        [_identity_row("malformed-date.SH", **{field: "not-a-date"})]
    ).resolve(
        signal_date="2020-03-01",
        knowledge_cutoff="2020-03-01T15:00:00",
        strategy_policy=UniversePolicy(),
        causal_view=DictCausalView(),
        snapshot_version=7,
        mode=PITQueryMode.AS_WAS_KNOWN,
    )

    assert resolution.eligible == ()
    assert resolution.quality_status is PITQualityStatus.PIT_INVALID
    assert resolution.master_exclusions[0].reason_code is ExclusionReasonCode.INVALID_LIFECYCLE_DATES


def test_missing_valid_from_is_open_lower_bound_and_research_only() -> None:
    row = _identity_row("missing-valid-from.SH")
    row["valid_from"] = None

    layers = _resolver([row]).resolve_identity_layers(
        signal_date="2020-03-01",
        knowledge_cutoff="2020-03-01T15:00:00",
        strategy_policy=UniversePolicy(),
        causal_view=DictCausalView(),
        snapshot_version=7,
        mode=PITQueryMode.AS_WAS_KNOWN,
    )

    assert layers.u0.eligible_codes == ("missing-valid-from.SH",)
    assert layers.u0.identity_mapping == layers.u1.identity_mapping
    assert layers.resolution.eligible[0].valid_from is None
    assert layers.u1.eligible_codes == layers.u0.eligible_codes
    assert layers.u1.coverage_diagnostics["pit_evidence_status"] == "PARTIAL"
    assert layers.u1.coverage_diagnostics["research_execution_allowed"] is True
    assert layers.u1.coverage_diagnostics["promotion_allowed"] is False
    assert layers.u1.coverage_diagnostics["deployment_allowed"] is False
    assert layers.u1.quality_status is PITQualityStatus.RESEARCH_ONLY_UNVERIFIED_UNIVERSE


def test_absent_valid_from_column_is_open_lower_bound_and_research_only() -> None:
    row = _identity_row("absent-valid-from.SH")
    row.pop("valid_from")

    layers = _resolver([row]).resolve_identity_layers(
        signal_date="2020-03-01",
        knowledge_cutoff="2020-03-01T15:00:00",
        strategy_policy=UniversePolicy(),
        causal_view=DictCausalView(),
        snapshot_version=7,
        mode=PITQueryMode.AS_WAS_KNOWN,
    )

    assert layers.u0.eligible_codes == ("absent-valid-from.SH",)
    assert layers.resolution.eligible[0].valid_from is None
    assert layers.u1.eligible_codes == layers.u0.eligible_codes
    assert layers.u1.identity_mapping == layers.u0.identity_mapping
    assert layers.u1.coverage_diagnostics["pit_evidence_status"] != "VERIFIED"
    assert layers.u1.coverage_diagnostics["research_execution_allowed"] is True
    assert layers.u1.coverage_diagnostics["promotion_allowed"] is False
    assert layers.u1.coverage_diagnostics["deployment_allowed"] is False
    assert layers.u1.quality_status is PITQualityStatus.RESEARCH_ONLY_UNVERIFIED_UNIVERSE


def test_missing_known_from_remains_research_eligible_without_fabrication() -> None:
    row = _identity_row("missing-known-from.SH")
    row["known_from"] = None

    layers = _resolver([row]).resolve_identity_layers(
        signal_date="2020-03-01",
        knowledge_cutoff="2020-03-01T15:00:00",
        strategy_policy=UniversePolicy(),
        causal_view=DictCausalView(),
        snapshot_version=7,
        mode=PITQueryMode.AS_WAS_KNOWN,
    )

    assert layers.resolution.eligible[0].known_from is None
    assert layers.u0.eligible_codes == ("missing-known-from.SH",)
    assert layers.u1.eligible_codes == layers.u0.eligible_codes
    assert layers.u1.coverage_diagnostics["pit_evidence_status"] != "VERIFIED"
    assert layers.u1.coverage_diagnostics["future_known_excluded_count"] == 0
    assert layers.u1.coverage_diagnostics["research_execution_allowed"] is True
    assert layers.u1.coverage_diagnostics["promotion_allowed"] is False
    assert layers.u1.coverage_diagnostics["deployment_allowed"] is False
    assert layers.u1.quality_status is PITQualityStatus.RESEARCH_ONLY_UNVERIFIED_UNIVERSE


def test_absent_known_from_column_remains_research_eligible_without_fabrication() -> None:
    row = _identity_row("absent-known-from.SH")
    row.pop("known_from")

    layers = _resolver([row]).resolve_identity_layers(
        signal_date="2020-03-01",
        knowledge_cutoff="2020-03-01T15:00:00",
        strategy_policy=UniversePolicy(),
        causal_view=DictCausalView(),
        snapshot_version=7,
        mode=PITQueryMode.AS_WAS_KNOWN,
    )

    assert layers.resolution.eligible[0].known_from is None
    assert layers.u0.eligible_codes == ("absent-known-from.SH",)
    assert layers.u1.eligible_codes == layers.u0.eligible_codes
    assert layers.u1.coverage_diagnostics["pit_evidence_status"] != "VERIFIED"
    assert layers.u1.coverage_diagnostics["future_known_excluded_count"] == 0
    assert layers.u1.coverage_diagnostics["research_execution_allowed"] is True
    assert layers.u1.coverage_diagnostics["promotion_allowed"] is False
    assert layers.u1.coverage_diagnostics["deployment_allowed"] is False
    assert layers.u1.quality_status is PITQualityStatus.RESEARCH_ONLY_UNVERIFIED_UNIVERSE


def test_missing_list_date_cannot_be_reported_as_verified() -> None:
    row = _identity_row("missing-list-date.SH")
    row.pop("list_date")

    resolution = _resolver([row]).resolve(
        signal_date="2020-03-01",
        knowledge_cutoff="2020-03-01T15:00:00",
        strategy_policy=UniversePolicy(),
        causal_view=DictCausalView(),
        snapshot_version=7,
        mode=PITQueryMode.AS_WAS_KNOWN,
    )

    assert resolution.eligible == ()
    assert resolution.quality_status is PITQualityStatus.PIT_INVALID


def test_reproducibility_fingerprint_includes_quality_status() -> None:
    kwargs = dict(
        signal_date="2020-03-01",
        knowledge_cutoff="2020-03-01T15:00:00",
        strategy_policy=UniversePolicy(),
        causal_view=DictCausalView(),
        snapshot_version=7,
        mode=PITQueryMode.AS_WAS_KNOWN,
        cross_source_audit={
            "source_a_codes": {"fingerprint.SH"},
            "source_b_codes": {"fingerprint.SH"},
        },
    )
    verified = _resolver([_identity_row("fingerprint.SH")]).resolve_identity_layers(**kwargs)
    unverified = _resolver(
        [_identity_row("fingerprint.SH", revision_chain_verified=False)]
    ).resolve_identity_layers(**kwargs)

    assert verified.u0.quality_status is PITQualityStatus.VERIFIED
    assert unverified.u0.quality_status is PITQualityStatus.PIT_UNVERIFIED
    assert verified.u0.snapshot_fingerprint != unverified.u0.snapshot_fingerprint


def test_boolean_and_numeric_leverage_values_share_identity() -> None:
    layers = _resolver(
        [
            _identity_row("leverage-bool.SH", leveraged_or_inverse=False),
            _identity_row("leverage-number.SH", leveraged_or_inverse=0),
        ]
    ).resolve_identity_layers(
        signal_date="2020-03-01",
        knowledge_cutoff="2020-03-01T15:00:00",
        strategy_policy=UniversePolicy(),
        causal_view=DictCausalView(),
        snapshot_version=7,
        mode=PITQueryMode.AS_WAS_KNOWN,
    )

    assert layers.u1.eligible_codes == layers.u0.eligible_codes
    assert layers.u1.coverage_diagnostics["u1_equals_u0"] is True
    assert layers.u1.coverage_diagnostics["identity_validation_status"] == "CONFLICT"
    assert layers.u1.coverage_diagnostics["research_execution_allowed"] is True
    assert layers.u1.coverage_diagnostics["promotion_allowed"] is False
    assert layers.u1.coverage_diagnostics["deployment_allowed"] is False
    assert layers.u1.quality_status is PITQualityStatus.RESEARCH_ONLY_UNVERIFIED_UNIVERSE
    assert {
        item.ts_code for item in layers.u1.membership if item.included
    } == set(layers.u0.eligible_codes)


def test_string_boolean_leverage_values_share_identity_and_invalid_numbers_do_not() -> None:
    layers = _resolver(
        [
            _identity_row("leverage-string-zero.SH", leveraged_or_inverse="0"),
            _identity_row("leverage-string-false.SH", leveraged_or_inverse="false"),
        ]
    ).resolve_identity_layers(
        signal_date="2020-03-01",
        knowledge_cutoff="2020-03-01T15:00:00",
        strategy_policy=UniversePolicy(),
        causal_view=DictCausalView(),
        snapshot_version=7,
        mode=PITQueryMode.AS_WAS_KNOWN,
    )
    invalid = _resolver(
        [_identity_row("leverage-string-two.SH", leveraged_or_inverse="2")]
    ).resolve_identity_layers(
        signal_date="2020-03-01",
        knowledge_cutoff="2020-03-01T15:00:00",
        strategy_policy=UniversePolicy(),
        causal_view=DictCausalView(),
        snapshot_version=7,
        mode=PITQueryMode.AS_WAS_KNOWN,
    )

    assert layers.u1.eligible_codes == layers.u0.eligible_codes
    assert layers.u1.coverage_diagnostics["u1_equals_u0"] is True
    assert layers.u1.coverage_diagnostics["identity_validation_status"] == "CONFLICT"
    assert layers.u1.coverage_diagnostics["research_execution_allowed"] is True
    assert layers.u1.coverage_diagnostics["promotion_allowed"] is False
    assert layers.u1.coverage_diagnostics["deployment_allowed"] is False
    assert layers.u1.quality_status is PITQualityStatus.RESEARCH_ONLY_UNVERIFIED_UNIVERSE
    assert {
        item.ts_code for item in layers.u1.membership if item.included
    } == set(layers.u0.eligible_codes)
    assert invalid.u1.eligible_codes == invalid.u0.eligible_codes
    assert invalid.u1.coverage_diagnostics["u1_equals_u0"] is True
    assert invalid.u1.coverage_diagnostics["identity_validation_status"] == "UNAVAILABLE"
    assert invalid.u1.coverage_diagnostics["research_execution_allowed"] is True
    assert invalid.u1.coverage_diagnostics["promotion_allowed"] is False
    assert invalid.u1.coverage_diagnostics["deployment_allowed"] is False
    assert invalid.u1.quality_status is PITQualityStatus.RESEARCH_ONLY_UNVERIFIED_UNIVERSE
    assert {
        item.ts_code for item in invalid.u1.membership if item.included
    } == set(invalid.u0.eligible_codes)


def test_snapshot_fingerprint_covers_final_research_diagnostics() -> None:
    resolution = _resolver([_identity_row("coverage.SH")]).resolve_identity_layers(
        signal_date="2020-03-01",
        knowledge_cutoff="2020-03-01T15:00:00",
        strategy_policy=UniversePolicy(),
        causal_view=DictCausalView(),
        snapshot_version=7,
        mode=PITQueryMode.AS_WAS_KNOWN,
    )

    assert resolution.u0.coverage_diagnostics["momentum_coverage"] == "unavailable"
    assert resolution.u0.coverage_diagnostics["effective_cluster_count"] == "unavailable"


def test_tradability_dates_are_normalized_before_lookup() -> None:
    view = StrictTradabilityView(
        [{"signal_date": "20200301", "ts_code": "DATE.SH", "tradable": True}]
    )

    assert view.tradable_status("DATE.SH", "2020-03-01") == (True, "")


def test_reversed_valid_range_fails_closed_with_stable_reason() -> None:
    resolution = _resolver(
        [_identity_row("reversed-range.SH", valid_from="2020-06-01", valid_to="2020-01-01")]
    ).resolve(
        signal_date="2020-03-01",
        knowledge_cutoff="2020-03-01T15:00:00",
        strategy_policy=UniversePolicy(),
        causal_view=DictCausalView(),
        snapshot_version=7,
        mode=PITQueryMode.AS_WAS_KNOWN,
    )

    assert resolution.eligible == ()
    assert resolution.quality_status is PITQualityStatus.PIT_INVALID
    assert resolution.master_exclusions[0].reason_code is ExclusionReasonCode.INVALID_LIFECYCLE_DATES


def test_unavailable_tradability_takes_precedence_over_research_only_quality() -> None:
    resolution = _resolver(
        [
            _identity_row("missing-delist.SH", delist_date=None),
            _identity_row("missing-tradability.SH"),
        ]
    ).resolve(
        signal_date="2020-03-01",
        knowledge_cutoff="2020-03-01T15:00:00",
        strategy_policy=UniversePolicy(),
        causal_view=None,
        snapshot_version=7,
        mode=PITQueryMode.AS_WAS_KNOWN,
    )

    assert resolution.quality_status is PITQualityStatus.PIT_INVALID


def test_experiment_outputs_are_immutable_after_first_write(tmp_path) -> None:
    output_dir = tmp_path / "batch_1"
    report_path = tmp_path / "batch_1_report.md"
    kwargs = dict(
        master_path=None,
        dates_path=None,
        tradability_path=None,
        output_dir=output_dir,
        report_path=report_path,
        cutoff_time="15:00:00",
    )

    generate(snapshot_version=1, **kwargs)
    generate(snapshot_version=1, **kwargs)

    with pytest.raises(FileExistsError):
        generate(snapshot_version=2, **kwargs)

    bad_kwargs = dict(kwargs)
    bad_kwargs["cutoff_time"] = "09:30:00"
    with pytest.raises(ValueError, match="15:00:00"):
        generate(snapshot_version=3, **bad_kwargs)


def test_generated_snapshot_serializes_u1_same_set_evidence(tmp_path) -> None:
    master_path = tmp_path / "master.json"
    dates_path = tmp_path / "dates.json"
    tradability_path = tmp_path / "tradability.json"
    output_dir = tmp_path / "batch_1"
    report_path = tmp_path / "report.md"
    master_path.write_text(
        json.dumps([_identity_row("valid.SH")]),
        encoding="utf-8",
    )
    dates_path.write_text(json.dumps(["2020-03-01"]), encoding="utf-8")
    tradability_path.write_text(
        json.dumps([{"signal_date": "2020-03-01", "ts_code": "valid.SH", "tradable": True}]),
        encoding="utf-8",
    )

    generate(
        master_path=master_path,
        dates_path=dates_path,
        tradability_path=tradability_path,
        output_dir=output_dir,
        report_path=report_path,
        snapshot_version=7,
        cutoff_time="15:00:00",
    )

    persisted_manifest = json.loads(
        (output_dir / "manifest.json").read_text(encoding="utf-8")
    )
    assert persisted_manifest["snapshot_status"]["status"] == "available_research_only"
    assert persisted_manifest["snapshot_status"]["date_count"] == 1
    assert persisted_manifest["snapshots"]
    for snapshot in persisted_manifest["snapshots"]:
        diagnostics = snapshot["u1"]["coverage_diagnostics"]
        assert diagnostics["u1_equals_u0"] is True
        assert diagnostics["identity_validation_status"] == "VERIFIED"
        assert diagnostics["pit_evidence_status"] == "PARTIAL"
        assert diagnostics["research_execution_allowed"] is True
        assert diagnostics["promotion_allowed"] is False
        assert diagnostics["deployment_allowed"] is False
        assert set(snapshot["u1"]["eligible_codes"]) == set(
            snapshot["u0"]["eligible_codes"]
        )
        assert snapshot["u0"]["quality_status"] != "PIT_INVALID"
        assert snapshot["u1"]["quality_status"] == "RESEARCH_ONLY_UNVERIFIED_UNIVERSE"
        assert snapshot["u1"]["membership"][0]["included"] is True
    assert "U1 从冻结 U0 派生并保持相同 eligible 集合" in report_path.read_text(
        encoding="utf-8"
    )
    assert (
        "身份缺失或冲突时仍保留相同 `eligible_codes` 作为研究对照，相关成员保持 `included`，"
        "U1 标记为 `RESEARCH_ONLY_UNVERIFIED_UNIVERSE`，允许研究运行但禁止 promotion/deployment。"
        "PIT 可选证据缺失或部分时同样允许研究运行但禁止 promotion/deployment。"
    ) in report_path.read_text(encoding="utf-8")


def test_manifest_top_level_status_reflects_invalid_snapshot_date(tmp_path) -> None:
    master_path = tmp_path / "master.json"
    dates_path = tmp_path / "dates.json"
    tradability_path = tmp_path / "tradability.json"
    output_dir = tmp_path / "batch_1"
    report_path = tmp_path / "report.md"
    invalid_row = _identity_row("invalid.SH")
    invalid_row.pop("list_date")
    master_path.write_text(
        json.dumps([invalid_row]),
        encoding="utf-8",
    )
    dates_path.write_text(json.dumps(["2020-03-01"]), encoding="utf-8")
    tradability_path.write_text(
        json.dumps([{"signal_date": "2020-03-01", "ts_code": "invalid.SH", "tradable": True}]),
        encoding="utf-8",
    )

    manifest = generate(
        master_path=master_path,
        dates_path=dates_path,
        tradability_path=tradability_path,
        output_dir=output_dir,
        report_path=report_path,
        snapshot_version=7,
        cutoff_time="15:00:00",
    )

    assert manifest["snapshot_status"]["status"] == "invalid"
    assert manifest["snapshot_status"]["invalid_dates"] == ["2020-03-01"]
