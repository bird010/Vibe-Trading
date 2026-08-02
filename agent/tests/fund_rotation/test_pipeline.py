"""End-to-end pipeline test with synthetic data — §19.5."""

import numpy as np
import pandas as pd
import pytest

from backtest.fund_rotation.config import FundRotationConfig
from backtest.fund_rotation.pipeline import run_signal_pipeline


def _synthetic_data(
    n_etfs: int = 10,
    n_weeks: int = 80,
    seed: int = 42,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Generate synthetic fund_daily, fund_adj, dim_fund for testing."""
    rng = np.random.default_rng(seed)

    # Generate weekly dates (Fridays)
    start = pd.Timestamp("2022-01-07")
    weeks = [start + pd.Timedelta(weeks=i) for i in range(n_weeks)]
    # Expand to daily (Mon-Fri) for each week
    dates = []
    for w in weeks:
        for offset in range(5):  # Mon-Fri
            d = w - pd.Timedelta(days=4) + pd.Timedelta(days=offset)
            dates.append(d.strftime("%Y%m%d"))

    codes = ["510300.SH"] + [f"{510000 + i * 10}.SH" for i in range(1, n_etfs)]

    # Build fund_daily
    rows = []
    prices = {c: 3.0 + rng.random() for c in codes}
    for d in dates:
        for c in codes:
            ret = rng.normal(0.001, 0.02)
            prices[c] *= (1 + ret)
            close = round(prices[c], 3)
            rows.append({
                "ts_code": c,
                "trade_date": d,
                "open": close,
                "high": round(close * 1.01, 3),
                "low": round(close * 0.99, 3),
                "close": close,
                "pre_close": close,
                "vol": int(rng.integers(100000, 5000000)),
                "amount": round(prices[c] * rng.integers(100000, 5000000), 2),
            })
    fund_daily = pd.DataFrame(rows)

    # Build fund_adj (constant factor = 1.0 for simplicity)
    adj_rows = [{"ts_code": c, "trade_date": d, "adj_factor": 1.0}
                for d in dates for c in codes]
    fund_adj = pd.DataFrame(adj_rows)

    # Build dim_fund
    dim_rows = [{"ts_code": c, "name": f"测试ETF{i}", "list_date": "20200101"}
                for i, c in enumerate(codes)]
    dim_fund = pd.DataFrame(dim_rows)

    return fund_daily, fund_adj, dim_fund


class TestPipelineE2E:
    """§19.5 — synthetic data gold-standard test."""

    def test_produces_targets(self):
        fund_daily, fund_adj, dim_fund = _synthetic_data(n_etfs=10, n_weeks=80)
        config = FundRotationConfig(
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
        result = run_signal_pipeline(config, fund_daily, fund_adj, dim_fund)
        # Should produce some target weeks
        assert len(result.weekly_targets) > 0
        assert result.num_reclusters >= 1

    def test_targets_sum_leq_one(self):
        fund_daily, fund_adj, dim_fund = _synthetic_data(n_etfs=10, n_weeks=80)
        config = FundRotationConfig(
            k=3, top_n=2, min_training_weeks=20,
            correlation_lookback_weeks=20, min_valid_weeks=10,
            min_pairwise_weeks=10, recluster_interval_weeks=10,
            momentum_window_weeks=4,
            start_date="20220101", end_date="20230701",
        )
        result = run_signal_pipeline(config, fund_daily, fund_adj, dim_fund)
        for week, targets in result.weekly_targets.items():
            total = sum(targets.values())
            assert total <= 1.0 + 1e-9, f"Week {week}: total weight {total} > 1"

    def test_deterministic(self):
        """Same input -> same output."""
        fund_daily, fund_adj, dim_fund = _synthetic_data(n_etfs=10, n_weeks=80)
        config = FundRotationConfig(
            k=3, top_n=2, min_training_weeks=20,
            correlation_lookback_weeks=20, min_valid_weeks=10,
            min_pairwise_weeks=10, recluster_interval_weeks=10,
            momentum_window_weeks=4,
            start_date="20220101", end_date="20230701",
        )
        r1 = run_signal_pipeline(config, fund_daily, fund_adj, dim_fund)
        r2 = run_signal_pipeline(config, fund_daily, fund_adj, dim_fund)
        assert r1.weekly_targets == r2.weekly_targets
        assert r1.num_reclusters == r2.num_reclusters

    def test_benchmarks_populated(self):
        fund_daily, fund_adj, dim_fund = _synthetic_data(n_etfs=10, n_weeks=80)
        config = FundRotationConfig(
            k=3, top_n=2, min_training_weeks=20,
            correlation_lookback_weeks=20, min_valid_weeks=10,
            min_pairwise_weeks=10, recluster_interval_weeks=10,
            momentum_window_weeks=4,
            start_date="20220101", end_date="20230701",
        )
        result = run_signal_pipeline(config, fund_daily, fund_adj, dim_fund)
        assert not result.equal_weight_benchmark.empty
        assert not result.buy_hold_benchmark.empty
        assert not result.cash_benchmark.empty

    def test_benchmarks_use_daily_executed_account(self):
        fund_daily, fund_adj, dim_fund = _synthetic_data(n_etfs=10, n_weeks=80)
        config = FundRotationConfig(
            k=3, top_n=2, min_training_weeks=20,
            correlation_lookback_weeks=20, min_valid_weeks=10,
            min_pairwise_weeks=10, recluster_interval_weeks=10,
            momentum_window_weeks=4, initial_capital=100_000,
            start_date="20220101", end_date="20230701",
        )
        result = run_signal_pipeline(config, fund_daily, fund_adj, dim_fund)

        # buy_hold still uses execution engine (daily, with entry costs)
        assert len(result.buy_hold_benchmark) > len(result.weekly_targets)
        assert set(result.buy_hold_benchmark.index).issubset(set(result.executed_equity.index))
        assert result.buy_hold_benchmark.iloc[0] < 1.0  # entry costs are in account NAV

        # equal_weight is now a theoretical index (no entry costs, NAV starts at 1.0)
        assert not result.equal_weight_benchmark.empty
        assert result.equal_weight_benchmark.iloc[0] == pytest.approx(1.0)
        assert set(result.equal_weight_benchmark.index) == set(result.executed_equity.index)

    def test_common_interval_drives_all_curves_metrics_and_bootstrap(self, monkeypatch):
        fund_daily, fund_adj, dim_fund = _synthetic_data(n_etfs=10, n_weeks=80)
        metric_indexes = []
        bootstrap_indexes = []

        def capture_metrics(series, periods_per_year=52):
            metric_indexes.append((series.index.copy(), periods_per_year))
            return {"annual_return": 0.0, "max_drawdown": 0.0}

        def capture_bootstrap(returns, **_kwargs):
            bootstrap_indexes.append(returns.index.copy())
            return {"n_bootstrap": 1}

        monkeypatch.setattr(
            "backtest.fund_rotation.pipeline.compute_performance_metrics",
            capture_metrics,
        )
        monkeypatch.setattr(
            "backtest.fund_rotation.pipeline.time_block_bootstrap",
            capture_bootstrap,
        )
        result = run_signal_pipeline(FundRotationConfig(
            k=3, top_n=2, min_training_weeks=20,
            correlation_lookback_weeks=20, min_valid_weeks=10,
            min_pairwise_weeks=10, recluster_interval_weeks=10,
            momentum_window_weeks=4,
            start_date="20220101", end_date="20230701",
        ), fund_daily, fund_adj, dim_fund)

        common = result.executed_equity.index
        assert common.equals(result.strategy_cumulative.index)
        assert common.equals(result.buy_hold_benchmark.index)
        assert common.equals(result.equal_weight_benchmark.index)
        assert common.equals(result.cash_benchmark.index)
        assert len(metric_indexes) == 4
        assert all(index.equals(common) and periods == 244 for index, periods in metric_indexes)
        assert len(bootstrap_indexes) == 1
        assert bootstrap_indexes[0].equals(common[1:])

    def test_short_common_interval_reports_structured_bootstrap_skip(self):
        fund_daily, fund_adj, dim_fund = _synthetic_data(n_etfs=10, n_weeks=24)
        result = run_signal_pipeline(FundRotationConfig(
            k=3, top_n=2, min_training_weeks=20,
            correlation_lookback_weeks=20, min_valid_weeks=10,
            min_pairwise_weeks=10, recluster_interval_weeks=10,
            momentum_window_weeks=4,
            start_date="20220101", end_date="20221231",
        ), fund_daily, fund_adj, dim_fund)

        diagnostic = result.robustness["bootstrap"]
        assert diagnostic == {
            "status": "SKIPPED",
            "reason": "insufficient_common_interval",
            "observations": len(result.strategy_cumulative) - 1,
            "minimum_observations": 24,
        }

    def test_strategy_metrics_populated(self):
        fund_daily, fund_adj, dim_fund = _synthetic_data(n_etfs=10, n_weeks=80)
        config = FundRotationConfig(
            k=3, top_n=2, min_training_weeks=20,
            correlation_lookback_weeks=20, min_valid_weeks=10,
            min_pairwise_weeks=10, recluster_interval_weeks=10,
            momentum_window_weeks=4,
            start_date="20220101", end_date="20230701",
        )
        result = run_signal_pipeline(config, fund_daily, fund_adj, dim_fund)
        if result.weekly_targets:
            assert "annual_return" in result.strategy_metrics
            assert "max_drawdown" in result.strategy_metrics

    def test_insufficient_data_raises(self):
        """Too few weeks -> task fails per §18.1."""
        fund_daily, fund_adj, dim_fund = _synthetic_data(n_etfs=10, n_weeks=5)
        config = FundRotationConfig(
            k=3, top_n=2, min_training_weeks=20,
            correlation_lookback_weeks=20, min_valid_weeks=10,
            min_pairwise_weeks=10,
            start_date="20220101", end_date="20220201",
        )
        with pytest.raises(ValueError, match="Insufficient history"):
            run_signal_pipeline(config, fund_daily, fund_adj, dim_fund)

    def test_stage_callbacks_follow_state_machine_order(self):
        """Pipeline stages must be emitted in the persisted state-machine order."""
        fund_daily, fund_adj, dim_fund = _synthetic_data(n_etfs=10, n_weeks=80)
        config = FundRotationConfig(
            k=3, top_n=2, min_training_weeks=20,
            correlation_lookback_weeks=20, min_valid_weeks=10,
            min_pairwise_weeks=10, recluster_interval_weeks=10,
            momentum_window_weeks=4,
            start_date="20220101", end_date="20230701",
        )
        stages: list[str] = []

        run_signal_pipeline(
            config, fund_daily, fund_adj, dim_fund,
            stage_callback=stages.append,
        )

        assert stages == [
            "PREPARING_RETURNS",
            "CLUSTERING",
            "GENERATING_TARGETS",
            "EXECUTING",
            "COMPUTING_BENCHMARKS",
        ]

    def test_full_pipeline_pct_change_uses_fill_method_none(self, monkeypatch):
        """§6/§32.1 — every pct_change across the fund-rotation path (returns,
        metrics, robustness) must explicitly pass fill_method=None."""
        calls: list = []
        orig_series = pd.Series.pct_change
        orig_df = pd.DataFrame.pct_change

        def series_spy(self, *args, **kwargs):
            calls.append(kwargs.get("fill_method", "ABSENT"))
            return orig_series(self, *args, **kwargs)

        def df_spy(self, *args, **kwargs):
            calls.append(kwargs.get("fill_method", "ABSENT"))
            return orig_df(self, *args, **kwargs)

        monkeypatch.setattr(pd.Series, "pct_change", series_spy)
        monkeypatch.setattr(pd.DataFrame, "pct_change", df_spy)

        fund_daily, fund_adj, dim_fund = _synthetic_data(n_etfs=10, n_weeks=80)
        config = FundRotationConfig(
            k=3, top_n=2, min_training_weeks=20,
            correlation_lookback_weeks=20, min_valid_weeks=10,
            min_pairwise_weeks=10, recluster_interval_weeks=10,
            momentum_window_weeks=4,
            start_date="20220101", end_date="20230701",
        )
        run_signal_pipeline(config, fund_daily, fund_adj, dim_fund)

        assert calls, "pct_change was not exercised by the pipeline"
        assert all(fm is None for fm in calls), (
            f"every pipeline pct_change must pass fill_method=None, got {calls}"
        )

    def test_correlation_window_is_exactly_lookback_rows(self, monkeypatch):
        """§32.1 — the window fed to correlation has exactly lookback rows and
        ends at the signal week (no future data)."""
        windows: list = []
        from backtest.fund_rotation import pipeline as pipeline_mod
        orig = pipeline_mod.compute_correlation_distance

        def spy(sub_returns, **kwargs):
            windows.append(list(sub_returns.index))
            return orig(sub_returns, **kwargs)

        monkeypatch.setattr(
            "backtest.fund_rotation.pipeline.compute_correlation_distance", spy,
        )
        fund_daily, fund_adj, dim_fund = _synthetic_data(n_etfs=10, n_weeks=80)
        config = FundRotationConfig(
            k=3, top_n=2, min_training_weeks=52, correlation_lookback_weeks=52,
            min_valid_weeks=20, min_pairwise_weeks=20, recluster_interval_weeks=26,
            momentum_window_weeks=4,
            start_date="20220101", end_date="20230701",
        )
        run_signal_pipeline(config, fund_daily, fund_adj, dim_fund)
        assert windows, "correlation distance was not exercised"
        for week_endings in windows:
            assert len(week_endings) == 52, (
                f"correlation window must contain exactly 52 weekly returns, "
                f"got {len(week_endings)}"
            )

    def test_52_week_boundary_first_signal_and_insufficient(self):
        """§32.1 — 52 week-endings cannot form a complete 52-return window (no
        signal, not an error); 53 week-endings produce the first signal; fewer
        than 52 raises a defined insufficient-history error (no silent window
        shortening)."""
        config = FundRotationConfig(
            k=3, top_n=2, min_training_weeks=52, correlation_lookback_weeks=52,
            min_valid_weeks=20, min_pairwise_weeks=20, recluster_interval_weeks=26,
            momentum_window_weeks=4,
            start_date="20220101", end_date="20230701",
        )

        # 51 week-endings -> insufficient history (defined failure).
        fd51, fa51, df51 = _synthetic_data(n_etfs=10, n_weeks=51)
        with pytest.raises(ValueError, match="Insufficient history"):
            run_signal_pipeline(config, fd51, fa51, df51)

        # 52 week-endings -> only 51 valid returns, no complete window -> no signal.
        fd52, fa52, df52 = _synthetic_data(n_etfs=10, n_weeks=52)
        r52 = run_signal_pipeline(config, fd52, fa52, df52)
        assert len(r52.weekly_targets) == 0

        # 54 week-endings -> the first signal lands on the 53rd week-ending
        # (20230106); the 54th week provides a next trading day so the signal
        # executes. (Exactly 53 weeks cannot run end-to-end: the first signal
        # would sit on the last week-ending with no execution day — §32.1.)
        fd54, fa54, df54 = _synthetic_data(n_etfs=10, n_weeks=54)
        r54 = run_signal_pipeline(config, fd54, fa54, df54)
        assert len(r54.weekly_targets) >= 1
        assert min(r54.weekly_targets) == "20230106"  # 53rd week-ending
