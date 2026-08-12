from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backtest.fund_rotation.config import FundRotationConfig
from backtest.fund_rotation.evaluation import EvaluationContext
from backtest.fund_rotation.runner import (
    CancellationToken,
    ExecutionConfig,
    FundRotationBacktestRunner,
    SubRunStatus,
)
from backtest.fund_rotation.signal_portfolio_risk import (
    run_decision_pipeline as real_run_decision_pipeline,
)
from backtest.fund_rotation.strategies.correlation_all_members.config import (
    CorrelationAllMembersConfig,
)
from backtest.fund_rotation.strategies.correlation_all_members.strategy import (
    CorrelationAllMembersStrategy,
)
from src.stockpred.fund_rotation.data_snapshot import PinnedFundDataSnapshot


def _synthetic_data(
    n_etfs: int = 10,
    n_weeks: int = 80,
    seed: int = 42,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    rng = np.random.default_rng(seed)
    start = pd.Timestamp("2022-01-07")
    weeks = [start + pd.Timedelta(weeks=i) for i in range(n_weeks)]
    dates = [
        (week - pd.Timedelta(days=4) + pd.Timedelta(days=offset)).strftime("%Y%m%d")
        for week in weeks
        for offset in range(5)
    ]
    codes = ["510300.SH"] + [f"{510000 + i * 10}.SH" for i in range(1, n_etfs)]

    prices = {code: 3.0 + rng.random() for code in codes}
    rows: list[dict[str, object]] = []
    for date in dates:
        for code in codes:
            prices[code] *= 1 + rng.normal(0.001, 0.02)
            close = round(prices[code], 3)
            rows.append(
                {
                    "ts_code": code,
                    "trade_date": date,
                    "open": close,
                    "high": round(close * 1.01, 3),
                    "low": round(close * 0.99, 3),
                    "close": close,
                    "pre_close": close,
                    "vol": int(rng.integers(100000, 5000000)),
                    "amount": round(prices[code] * rng.integers(100000, 5000000), 2),
                }
            )

    fund_daily = pd.DataFrame(rows)
    fund_adj = pd.DataFrame(
        {"ts_code": code, "trade_date": date, "adj_factor": 1.0}
        for date in dates
        for code in codes
    )
    dim_fund = pd.DataFrame(
        {"ts_code": code, "name": f"测试ETF{index}", "list_date": "20200101"}
        for index, code in enumerate(codes)
    )
    return fund_daily, fund_adj, dim_fund


def test_correlation_all_members_strategy_routes_decisions_through_unified_stages(monkeypatch):
    from backtest.fund_rotation.strategies.correlation_all_members import strategy as strategy_mod

    calls: list[dict[str, object]] = []

    def spy_run_decision_pipeline(**kwargs):
        calls.append(kwargs)
        return real_run_decision_pipeline(**kwargs)

    monkeypatch.setattr(strategy_mod, "run_decision_pipeline", spy_run_decision_pipeline, raising=False)

    fund_daily, fund_adj, dim_fund = _synthetic_data()
    all_trade_dates = tuple(sorted(fund_daily["trade_date"].astype(str).unique()))
    config = CorrelationAllMembersConfig.from_legacy(
        FundRotationConfig(
            k=3,
            top_n=2,
            min_training_weeks=20,
            correlation_lookback_weeks=20,
            min_valid_weeks=10,
            min_pairwise_weeks=10,
            recluster_interval_weeks=10,
            momentum_window_weeks=4,
            start_date="20220101",
            end_date="20230701",
        )
    )
    result = FundRotationBacktestRunner(fund_daily, fund_adj, dim_fund).run(
        strategy=CorrelationAllMembersStrategy(),
        config=config,
        snapshot=PinnedFundDataSnapshot(
            fund_version=0,
            fund_adj_version=0,
            dim_version=0,
            universe_codes=tuple(sorted(dim_fund["ts_code"].astype(str))),
            trading_dates=all_trade_dates,
            fingerprint="signal-portfolio-risk-integration",
        ),
        evaluation=EvaluationContext.from_range(
            all_trade_dates,
            "20220101",
            "20230701",
        ),
        execution=ExecutionConfig(),
        cancellation=CancellationToken(),
    )

    assert result.status is SubRunStatus.SUCCEEDED
    assert result.weekly_targets
    assert calls, "CorrelationAllMembersStrategy must call the unified signal/portfolio/risk pipeline"

    first_call = calls[0]
    assert set(first_call) >= {
        "raw_signal_scores",
        "coverage_available",
        "representatives",
        "asset_metadata",
        "selection_policy",
        "portfolio_policy",
        "risk_policy",
        "policy_versions",
    }

    decision = next(d for d in result.decisions if d.signal_date in result.weekly_targets)
    stage_records = decision.diagnostics["signal_pipeline_stage_records"]
    assert [record["stage"] for record in stage_records] == [
        "raw_signal_scores",
        "coverage_filtered_scores",
        "selected_clusters",
        "selected_representatives",
        "raw_portfolio_weights",
        "risk_scaled_weights",
        "execution_targets",
    ]
    assert stage_records[-1]["output"]["weights"] == result.weekly_targets[decision.signal_date]
    assert "signal_pipeline_reason_codes" in decision.diagnostics
