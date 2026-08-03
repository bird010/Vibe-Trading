"""Phase 1 Task 3 — explicit whitelist Catalog tests (design §16)."""

from __future__ import annotations

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
    def test_list_sorted_and_immutable(self):
        catalog = _catalog()
        entries = catalog.list()
        assert [e.strategy_id for e in entries] == [
            "correlation_all_members", "correlation_representative",
        ]
        for entry in entries:
            assert entry.interface_version == "1.0"
            assert entry.implementation_hash  # non-empty
        with pytest.raises(Exception):
            entries[0].strategy_id = "x"  # frozen

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
        # Stable across catalog rebuilds (startup-fixed source hashing).
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
            pass  # same descriptor.id

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

        # Insert out of order; list() must sort by strategy_id.
        catalog = FundRotationStrategyCatalog([_make("zeta"), _make("alpha")])
        assert [e.strategy_id for e in catalog.list()] == ["alpha", "zeta"]

    def test_snapshot_invalid_on_unreadable_source(self, monkeypatch):
        import backtest.fund_rotation.catalog as catalog_mod

        def _bad_getfile(cls):
            return "/nonexistent/path/strategy.py"

        monkeypatch.setattr(catalog_mod.inspect, "getfile", _bad_getfile)
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
            "correlation_all_members", {"k": 8, "top_n": 3, "initial_capital": 1_000_000.0},
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
        assert spec.implementation_hash  # bound startup snapshot
        assert spec.resolved_requirements.warmup_trade_days > 0
        assert spec.resolved_requirements_hash  # non-empty
        assert spec.config_schema_hash  # non-empty

    def test_factory_not_in_persistable_spec(self):
        catalog = _catalog()
        binding = catalog.resolve("correlation_all_members", {})
        spec_fields = set(binding.spec.__dataclass_fields__)
        assert "factory" not in spec_fields
        # registered holds the factory; spec does not
        assert callable(binding.registered.factory)

    def test_resolve_returns_instantiated_strategy(self):
        from backtest.fund_rotation.contracts import FundRotationStrategy

        catalog = _catalog()
        binding = catalog.resolve("correlation_all_members", {})
        assert isinstance(binding.strategy, FundRotationStrategy)


class TestRepresentativeCatalogResolve:
    """Phase 3 Task 6 — the representative strategy resolves through the same
    catalog machinery: descriptor, schema, defaults, hashes, requirements."""

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
