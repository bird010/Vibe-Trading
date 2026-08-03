"""Phase 1 Task 2 — correlation_all_members baseline strategy & config tests."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from backtest.fund_rotation.config import FundRotationConfig
from backtest.fund_rotation.contracts import (
    DecisionKind,
    FundRotationStrategy,
    FundRotationStrategySession,
    StrategyInitializationContext,
)
from backtest.fund_rotation.strategies.correlation_all_members.config import (
    CorrelationAllMembersConfig,
)
from backtest.fund_rotation.strategies.correlation_all_members.strategy import (
    CorrelationAllMembersStrategy,
)


class TestConfigContract:
    def test_strategy_defaults_mirror_legacy_algorithm_fields(self):
        cfg = CorrelationAllMembersConfig()
        legacy = FundRotationConfig()
        for field in CorrelationAllMembersConfig.model_fields:
            assert getattr(cfg, field) == getattr(legacy, field), field

    def test_unknown_and_execution_fields_rejected(self):
        for payload in (
            {"not_a_field": 1},
            {"initial_capital": 500_000.0},
            {"commission_rate": 0.001},
            {"start_date": "20240101"},
            {"end_date": "20241231"},
        ):
            with pytest.raises(ValidationError):
                CorrelationAllMembersConfig(**payload)

    def test_range_violations_rejected(self):
        with pytest.raises(ValidationError):
            CorrelationAllMembersConfig(k=0)
        with pytest.raises(ValidationError):
            CorrelationAllMembersConfig(momentum_window_weeks=0)

    def test_top_n_must_not_exceed_k(self):
        with pytest.raises(ValidationError, match="top_n"):
            CorrelationAllMembersConfig(k=3, top_n=5)

    def test_min_pairwise_must_not_exceed_lookback(self):
        with pytest.raises(ValidationError, match="min_pairwise_weeks"):
            CorrelationAllMembersConfig(
                correlation_lookback_weeks=20,
                min_pairwise_weeks=30,
            )

    def test_momentum_window_must_be_subset_of_lookback(self):
        with pytest.raises(ValidationError, match="momentum_window_weeks"):
            CorrelationAllMembersConfig(
                correlation_lookback_weeks=20,
                momentum_window_weeks=20,
            )
        with pytest.raises(ValidationError, match="momentum_window_weeks"):
            CorrelationAllMembersConfig(
                correlation_lookback_weeks=20,
                momentum_window_weeks=30,
            )

    def test_config_is_immutable(self):
        cfg = CorrelationAllMembersConfig()
        with pytest.raises(ValidationError):
            cfg.k = 5

    def test_json_round_trip(self):
        cfg = CorrelationAllMembersConfig(
            k=4,
            top_n=2,
            correlation_lookback_weeks=30,
        )
        dumped = cfg.model_dump(mode="json")
        restored = CorrelationAllMembersConfig.model_validate(dumped)
        assert restored == cfg


class TestJsonSchema:
    def test_schema_has_algorithm_defaults_bounds_and_descriptions(self):
        schema = CorrelationAllMembersConfig.model_json_schema()
        assert schema["title"] == "CorrelationAllMembersConfig"
        assert schema["type"] == "object"
        properties = schema["properties"]
        assert properties["k"]["minimum"] == 1
        assert properties["k"]["default"] == 8
        assert properties["top_n"]["default"] == 3
        assert "聚类簇数量" in properties["k"]["description"]

    def test_schema_excludes_execution_and_evaluation_fields(self):
        properties = CorrelationAllMembersConfig.model_json_schema()["properties"]
        assert {
            "initial_capital",
            "commission_rate",
            "commission_min",
            "max_participation_rate",
            "base_slippage_bps",
            "start_date",
            "end_date",
        }.isdisjoint(properties)


class TestStrategyProtocol:
    def test_strategy_satisfies_protocol(self):
        strategy = CorrelationAllMembersStrategy()
        assert isinstance(strategy, FundRotationStrategy)
        assert strategy.descriptor.id == "correlation_all_members"
        assert strategy.descriptor.deterministic is True
        assert strategy.config_model is CorrelationAllMembersConfig

    def test_resolve_requirements_is_config_dependent(self):
        strategy = CorrelationAllMembersStrategy()
        req_small = strategy.resolve_requirements(
            CorrelationAllMembersConfig(
                correlation_lookback_weeks=20,
                min_training_weeks=20,
                momentum_window_weeks=4,
            )
        )
        req_large = strategy.resolve_requirements(
            CorrelationAllMembersConfig(
                correlation_lookback_weeks=52,
                min_training_weeks=52,
                momentum_window_weeks=4,
            )
        )
        assert req_large.warmup_trade_days > req_small.warmup_trade_days
        assert req_small.warmup_trade_days == (20 + 1) * 5 - 1
        assert req_large.warmup_trade_days == (52 + 1) * 5 - 1
        assert "fund" in req_small.required_datasets
        assert req_small.needs_benchmark is True

    def test_create_session_returns_session_protocol(self):
        strategy = CorrelationAllMembersStrategy()
        cfg = CorrelationAllMembersConfig()
        initialization = StrategyInitializationContext(
            run_id="r1",
            evaluation_calendar=("20240101", "20240102"),
        )
        session = strategy.create_session(initialization, cfg)
        assert isinstance(session, FundRotationStrategySession)

    def test_session_scheduled_dates_are_week_endings_within_bounds(self):
        strategy = CorrelationAllMembersStrategy()
        cfg = CorrelationAllMembersConfig()
        initialization = StrategyInitializationContext(
            run_id="r1",
            evaluation_calendar=(),
        )
        session = strategy.create_session(initialization, cfg)
        calendar = tuple(
            f"2024{month:02d}{day:02d}"
            for month, day in [
                (1, 1),
                (1, 2),
                (1, 3),
                (1, 4),
                (1, 5),
                (1, 8),
                (1, 9),
                (1, 10),
            ]
        )
        dates = session.scheduled_dates(
            calendar,
            "20240102",
            "20240109",
        )
        assert dates == ("20240105",)

    def test_session_evaluate_produces_real_decision(self):
        import numpy as np
        import pandas as pd

        from backtest.fund_rotation.causal_data import CausalDataView
        from backtest.fund_rotation.contracts import StrategyDecisionContext

        strategy = CorrelationAllMembersStrategy()
        cfg = CorrelationAllMembersConfig(
            k=2,
            top_n=1,
            correlation_lookback_weeks=4,
            min_training_weeks=4,
            min_valid_weeks=2,
            min_pairwise_weeks=2,
            recluster_interval_weeks=2,
            momentum_window_weeks=2,
        )
        rng = np.random.default_rng(7)
        start = pd.Timestamp("2024-01-01")
        dates = [
            (start + pd.Timedelta(weeks=week, days=day)).strftime("%Y%m%d")
            for week in range(8)
            for day in range(5)
        ]
        codes = ["510001.SH", "510002.SH", "510003.SH"]
        rows: list[dict] = []
        adjustments: list[dict] = []
        prices = {code: 2.0 + rng.random() for code in codes}
        for date in dates:
            for code in codes:
                prices[code] *= 1 + rng.normal(0.001, 0.02)
                close = round(prices[code], 3)
                rows.append(
                    {
                        "ts_code": code,
                        "trade_date": date,
                        "open": close,
                        "close": close,
                        "high": close,
                        "low": close,
                        "pre_close": close,
                        "vol": 100000,
                        "amount": close * 1_000_000,
                    }
                )
                adjustments.append(
                    {
                        "ts_code": code,
                        "trade_date": date,
                        "adj_factor": 1.0,
                    }
                )
        fund_daily = pd.DataFrame(rows)
        fund_adj = pd.DataFrame(adjustments)
        dim_fund = pd.DataFrame(
            [
                {
                    "ts_code": code,
                    "name": f"测试ETF{index}",
                    "list_date": "20200101",
                }
                for index, code in enumerate(codes)
            ]
        )

        requirements = strategy.resolve_requirements(cfg)
        initialization = StrategyInitializationContext(
            run_id="r1",
            evaluation_calendar=(),
        )
        session = strategy.create_session(initialization, cfg)
        signal_date = dates[24]
        view = CausalDataView(
            fund_daily,
            fund_adj,
            dim_fund,
            requirements,
            pd.Timestamp(signal_date),
            frozenset(codes),
        )
        context = StrategyDecisionContext(
            signal_date=signal_date,
            data_view=view,
        )
        decision = session.evaluate(context)

        assert decision.signal_date == signal_date
        assert decision.decision_id
        assert decision.action is DecisionKind.SET_TARGETS
        total = decision.cash_weight + sum(decision.target_weights.values())
        assert abs(total - 1.0) < 1e-9
        assert set(decision.target_weights).issubset(set(codes))
        diagnostics = session.finalize()
        assert any(
            artifact.role == "cluster_history"
            for artifact in diagnostics.artifacts
        )


class TestLegacyAdapter:
    def test_from_legacy_one_way_conversion(self):
        legacy = FundRotationConfig(
            k=4,
            top_n=2,
            initial_capital=500_000.0,
            start_date="20240101",
            end_date="20241231",
        )
        cfg = CorrelationAllMembersConfig.from_legacy(legacy)
        assert cfg.k == 4
        assert cfg.top_n == 2
        assert (
            cfg.correlation_lookback_weeks
            == legacy.correlation_lookback_weeks
        )
        assert "initial_capital" not in cfg.model_fields
        assert "start_date" not in cfg.model_fields
        assert "end_date" not in cfg.model_fields
        assert not hasattr(cfg, "initial_capital")
