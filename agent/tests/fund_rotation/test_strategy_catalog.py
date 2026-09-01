"""Phase 1 Task 3 — explicit whitelist Catalog tests (design §16)."""

from __future__ import annotations

import re

import pytest

from backtest.fund_rotation.catalog import (
    FUND_ROTATION_CONFIG_INVALID,
    FUND_ROTATION_DUPLICATE_STRATEGY_ID,
    FUND_ROTATION_INTERFACE_INCOMPATIBLE,
    FUND_ROTATION_STRATEGY_NOT_FOUND,
    FUND_ROTATION_STRATEGY_SNAPSHOT_INVALID,
    CatalogError,
    FundRotationStrategyCatalog,
)
from backtest.fund_rotation.strategies.registry import (
    default_fund_rotation_strategies,
)
from backtest.fund_rotation.contracts import FundRotationStrategyDescriptor
from backtest.fund_rotation.strategies.correlation_all_members.strategy import (
    CorrelationAllMembersStrategy,
)
from backtest.fund_rotation.strategies.correlation_representative.strategy import (
    CorrelationRepresentativeStrategy,
)


def _catalog() -> FundRotationStrategyCatalog:
    return FundRotationStrategyCatalog(list(default_fund_rotation_strategies()))


class TestCatalogRegistration:
    def test_ai_rotation_names_start_with_their_strategy_code(self):
        catalog = _catalog()

        for entry in catalog.list():
            match = re.match(r"^ai_rotation_(r\d+)_", entry.strategy_id)
            if match:
                assert entry.name.startswith(match.group(1).upper())

    def test_list_sorted_and_immutable(self):
        catalog = _catalog()
        entries = catalog.list()
        assert [e.strategy_id for e in entries] == [
            "ai_rotation_r05_mom_persist",
            "ai_rotation_r06_rank_buffer",
            "ai_rotation_r07_tail_persist",
            "ai_rotation_r11_persist_geom",
            "ai_rotation_r12_nondecay_geom",
            "ai_rotation_r13_arith_persist",
            "ai_rotation_r14_median_persist",
            "ai_rotation_r15_weighted_persist",
            "ai_rotation_r16_rank_consensus",
            "ai_rotation_r17_winsor_geom",
            "ai_rotation_r18_min_persist",
            "ai_rotation_r19_top2_cash",
            "ai_rotation_r20_rank_frontload",
            "ai_rotation_r21_harmonic_persist",
            "ai_rotation_r22_path_consistency",
            "ai_rotation_r23_downside_geom",
            "ai_rotation_r24_dispersion_geom",
            "ai_rotation_r25_rep_persist_geom",
            "ai_rotation_r26_path_vol_geom",
            "ai_rotation_r27_breadth_persist_geom",
            "ai_rotation_r28_size_reliability_geom",
            "ai_rotation_r29_invvol_slots",
            "ai_rotation_r30_endpoint_breadth_geom",
            "ai_rotation_r31_fast_exit",
            "ai_rotation_r32_market_regime",
            "ai_rotation_r33_quality_fallback",
            "ai_rotation_r34_staged_reentry",
            "ai_rotation_r35_short_gap_reentry",
            "ai_rotation_r36_tail_slot_full_entry",
            "ai_rotation_r37_decelerating_full_entry",
            "ai_rotation_r38_replacement_full_entry",
            "ai_rotation_r39_incumbent_carry",
            "ai_rotation_r40_single_name_ceiling",
            "ai_rotation_r41_breadth_gated_carry",
            "ai_rotation_r42_single_incumbent_half_carry",
            "ai_rotation_r43_multi_new_breadth_gate",
            "ai_rotation_r44_persistent_incumbent_carry",
            "ai_rotation_r45_cash_floor_carry",
            "ai_rotation_r46_cash_floor_tight_carry",
            "ai_rotation_r47_breadth_tight_floor",
            "ai_rotation_r48_cash_floor_very_tight",
            "ai_rotation_r49_cash_floor_micro",
            "ai_rotation_r50_cash_floor_nano",
            "ai_rotation_r51_cash_floor_pico",
            "ai_rotation_r52_cash_floor_femto",
            "ai_rotation_r53_cash_floor_atto",
            "ai_rotation_r54_cash_floor_zepto",
            "ai_rotation_r55_cash_floor_yotta",
            "ai_rotation_r56_cash_floor_ronna",
            "ai_rotation_r57_three_factor_representative",
            "ai_rotation_r58_r39_signal_r57",
            "ai_rotation_r59_r39_signal_r57_positive_slope",
            "ai_rotation_r60_r59_medium_trend_gate",
            "ai_rotation_r61_r59_dual_horizon_score",
            "ai_rotation_r62_r59_true_invvol",
            "ai_rotation_r63_r59_rank_buffer",
            "ai_rotation_r64_direct_corr_diversification",
            "ai_rotation_r65_r64_direct_corr_rank_buffer",
            "ai_rotation_r66_lazy_correlation",
            "ai_rotation_r67_r39_rank_buffer",
            "ai_rotation_r69_r39_transition_cap_50",
            "ai_rotation_r70_r39_transition_cap_25",
            "ai_rotation_r71_r39_capacity_aware_representative",
            "ai_rotation_r72_r39_absolute_momentum",
            "ai_rotation_r73_r39_multi_horizon_rank",
            "ai_rotation_r74_r39_vol_adjusted_score",
            "ai_rotation_r75_r39_vol_target",
            "ai_rotation_r76_cash_defense_baseline",
            "ai_rotation_r76_fixed_short_bond",
            "ai_rotation_r77_defense_relative_momentum",
            "ai_rotation_r78_survivor_combo",
            "ai_rotation_r79_economic_role_members",
            "ai_rotation_r80_economic_role_fixed_rep",
            "ai_rotation_r81_economic_role_dynamic_rep",
            "correlation_all_members", "correlation_representative",
        ]
        for entry in entries:
            assert entry.interface_version == "1.0"
            assert entry.implementation_hash
        with pytest.raises(Exception):
            entries[0].strategy_id = "x"

    def test_default_whitelist_contains_both_strategies(self):
        strategies = default_fund_rotation_strategies()
        assert CorrelationAllMembersStrategy in strategies
        assert CorrelationRepresentativeStrategy in strategies

    def test_representative_implementation_hash_stable_and_distinct(self):
        catalog = _catalog()
        entries = {e.strategy_id: e for e in catalog.list()}
        baseline_hash = entries["correlation_all_members"].implementation_hash
        representative_hash = entries["correlation_representative"].implementation_hash
        assert representative_hash != baseline_hash
        rebuilt = _catalog()
        rebuilt_entries = {e.strategy_id: e for e in rebuilt.list()}
        assert (
            rebuilt_entries["correlation_representative"].implementation_hash
            == representative_hash
        )

    def test_require_unknown_raises_not_found(self):
        catalog = _catalog()
        with pytest.raises(CatalogError) as exc_info:
            catalog.require("does_not_exist")
        assert exc_info.value.code == FUND_ROTATION_STRATEGY_NOT_FOUND

    def test_duplicate_id_detected_at_startup(self):
        class _Dup(CorrelationAllMembersStrategy):
            pass

        with pytest.raises(CatalogError) as exc_info:
            FundRotationStrategyCatalog([CorrelationAllMembersStrategy, _Dup])
        assert exc_info.value.code == FUND_ROTATION_DUPLICATE_STRATEGY_ID

    def test_incompatible_interface_detected_at_startup(self):
        bad_descriptor = FundRotationStrategyDescriptor(
            id="bad", name="bad", description="d",
            interface_version="9.9", supported_universe=("etf",), deterministic=True,
        )

        class _Bad:
            descriptor = bad_descriptor
            config_model = CorrelationAllMembersStrategy.config_model

            def resolve_requirements(self, config):
                raise NotImplementedError

            def create_session(self, initialization, config):
                raise NotImplementedError

        with pytest.raises(CatalogError) as exc_info:
            FundRotationStrategyCatalog([_Bad])
        assert exc_info.value.code == FUND_ROTATION_INTERFACE_INCOMPATIBLE

    def test_empty_interface_version_rejected_at_startup(self):
        desc = FundRotationStrategyDescriptor(
            id="empty_ver", name="x", description="d",
            interface_version="", supported_universe=("etf",), deterministic=True,
        )

        class _EmptyVer:
            descriptor = desc
            config_model = CorrelationAllMembersStrategy.config_model

            def resolve_requirements(self, config):
                raise NotImplementedError

            def create_session(self, initialization, config):
                raise NotImplementedError

        with pytest.raises(CatalogError) as exc_info:
            FundRotationStrategyCatalog([_EmptyVer])
        assert exc_info.value.code == FUND_ROTATION_INTERFACE_INCOMPATIBLE

    def test_list_multiple_strategies_sorted_by_id(self):
        def _make(cls_id: str):
            desc = FundRotationStrategyDescriptor(
                id=cls_id, name=cls_id, description="d",
                interface_version="1.0", supported_universe=("etf",), deterministic=True,
            )

            class _S:
                descriptor = desc
                config_model = CorrelationAllMembersStrategy.config_model

                def resolve_requirements(self, config):
                    raise NotImplementedError

                def create_session(self, initialization, config):
                    raise NotImplementedError

            return _S

        catalog = FundRotationStrategyCatalog([_make("zeta"), _make("alpha")])
        assert [e.strategy_id for e in catalog.list()] == ["alpha", "zeta"]

    def test_snapshot_invalid_on_unreadable_source(self, monkeypatch):
        import src.stockpred.fund_rotation.strategy_snapshot as snapshot_mod

        def unreadable(_strategy_cls):
            raise OSError("strategy source is unreadable")

        monkeypatch.setattr(
            snapshot_mod,
            "snapshot_strategy_package",
            unreadable,
        )
        with pytest.raises(CatalogError) as exc_info:
            FundRotationStrategyCatalog([CorrelationAllMembersStrategy])
        assert exc_info.value.code == FUND_ROTATION_STRATEGY_SNAPSHOT_INVALID


class TestCatalogResolve:
    def test_resolve_fills_defaults(self):
        catalog = _catalog()
        binding = catalog.resolve("correlation_all_members", {})
        assert binding.spec.resolved_config["k"] == 8
        assert binding.spec.resolved_config["top_n"] == 3
        assert binding.spec.strategy_id == "correlation_all_members"

    def test_omitted_and_explicit_defaults_same_hash(self):
        catalog = _catalog()
        omitted = catalog.resolve("correlation_all_members", {})
        explicit = catalog.resolve(
            "correlation_all_members",
            {"k": 8, "top_n": 3},
        )
        assert omitted.spec.resolved_config_hash == explicit.spec.resolved_config_hash

    def test_different_config_different_hash(self):
        catalog = _catalog()
        a = catalog.resolve("correlation_all_members", {"k": 8})
        b = catalog.resolve("correlation_all_members", {"k": 4})
        assert a.spec.resolved_config_hash != b.spec.resolved_config_hash

    def test_unknown_field_rejected(self):
        catalog = _catalog()
        with pytest.raises(CatalogError) as exc_info:
            catalog.resolve("correlation_all_members", {"not_a_field": 1})
        assert exc_info.value.code == FUND_ROTATION_CONFIG_INVALID

    def test_invalid_value_rejected(self):
        catalog = _catalog()
        with pytest.raises(CatalogError) as exc_info:
            catalog.resolve("correlation_all_members", {"k": 0})
        assert exc_info.value.code == FUND_ROTATION_CONFIG_INVALID

    def test_resolve_unknown_strategy_not_found(self):
        catalog = _catalog()
        with pytest.raises(CatalogError) as exc_info:
            catalog.resolve("nope", {})
        assert exc_info.value.code == FUND_ROTATION_STRATEGY_NOT_FOUND

    def test_resolve_binds_requirements_and_snapshot(self):
        catalog = _catalog()
        binding = catalog.resolve("correlation_all_members", {})
        spec = binding.spec
        assert spec.implementation_hash
        assert spec.resolved_requirements.warmup_trade_days > 0
        assert spec.resolved_requirements_hash
        assert spec.config_schema_hash

    def test_factory_not_in_persistable_spec(self):
        catalog = _catalog()
        binding = catalog.resolve("correlation_all_members", {})
        spec_fields = set(binding.spec.__dataclass_fields__)
        assert "factory" not in spec_fields
        assert callable(binding.registered.factory)

    def test_resolve_returns_instantiated_strategy(self):
        from backtest.fund_rotation.contracts import FundRotationStrategy

        catalog = _catalog()
        binding = catalog.resolve("correlation_all_members", {})
        assert isinstance(binding.strategy, FundRotationStrategy)


class TestRepresentativeCatalogResolve:
    """Representative strategy resolves through the shared catalog machinery."""

    def test_resolve_fills_design_section_4_defaults(self):
        catalog = _catalog()
        binding = catalog.resolve("correlation_representative", {})
        resolved = binding.spec.resolved_config
        assert resolved["k"] == 8
        assert resolved["top_n"] == 3
        assert resolved["correlation_lookback_weeks"] == 52
        assert resolved["representative_candidate_count"] == 5
        assert resolved["representative_min_cluster_corr"] == 0.85
        assert resolved["max_cluster_share_warn"] == 0.50
        assert resolved["max_cluster_share_reject"] == 0.80
        assert resolved["min_effective_cluster_count_warn"] == 4.0
        assert resolved["min_effective_cluster_count_reject"] == 2.5

    def test_schema_hash_and_requirements_populated(self):
        catalog = _catalog()
        binding = catalog.resolve("correlation_representative", {})
        spec = binding.spec
        assert spec.strategy_id == "correlation_representative"
        assert spec.implementation_hash
        assert spec.config_schema_hash
        assert spec.resolved_config_hash
        assert spec.resolved_requirements.frequency == "weekly"
        assert "amount" in spec.resolved_requirements.required_fields
        assert spec.resolved_requirements.warmup_trade_days == (52 + 1) * 5 - 1

    def test_gate_threshold_change_changes_config_hash(self):
        catalog = _catalog()
        a = catalog.resolve("correlation_representative", {})
        b = catalog.resolve(
            "correlation_representative", {"max_cluster_share_reject": 0.9},
        )
        assert a.spec.resolved_config_hash != b.spec.resolved_config_hash

    def test_conflicting_gate_thresholds_rejected_at_resolve(self):
        catalog = _catalog()
        with pytest.raises(CatalogError) as exc_info:
            catalog.resolve(
                "correlation_representative",
                {"max_cluster_share_warn": 0.9, "max_cluster_share_reject": 0.8},
            )
        assert exc_info.value.code == FUND_ROTATION_CONFIG_INVALID
