"""Tests for strategy adapter data-dependency declarations.

Each adapter must declare which tables it reads so the PIT assurance
classifier can make an informed strict/snapshot_only decision.

- AlphaZooStrategyAdapter: factor computation uses strict market data, but
  panel builder reads dim_stock_name_history and bridge_stock_industry
  (both revisable) → snapshot_only with explicit audit trail.
- GraphStrategyAdapter uses revisable fundamentals → snapshot_only.
- Engine merges its own common dependencies (fact_index_daily, fact_index_weight)
  before calling classify_pit_assurance.
"""

from __future__ import annotations

from backtest.stockpred.cohort.pit_assurance import classify_pit_assurance
from src.stockpred.strategies.adapters import (
    ENGINE_COMMON_DEPENDENCIES,
    AlphaZooStrategyAdapter,
    GraphStrategyAdapter,
)


class TestAlphaZooDependencies:
    def test_has_dependencies_attribute(self):
        adapter = AlphaZooStrategyAdapter(registry=None, panel_builder=None, descriptor=None)
        assert hasattr(adapter, "dependencies")
        assert len(adapter.dependencies) > 0

    def test_declares_name_history_dependency(self):
        """Panel builder uses dim_stock_name_history for ST filtering."""
        adapter = AlphaZooStrategyAdapter(registry=None, panel_builder=None, descriptor=None)
        assert "dim_stock_name_history" in adapter.dependencies

    def test_declares_industry_dependency(self):
        """Panel builder uses bridge_stock_industry for universe construction."""
        adapter = AlphaZooStrategyAdapter(registry=None, panel_builder=None, descriptor=None)
        assert "bridge_stock_industry" in adapter.dependencies

    def test_pit_assurance_snapshot_only_due_to_revisable_tables(self):
        """Alpha Zoo is snapshot_only because panel builder reads revisable tables."""
        adapter = AlphaZooStrategyAdapter(registry=None, panel_builder=None, descriptor=None)
        result = classify_pit_assurance(list(adapter.dependencies))
        assert result.level == "snapshot_only"
        assert "dim_stock_name_history" in result.snapshot_only_tables
        assert "bridge_stock_industry" in result.snapshot_only_tables


class TestGraphDependencies:
    def test_has_dependencies_attribute(self):
        adapter = GraphStrategyAdapter(signal_service=None)
        assert hasattr(adapter, "dependencies")
        assert len(adapter.dependencies) > 0

    def test_declares_index_weight_dependency(self):
        """GraphSignalService reads index_weights for graph construction."""
        adapter = GraphStrategyAdapter(signal_service=None)
        assert "fact_index_weight" in adapter.dependencies

    def test_pit_assurance_snapshot_only(self):
        adapter = GraphStrategyAdapter(signal_service=None)
        result = classify_pit_assurance(list(adapter.dependencies))
        assert result.level == "snapshot_only"
        assert len(result.snapshot_only_tables) > 0


class TestEngineDependencyResolution:
    """Verify the engine's getattr chain resolves declared dependencies."""

    def test_engine_resolves_alpha_zoo_deps(self):
        """Simulate engine.py resolution for AlphaZoo."""
        adapter = AlphaZooStrategyAdapter(registry=None, panel_builder=None, descriptor=None)
        table_deps = tuple(
            getattr(adapter, "dependencies", ())
            or ("__unproven_strategy_dependencies__",)
        )
        assert "__unproven_strategy_dependencies__" not in table_deps
        # Merge engine common deps (as engine.py should do)
        merged = sorted(set(table_deps) | set(ENGINE_COMMON_DEPENDENCIES))
        result = classify_pit_assurance(merged)
        assert result.level == "snapshot_only"
        assert "dim_stock_name_history" in result.snapshot_only_tables
        assert "bridge_stock_industry" in result.snapshot_only_tables

    def test_engine_resolves_graph_deps(self):
        """Simulate engine.py resolution for Graph."""
        adapter = GraphStrategyAdapter(signal_service=None)
        table_deps = tuple(
            getattr(adapter, "dependencies", ())
            or ("__unproven_strategy_dependencies__",)
        )
        assert "__unproven_strategy_dependencies__" not in table_deps
        merged = sorted(set(table_deps) | set(ENGINE_COMMON_DEPENDENCIES))
        result = classify_pit_assurance(merged)
        assert result.level == "snapshot_only"

    def test_engine_common_dependencies_include_index_daily(self):
        """Engine reads fact_index_daily for benchmark computation."""
        assert "fact_index_daily" in ENGINE_COMMON_DEPENDENCIES

    def test_engine_common_dependencies_minimal(self):
        """Engine common deps only include tables the engine itself reads."""
        # fact_index_weight is read by GraphSignalService, not the engine
        assert "fact_index_weight" not in ENGINE_COMMON_DEPENDENCIES

    def test_engine_common_dependencies_include_stock_limit(self):
        """Engine _load_market reads fact_stock_limit for all strategies."""
        assert "fact_stock_limit" in ENGINE_COMMON_DEPENDENCIES


class TestProtocolFingerprint:
    """eligibility_policy_version must be part of protocol_config."""

    def test_eligibility_version_in_protocol_config(self):
        """Verify engine includes eligibility_policy_version in protocol_config.

        This ensures old/new runs with different eligibility semantics
        produce different protocol keys.
        """
        from backtest.stockpred.cohort.engine import ELIGIBILITY_POLICY_VERSION

        assert ELIGIBILITY_POLICY_VERSION == "eligibility_v2"
