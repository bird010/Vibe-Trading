"""PIT fund master and universe resolver vertical-slice tests."""

from __future__ import annotations

import pandas as pd
import pytest

from backtest.fund_rotation.pit_universe import (
    ExclusionLayer,
    ExclusionReasonCode,
    FundInstrumentVersion,
    PITFundMaster,
    PITQueryMode,
    PITQualityStatus,
    UniversePolicy,
    UniverseResolver,
    to_market_rule_instrument_version,
)


class DictCausalView:
    """Small causal market-data view used by resolver tests."""

    def __init__(self, statuses: dict[str, tuple[bool, str]] | None = None):
        self._statuses = statuses or {}

    def tradable_status(self, ts_code: str, signal_date: str) -> tuple[bool, str]:
        return self._statuses.get(ts_code, (True, ""))


def _verified_row(ts_code: str, **overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "ts_code": ts_code,
        "valid_from": "2020-01-01",
        "valid_to": None,
        "known_from": "2020-01-02T00:00:00",
        "revision_id": "r1",
        "source_id": "fixture",
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
        "exchange": "SH",
        "revision_chain_verified": True,
        "independent_source_verified": True,
    }
    row.update(overrides)
    return row


def test_query_modes_select_as_was_known_or_latest_restated_rows() -> None:
    """Catches ignoring mode/knowledge_cutoff and leaking later-known rows."""

    rows = [
        _verified_row(
            "510300.SH",
            known_from="2020-01-02T00:00:00",
            revision_id="r1",
            source_record_id="src-510300-r1",
            name="old known ETF",
            tracking_index="000300.SH",
        ),
        _verified_row(
            "510500.SH",
            known_from="2020-06-01T00:00:00",
            revision_id="r2",
            source_record_id="src-510500-r2",
            name="latest restated ETF",
            tracking_index="000905.SH",
        ),
    ]
    master = PITFundMaster(pd.DataFrame(rows))

    as_was_known = master.instruments_at(
        signal_date="2020-03-01",
        knowledge_cutoff="2020-03-02T00:00:00",
        snapshot_version=7,
        mode=PITQueryMode.AS_WAS_KNOWN,
    )
    latest_restated = master.instruments_at(
        signal_date="2020-03-01",
        knowledge_cutoff="2020-03-02T00:00:00",
        snapshot_version=7,
        mode=PITQueryMode.LATEST_RESTATED,
    )

    assert [(instrument.ts_code, instrument.revision_id) for instrument in as_was_known] == [
        ("510300.SH", "r1")
    ]
    assert as_was_known[0].tracking_index == "000300.SH"
    assert [(instrument.ts_code, instrument.revision_id) for instrument in latest_restated] == [
        ("510300.SH", "r1"),
        ("510500.SH", "r2"),
    ]
    assert latest_restated[1].tracking_index == "000905.SH"


def test_valid_interval_and_known_from_cutoff_are_half_open_and_causal() -> None:
    """Catches inclusive valid_to, missing valid_from checks, and future knowledge leaks."""

    rows = [
        _verified_row("valid.SH"),
        _verified_row("ended.SH", valid_to="2020-03-01"),
        _verified_row("future_valid.SH", valid_from="2020-03-02"),
        _verified_row("future_known.SH", known_from="2020-03-02T00:00:00"),
    ]
    resolver = UniverseResolver(PITFundMaster(rows))

    resolution = resolver.resolve(
        signal_date="2020-03-01",
        knowledge_cutoff="2020-03-01T15:00:00",
        strategy_policy=UniversePolicy(),
        causal_view=DictCausalView(),
        snapshot_version=7,
        mode=PITQueryMode.AS_WAS_KNOWN,
    )

    assert [instrument.ts_code for instrument in resolution.eligible] == ["valid.SH"]
    reasons = {record.ts_code: record.reason_code for record in resolution.master_exclusions}
    assert reasons == {
        "ended.SH": ExclusionReasonCode.NOT_VALID_ON_SIGNAL_DATE,
        "future_valid.SH": ExclusionReasonCode.NOT_VALID_ON_SIGNAL_DATE,
        "future_known.SH": ExclusionReasonCode.KNOWN_AFTER_CUTOFF,
    }


def test_signal_date_selects_single_non_overlapping_revision_for_same_ts_code() -> None:
    """Catches returning multiple valid intervals or choosing by input order."""

    rows = [
        _verified_row(
            "510500.SH",
            valid_from="2020-01-01",
            valid_to="2020-01-15",
            known_from="2020-01-02T00:00:00",
            revision_id="r1",
            name="first ETF",
        ),
        _verified_row(
            "510500.SH",
            valid_from="2020-02-02",
            valid_to=None,
            known_from="2020-02-02T00:00:00",
            revision_id="r2",
            name="second ETF",
        ),
        _verified_row(
            "510500.SH",
            valid_from="2020-01-15",
            valid_to="2020-02-02",
            known_from="2020-01-15T00:00:00",
            revision_id="r1b",
            name="middle ETF",
        ),
    ]
    master = PITFundMaster(list(reversed(rows)))

    result = master.instruments_at(
        signal_date="2020-02-15",
        knowledge_cutoff="2020-02-15T15:00:00",
        snapshot_version=3,
        mode=PITQueryMode.AS_WAS_KNOWN,
    )

    assert [(instrument.ts_code, instrument.revision_id, instrument.name) for instrument in result] == [
        ("510500.SH", "r2", "second ETF")
    ]


def test_unverifiable_revision_chain_degrades_quality_and_never_claims_verified() -> None:
    """Catches treating backfilled rows without source lineage as verified PIT."""

    rows = [
        _verified_row(
            "unverified.SH",
            revision_id=None,
            source_record_id=None,
            source_published_at=None,
            revision_chain_verified=False,
            independent_source_verified=False,
        )
    ]
    resolver = UniverseResolver(PITFundMaster(rows))

    resolution = resolver.resolve(
        signal_date="2020-03-01",
        knowledge_cutoff="2020-03-01T15:00:00",
        strategy_policy=UniversePolicy(),
        causal_view=DictCausalView(),
        snapshot_version=7,
        mode=PITQueryMode.AS_WAS_KNOWN,
    )

    assert resolution.eligible[0].quality_status == PITQualityStatus.KNOWLEDGE_TIME_UNVERIFIED
    assert resolution.eligible[0].source_published_at is None
    assert resolution.quality_status == PITQualityStatus.PIT_UNVERIFIED
    assert resolution.quality_status != PITQualityStatus.VERIFIED
    assert resolution.audit_metrics["knowledge_time_unverified_count"] == 1


def test_master_layer_excludes_not_listed_delisted_and_unknown_status() -> None:
    """Catches survivor inclusion and unknown-status admission."""

    rows = [
        _verified_row("not_yet.SH", list_date="2020-03-02"),
        _verified_row("delisted.SH", delist_date="2020-03-01"),
        _verified_row("unknown.SH", fund_status="UNKNOWN"),
        _verified_row("active.SH"),
    ]
    resolver = UniverseResolver(PITFundMaster(rows))

    resolution = resolver.resolve(
        signal_date="2020-03-01",
        knowledge_cutoff="2020-03-01T15:00:00",
        strategy_policy=UniversePolicy(),
        causal_view=DictCausalView(),
        snapshot_version=7,
        mode=PITQueryMode.AS_WAS_KNOWN,
    )

    assert [instrument.ts_code for instrument in resolution.eligible] == ["active.SH"]
    assert [(record.layer, record.ts_code, record.reason_code) for record in resolution.master_exclusions] == [
        (ExclusionLayer.FUND_MASTER, "delisted.SH", ExclusionReasonCode.DELISTED),
        (ExclusionLayer.FUND_MASTER, "not_yet.SH", ExclusionReasonCode.NOT_YET_LISTED),
        (ExclusionLayer.FUND_MASTER, "unknown.SH", ExclusionReasonCode.UNKNOWN_STATUS),
    ]


def test_three_universe_layers_keep_stable_exclusion_reasons() -> None:
    """Catches collapsing strategy and tradability exclusions into one bucket."""

    rows = [
        _verified_row("master_excluded.SH", fund_status=None),
        _verified_row("strategy_excluded.SH", fund_type="LOF"),
        _verified_row("tradable_excluded.SH"),
        _verified_row("eligible.SH"),
    ]
    resolver = UniverseResolver(PITFundMaster(rows))

    resolution = resolver.resolve(
        signal_date="2020-03-01",
        knowledge_cutoff="2020-03-01T15:00:00",
        strategy_policy=UniversePolicy(fund_types=frozenset({"ETF"})),
        causal_view=DictCausalView({"tradable_excluded.SH": (False, "SUSPENDED")}),
        snapshot_version=7,
        mode=PITQueryMode.AS_WAS_KNOWN,
    )

    assert [instrument.ts_code for instrument in resolution.eligible] == ["eligible.SH"]
    assert [(record.layer, record.reason_code.value) for record in resolution.master_exclusions] == [
        (ExclusionLayer.FUND_MASTER, "UNKNOWN_STATUS")
    ]
    assert [(record.layer, record.reason_code.value) for record in resolution.strategy_exclusions] == [
        (ExclusionLayer.STRATEGY, "FUND_TYPE_NOT_ALLOWED")
    ]
    assert [(record.layer, record.reason_code.value) for record in resolution.tradable_exclusions] == [
        (ExclusionLayer.TRADABLE, "NOT_TRADABLE")
    ]
    assert resolution.tradable_exclusions[0].details == "SUSPENDED"


def test_fixed_inputs_produce_deterministic_resolution() -> None:
    """Catches dependence on input row order or unstable exclusion ordering."""

    rows = [
        _verified_row("b_strategy.SH", fund_type="LOF"),
        _verified_row("a_eligible.SH"),
        _verified_row("c_master.SH", fund_status="UNKNOWN"),
        _verified_row("d_tradable.SH"),
    ]
    policy = UniversePolicy(fund_types=frozenset({"ETF"}))
    causal_view = DictCausalView({"d_tradable.SH": (False, "NO_VOLUME")})

    first = UniverseResolver(PITFundMaster(rows)).resolve(
        signal_date="2020-03-01",
        knowledge_cutoff="2020-03-01T15:00:00",
        strategy_policy=policy,
        causal_view=causal_view,
        snapshot_version=7,
        mode=PITQueryMode.AS_WAS_KNOWN,
    )
    second = UniverseResolver(PITFundMaster(list(reversed(rows)))).resolve(
        signal_date="2020-03-01",
        knowledge_cutoff="2020-03-01T15:00:00",
        strategy_policy=policy,
        causal_view=causal_view,
        snapshot_version=7,
        mode=PITQueryMode.AS_WAS_KNOWN,
    )

    assert first == second
    assert [instrument.ts_code for instrument in first.eligible] == ["a_eligible.SH"]


def test_resolve_requires_explicit_snapshot_version_argument() -> None:
    """Catches falling back to UniversePolicy.snapshot_version for formal PIT calls."""

    resolver = UniverseResolver(PITFundMaster([_verified_row("510300.SH", snapshot_version=7)]))

    with pytest.raises(TypeError):
        resolver.resolve(
            signal_date="2020-03-01",
            knowledge_cutoff="2020-03-01T15:00:00",
            strategy_policy=UniversePolicy(),
            causal_view=DictCausalView(),
            mode=PITQueryMode.AS_WAS_KNOWN,
        )


def test_resolve_requires_explicit_mode_argument() -> None:
    """Catches falling back to UniversePolicy.query_mode or default AS_WAS_KNOWN."""

    resolver = UniverseResolver(PITFundMaster([_verified_row("510300.SH", snapshot_version=7)]))

    with pytest.raises(TypeError):
        resolver.resolve(
            signal_date="2020-03-01",
            knowledge_cutoff="2020-03-01T15:00:00",
            strategy_policy=UniversePolicy(),
            causal_view=DictCausalView(),
            snapshot_version=7,
        )


def test_overlapping_valid_ranges_make_entire_resolution_invalid() -> None:
    """Catches accepting one row from an overlapping PIT validity interval."""

    rows = [
        _verified_row("510300.SH", valid_from="2020-01-01", valid_to="2020-06-01", revision_id="r1"),
        _verified_row("510300.SH", valid_from="2020-03-01", valid_to="2020-12-31", revision_id="r2"),
    ]
    resolver = UniverseResolver(PITFundMaster(rows))

    resolution = resolver.resolve(
        signal_date="2020-04-01",
        knowledge_cutoff="2020-04-01T15:00:00",
        strategy_policy=UniversePolicy(),
        causal_view=DictCausalView(),
        snapshot_version=7,
        mode=PITQueryMode.AS_WAS_KNOWN,
    )

    assert resolution.eligible == ()
    assert resolution.quality_status == PITQualityStatus.PIT_INVALID
    assert resolution.audit_metrics["overlapping_valid_range_count"] == 1
    assert resolution.master_exclusions[0].reason_code == ExclusionReasonCode.OVERLAPPING_VALID_RANGE


def test_overlapping_valid_ranges_with_different_known_from_make_resolution_invalid() -> None:
    """Catches grouping overlap validation by known_from before checking valid ranges."""

    rows = [
        _verified_row(
            "510300.SH",
            valid_from="2020-01-01",
            valid_to="2020-06-01",
            known_from="2020-01-02T00:00:00",
            revision_id="r1",
            source_record_id="src-510300-r1",
        ),
        _verified_row(
            "510300.SH",
            valid_from="2020-03-01",
            valid_to="2020-12-31",
            known_from="2020-02-02T00:00:00",
            revision_id="r2",
            source_record_id="src-510300-r2",
        ),
    ]
    resolver = UniverseResolver(PITFundMaster(rows))

    resolution = resolver.resolve(
        signal_date="2020-04-01",
        knowledge_cutoff="2020-04-01T15:00:00",
        strategy_policy=UniversePolicy(),
        causal_view=DictCausalView(),
        snapshot_version=7,
        mode=PITQueryMode.AS_WAS_KNOWN,
    )

    assert resolution.eligible == ()
    assert resolution.quality_status == PITQualityStatus.PIT_INVALID
    assert resolution.audit_metrics["overlapping_valid_range_count"] == 1
    assert resolution.master_exclusions[0].reason_code == ExclusionReasonCode.OVERLAPPING_VALID_RANGE


def test_unsortable_same_knowledge_revision_makes_entire_resolution_invalid() -> None:
    """Catches treating an ambiguous same-knowledge revision tie as a row-level exclusion."""

    rows = [
        _verified_row("510300.SH", known_from="2020-01-02T00:00:00", revision_id=None),
        _verified_row("510300.SH", known_from="2020-01-02T00:00:00", revision_id=None, name="duplicate"),
    ]
    resolver = UniverseResolver(PITFundMaster(rows))

    resolution = resolver.resolve(
        signal_date="2020-03-01",
        knowledge_cutoff="2020-03-01T15:00:00",
        strategy_policy=UniversePolicy(),
        causal_view=DictCausalView(),
        snapshot_version=7,
        mode=PITQueryMode.AS_WAS_KNOWN,
    )

    assert resolution.eligible == ()
    assert resolution.quality_status == PITQualityStatus.PIT_INVALID
    assert resolution.master_exclusions[0].reason_code == ExclusionReasonCode.AMBIGUOUS_REVISION_ORDER


def test_list_date_after_delist_date_makes_entire_resolution_invalid() -> None:
    """Catches masking impossible lifecycle dates as ordinary delisted/not-listed rows."""

    rows = [_verified_row("510300.SH", list_date="2020-05-01", delist_date="2020-04-01")]
    resolver = UniverseResolver(PITFundMaster(rows))

    resolution = resolver.resolve(
        signal_date="2020-03-01",
        knowledge_cutoff="2020-03-01T15:00:00",
        strategy_policy=UniversePolicy(),
        causal_view=DictCausalView(),
        snapshot_version=7,
        mode=PITQueryMode.AS_WAS_KNOWN,
    )

    assert resolution.eligible == ()
    assert resolution.quality_status == PITQualityStatus.PIT_INVALID
    assert resolution.master_exclusions[0].reason_code == ExclusionReasonCode.INVALID_LIFECYCLE_DATES


def test_missing_delist_date_has_stable_reason_and_is_not_formally_eligible() -> None:
    """Catches treating missing delist_date as proof the fund still existed."""

    rows = [_verified_row("510300.SH", delist_date=None)]
    resolver = UniverseResolver(PITFundMaster(rows))

    resolution = resolver.resolve(
        signal_date="2020-03-01",
        knowledge_cutoff="2020-03-01T15:00:00",
        strategy_policy=UniversePolicy(),
        causal_view=DictCausalView(),
        snapshot_version=7,
        mode=PITQueryMode.AS_WAS_KNOWN,
    )

    assert resolution.eligible == ()
    assert resolution.quality_status == PITQualityStatus.RESEARCH_ONLY_UNVERIFIED_UNIVERSE
    assert resolution.audit_metrics["missing_delist_date_count"] == 1
    assert resolution.master_exclusions[0].reason_code == ExclusionReasonCode.MISSING_DELIST_DATE


def test_unknown_fund_type_is_excluded_even_without_policy_type_filter() -> None:
    """Catches admitting unclassified funds when the strategy policy has no type constraint."""

    rows = [_verified_row("510300.SH", fund_type=None)]
    resolver = UniverseResolver(PITFundMaster(rows))

    resolution = resolver.resolve(
        signal_date="2020-03-01",
        knowledge_cutoff="2020-03-01T15:00:00",
        strategy_policy=UniversePolicy(),
        causal_view=DictCausalView(),
        snapshot_version=7,
        mode=PITQueryMode.AS_WAS_KNOWN,
    )

    assert resolution.eligible == ()
    assert resolution.audit_metrics["unknown_type_count"] == 1
    assert resolution.master_exclusions[0].reason_code == ExclusionReasonCode.UNKNOWN_TYPE


def test_cross_source_missing_code_outputs_audit_and_research_only_quality() -> None:
    """Catches missing independent-source coverage gates and absent audit output."""

    rows = [_verified_row("510300.SH")]
    resolver = UniverseResolver(PITFundMaster(rows))

    resolution = resolver.resolve(
        signal_date="2020-03-01",
        knowledge_cutoff="2020-03-01T15:00:00",
        strategy_policy=UniversePolicy(),
        causal_view=DictCausalView(),
        snapshot_version=7,
        mode=PITQueryMode.AS_WAS_KNOWN,
        cross_source_audit={
            "source_a_codes": frozenset({"510300.SH"}),
            "source_b_codes": frozenset({"510300.SH", "missing.SH"}),
            "status_conflicts": frozenset(),
            "date_conflicts": frozenset(),
            "classification_conflicts": frozenset(),
        },
    )

    assert [instrument.ts_code for instrument in resolution.eligible] == ["510300.SH"]
    assert resolution.quality_status == PITQualityStatus.RESEARCH_ONLY_UNVERIFIED_UNIVERSE
    assert resolution.audit_metrics["cross_source_missing_count"] == 1
    assert resolution.cross_source_audit["only_in_b"] == frozenset({"missing.SH"})


def test_market_rule_identity_adapter_prefers_explicit_instrument_type() -> None:
    instrument = FundInstrumentVersion(
        ts_code="510300.SH",
        valid_from="2020-01-01",
        valid_to=None,
        known_from="2020-01-02T00:00:00",
        revision_id="r1",
        source_id="fixture",
        source_record_id="src-510300-r1",
        source_published_at=None,
        ingested_at="2020-01-02T01:00:00",
        list_date="2020-01-01",
        delist_date="2099-12-31",
        fund_status="ACTIVE",
        name="沪深300ETF",
        fund_type="ETF",
        asset_class="equity",
        tracking_index="000300.SH",
        exchange="SH",
        quality_status=PITQualityStatus.VERIFIED,
        instrument_type="cross_border_etf",
    )

    identity = to_market_rule_instrument_version(instrument)

    assert identity is not None
    assert identity.ts_code == "510300.SH"
    assert identity.instrument_type == "cross_border_etf"
    assert identity.version == "src-510300-r1"


def test_market_rule_identity_adapter_can_map_from_fund_type_and_asset_class() -> None:
    instrument = FundInstrumentVersion(
        ts_code="511990.SH",
        valid_from="2020-01-01",
        valid_to=None,
        known_from="2020-01-02T00:00:00",
        revision_id="r1",
        source_id="fixture",
        source_record_id="src-511990-r1",
        source_published_at=None,
        ingested_at="2020-01-02T01:00:00",
        list_date="2020-01-01",
        delist_date="2099-12-31",
        fund_status="ACTIVE",
        name="货币ETF",
        fund_type="ETF",
        asset_class="money_market",
        tracking_index=None,
        exchange="SH",
        quality_status=PITQualityStatus.VERIFIED,
    )

    identity = to_market_rule_instrument_version(instrument)

    assert identity is not None
    assert identity.instrument_type == "money_market_etf"
    assert identity.version == "src-511990-r1"


def test_market_rule_identity_adapter_returns_none_for_unmappable_instrument() -> None:
    instrument = FundInstrumentVersion(
        ts_code="999999.SH",
        valid_from="2020-01-01",
        valid_to=None,
        known_from="2020-01-02T00:00:00",
        revision_id="r1",
        source_id="fixture",
        source_record_id="src-999999-r1",
        source_published_at=None,
        ingested_at="2020-01-02T01:00:00",
        list_date="2020-01-01",
        delist_date="2099-12-31",
        fund_status="ACTIVE",
        name="未知基金",
        fund_type="LOF",
        asset_class="alternatives",
        tracking_index=None,
        exchange="SZ",
        quality_status=PITQualityStatus.VERIFIED,
    )

    assert to_market_rule_instrument_version(instrument) is None
