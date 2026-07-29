"""Zoo Bench vs StockPred 回测相关性验证实验.

实验假设: 使用相同时间段数据、相同策略，Zoo Bench 中 IC 表现差的因子，
在 StockPred 组合回测中表现也差（两系统对策略优劣的排序存在正相关）。

Usage:
    cd agent
    python scripts/correlation_experiment.py [--phase 1|2|all] [--n-strategies 30]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

# Ensure agent root is on path
_AGENT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_AGENT_ROOT))

from dotenv import load_dotenv

load_dotenv(_AGENT_ROOT / ".env")


# ─── Configuration ────────────────────────────────────────────────────────────

UNIVERSE = "csi300"
PERIOD = "2024-2025"
STOCKPRED_START = "20240101"
STOCKPRED_END = "20250101"

# Phase 1: default StockPred params
PHASE1_PARAMS = {"top_n": 50, "eval_step": 5, "forward_days": 5}
# Phase 2a: aligned holding period
PHASE2A_PARAMS = {"top_n": 50, "eval_step": 1, "forward_days": 1}

OUTPUT_DIR = _AGENT_ROOT / "runs" / "correlation_experiment"


def forward_returns_on_next_trade_day(
    prices: pd.DataFrame,
    factors: pd.Series,
    eval_date: str,
    trade_dates: list[str],
) -> pd.Series:
    """Return cross-sectional forward returns from T close to next trade day close.

    Uses ``adj_close`` (forward-adjusted) when available to avoid ex-dividend
    pollution; falls back to raw ``close`` only if ``adj_close`` is absent.

    Args:
        prices: Long-form DataFrame with columns [ts_code, trade_date, close]
            and optionally [adj_close]. When adj_close is present it is preferred.
        factors: Factor cross-section indexed by ts_code (used to filter valid codes).
        eval_date: The evaluation date T (factor computation date).
        trade_dates: Sorted list of all available trade dates.

    Returns:
        Series indexed by ts_code with forward returns. Empty if no next trade day
        or no valid overlapping codes.
    """
    # Find next trade day after eval_date
    try:
        eval_idx = trade_dates.index(eval_date)
    except ValueError:
        return pd.Series(dtype=float)
    if eval_idx + 1 >= len(trade_dates):
        return pd.Series(dtype=float)
    next_date = trade_dates[eval_idx + 1]

    # Prefer adjusted close to avoid ex-dividend artifacts
    price_col = "adj_close" if "adj_close" in prices.columns else "close"

    # Get prices for T and next-T
    prices_t = prices[prices["trade_date"] == eval_date].set_index("ts_code")[price_col]
    prices_next = prices[prices["trade_date"] == next_date].set_index("ts_code")[price_col]

    # Only compute for codes in both factor index and both price dates
    valid_codes = factors.index.intersection(prices_t.index).intersection(prices_next.index)
    if valid_codes.empty:
        return pd.Series(dtype=float)

    close_t = prices_t.loc[valid_codes]
    close_next = prices_next.loc[valid_codes]

    # Filter out NaN/inf prices
    valid_mask = close_t.notna() & close_next.notna() & (close_t != 0)
    valid_mask &= np.isfinite(close_t) & np.isfinite(close_next)
    if not valid_mask.any():
        return pd.Series(dtype=float)

    returns = close_next[valid_mask] / close_t[valid_mask] - 1.0
    return returns


# ─── Helpers ──────────────────────────────────────────────────────────────────


def select_strategies(n: int = 30) -> list[str]:
    """Select a diverse set of equity_cn alpha strategies."""
    from src.factors.registry import Registry

    reg = Registry()
    cn_ids = reg.list(universe="equity_cn")

    # Stratified sampling: proportional from each zoo, ensure diversity
    by_zoo: dict[str, list[str]] = {}
    for aid in cn_ids:
        zoo = reg.get(aid).zoo
        by_zoo.setdefault(zoo, []).append(aid)

    # Deterministic selection with seed for reproducibility
    rng = np.random.default_rng(42)
    selected: list[str] = []

    # Always include all academic (only 10)
    academic = by_zoo.get("academic", [])
    selected.extend(academic)

    # Sample from gtja191 and qlib158
    remaining_budget = max(0, n - len(selected))
    gtja = by_zoo.get("gtja191", [])
    qlib = by_zoo.get("qlib158", [])

    # Proportional allocation
    total_pool = len(gtja) + len(qlib)
    n_gtja = min(len(gtja), int(remaining_budget * len(gtja) / total_pool))
    n_qlib = min(len(qlib), remaining_budget - n_gtja)

    gtja_sample = sorted(rng.choice(gtja, size=n_gtja, replace=False).tolist())
    qlib_sample = sorted(rng.choice(qlib, size=n_qlib, replace=False).tolist())

    selected.extend(gtja_sample)
    selected.extend(qlib_sample)

    print(f"[select] {len(selected)} strategies: "
          f"academic={len(academic)}, gtja191={len(gtja_sample)}, qlib158={len(qlib_sample)}")
    return selected


def run_zoo_bench(strategy_ids: list[str]) -> dict[str, dict[str, float]]:
    """Run Zoo Bench (IC evaluation) for selected strategies."""
    from src.factors.bench_runner import run_bench

    print(f"\n{'='*60}")
    print(f"[zoo] Running Zoo Bench: universe={UNIVERSE}, period={PERIOD}")
    print(f"{'='*60}")

    # Group by zoo for efficient bench runs
    from src.factors.registry import Registry
    reg = Registry()
    by_zoo: dict[str, list[str]] = {}
    for aid in strategy_ids:
        zoo = reg.get(aid).zoo
        by_zoo.setdefault(zoo, []).append(aid)

    results: dict[str, dict[str, float]] = {}
    for zoo, ids in sorted(by_zoo.items()):
        print(f"\n[zoo] Benching {zoo}: {len(ids)} alphas...")
        t0 = time.monotonic()
        bench_result = run_bench(
            zoo=zoo,
            universe=UNIVERSE,
            period=PERIOD,
            top=len(ids),
            only=ids,
            registry=reg,
        )
        elapsed = time.monotonic() - t0

        if bench_result.get("status") != "ok":
            print(f"[zoo] ERROR: {bench_result.get('error', 'unknown')}")
            continue

        rows = bench_result.get("rows", [])
        for row in rows:
            results[row["id"]] = {
                "ic_mean": row["ic_mean"],
                "ic_std": row["ic_std"],
                "ir": row["ir"],
                "ic_positive_ratio": row["ic_positive_ratio"],
                "ic_count": row["ic_count"],
                "category": row.get("_category", "unknown"),
            }
        print(f"[zoo] {zoo}: {len(rows)} evaluated, "
              f"{bench_result.get('n_skipped', 0)} skipped ({elapsed:.1f}s)")

    print(f"\n[zoo] Total: {len(results)}/{len(strategy_ids)} strategies evaluated")
    return results


def run_stockpred_backtest(
    strategy_ids: list[str],
    *,
    top_n: int = 50,
    eval_step: int = 5,
    forward_days: int = 5,
    label: str = "phase1",
) -> dict[str, dict[str, float]]:
    """Run StockPred portfolio backtest for selected strategies.

    Pre-loads all price data once from Lance, then builds panels from memory
    for each eval date. This avoids repeated Lance reads per date.
    """
    from src.factors.registry import Registry
    from src.stockpred.gateway import StockPredDataGateway
    from src.stockpred.snapshot import build_snapshot, resolve_stockpred_root
    from src.stockpred.contracts import ModelSnapshot
    from src.stockpred.strategies.catalog import StrategyCatalog
    from src.stockpred.graph.portfolio import build_equal_weight_targets
    from src.stockpred.graph.adjustment import apply_qfq
    from src.stockpred.graph.universe import build_pit_universe
    from backtest.stockpred_graph.execution import build_daily_ledger, execute_target_portfolio
    from backtest.stockpred_graph.performance import calculate_performance_metrics

    print(f"\n{'='*60}")
    print(f"[stockpred] Running StockPred Backtest ({label})")
    print(f"[stockpred] start={STOCKPRED_START}, end={STOCKPRED_END}, "
          f"top_n={top_n}, eval_step={eval_step}, forward_days={forward_days}")
    print(f"{'='*60}")

    # Build data snapshot
    root = resolve_stockpred_root()
    from zoneinfo import ZoneInfo
    manifest = build_snapshot(
        root,
        as_of=datetime.now(ZoneInfo("Asia/Shanghai")),
        model=ModelSnapshot(id="correlation-exp", version="v1", config_sha256="0" * 64),
    )
    gateway = StockPredDataGateway(root, manifest)

    # Load static inputs once
    print("[stockpred] Loading static inputs...")
    t_static = time.monotonic()
    all_trade_dates = gateway.trade_dates("19900101", "99991231")
    stock_dimension = gateway.stock_dimension()
    name_history = gateway.name_history()
    industry_history = gateway.industry_history()
    print(f"[stockpred] Static inputs loaded ({time.monotonic() - t_static:.1f}s)")

    # Get evaluation dates
    trade_dates = [d for d in all_trade_dates if STOCKPRED_START <= d <= STOCKPRED_END]
    scheduled_dates = trade_dates[::eval_step]
    print(f"[stockpred] Trade dates: {len(trade_dates)}, "
          f"scheduled evaluations: {len(scheduled_dates)}")

    # Build catalog and registry
    reg = Registry()
    catalog = StrategyCatalog(reg)

    # Resolve descriptors
    descriptors = []
    for sid in strategy_ids:
        try:
            descriptors.append(catalog.require(sid))
        except KeyError:
            print(f"  {sid}: NOT FOUND in catalog, skip")

    # Pre-load ALL price data once (from lookback start to period end)
    data_lookback = 300
    # Find the earliest date we need (first eval date - lookback)
    first_eval = scheduled_dates[0]
    first_eval_idx = all_trade_dates.index(first_eval)
    lookback_start = all_trade_dates[max(0, first_eval_idx - data_lookback)]
    # Add 60 days past end for trade execution
    exec_end = (datetime.strptime(STOCKPRED_END, "%Y%m%d") + __import__("datetime").timedelta(days=60)).strftime("%Y%m%d")

    # Build PIT universe at midpoint to get a stable stock set
    mid_date = scheduled_dates[len(scheduled_dates) // 2]
    print(f"[stockpred] Building PIT universe at {mid_date}...")
    universe, _ = build_pit_universe(
        stock_dimension, eval_date=mid_date, trade_dates=all_trade_dates,
        min_listed_trade_days=60, name_history=name_history,
        industry_history=industry_history,
    )
    all_codes = sorted(universe["ts_code"].astype(str))
    # Subsample to ~1000 stocks for feasible computation time
    # (factor computation on 5000 stocks x 300 days is ~5s/alpha/date)
    rng_universe = np.random.default_rng(123)
    if len(all_codes) > 1000:
        codes = sorted(rng_universe.choice(all_codes, size=1000, replace=False).tolist())
    else:
        codes = all_codes
    print(f"[stockpred] Universe: {len(codes)} stocks (from {len(all_codes)} PIT)")

    # Pre-load all prices and adjustment factors in one batch
    print(f"[stockpred] Pre-loading prices ({lookback_start} to {exec_end}) for {len(codes)} stocks...")
    t_load = time.monotonic()
    raw_prices = gateway.prices(lookback_start, exec_end, codes)
    adj_factors = gateway.adjustment_factors(lookback_start, exec_end, codes)
    all_prices = apply_qfq(raw_prices, adj_factors)
    limits = gateway.stock_limits(STOCKPRED_START, exec_end, codes)
    all_prices = all_prices.merge(
        limits[["ts_code", "trade_date", "up_limit", "down_limit"]],
        on=["ts_code", "trade_date"], how="left",
    )
    print(f"[stockpred] Prices loaded: {len(all_prices)} rows ({time.monotonic() - t_load:.1f}s)")

    # Pre-build wide panels ONCE (avoid repeated pivot per date)
    print("[stockpred] Building wide panels...")
    t_wide = time.monotonic()
    multiplier = all_prices["adj_close"].div(all_prices["close"].where(all_prices["close"] != 0))
    all_prices["adj_high"] = all_prices["high"] * multiplier
    all_prices["adj_low"] = all_prices["low"] * multiplier
    all_prices["volume"] = all_prices["vol"]
    all_prices["vwap"] = all_prices["amount"] * 1000.0 / (all_prices["vol"] * 100.0 + 1.0)

    def _pivot_wide(col: str) -> pd.DataFrame:
        result = all_prices.pivot(index="trade_date", columns="ts_code", values=col)
        result = result.sort_index().sort_index(axis=1).rename_axis(index=None, columns=None)
        return result

    wide_panels = {
        "open": _pivot_wide("adj_open"),
        "high": _pivot_wide("adj_high"),
        "low": _pivot_wide("adj_low"),
        "close": _pivot_wide("adj_close"),
        "volume": _pivot_wide("volume"),
        "amount": _pivot_wide("amount"),
        "vwap": _pivot_wide("vwap"),
    }
    # Convert index to string dates for easy slicing
    wide_index = [str(d) for d in wide_panels["close"].index]
    for key in wide_panels:
        wide_panels[key].index = wide_index
    print(f"[stockpred] Wide panels built: {wide_panels['close'].shape} ({time.monotonic() - t_wide:.1f}s)")

    # Helper: slice pre-built wide panel for a given eval date
    def _build_panel(eval_date: str) -> dict[str, pd.DataFrame]:
        if eval_date not in wide_index:
            return {}
        eval_pos = wide_index.index(eval_date)
        start_pos = max(0, eval_pos - data_lookback + 1)
        panel = {}
        for key, wide_df in wide_panels.items():
            sliced = wide_df.iloc[start_pos:eval_pos + 1]
            # Convert index to datetime for factor computation
            sliced = sliced.copy()
            sliced.index = pd.to_datetime(sliced.index)
            panel[key] = sliced
        return panel

    # Per-strategy state
    strategy_signals: dict[str, list[pd.DataFrame]] = {d.id: [] for d in descriptors}
    strategy_selected: dict[str, list[pd.DataFrame]] = {d.id: [] for d in descriptors}
    strategy_valid_dates: dict[str, list[str]] = {d.id: [] for d in descriptors}
    strategy_prev_holdings: dict[str, set[str]] = {d.id: set() for d in descriptors}

    # Date-major iteration
    print(f"[stockpred] Evaluating {len(descriptors)} strategies x {len(scheduled_dates)} dates...")
    for date_idx, eval_date in enumerate(scheduled_dates):
        t0 = time.monotonic()
        panel = _build_panel(eval_date)
        if not panel or panel.get("close") is None or panel["close"].empty:
            continue

        n_ok = 0
        for descriptor in descriptors:
            try:
                values = reg.compute(descriptor.id, panel)
                if values.empty:
                    continue
                scores = values.iloc[-1].rename_axis("ts_code").rename("score").dropna().reset_index()
                if scores.empty:
                    continue
                scores["trade_date"] = eval_date
                targets = build_equal_weight_targets(
                    scores, top_n=top_n,
                    previous_holdings=strategy_prev_holdings[descriptor.id],
                    retain_rank=15,
                )
                strategy_prev_holdings[descriptor.id] = set(targets["ts_code"].astype(str))
                strategy_signals[descriptor.id].append(scores)
                strategy_selected[descriptor.id].append(targets)
                strategy_valid_dates[descriptor.id].append(eval_date)
                n_ok += 1
            except Exception:
                pass

        elapsed = time.monotonic() - t0
        if (date_idx + 1) % 10 == 0 or date_idx == 0:
            print(f"  [date {date_idx+1}/{len(scheduled_dates)}] {eval_date}: "
                  f"{n_ok}/{len(descriptors)} strategies OK ({elapsed:.1f}s)")

    # Finalize: execute trades and compute metrics per strategy
    print(f"\n[stockpred] Executing trades and computing metrics...")
    results: dict[str, dict[str, float]] = {}

    for descriptor in descriptors:
        sid = descriptor.id
        valid_dates = strategy_valid_dates[sid]
        selections = strategy_selected[sid]
        ratio = len(valid_dates) / len(scheduled_dates) if scheduled_dates else 0.0

        if ratio < 0.5 or not selections:
            print(f"  {sid}: insufficient valid evals ({len(valid_dates)}/{len(scheduled_dates)})")
            continue

        try:
            # Use pre-loaded market data for execution
            exec_codes = sorted(set().union(*(set(f["ts_code"].astype(str)) for f in selections)))
            market = all_prices[all_prices["ts_code"].astype(str).isin(exec_codes)].copy()

            trades = pd.concat([
                execute_target_portfolio(
                    market, targets, signal_date=date,
                    holding_days=forward_days,
                    capital=10_000_000.0,
                    max_participation=0.05,
                )
                for date, targets in zip(valid_dates, selections, strict=True)
            ], ignore_index=True)
            positions, equity = build_daily_ledger(trades, market, initial_capital=10_000_000.0)

            metrics: dict[str, float] = {
                "valid_eval_ratio": ratio,
                "trade_count": float(len(trades)),
            }
            if not equity.empty:
                metrics.update(calculate_performance_metrics(equity, trades))

            results[sid] = {
                "sharpe": metrics.get("sharpe", 0.0),
                "total_return": metrics.get("total_return", 0.0),
                "annual_return": metrics.get("annual_return", 0.0),
                "max_drawdown": metrics.get("max_drawdown", 0.0),
                "win_rate": metrics.get("win_rate", 0.0),
                "trade_count": metrics.get("trade_count", 0.0),
                "valid_eval_ratio": ratio,
            }
            sharpe = metrics.get("sharpe", 0.0)
            print(f"  {sid}: sharpe={sharpe:.3f}, return={metrics.get('total_return', 0):.4f}, "
                  f"evals={len(valid_dates)}/{len(scheduled_dates)}")
        except Exception as exc:
            print(f"  {sid}: EXEC FAILED ({type(exc).__name__}: {exc})")

    print(f"\n[stockpred] Total: {len(results)}/{len(descriptors)} strategies evaluated")
    return results


def compute_correlation(
    zoo_results: dict[str, dict[str, float]],
    stockpred_results: dict[str, dict[str, float]],
    label: str = "phase1",
) -> dict[str, Any]:
    """Compute rank correlation between Zoo IC metrics and StockPred portfolio metrics."""
    from scipy.stats import spearmanr, pearsonr

    # Find common strategies
    common = sorted(set(zoo_results.keys()) & set(stockpred_results.keys()))
    print(f"\n{'='*60}")
    print(f"[correlation] Computing correlation ({label}): {len(common)} common strategies")
    print(f"{'='*60}")

    if len(common) < 5:
        print("[correlation] ERROR: too few common strategies for meaningful correlation")
        return {"error": "too few common strategies", "n_common": len(common)}

    # Extract metric arrays
    ic_means = np.array([zoo_results[s]["ic_mean"] for s in common])
    irs = np.array([zoo_results[s]["ir"] for s in common])
    ic_pos_ratios = np.array([zoo_results[s]["ic_positive_ratio"] for s in common])
    sharpes = np.array([stockpred_results[s]["sharpe"] for s in common])
    total_returns = np.array([stockpred_results[s]["total_return"] for s in common])
    annual_returns = np.array([stockpred_results[s]["annual_return"] for s in common])

    # Spearman rank correlations
    pairs = [
        ("ic_mean vs sharpe", ic_means, sharpes),
        ("ir vs sharpe", irs, sharpes),
        ("ic_mean vs total_return", ic_means, total_returns),
        ("ir vs total_return", irs, total_returns),
        ("ic_mean vs annual_return", ic_means, annual_returns),
        ("ic_positive_ratio vs sharpe", ic_pos_ratios, sharpes),
    ]

    correlations: dict[str, dict[str, float]] = {}
    for name, x, y in pairs:
        # Filter NaN/inf
        mask = np.isfinite(x) & np.isfinite(y)
        if mask.sum() < 5:
            correlations[name] = {"spearman_r": 0.0, "p_value": 1.0, "n": int(mask.sum())}
            continue
        rho, pval = spearmanr(x[mask], y[mask])
        correlations[name] = {
            "spearman_r": round(float(rho), 4),
            "p_value": round(float(pval), 6),
            "n": int(mask.sum()),
        }
        sig = "***" if pval < 0.001 else "**" if pval < 0.01 else "*" if pval < 0.05 else ""
        print(f"  {name}: rho={rho:.4f}, p={pval:.4f} {sig} (n={mask.sum()})")

    # Category consistency analysis
    categories = {s: zoo_results[s].get("category", "unknown") for s in common}
    dead_sharpes = [sharpes[i] for i, s in enumerate(common) if categories[s] == "dead"]
    alive_sharpes = [sharpes[i] for i, s in enumerate(common) if categories[s] == "alive"]

    category_analysis = {
        "n_alive": len(alive_sharpes),
        "n_dead": len(dead_sharpes),
        "alive_mean_sharpe": round(float(np.mean(alive_sharpes)), 4) if alive_sharpes else None,
        "dead_mean_sharpe": round(float(np.mean(dead_sharpes)), 4) if dead_sharpes else None,
        "dead_negative_sharpe_ratio": round(
            float(np.mean([s < 0 for s in dead_sharpes])), 4
        ) if dead_sharpes else None,
    }
    print(f"\n  Category analysis:")
    print(f"    alive ({category_analysis['n_alive']}): mean sharpe = {category_analysis['alive_mean_sharpe']}")
    print(f"    dead  ({category_analysis['n_dead']}): mean sharpe = {category_analysis['dead_mean_sharpe']}")
    print(f"    dead with negative sharpe: {category_analysis['dead_negative_sharpe_ratio']}")

    # Per-strategy detail
    detail = []
    for s in common:
        detail.append({
            "strategy_id": s,
            "zoo_ic_mean": zoo_results[s]["ic_mean"],
            "zoo_ir": zoo_results[s]["ir"],
            "zoo_category": zoo_results[s].get("category", "unknown"),
            "stockpred_sharpe": stockpred_results[s]["sharpe"],
            "stockpred_total_return": stockpred_results[s]["total_return"],
            "stockpred_annual_return": stockpred_results[s]["annual_return"],
        })

    return {
        "label": label,
        "n_common": len(common),
        "correlations": correlations,
        "category_analysis": category_analysis,
        "detail": detail,
    }


def save_results(results: dict[str, Any], filename: str = "results.json") -> Path:
    """Save experiment results to JSON."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_DIR / filename
    path.write_text(json.dumps(results, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    print(f"\n[save] Results saved to {path}")
    return path


# ─── Phase Orchestration ──────────────────────────────────────────────────────


def run_phase1(strategy_ids: list[str]) -> dict[str, Any]:
    """Phase 1: Baseline experiment with default parameters."""
    print("\n" + "=" * 70)
    print("  PHASE 1: Baseline Experiment (Default Parameters)")
    print("  Zoo: IC evaluation on CSI300, 2024-2025")
    print("  StockPred: Portfolio backtest, top_n=50, eval_step=5, forward_days=5")
    print("=" * 70)

    zoo_results = run_zoo_bench(strategy_ids)
    stockpred_results = run_stockpred_backtest(
        strategy_ids,
        label="phase1",
        **PHASE1_PARAMS,
    )
    correlation = compute_correlation(zoo_results, stockpred_results, label="phase1")

    return {
        "phase": "phase1",
        "params": {"zoo": {"universe": UNIVERSE, "period": PERIOD}, "stockpred": PHASE1_PARAMS},
        "zoo_results": zoo_results,
        "stockpred_results": stockpred_results,
        "correlation": correlation,
    }


def run_phase2a(strategy_ids: list[str], zoo_results: dict[str, dict]) -> dict[str, Any]:
    """Phase 2a: Align holding period (forward_days=1, eval_step=1)."""
    print("\n" + "=" * 70)
    print("  PHASE 2a: Aligned Holding Period")
    print("  StockPred: forward_days=1, eval_step=1 (matches Zoo's 1-day forward return)")
    print("=" * 70)

    stockpred_results = run_stockpred_backtest(
        strategy_ids,
        label="phase2a",
        **PHASE2A_PARAMS,
    )
    correlation = compute_correlation(zoo_results, stockpred_results, label="phase2a")

    return {
        "phase": "phase2a",
        "params": {"zoo": {"universe": UNIVERSE, "period": PERIOD}, "stockpred": PHASE2A_PARAMS},
        "stockpred_results": stockpred_results,
        "correlation": correlation,
    }


def run_phase2d_ic_from_stockpred(
    strategy_ids: list[str],
) -> dict[str, Any]:
    """Phase 2d: Compute IC from StockPred signals (apples-to-apples IC comparison).

    Instead of comparing IC vs Sharpe, we compute IC directly from StockPred's
    factor scores vs actual forward returns, using the same PIT universe.
    """
    from src.factors.registry import Registry
    from src.factors.factor_analysis_core import compute_ic_series
    from src.stockpred.gateway import StockPredDataGateway
    from src.stockpred.snapshot import build_snapshot, resolve_stockpred_root
    from src.stockpred.contracts import ModelSnapshot
    from src.stockpred.strategies.catalog import StrategyCatalog
    from src.stockpred.strategies.panel import StockPredPanelBuilder
    from src.stockpred.graph.adjustment import apply_qfq

    print("\n" + "=" * 70)
    print("  PHASE 2d: IC from StockPred Data (PIT universe, Lance data)")
    print("  Computes IC using StockPred's PIT panels instead of Zoo's static panel")
    print("=" * 70)

    root = resolve_stockpred_root()
    from zoneinfo import ZoneInfo
    manifest = build_snapshot(
        root,
        as_of=datetime.now(ZoneInfo("Asia/Shanghai")),
        model=ModelSnapshot(id="correlation-exp-2d", version="v1", config_sha256="0" * 64),
    )
    gateway = StockPredDataGateway(root, manifest)
    reg = Registry()
    catalog = StrategyCatalog(reg)

    # Get trade dates for evaluation
    trade_dates = gateway.trade_dates(STOCKPRED_START, STOCKPRED_END)
    # Use every 5th date for efficiency
    eval_dates = trade_dates[::5]
    print(f"[phase2d] Evaluating IC on {len(eval_dates)} dates from StockPred PIT data")

    panel_builder = StockPredPanelBuilder(gateway, data_lookback_days=180)

    results: dict[str, dict[str, float]] = {}
    n_total = len(strategy_ids)

    for idx, strategy_id in enumerate(strategy_ids, 1):
        t0 = time.monotonic()
        try:
            descriptor = catalog.require(strategy_id)
        except KeyError:
            continue

        try:
            # Collect factor scores and forward returns across eval dates
            factor_frames: list[pd.DataFrame] = []
            return_frames: list[pd.DataFrame] = []

            for eval_date in eval_dates:
                try:
                    panel = panel_builder.build(eval_date, descriptor)
                    if panel.get("close") is None or panel["close"].empty:
                        continue

                    # Compute factor values
                    factor_values = reg.compute(strategy_id, panel)
                    if factor_values.empty:
                        continue

                    # Get last cross-section as factor scores
                    last_factor = factor_values.iloc[-1]

                    # Compute TRUE forward return: T close to next-T close
                    # Convert wide panel to long format for the helper function
                    close_wide = panel["close"]
                    panel_trade_dates = sorted(str(d) for d in close_wide.index)
                    # We need next-T data which may not be in panel; use gateway
                    next_dates = [d for d in trade_dates if d > eval_date]
                    if not next_dates:
                        continue
                    next_t = next_dates[0]
                    # Fetch prices for T and next-T from gateway
                    codes_in_factor = list(last_factor.dropna().index)
                    if len(codes_in_factor) < 30:
                        continue
                    raw_prices = gateway.prices(eval_date, next_t, codes_in_factor)
                    adj_factors = gateway.adjustment_factors(eval_date, next_t, codes_in_factor)
                    adj_prices = apply_qfq(raw_prices, adj_factors)
                    if adj_prices.empty:
                        continue
                    # Use the forward_returns helper
                    fwd_ret = forward_returns_on_next_trade_day(
                        adj_prices[["ts_code", "trade_date", "close", "adj_close"]],
                        last_factor,
                        eval_date,
                        [eval_date, next_t],
                    )
                    if fwd_ret.empty:
                        continue

                    # Align factor and return
                    common_codes = last_factor.index.intersection(fwd_ret.index)
                    if len(common_codes) < 30:
                        continue

                    f = last_factor.loc[common_codes]
                    r = fwd_ret.loc[common_codes]
                    valid = f.notna() & r.notna() & np.isfinite(f) & np.isfinite(r)
                    if valid.sum() < 30:
                        continue

                    # Spearman IC for this cross-section
                    ic = f[valid].corr(r[valid], method="spearman")
                    if np.isfinite(ic):
                        factor_frames.append(ic)

                except Exception:
                    continue

            if len(factor_frames) < 10:
                continue

            ic_series = pd.Series(factor_frames)
            ic_mean = float(ic_series.mean())
            ic_std = float(ic_series.std())
            ir = ic_mean / ic_std if ic_std > 0 else 0.0

            results[strategy_id] = {
                "ic_mean": round(ic_mean, 6),
                "ic_std": round(ic_std, 6),
                "ir": round(ir, 4),
                "ic_positive_ratio": round(float((ic_series > 0).mean()), 4),
                "ic_count": len(ic_series),
                "return_horizon": "next_trade_day",
            }
            elapsed = time.monotonic() - t0
            if idx % 5 == 0 or idx == n_total:
                print(f"  [{idx}/{n_total}] {strategy_id}: "
                      f"IC={ic_mean:.4f}, IR={ir:.3f} ({elapsed:.1f}s)")

        except Exception as exc:
            if idx % 10 == 0:
                print(f"  [{idx}/{n_total}] {strategy_id}: FAILED ({exc})")

    print(f"\n[phase2d] Total: {len(results)}/{n_total} strategies evaluated")
    return results


def run_phase2_comparison(
    zoo_results: dict[str, dict],
    stockpred_ic_results: dict[str, dict],
) -> dict[str, Any]:
    """Compare Zoo IC with StockPred IC (same metric, different data/universe)."""
    from scipy.stats import spearmanr

    common = sorted(set(zoo_results.keys()) & set(stockpred_ic_results.keys()))
    print(f"\n{'='*60}")
    print(f"[phase2d-correlation] Zoo IC vs StockPred IC: {len(common)} common strategies")
    print(f"{'='*60}")

    if len(common) < 5:
        return {"error": "too few common strategies"}

    zoo_ic = np.array([zoo_results[s]["ic_mean"] for s in common])
    sp_ic = np.array([stockpred_ic_results[s]["ic_mean"] for s in common])
    zoo_ir = np.array([zoo_results[s]["ir"] for s in common])
    sp_ir = np.array([stockpred_ic_results[s]["ir"] for s in common])

    mask = np.isfinite(zoo_ic) & np.isfinite(sp_ic)
    rho_ic, pval_ic = spearmanr(zoo_ic[mask], sp_ic[mask])
    mask_ir = np.isfinite(zoo_ir) & np.isfinite(sp_ir)
    rho_ir, pval_ir = spearmanr(zoo_ir[mask_ir], sp_ir[mask_ir])

    print(f"  Zoo IC_mean vs StockPred IC_mean: rho={rho_ic:.4f}, p={pval_ic:.6f}")
    print(f"  Zoo IR vs StockPred IR: rho={rho_ir:.4f}, p={pval_ir:.6f}")

    return {
        "label": "phase2d_ic_comparison",
        "n_common": len(common),
        "correlations": {
            "ic_mean_vs_ic_mean": {"spearman_r": round(float(rho_ic), 4), "p_value": round(float(pval_ic), 6)},
            "ir_vs_ir": {"spearman_r": round(float(rho_ir), 4), "p_value": round(float(pval_ir), 6)},
        },
    }


# ─── Main ─────────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="Zoo vs StockPred Correlation Experiment")
    parser.add_argument("--phase", choices=["1", "2", "all"], default="all",
                        help="Which phase to run")
    parser.add_argument("--n-strategies", type=int, default=35,
                        help="Number of strategies to test")
    args = parser.parse_args()

    print("=" * 70)
    print("  Zoo Bench vs StockPred Correlation Experiment")
    print(f"  Started: {datetime.now().isoformat()}")
    print("=" * 70)

    # Select strategies
    strategy_ids = select_strategies(args.n_strategies)

    all_results: dict[str, Any] = {
        "experiment_start": datetime.now().isoformat(),
        "strategies": strategy_ids,
        "phases": {},
    }

    # Phase 1: Baseline
    if args.phase in ("1", "all"):
        phase1 = run_phase1(strategy_ids)
        all_results["phases"]["phase1"] = phase1

        # Check if phase 2 is needed
        corr = phase1["correlation"]
        if "correlations" in corr:
            primary_rho = corr["correlations"].get("ic_mean vs sharpe", {}).get("spearman_r", 0)
            print(f"\n[decision] Phase 1 primary correlation (ic_mean vs sharpe): rho={primary_rho}")
            if abs(primary_rho) < 0.5:
                print("[decision] Correlation weak → Phase 2 needed")
            else:
                print("[decision] Correlation strong → Phase 2 may not be needed")

    # Phase 2: Control variables
    if args.phase in ("2", "all"):
        # Get zoo results (reuse from phase 1 if available)
        zoo_results = all_results["phases"].get("phase1", {}).get("zoo_results")
        if zoo_results is None:
            zoo_results = run_zoo_bench(strategy_ids)

        # Phase 2a: Aligned holding period
        phase2a = run_phase2a(strategy_ids, zoo_results)
        all_results["phases"]["phase2a"] = phase2a

        # Phase 2d: IC from StockPred data
        stockpred_ic = run_phase2d_ic_from_stockpred(strategy_ids)
        phase2d_corr = run_phase2_comparison(zoo_results, stockpred_ic)
        all_results["phases"]["phase2d"] = {
            "stockpred_ic_results": stockpred_ic,
            "correlation": phase2d_corr,
        }

    # Summary
    all_results["experiment_end"] = datetime.now().isoformat()
    print_summary(all_results)
    save_results(all_results)


def print_summary(results: dict[str, Any]) -> None:
    """Print final experiment summary."""
    print("\n" + "=" * 70)
    print("  EXPERIMENT SUMMARY")
    print("=" * 70)

    for phase_name, phase_data in results.get("phases", {}).items():
        corr = phase_data.get("correlation", {})
        if "correlations" in corr:
            print(f"\n  [{phase_name}]")
            for pair_name, stats in corr["correlations"].items():
                rho = stats.get("spearman_r", "N/A")
                pval = stats.get("p_value", "N/A")
                print(f"    {pair_name}: rho={rho}, p={pval}")
            cat = corr.get("category_analysis", {})
            if cat:
                print(f"    alive mean sharpe: {cat.get('alive_mean_sharpe')}")
                print(f"    dead mean sharpe:  {cat.get('dead_mean_sharpe')}")


if __name__ == "__main__":
    main()
