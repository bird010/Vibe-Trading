"""End-to-end pipeline test with synthetic data — §19.5."""

import numpy as np
import pandas as pd
import pytest

from backtest.fund_rotation.config import FundRotationConfig
from backtest.fund_rotation.pipeline import run_signal_pipeline
from backtest.fund_rotation.pit_universe import PITQueryMode
from tests.fund_rotation.conftest import make_test_market_rule_inputs


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


def _run_signal_pipeline(config, fund_daily, fund_adj, dim_fund, **kwargs):
    codes = tuple(sorted(dim_fund["ts_code"].astype(str)))
    rule_resolver, rule_instruments = make_test_market_rule_inputs(codes)
    return run_signal_pipeline(
        config,
        fund_daily,
        fund_adj,
        dim_fund,
        market_rule_resolver=rule_resolver,
        market_rule_instruments=rule_instruments,
        market_rule_mode=PITQueryMode.AS_WAS_KNOWN,
        market_rule_snapshot_version=1,
        **kwargs,
    )


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
        result = _run_signal_pipeline(config, fund_daily, fund_adj, dim_fund)
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
        result = _run_signal_pipeline(config, fund_daily, fund_adj, dim_fund)
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
        r1 = _run_signal_pipeline(config, fund_daily, fund_adj, dim_fund)
        r2 = _run_signal_pipeline(config, fund_daily, fund_adj, dim_fund)
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
        result = _run_signal_pipeline(config, fund_daily, fund_adj, dim_fund)
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
        result = _run_signal_pipeline(config, fund_daily, fund_adj, dim_fund)

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

        def capture_metrics(series, periods_per_year=52, initial_nav=1.0):
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
        result = _run_signal_pipeline(FundRotationConfig(
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
        result = _run_signal_pipeline(FundRotationConfig(
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
        result = _run_signal_pipeline(config, fund_daily, fund_adj, dim_fund)
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
            _run_signal_pipeline(config, fund_daily, fund_adj, dim_fund)

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

        _run_signal_pipeline(
            config, fund_daily, fund_adj, dim_fund,
            stage_callback=stages.append,
        )

        assert stages == [
            "PREPARING_DATA",
            "GENERATING_SIGNALS",
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
        _run_signal_pipeline(config, fund_daily, fund_adj, dim_fund)

        assert calls, "pct_change was not exercised by the pipeline"
        assert all(fm is None for fm in calls), (
            f"every pipeline pct_change must pass fill_method=None, got {calls}"
        )

    def test_correlation_window_is_exactly_lookback_rows(self, monkeypatch):
        """§32.1 — the window fed to correlation has exactly lookback rows and
        ends at the signal week (no future data). Phase 2: signal generation
        moved to the baseline strategy session, so the spy targets the session
        module's call site."""
        windows: list = []
        from backtest.fund_rotation.strategies.correlation_all_members import (
            strategy as strategy_mod,
        )
        orig = strategy_mod.compute_correlation_distance

        def spy(sub_returns, **kwargs):
            windows.append(list(sub_returns.index))
            return orig(sub_returns, **kwargs)

        monkeypatch.setattr(
            "backtest.fund_rotation.strategies.correlation_all_members.strategy."
            "compute_correlation_distance", spy,
        )
        fund_daily, fund_adj, dim_fund = _synthetic_data(n_etfs=10, n_weeks=80)
        config = FundRotationConfig(
            k=3, top_n=2, min_training_weeks=52, correlation_lookback_weeks=52,
            min_valid_weeks=20, min_pairwise_weeks=20, recluster_interval_weeks=26,
            momentum_window_weeks=4,
            start_date="20220101", end_date="20230701",
        )
        _run_signal_pipeline(config, fund_daily, fund_adj, dim_fund)
        assert windows, "correlation distance was not exercised"
        for week_endings in windows:
            assert len(week_endings) == 52, (
                f"correlation window must contain exactly 52 weekly returns, "
                f"got {len(week_endings)}"
            )

    def test_sparse_calendar_first_signal_aligns_with_week_index(self):
        """Holiday-shortened weeks must not shift the first decision date: the
        warmup boundary aligns with ISO week-endings, so the first signal is
        the (min_weeks_needed+1)-th week-ending regardless of per-week
        trading-day counts (§6, sparse calendar)."""
        fund_daily, fund_adj, dim_fund = _synthetic_data(n_etfs=10, n_weeks=60)
        # Simulate a holiday-shortened week (drop its Monday/Tuesday).
        drop_dates = {"20220228", "20220301"}
        fund_daily = fund_daily[~fund_daily["trade_date"].isin(drop_dates)]
        fund_adj = fund_adj[~fund_adj["trade_date"].isin(drop_dates)]
        config = FundRotationConfig(
            k=3, top_n=2, min_training_weeks=20, correlation_lookback_weeks=20,
            min_valid_weeks=10, min_pairwise_weeks=10, recluster_interval_weeks=10,
            momentum_window_weeks=4,
            start_date="20220101", end_date="20230401",
        )
        result = _run_signal_pipeline(config, fund_daily, fund_adj, dim_fund)

        from backtest.fund_rotation.evaluation import iso_week_endings
        endings = iso_week_endings(
            sorted(fund_daily["trade_date"].astype(str).unique())
        )
        # min_weeks_needed = 20 -> first signal at the 21st week-ending.
        assert min(result.weekly_targets) == endings[20]

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
            _run_signal_pipeline(config, fd51, fa51, df51)

        # 52 week-endings -> only 51 valid returns, no complete window -> no signal.
        fd52, fa52, df52 = _synthetic_data(n_etfs=10, n_weeks=52)
        r52 = _run_signal_pipeline(config, fd52, fa52, df52)
        assert len(r52.weekly_targets) == 0

        # 54 week-endings -> the first signal lands on the 53rd week-ending
        # (20230106); the 54th week provides a next trading day so the signal
        # executes. (Exactly 53 weeks cannot run end-to-end: the first signal
        # would sit on the last week-ending with no execution day — §32.1.)
        fd54, fa54, df54 = _synthetic_data(n_etfs=10, n_weeks=54)
        r54 = _run_signal_pipeline(config, fd54, fa54, df54)
        assert len(r54.weekly_targets) >= 1
        assert min(r54.weekly_targets) == "20230106"  # 53rd week-ending

    def test_pre_evaluation_target_executes_at_first_evaluation_day(self):
        """§24 — a target dated before start_date is preserved and builds the
        initial position at the first evaluation trading day (not discarded)."""
        fund_daily, fund_adj, dim_fund = _synthetic_data(n_etfs=10, n_weeks=80)
        # First signal week is 20220527 (Friday); the Monday after is 20220530.
        # Setting start_date=20220530 makes 20220527 a pre-evaluation target.
        config = FundRotationConfig(
            k=3, top_n=2, min_training_weeks=20, correlation_lookback_weeks=20,
            min_valid_weeks=10, min_pairwise_weeks=10, recluster_interval_weeks=10,
            momentum_window_weeks=4,
            start_date="20220530", end_date="20230701",
        )
        result = _run_signal_pipeline(config, fund_daily, fund_adj, dim_fund)

        # Output signals are trimmed to >= start_date (pre-eval signal excluded).
        assert "20220527" not in result.weekly_targets
        assert min(result.weekly_targets) == "20220603"

        # But execution preserved the pre-evaluation target: the first order
        # lands on the first evaluation trading day (20220530), NOT on the first
        # in-evaluation signal's execution day (which would be 20220606).
        order_dates = sorted(o["trade_date"] for o in result.orders if o["trade_date"])
        assert order_dates[0] == "20220530"

        # The pre-evaluation order targets the 20220527 signal's ETFs.
        first_orders = [o for o in result.orders if o["trade_date"] == "20220530"]
        assert first_orders, "expected orders on the first evaluation day"
        assert all(o["trade_date"] == "20220530" for o in first_orders)
        # The initial position is built via BUY orders at the first eval day open.
        assert any(o["direction"] == "BUY" for o in first_orders)

    def test_three_path_first_execution_day_consistency(self):
        """§24/step 4 — the real executor schedules the same pre-evaluation
        target to the same first evaluation day produced by the shared
        schedule_targets (the ideal executor's identical scheduling is covered
        by test_ideal_executor.py)."""
        from backtest.fund_rotation.evaluation import TargetSnapshot, schedule_targets
        fund_daily, fund_adj, dim_fund = _synthetic_data(n_etfs=10, n_weeks=80)
        base = dict(k=3, top_n=2, min_training_weeks=20, correlation_lookback_weeks=20,
                    min_valid_weeks=10, min_pairwise_weeks=10, recluster_interval_weeks=10,
                    momentum_window_weeks=4)
        # Recover the pre-evaluation target (week 20220527) via an early start.
        early = _run_signal_pipeline(
            FundRotationConfig(**base, start_date="20220101", end_date="20230701"),
            fund_daily, fund_adj, dim_fund,
        )
        pre_eval_target = early.weekly_targets["20220527"]

        # Canonical schedule from the shared function + evaluation calendar.
        eval_dates = [pd.Timestamp(d) for d in sorted(
            d for d in fund_daily["trade_date"].astype(str).unique()
            if "20220530" <= d <= "20230701"
        )]
        schedule = schedule_targets(
            [TargetSnapshot(pd.Timestamp("20220527"), pre_eval_target)], eval_dates,
        )
        canonical_first = min(schedule).strftime("%Y%m%d")
        assert canonical_first == "20220530"

        # Real executor's first order matches the canonical schedule.
        result = _run_signal_pipeline(
            FundRotationConfig(**base, start_date="20220530", end_date="20230701"),
            fund_daily, fund_adj, dim_fund,
        )
        real_first = min(o["trade_date"] for o in result.orders if o["trade_date"])
        assert real_first == canonical_first

    def test_no_pre_evaluation_target_first_execution_at_first_signal(self):
        """With start_date before the first signal, the first execution is the
        first trading day after the first in-evaluation signal (20220527 ->
        20220530), i.e. the pre-evaluation path is not triggered."""
        fund_daily, fund_adj, dim_fund = _synthetic_data(n_etfs=10, n_weeks=80)
        config = FundRotationConfig(
            k=3, top_n=2, min_training_weeks=20, correlation_lookback_weeks=20,
            min_valid_weeks=10, min_pairwise_weeks=10, recluster_interval_weeks=10,
            momentum_window_weeks=4,
            start_date="20220101", end_date="20230701",
        )
        result = _run_signal_pipeline(config, fund_daily, fund_adj, dim_fund)
        # First signal 20220527 is in-evaluation; executes next trading day.
        assert min(result.weekly_targets) == "20220527"
        order_dates = sorted(o["trade_date"] for o in result.orders if o["trade_date"])
        assert order_dates[0] == "20220530"

    def test_no_targets_produces_cash_nav_over_full_evaluation_interval(self):
        """§32.1 — with no targets the execution loop still emits a cash NAV
        (initial NAV) spanning the full evaluation calendar, not an empty
        series."""
        from backtest.fund_rotation.pipeline import (
            PipelineResult, _build_execution_context, _run_execution_loop,
        )
        fund_daily, fund_adj, _ = _synthetic_data(n_etfs=10, n_weeks=80)
        config = FundRotationConfig(
            k=3, top_n=2, min_training_weeks=20, correlation_lookback_weeks=20,
            min_valid_weeks=10, min_pairwise_weeks=10, recluster_interval_weeks=10,
            momentum_window_weeks=4,
            start_date="20220101", end_date="20230701",
        )
        ctx = _build_execution_context(fund_daily, fund_adj, config)
        eval_dates = [d for d in ctx.all_trade_dates if "20220101" <= d <= "20230701"]
        result = PipelineResult()
        _run_execution_loop(result, config, ctx, execution_targets={},
                            evaluation_dates=eval_dates)
        assert len(result.executed_equity) == len(eval_dates)
        assert (result.executed_equity == 1.0).all()

    def test_all_blocked_produces_cash_nav_over_full_interval(self):
        """§32.1 — when every increase is blocked (insufficient cash after
        commission), the NAV stays cash over the full evaluation interval."""
        from backtest.fund_rotation.pipeline import (
            PipelineResult, _build_execution_context, _run_execution_loop,
        )
        fund_daily, fund_adj, _ = _synthetic_data(n_etfs=10, n_weeks=80)
        # Tiny capital: target size rounds below one lot (100 shares), so every
        # buy is blocked for insufficient cash after commission/lot.
        config = FundRotationConfig(
            k=3, top_n=2, min_training_weeks=20, correlation_lookback_weeks=20,
            min_valid_weeks=10, min_pairwise_weeks=10, recluster_interval_weeks=10,
            momentum_window_weeks=4,
            initial_capital=10.0, commission_min=5.0,
            start_date="20220101", end_date="20230701",
        )
        ctx = _build_execution_context(fund_daily, fund_adj, config)
        eval_dates = [d for d in ctx.all_trade_dates if "20220101" <= d <= "20230701"]
        targets = {"20220603": {"510300.SH": 1.0}}
        result = PipelineResult(weekly_targets=dict(targets))
        _run_execution_loop(result, config, ctx, execution_targets=dict(targets),
                            evaluation_dates=eval_dates)
        # Every buy attempt is blocked for insufficient cash.
        buy_events = [e for e in result.trade_events if e.get("action") == "BUY"]
        assert buy_events, "expected buy attempts"
        assert all(e["status"] == "BLOCKED" for e in buy_events)
        assert all("insufficient_cash" in e["reason"] for e in buy_events)
        # Cash NAV over the full evaluation interval.
        assert len(result.executed_equity) == len(eval_dates)
        assert (result.executed_equity == 1.0).all()
