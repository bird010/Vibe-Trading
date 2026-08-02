"""Phase 1 Task 3 — explicit whitelist Catalog tests (design §16)."""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from backtest.fund_rotation.catalog import (
    FUND_ROTATION_CONFIG_INVALID,
    FUND_ROTATION_DUPLICATE_STRATEGY_ID,
    FUND_ROTATION_INTERFACE_INCOMPATIBLE,
    FUND_ROTATION_STRATEGY_NOT_FOUND,
    CatalogError,
    FundRotationStrategyCatalog,
)
from backtest.fund_rotation.contracts import (
    FundRotationStrategyDescriptor,
    StrategyDiagnostics,
)
from backtest.fund_rotation.strategies.correlation_all_members.strategy import (
    CorrelationAllMembersStrategy,
)


def _catalog() -> FundRotationStrategyCatalog:
    return FundRotationStrategyCatalog([CorrelationAllMembersStrategy])


class TestCatalogRegistration:
    def test_list_sorted_and_immutable(self):
        catalog = _catalog()
        entries = catalog.list()
        assert len(entries) == 1
        assert entries[0].strategy_id == "correlation_all_members"
        assert entries[0].interface_version == "1.0"
        assert entries[0].implementation_hash  # non-empty
        with pytest.raises(Exception):
            entries[0].strategy_id = "x"  # frozen

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
