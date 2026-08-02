"""Phase 1 Task 4 — config-dependent data requirement resolution tests (§23)."""

from __future__ import annotations

import pytest

from backtest.fund_rotation.contracts import (
    StrategyDataRequirements,
    merge_requirements,
)
from backtest.fund_rotation.strategies.correlation_all_members.config import (
    CorrelationAllMembersConfig,
)
from backtest.fund_rotation.strategies.correlation_all_members.strategy import (
    CorrelationAllMembersStrategy,
)


class TestConfigDependentRequirements:
    def test_warmup_scales_with_lookback(self):
        strategy = CorrelationAllMembersStrategy()
        small = strategy.resolve_requirements(
            CorrelationAllMembersConfig(correlation_lookback_weeks=20)
        )
        large = strategy.resolve_requirements(
            CorrelationAllMembersConfig(correlation_lookback_weeks=52)
        )
        assert large.warmup_trade_days > small.warmup_trade_days

    def test_requirements_declare_adv_and_benchmark_fields(self):
        strategy = CorrelationAllMembersStrategy()
        req = strategy.resolve_requirements(CorrelationAllMembersConfig())
        # ADV20 needs turnover/amount; benchmark needed.
        assert "amount" in req.required_fields
        assert "adj_factor" in req.required_fields
        assert req.needs_benchmark is True
        assert "fund" in req.required_datasets

    def test_resolve_is_pure_function_of_config(self):
        strategy = CorrelationAllMembersStrategy()
        cfg = CorrelationAllMembersConfig(correlation_lookback_weeks=30)
        # Same config -> identical requirements (no I/O, no clock, no randomness).
        assert strategy.resolve_requirements(cfg) == strategy.resolve_requirements(cfg)

    def test_requirements_carry_no_clustering_gate_params(self):
        req = CorrelationAllMembersStrategy().resolve_requirements(
            CorrelationAllMembersConfig()
        )
        gate_fields = {
            "max_cluster_share_warn", "max_cluster_share_reject",
            "min_effective_cluster_count_warn", "min_effective_cluster_count_reject",
            "k", "top_n",
        }
        assert gate_fields.isdisjoint(set(req.__dataclass_fields__))


class TestMergeRequirements:
    def test_unions_datasets_and_fields(self):
        a = StrategyDataRequirements(
            required_datasets=("fund",), required_fields=("close", "vol"),
            warmup_trade_days=100, frequency="W", needs_benchmark=False,
        )
        b = StrategyDataRequirements(
            required_datasets=("dim_fund",), required_fields=("close", "amount"),
            warmup_trade_days=50, frequency="W", needs_benchmark=True,
        )
        merged = merge_requirements([a, b])
        assert set(merged.required_datasets) == {"fund", "dim_fund"}
        assert set(merged.required_fields) == {"close", "vol", "amount"}

    def test_warmup_is_max(self):
        a = StrategyDataRequirements(
            required_datasets=(), required_fields=(), warmup_trade_days=100,
            frequency="W", needs_benchmark=False,
        )
        b = StrategyDataRequirements(
            required_datasets=(), required_fields=(), warmup_trade_days=250,
            frequency="W", needs_benchmark=False,
        )
        assert merge_requirements([a, b]).warmup_trade_days == 250

    def test_needs_benchmark_is_or(self):
        a = StrategyDataRequirements(
            required_datasets=(), required_fields=(), warmup_trade_days=10,
            frequency="W", needs_benchmark=False,
        )
        b = StrategyDataRequirements(
            required_datasets=(), required_fields=(), warmup_trade_days=10,
            frequency="W", needs_benchmark=True,
        )
        assert merge_requirements([a, b]).needs_benchmark is True

    def test_conflicting_frequency_fails(self):
        a = StrategyDataRequirements(
            required_datasets=(), required_fields=(), warmup_trade_days=10,
            frequency="W", needs_benchmark=False,
        )
        b = StrategyDataRequirements(
            required_datasets=(), required_fields=(), warmup_trade_days=10,
            frequency="M", needs_benchmark=False,
        )
        with pytest.raises(ValueError, match="conflicting"):
            merge_requirements([a, b])

    def test_empty_merge_fails(self):
        with pytest.raises(ValueError):
            merge_requirements([])
