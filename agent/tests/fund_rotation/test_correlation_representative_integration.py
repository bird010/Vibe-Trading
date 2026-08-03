"""Phase 3 Task 6 — correlation_representative integration tests.

Two levels:

1. Synthetic end-to-end through the strategy-neutral Runner: holdings never
   exceed the number of selected clusters, at most one ETF per cluster, and
   fills respect ETF lot size (100) and ADV20 participation capacity.
2. Read-only research smoke on the local data snapshot recorded by run
   ``bac86bdddcf85601`` (pinned Lance versions; the run directory itself is
   never used as a data source). The comparison against the baseline
   all-members strategy is diagnostic only — higher returns are NOT an
   acceptance condition (§acceptance: causality, tradability,
   explainability).
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from backtest.fund_rotation.catalog import FundRotationStrategyCatalog
from backtest.fund_rotation.strategies.registry import (
    default_fund_rotation_strategies,
)
from backtest.fund_rotation.evaluation import EvaluationContext
from backtest.fund_rotation.runner import (
    CancellationToken,
    ExecutionConfig,
    FundRotationBacktestRunner,
    SubRunStatus,
)
from backtest.fund_rotation.universe import filter_etf_universe
from src.stockpred.fund_rotation.data_snapshot import PinnedFundDataSnapshot


# ── synthetic end-to-end ──

def _synthetic_frames(n_weeks: int = 80, seed: int = 99):
    """Three correlated blocks of three ETFs with positive drift."""
    rng = np.random.default_rng(seed)
    start = pd.Timestamp("2022-01-03")  # a Monday
    dates = [
        (start + pd.Timedelta(weeks=w, days=d)).strftime("%Y%m%d")
        for w in range(n_weeks) for d in range(5)
    ]
    blocks = {"b1": ["E1", "E2", "E3"], "b2": ["E4", "E5", "E6"],
              "b3": ["E7", "E8", "E9"]}
    weekly_factors = {name: rng.normal(0.01, 0.02, n_weeks) for name in blocks}
    daily_factors = {
        name: np.repeat(factor / 5.0, 5) for name, factor in weekly_factors.items()
    }
    noises: dict[str, np.ndarray] = {}
    prices: dict[str, float] = {}
    for members in blocks.values():
        for code in members:
            noises[code] = rng.normal(0.0, 0.002, len(dates))
            prices[code] = 2.0 + rng.random()
    rows, adj = [], []
    for i, d in enumerate(dates):
        for name, members in blocks.items():
            for code in members:
                prices[code] *= 1 + daily_factors[name][i] + noises[code][i]
                close = round(prices[code], 3)
                rows.append({
                    "ts_code": code, "trade_date": d, "open": close,
                    "close": close, "high": close, "low": close,
                    "pre_close": close, "vol": 1_000_000,
                    "amount": close * 5_000_000,
                })
                adj.append({"ts_code": code, "trade_date": d, "adj_factor": 1.0})
    fund_daily = pd.DataFrame(rows)
    fund_adj = pd.DataFrame(adj)
    codes = sorted(prices)
    dim_fund = pd.DataFrame([
        {"ts_code": c, "name": f"测试ETF{i}", "list_date": "20200101"}
        for i, c in enumerate(codes)
    ])
    return fund_daily, fund_adj, dim_fund, codes


def _representative_params() -> dict:
    return dict(
        k=3, top_n=2,
        correlation_lookback_weeks=20, momentum_window_weeks=4,
        recluster_interval_weeks=10, min_valid_weeks=10, min_pairwise_weeks=10,
        representative_candidate_count=3, representative_min_cluster_corr=0.7,
        representative_liquidity_window_days=20,
        representative_min_liquidity_observations=10,
        min_effective_cluster_count_warn=2.0,
        min_effective_cluster_count_reject=1.5,
    )


@pytest.fixture(scope="module")
def synthetic_run():
    from backtest.fund_rotation.strategies.correlation_representative.strategy import (
        CorrelationRepresentativeStrategy,
    )

    fund_daily, fund_adj, dim_fund, codes = _synthetic_frames()
    strategy = CorrelationRepresentativeStrategy()
    config = strategy.config_model(**_representative_params())
    requirements = strategy.resolve_requirements(config)

    all_dates = tuple(sorted(fund_daily["trade_date"].astype(str).unique()))
    snapshot = PinnedFundDataSnapshot(
        fund_version=0, fund_adj_version=0, dim_version=0,
        universe_codes=tuple(sorted(codes)), trading_dates=all_dates,
        fingerprint="integration-synthetic",
    )
    evaluation = EvaluationContext.from_range(all_dates, "20220701", "20230601")
    execution = ExecutionConfig(initial_capital=1_000_000)
    runner = FundRotationBacktestRunner(fund_daily, fund_adj, dim_fund)
    result = runner.run(
        strategy=strategy, config=config, snapshot=snapshot,
        evaluation=evaluation, execution=execution,
        cancellation=CancellationToken(),
    )
    return result, config, requirements


class TestSyntheticEndToEnd:
    def test_run_succeeds_with_decisions(self, synthetic_run):
        result, config, _ = synthetic_run
        assert result.status is SubRunStatus.SUCCEEDED, (
            f"{result.error_code}: {result.error_message}"
        )
        assert result.decisions, "no decisions produced"
        assert result.weekly_targets, "no targets produced"

    def test_holdings_never_exceed_selected_clusters(self, synthetic_run):
        result, config, _ = synthetic_run
        # Per-day upper bound = the target count of the latest decision whose
        # signal strictly precedes that day (execution happens after signals).
        decisions = sorted(result.decisions, key=lambda d: d.signal_date)
        bounds: list[tuple[str, int]] = [
            (d.signal_date, len(d.target_weights)) for d in decisions
        ]

        def bound_for(trade_date: str) -> int:
            limit = 0
            for signal_date, count in bounds:
                if signal_date < trade_date:
                    limit = count
                else:
                    break
            return limit

        for snapshot in result.positions_history:
            holdings = {
                code: qty for code, qty in snapshot["positions"].items() if qty > 0
            }
            assert len(holdings) <= bound_for(snapshot["trade_date"]), (
                f"{snapshot['trade_date']}: {len(holdings)} holdings exceed "
                "the selected-cluster count of the latest decision"
            )

    def test_at_most_one_etf_per_cluster(self, synthetic_run):
        result, _, _ = synthetic_run
        artifacts = {a.role: a.payload for a in result.diagnostics.artifacts}
        selections = artifacts["representatives"]
        assert selections, "no representative diagnostics"
        by_week: dict[str, list[str]] = {}
        for entry in selections:
            if entry["selected"]:
                by_week.setdefault(entry["week"], []).append(entry["selected"])
        assert by_week, "no representatives ever selected"
        for week, selected_codes in by_week.items():
            # One representative per cluster -> no duplicate ETF in one week.
            assert len(selected_codes) == len(set(selected_codes)), (
                f"{week}: duplicate representative {selected_codes}"
            )

    def test_fills_respect_adv_capacity_and_buy_lots(self, synthetic_run):
        result, _, _ = synthetic_run
        filled_events = [
            e for e in result.trade_events if int(e.get("filled", 0)) > 0
        ]
        assert filled_events, "no fills produced"
        for event in filled_events:
            assert event["participation_rate"] <= 0.05 + 1e-9, (
                f"participation {event['participation_rate']} exceeds ADV20 cap"
            )
        # Buy fills respect the ETF 100-share lot (sells may liquidate
        # residual odd lots — legacy execution behavior, golden-frozen).
        buy_fills = [e for e in filled_events if e.get("action") == "BUY"]
        assert buy_fills, "no buy fills produced"
        for event in buy_fills:
            assert int(event["filled"]) % 100 == 0, (
                f"buy fill {event['filled']} violates ETF 100-share lot"
            )


# ── read-only research smoke on the local pinned snapshot ──

SNAPSHOT_RUN_DIR = (
    Path(__file__).resolve().parents[2]
    / "runs" / "fund_rotation" / "bac86bdddcf85601"
)


def _load_snapshot_frames():
    """Read fund/dim/adj frames at the versions pinned by the recorded run.

    The run directory supplies ONLY the pinned version identity; market data
    is read from the Lance datasets it points to (never from the run itself).
    Falls back to the current dataset version when the recorded version is no
    longer reachable (e.g. after compaction).
    """
    import lance

    snapshot_path = SNAPSHOT_RUN_DIR / "data_snapshot.json"
    record = json.loads(snapshot_path.read_text(encoding="utf-8"))

    frames = {}
    for key, columns in (
        ("fund.lance", ["ts_code", "trade_date", "open", "close", "high",
                        "low", "pre_close", "vol", "amount"]),
        ("dim_fund.lance", ["ts_code", "name", "list_date"]),
        ("fact_fund_adj.lance", ["ts_code", "trade_date", "adj_factor"]),
    ):
        info = record["datasets"][key]
        path = Path(info["path"])
        if not path.exists():
            pytest.skip(f"lance dataset unavailable: {path}")
        try:
            dataset = lance.dataset(str(path), version=int(info["version"]))
        except Exception as exc:
            # Pinned version unreachable (e.g. compacted): fall back to the
            # current version, but make the identity drift visible.
            import warnings

            warnings.warn(
                f"pinned version {info['version']} of {key} unreachable "
                f"({exc}); smoke uses the current dataset version instead",
                stacklevel=2,
            )
            dataset = lance.dataset(str(path))
        date_filter = None
        if key != "dim_fund.lance":
            # Bounded window: enough for the 52-week warmup plus a short
            # evaluation interval (keeps the smoke fast).
            date_filter = "trade_date >= '20230101' AND trade_date <= '20240630'"
        available = [c for c in columns if c in dataset.schema.names]
        frames[key] = dataset.to_table(
            columns=available, filter=date_filter,
        ).to_pandas()
    return frames["fund.lance"], frames["fact_fund_adj.lance"], frames["dim_fund.lance"]


@pytest.fixture(scope="module")
def smoke_runs():
    if not (SNAPSHOT_RUN_DIR / "data_snapshot.json").exists():
        pytest.skip("local research snapshot not present")
    fund_daily, fund_adj, dim_fund = _load_snapshot_frames()

    etf_pool = filter_etf_universe(dim_fund)
    pool_codes = set(etf_pool["ts_code"].astype(str))
    fund_daily = fund_daily[fund_daily["ts_code"].astype(str).isin(pool_codes)]
    fund_adj = fund_adj[fund_adj["ts_code"].astype(str).isin(pool_codes)]
    if fund_daily.empty or fund_adj.empty:
        pytest.skip("snapshot frames empty after ETF pool filter")

    all_dates = tuple(sorted(fund_daily["trade_date"].astype(str).unique()))
    snapshot = PinnedFundDataSnapshot(
        fund_version=0, fund_adj_version=0, dim_version=0,
        universe_codes=tuple(sorted(pool_codes)), trading_dates=all_dates,
        fingerprint="research-smoke",
    )
    evaluation = EvaluationContext.from_range(all_dates, "20240101", "20240331")
    execution = ExecutionConfig(initial_capital=1_000_000)
    runner = FundRotationBacktestRunner(fund_daily, fund_adj, dim_fund)

    catalog = FundRotationStrategyCatalog(list(default_fund_rotation_strategies()))

    results = {}
    for strategy_id in ("correlation_representative", "correlation_all_members"):
        binding = catalog.resolve(strategy_id, {})
        config = binding.registered.config_model.model_validate(
            dict(binding.spec.resolved_config)
        )
        results[strategy_id] = runner.run(
            strategy=binding.strategy, config=config,
            snapshot=snapshot, evaluation=evaluation, execution=execution,
            cancellation=CancellationToken(),
        )
    return results


class TestResearchSmoke:
    def test_representative_run_succeeds_on_real_snapshot(self, smoke_runs):
        result = smoke_runs["correlation_representative"]
        assert result.status is SubRunStatus.SUCCEEDED, (
            f"{result.error_code}: {result.error_message}"
        )
        assert result.decisions, "no decisions on real data"
        for snapshot in result.positions_history:
            holdings = {c: q for c, q in snapshot["positions"].items() if q > 0}
            assert len(holdings) <= 3  # default top_n

    def test_baseline_run_succeeds_on_same_snapshot(self, smoke_runs):
        result = smoke_runs["correlation_all_members"]
        assert result.status is SubRunStatus.SUCCEEDED, (
            f"{result.error_code}: {result.error_message}"
        )

    def test_comparison_is_diagnostic_not_acceptance(self, smoke_runs):
        """§acceptance — compare structure and tradability only; returns are
        printed as diagnostics, never asserted higher/lower."""
        representative = smoke_runs["correlation_representative"]
        baseline = smoke_runs["correlation_all_members"]
        rep_metrics = representative.strategy_metrics
        base_metrics = baseline.strategy_metrics
        assert set(rep_metrics) == set(base_metrics)
        # Diagnostic summary (visible under pytest -s); no return assertion.
        reason_counts: dict[str, int] = {}
        for decision in representative.decisions:
            key = decision.reason_code or decision.action.value
            reason_counts[key] = reason_counts.get(key, 0) + 1
        print(
            "\n[research smoke] representative annual_return="
            f"{rep_metrics.get('annual_return')} vs baseline "
            f"{base_metrics.get('annual_return')} (diagnostic only); "
            f"decision reasons={reason_counts}"
        )
