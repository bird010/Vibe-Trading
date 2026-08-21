"""Focused tests for the R11 champion-validation Universe Assurance gate."""

from __future__ import annotations

from backtest.fund_rotation.champion_validation.universe_assurance import assure_universe
from backtest.fund_rotation.pit_universe import (
    ExclusionLayer,
    ExclusionReasonCode,
    FundInstrumentVersion,
    PITQueryMode,
    PITQualityStatus,
    UniverseExclusion,
    UniverseResolution,
)


def _instrument(
    code: str,
    *,
    known_from: str | None = "2020-01-02",
    list_date: str | None = "2020-01-01",
    delist_date: str | None = "2099-12-31",
) -> FundInstrumentVersion:
    return FundInstrumentVersion(
        ts_code=code,
        valid_from="2020-01-01",
        valid_to=None,
        known_from=known_from,
        revision_id="r1",
        source_id="fixture-a",
        source_record_id=f"{code}-r1",
        source_published_at=None,
        ingested_at=None,
        list_date=list_date,
        delist_date=delist_date,
        fund_status="ACTIVE",
        name=code,
        fund_type="ETF",
        asset_class="equity",
        tracking_index="000300.SH",
        exchange="SH",
        quality_status=PITQualityStatus.VERIFIED,
    )


def _resolution(**overrides: object) -> UniverseResolution:
    values: dict[str, object] = {
        "eligible": (_instrument("510300.SH"),),
        "master_exclusions": (),
        "strategy_exclusions": (),
        "tradable_exclusions": (),
        "source_snapshot_version": 7,
        "signal_date": "20200301",
        "knowledge_cutoff": "20200301T150000",
        "query_mode": PITQueryMode.AS_WAS_KNOWN,
        "audit_metrics": {
            "overlapping_valid_range_count": 0,
            "overlapping_knowledge_range_count": 0,
            "knowledge_time_unverified_count": 0,
            "cross_source_missing_count": 0,
            "status_conflict_count": 0,
            "date_conflict_count": 0,
            "classification_conflict_count": 0,
        },
        "quality_status": PITQualityStatus.VERIFIED,
        "cross_source_audit": {
            "reconciliation_status": "PASSED",
            "only_in_a": frozenset(),
            "only_in_b": frozenset(),
            "status_conflicts": frozenset(),
            "date_conflicts": frozenset(),
            "classification_conflicts": frozenset(),
        },
    }
    values.update(overrides)
    return UniverseResolution(**values)


def test_verified_pit_resolution_passes_every_zero_count_gate() -> None:
    result = assure_universe([_resolution()])

    assert result.status == PITQualityStatus.VERIFIED
    assert result.passed is True
    assert result.gate_counts["unresolved_cross_source_conflicts"] == 0
    assert result.gate_counts["included_without_knowledge_time"] == 0


def test_conflicts_and_lifecycle_leakage_are_reported_without_weakening_the_gate() -> None:
    result = assure_universe(
        [
            _resolution(
                master_exclusions=(
                    UniverseExclusion(ExclusionLayer.FUND_MASTER, "INFO.SH", ExclusionReasonCode.MISSING_KNOWN_FROM),
                ),
                eligible=(
                    _instrument("LEAK.SH", list_date="2020-04-01"),
                    _instrument("OLD.SH", delist_date="2020-02-29"),
                ),
                audit_metrics={
                    "overlapping_valid_range_count": 1,
                    "overlapping_knowledge_range_count": 1,
                    "knowledge_time_unverified_count": 0,
                    "cross_source_missing_count": 1,
                    "status_conflict_count": 1,
                    "date_conflict_count": 0,
                    "classification_conflict_count": 0,
                },
                quality_status=PITQualityStatus.PIT_INVALID,
                cross_source_audit={
                    "reconciliation_status": "CONFLICTS",
                    "only_in_a": frozenset({"A.SH"}),
                    "only_in_b": frozenset(),
                    "status_conflicts": frozenset({"B.SH"}),
                    "date_conflicts": frozenset(),
                    "classification_conflicts": frozenset(),
                },
            )
        ]
    )

    assert result.passed is False
    assert result.status == "INCONCLUSIVE_UNIVERSE"
    assert result.gate_counts["overlapping_valid_periods"] == 1
    assert result.gate_counts["unresolved_cross_source_conflicts"] == 2
    assert "NOT_YET_LISTED" in result.reason_codes
    assert "DELISTED" in result.reason_codes


def test_missing_knowledge_time_on_an_included_instrument_is_not_verified() -> None:
    result = assure_universe(
        [_resolution(eligible=(_instrument("MISSING.SH", known_from=None),))]
    )

    assert result.passed is False
    assert result.status == "INCONCLUSIVE_UNIVERSE"
    assert result.gate_counts["included_without_knowledge_time"] == 1


def test_sparse_universe_evidence_is_not_verified() -> None:
    result = assure_universe([_resolution(eligible=())])

    assert result.passed is False
    assert result.status == "INCONCLUSIVE_UNIVERSE"
    assert "SPARSE_UNIVERSE_EVIDENCE" in result.reason_codes


def test_missing_snapshot_version_is_not_verified() -> None:
    result = assure_universe([_resolution(source_snapshot_version=None)])

    assert result.passed is False
    assert "MISSING_UNIVERSE_SNAPSHOT_VERSION" in result.reason_codes


def test_negative_audit_count_is_not_verified_as_zero() -> None:
    metrics = dict(_resolution().audit_metrics)
    metrics["status_conflict_count"] = -1

    result = assure_universe([_resolution(audit_metrics=metrics)])

    assert result.passed is False
    assert "INVALID_AUDIT_COUNT" in result.reason_codes


def test_invalid_universe_dates_are_not_verified() -> None:
    invalid_signal = assure_universe([_resolution(signal_date="not-a-date")])
    invalid_lifecycle = assure_universe(
        [_resolution(eligible=(_instrument("BAD.SH", list_date="not-a-date"),))]
    )

    assert invalid_signal.passed is False
    assert invalid_lifecycle.passed is False
    assert "INVALID_UNIVERSE_DATE" in invalid_signal.reason_codes
    assert "INVALID_UNIVERSE_DATE" in invalid_lifecycle.reason_codes
