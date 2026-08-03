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
    def test_defaults_mirror_legacy(self):
        cfg = CorrelationAllMembersConfig()
        legacy = FundRotationConfig()
        for field in CorrelationAllMembersConfig.model_fields:
            assert getattr(cfg, field) == getattr(legacy, field), field

    def test_unknown_field_rejected(self):
        with pytest.raises(ValidationError):
            CorrelationAllMembersConfig(not_a_field=1)

    def test_range_violations_rejected(self):
        with pytest.raises(ValidationError):
            CorrelationAllMembersConfig(k=0)
        with pytest.raises(ValidationError):
            CorrelationAllMembersConfig(initial_capital=0)
        with pytest.raises(ValidationError):
            CorrelationAllMembersConfig(momentum_window_weeks=0)

    def test_top_n_must_not_exceed_k(self):
        with pytest.raises(ValidationError, match="top_n"):
            CorrelationAllMembersConfig(k=3, top_n=5)

    def test_min_pairwise_must_not_exceed_lookback(self):
        with pytest.raises(ValidationError, match="min_pairwise_weeks"):
            CorrelationAllMembersConfig(correlation_lookback_weeks=20, min_pairwise_weeks=30)

    def test_momentum_window_must_be_subset_of_lookback(self):
        # momentum_window_weeks must stay strictly inside the correlation
        # window so the session parity domain is well-defined (Phase 2 Task 4).
        with pytest.raises(ValidationError, match="momentum_window_weeks"):
            CorrelationAllMembersConfig(
                correlation_lookback_weeks=20, momentum_window_weeks=20,
            )
        with pytest.raises(ValidationError, match="momentum_window_weeks"):
            CorrelationAllMembersConfig(
                correlation_lookback_weeks=20, momentum_window_weeks=30,
            )

    def test_start_date_must_not_exceed_end_date(self):
        with pytest.raises(ValidationError, match="start_date"):
            CorrelationAllMembersConfig(start_date="20230101", end_date="20220101")

    def test_config_is_immutable(self):
        cfg = CorrelationAllMembersConfig()
        with pytest.raises(ValidationError):
            cfg.k = 5  # frozen

    def test_old_json_mapping_round_trip(self):
        cfg = CorrelationAllMembersConfig(k=4, top_n=2, correlation_lookback_weeks=30)
        dumped = cfg.model_dump(mode="json")
        restored = CorrelationAllMembersConfig.model_validate(dumped)
        assert restored == cfg


class TestJsonSchema:
    def test_schema_has_title_type_defaults_bounds_descriptions(self):
        schema = CorrelationAllMembersConfig.model_json_schema()
        assert schema["title"] == "CorrelationAllMembersConfig"
        assert schema["type"] == "object"
        props = schema["properties"]
        # bounds present
        assert props["k"]["minimum"] == 1
        assert props["initial_capital"]["exclusiveMinimum"] == 0
        # defaults present
        assert props["k"]["default"] == 8
        assert props["top_n"]["default"] == 3
        # Chinese descriptions present
        assert "聚类簇数量" in props["k"]["description"]
        assert "初始资金" in props["initial_capital"]["description"]


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
                correlation_lookback_weeks=20, min_training_weeks=20,
                momentum_window_weeks=4,
            )
        )
        req_large = strategy.resolve_requirements(
            CorrelationAllMembersConfig(
                correlation_lookback_weeks=52, min_training_weeks=52,
                momentum_window_weeks=4,
            )
        )
        assert req_large.warmup_trade_days > req_small.warmup_trade_days
        # Warmup = one full window of weekly returns: (N+1) week-endings.
        assert req_small.warmup_trade_days == (20 + 1) * 5 - 1
        assert req_large.warmup_trade_days == (52 + 1) * 5 - 1
        assert "fund" in req_small.required_datasets
        assert req_small.needs_benchmark is True

    def test_create_session_returns_session_protocol(self):
        strategy = CorrelationAllMembersStrategy()
        cfg = CorrelationAllMembersConfig()
        init = StrategyInitializationContext(
            run_id="r1", evaluation_calendar=("20240101", "20240102"),
        )
        session = strategy.create_session(init, cfg)
        assert isinstance(session, FundRotationStrategySession)

    def test_session_scheduled_dates_are_week_endings_within_bounds(self):
        strategy = CorrelationAllMembersStrategy()
        cfg = CorrelationAllMembersConfig()
        init = StrategyInitializationContext(run_id="r1", evaluation_calendar=())
        session = strategy.create_session(init, cfg)
        # Two full ISO weeks of trading days (Mon-Fri).
        calendar = tuple(f"2024{m:02d}{d:02d}" for m, d in [
            (1, 1), (1, 2), (1, 3), (1, 4), (1, 5),
            (1, 8), (1, 9), (1, 10),
        ])
        dates = session.scheduled_dates(calendar, "20240102", "20240109")
        # ISO week-endings: Friday 20240105 in range; 20240110 past eval end.
        assert dates == ("20240105",)

    def test_session_evaluate_produces_real_decision(self):
        import numpy as np
        import pandas as pd
        from backtest.fund_rotation.causal_data import CausalDataView
        from backtest.fund_rotation.contracts import StrategyDecisionContext

        strategy = CorrelationAllMembersStrategy()
        cfg = CorrelationAllMembersConfig(
            k=2, top_n=1,
            correlation_lookback_weeks=4, min_training_weeks=4,
            min_valid_weeks=2, min_pairwise_weeks=2,
            recluster_interval_weeks=2, momentum_window_weeks=2,
        )
        # Eight Mon-Fri weeks for three ETFs.
        rng = np.random.default_rng(7)
        start = pd.Timestamp("2024-01-01")  # a Monday
        dates = [
            (start + pd.Timedelta(weeks=w, days=d)).strftime("%Y%m%d")
            for w in range(8) for d in range(5)
        ]
        codes = ["510001.SH", "510002.SH", "510003.SH"]
        rows, adj = [], []
        prices = {c: 2.0 + rng.random() for c in codes}
        for d in dates:
            for c in codes:
                prices[c] *= 1 + rng.normal(0.001, 0.02)
                close = round(prices[c], 3)
                rows.append({
                    "ts_code": c, "trade_date": d, "open": close,
                    "close": close, "high": close, "low": close,
                    "pre_close": close, "vol": 100000,
                    "amount": close * 1_000_000,
                })
                adj.append({"ts_code": c, "trade_date": d, "adj_factor": 1.0})
        fund_daily = pd.DataFrame(rows)
        fund_adj = pd.DataFrame(adj)
        dim_fund = pd.DataFrame([
            {"ts_code": c, "name": f"测试ETF{i}", "list_date": "20200101"}
            for i, c in enumerate(codes)
        ])

        requirements = strategy.resolve_requirements(cfg)
        init = StrategyInitializationContext(run_id="r1", evaluation_calendar=())
        session = strategy.create_session(init, cfg)
        # warmup = (4+1)*5 - 1 = 24 -> first signal = dates[24] (week-4 Friday)
        signal_date = dates[24]
        view = CausalDataView(
            fund_daily, fund_adj, dim_fund, requirements,
            pd.Timestamp(signal_date), frozenset(codes),
        )
        ctx = StrategyDecisionContext(signal_date=signal_date, data_view=view)
        decision = session.evaluate(ctx)

        assert decision.signal_date == signal_date
        assert decision.decision_id
        assert decision.action is DecisionKind.SET_TARGETS
        total = decision.cash_weight + sum(decision.target_weights.values())
        assert abs(total - 1.0) < 1e-9
        assert set(decision.target_weights).issubset(set(codes))
        diag = session.finalize()
        assert any(a.role == "cluster_history" for a in diag.artifacts)


class TestLegacyAdapter:
    def test_from_legacy_one_way_conversion(self):
        legacy = FundRotationConfig(k=4, top_n=2, initial_capital=500_000.0)
        cfg = CorrelationAllMembersConfig.from_legacy(legacy)
        assert cfg.k == 4
        assert cfg.top_n == 2
        assert cfg.initial_capital == 500_000.0
        # Unspecified fields fall back to defaults (== legacy defaults).
        assert cfg.correlation_lookback_weeks == legacy.correlation_lookback_weeks
