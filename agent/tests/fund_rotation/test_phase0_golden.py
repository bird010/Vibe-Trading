"""Phase 0 golden baseline — capture pre-fix pipeline behavior for regression detection.

Purpose (Phase 0 Task 1):
    Freeze the CURRENT pipeline output on a fixed 80-week synthetic dataset so
    that the behavior changes introduced by Phase 0 Tasks 2-7 (data snapshot,
    missing-return policy, 52-week boundary, evaluation calendar, initial target
    scheduling, full-interval equity) are detected as discrete/numeric diffs.

The golden dataset deliberately contains:
    * a real 52-week correlation/training lookback (so the Task 4 first-signal
      boundary shift is captured);
    * ONE missing close on a Friday week-ending (so the Task 3 missing-value
      semantics change is captured);
    * ONE adj-factor corporate action that fires while the ETF is held (so the
      execution-loop corporate-action path is captured).

Comparison tolerances follow design §35.1 (field-class based, not a single loose
global tolerance):
    dates/codes/actions/reason codes/directions/statuses/cluster labels/integer
    shares  -> exact
    target weights                                  -> rtol=0,   atol=1e-12
    prices/commission/cash/equity amounts           -> rtol=1e-12, atol=1e-6
    normalized NAV                                  -> rtol=1e-10, atol=1e-10
    return/risk metrics                             -> rtol=1e-9,  atol=1e-10

Regenerate the golden (only before fixes start, or to record an approved new
baseline) with:
    PHASE0_REGEN=1 python -m pytest agent/tests/fund_rotation/test_phase0_golden.py -q
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from backtest.fund_rotation.config import FundRotationConfig
from backtest.fund_rotation.pipeline import run_signal_pipeline

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "phase0"
GOLDEN_PATH = FIXTURE_DIR / "pre_fix_golden.json"
APPROVED_DELTA_PATH = FIXTURE_DIR / "approved_delta.json"

# ── design §35.1 tolerances ──
WEIGHT_TOL = dict(rtol=0.0, atol=1e-12)
MONEY_TOL = dict(rtol=1e-12, atol=1e-6)
NAV_TOL = dict(rtol=1e-10, atol=1e-10)
METRIC_TOL = dict(rtol=1e-9, atol=1e-10)

# Row fields compared with money tolerance (prices/fees/ratios/amounts).
_MONEY_KEYS = {
    "price", "commission", "raw_open", "slippage_bps", "adv20",
    "participation_rate", "cash_in_lieu", "last_close_before",
    "last_close_after", "fractional_remainder", "attempt_quantity_basis",
    "current_quantity_basis",
}
# Row fields compared with weight tolerance.
_WEIGHT_KEYS = {"target_weight", "actual_weight"}


# ── deterministic synthetic dataset ──

def build_golden_data(seed: int = 20260802):
    """Fixed 80-week synthetic dataset with one adj event and one missing close.

    Returns (fund_daily, fund_adj, dim_fund).
    """
    rng = np.random.default_rng(seed)
    start = pd.Timestamp("2022-01-07")  # a Friday
    n_weeks = 80
    weeks = [start + pd.Timedelta(weeks=i) for i in range(n_weeks)]
    dates: list[str] = []
    for w in weeks:
        for offset in range(5):  # Mon-Fri
            d = w - pd.Timedelta(days=4) + pd.Timedelta(days=offset)
            dates.append(d.strftime("%Y%m%d"))

    # 510300.SH benchmark + 9 investable ETFs.
    codes = ["510300.SH"] + [f"{510000 + i * 10}.SH" for i in range(1, 10)]

    rows = []
    prices = {c: 3.0 + rng.random() for c in codes}
    for d in dates:
        for c in codes:
            ret = rng.normal(0.001, 0.02)
            prices[c] *= (1 + ret)
            close = round(prices[c], 3)
            rows.append({
                "ts_code": c, "trade_date": d,
                "open": close, "high": round(close * 1.01, 3),
                "low": round(close * 0.99, 3), "close": close,
                "pre_close": close,
                "vol": int(rng.integers(100000, 5000000)),
                "amount": round(prices[c] * rng.integers(100000, 5000000), 2),
            })
    fund_daily = pd.DataFrame(rows)

    # ONE corporate action: 510010.SH adj_factor 1.0 -> 2.0 from week 65 Monday
    # (after rebalances begin at week 52, so the ETF is held when it fires).
    mid_date = dates[65 * 5]
    adj_rows = []
    for d in dates:
        for c in codes:
            factor = 2.0 if (c == "510010.SH" and d >= mid_date) else 1.0
            adj_rows.append({"ts_code": c, "trade_date": d, "adj_factor": factor})
    fund_adj = pd.DataFrame(adj_rows)

    # ONE missing close: 510020.SH on a Friday week-ending (week 30) so it
    # affects a weekly return and exercises missing-value semantics.
    missing_date = dates[30 * 5 + 4]
    mask = (fund_daily["ts_code"] == "510020.SH") & (fund_daily["trade_date"] == missing_date)
    fund_daily.loc[mask, ["open", "high", "low", "close", "pre_close", "vol", "amount"]] = np.nan

    dim_rows = [{"ts_code": c, "name": f"测试ETF{i}", "list_date": "20200101"}
                for i, c in enumerate(codes)]
    dim_fund = pd.DataFrame(dim_rows)
    return fund_daily, fund_adj, dim_fund


def build_config() -> FundRotationConfig:
    """Fixed golden config: real 52-week lookback, k=4, top_n=2."""
    return FundRotationConfig(
        k=4, top_n=2,
        min_training_weeks=52, correlation_lookback_weeks=52,
        min_valid_weeks=20, min_pairwise_weeks=20,
        recluster_interval_weeks=26, momentum_window_weeks=4,
        initial_capital=1_000_000,
        start_date="20220101", end_date="20230720",
    )


# ── normalization ──

def _series_to_map(series: pd.Series) -> dict[str, float]:
    if series is None or series.empty:
        return {}
    out: dict[str, float] = {}
    for idx, val in series.items():
        f = float(val)
        assert math.isfinite(f), f"non-finite NAV at {idx}: {val}"
        out[str(idx)] = f
    return out


def _clean_row(row: dict) -> dict:
    """Normalize one event/order row to JSON-safe Python scalars."""
    out: dict = {}
    for key, val in row.items():
        if isinstance(val, (np.integer,)):
            out[key] = int(val)
        elif isinstance(val, (np.floating, float)):
            f = float(val)
            assert math.isfinite(f), f"non-finite field {key}: {val}"
            out[key] = f
        elif isinstance(val, (int, str, bool)):
            out[key] = val
        elif val is None:
            out[key] = None
        else:
            out[key] = str(val)
    return out


def normalize(result) -> dict:
    """Reduce a PipelineResult to a deterministic, JSON-serializable structure."""
    norm: dict = {
        "scalars": {
            "num_weeks": int(result.num_weeks),
            "num_reclusters": int(result.num_reclusters),
            "num_etfs_used": int(result.num_etfs_used),
        },
        "rebalance_weeks": sorted(result.weekly_targets.keys()),
        "weekly_targets": {
            wk: {code: float(w) for code, w in sorted(targets.items())}
            for wk, targets in sorted(result.weekly_targets.items())
        },
        "cluster_history": [
            {
                "week": str(ch["week"]),
                "num_etfs": int(ch["num_etfs"]),
                "clusters": {str(c): int(cid) for c, cid in sorted(ch["clusters"].items())},
            }
            for ch in result.cluster_history
        ],
        "orders": [_clean_row(r) for r in result.orders],
        "trade_events": [_clean_row(r) for r in result.trade_events],
        "equity_curves": {
            "executed_equity": _series_to_map(result.executed_equity),
            "strategy_cumulative": _series_to_map(result.strategy_cumulative),
            "equal_weight_benchmark": _series_to_map(result.equal_weight_benchmark),
            "buy_hold_benchmark": _series_to_map(result.buy_hold_benchmark),
            "cash_benchmark": _series_to_map(result.cash_benchmark),
        },
        "positions_summary": [
            {
                "trade_date": str(p["trade_date"]),
                "cash": float(p["cash"]),
                "equity": float(p["equity"]),
                "positions": {str(c): int(q) for c, q in sorted(p["positions"].items())},
            }
            for p in result.positions_history
        ],
        "strategy_metrics": {k: float(v) for k, v in sorted(result.strategy_metrics.items())},
        "benchmark_metrics": {
            name: {k: float(v) for k, v in sorted(metrics.items())}
            for name, metrics in sorted(result.benchmark_metrics.items())
        },
    }
    return norm


def _run_and_normalize():
    fund_daily, fund_adj, dim_fund = build_golden_data()
    result = run_signal_pipeline(build_config(), fund_daily, fund_adj, dim_fund)
    return normalize(result)


# ── comparison ──

def _isclose(a: float, b: float, tol: dict) -> bool:
    return math.isclose(a, b, rel_tol=tol["rtol"], abs_tol=tol["atol"])


def _row_diffs(path: str, exp: dict, act: dict) -> list[str]:
    diffs: list[str] = []
    exp_keys, act_keys = set(exp), set(act)
    if exp_keys != act_keys:
        diffs.append(f"{path}: key set differs exp={sorted(exp_keys)} act={sorted(act_keys)}")
        return diffs
    for key in exp_keys:
        ev, av = exp[key], act[key]
        if isinstance(ev, bool) or isinstance(av, bool):
            if ev != av:
                diffs.append(f"{path}.{key}: {ev!r} != {av!r}")
            continue
        if isinstance(ev, (int, float)) and isinstance(av, (int, float)) and not isinstance(ev, bool):
            if key in _WEIGHT_KEYS:
                tol = WEIGHT_TOL
            elif key in _MONEY_KEYS:
                tol = MONEY_TOL
            else:
                # integer shares / counts / exact numeric (adj factors etc.)
                if ev != av:
                    diffs.append(f"{path}.{key}: {ev!r} != {av!r}")
                continue
            if not _isclose(float(ev), float(av), tol):
                diffs.append(f"{path}.{key}: {ev!r} != {av!r} (tol {tol})")
            continue
        if ev != av:
            diffs.append(f"{path}.{key}: {ev!r} != {av!r}")
    return diffs


def _curve_diffs(path: str, exp: dict, act: dict, tol: dict) -> list[str]:
    diffs: list[str] = []
    if set(exp) != set(act):
        exp_idx, act_idx = list(exp), list(act)
        diffs.append(
            f"{path}: index differs (len exp={len(exp_idx)} act={len(act_idx)}; "
            f"exp[:3]={exp_idx[:3]} act[:3]={act_idx[:3]})"
        )
        return diffs
    for date in exp:
        if not _isclose(exp[date], act[date], tol):
            diffs.append(f"{path}[{date}]: {exp[date]!r} != {act[date]!r} (tol {tol})")
    return diffs


def compare_golden(exp: dict, act: dict) -> list[str]:
    """Field-by-field comparison per design §35.1. Returns a list of diffs."""
    diffs: list[str] = []

    # Scalars (exact)
    for key in exp["scalars"]:
        if exp["scalars"][key] != act["scalars"][key]:
            diffs.append(f"scalars.{key}: {exp['scalars'][key]} != {act['scalars'][key]}")

    # Rebalance weeks (exact, ordered)
    if exp["rebalance_weeks"] != act["rebalance_weeks"]:
        diffs.append(
            f"rebalance_weeks differ: exp={exp['rebalance_weeks'][:3]}... "
            f"act={act['rebalance_weeks'][:3]}..."
        )

    # Weekly targets (keys exact, weights atol 1e-12)
    if set(exp["weekly_targets"]) != set(act["weekly_targets"]):
        diffs.append("weekly_targets week keys differ")
    else:
        for wk in exp["weekly_targets"]:
            ew, aw = exp["weekly_targets"][wk], act["weekly_targets"][wk]
            if set(ew) != set(aw):
                diffs.append(f"weekly_targets[{wk}] codes differ exp={sorted(ew)} act={sorted(aw)}")
                continue
            for code in ew:
                if not _isclose(ew[code], aw[code], WEIGHT_TOL):
                    diffs.append(f"weekly_targets[{wk}][{code}]: {ew[code]!r} != {aw[code]!r}")

    # Cluster history (exact)
    if exp["cluster_history"] != act["cluster_history"]:
        diffs.append("cluster_history differs")

    # Orders / trade events (row-wise mixed tolerance)
    for section in ("orders", "trade_events"):
        er, ar = exp[section], act[section]
        if len(er) != len(ar):
            diffs.append(f"{section}: length differs exp={len(er)} act={len(ar)}")
            continue
        for i, (e_row, a_row) in enumerate(zip(er, ar)):
            diffs.extend(_row_diffs(f"{section}[{i}]", e_row, a_row))

    # Equity curves (NAV atol 1e-10)
    for name in exp["equity_curves"]:
        diffs.extend(_curve_diffs(
            f"equity_curves.{name}",
            exp["equity_curves"][name], act["equity_curves"][name], NAV_TOL,
        ))

    # Positions summary (cash/equity money tol, integer shares exact)
    ep, ap = exp["positions_summary"], act["positions_summary"]
    if len(ep) != len(ap):
        diffs.append(f"positions_summary: length differs exp={len(ep)} act={len(ap)}")
    else:
        for i, (e_row, a_row) in enumerate(zip(ep, ap)):
            p = f"positions_summary[{i}]"
            if e_row["trade_date"] != a_row["trade_date"]:
                diffs.append(f"{p}.trade_date: {e_row['trade_date']} != {a_row['trade_date']}")
            for money_key in ("cash", "equity"):
                if not _isclose(e_row[money_key], a_row[money_key], MONEY_TOL):
                    diffs.append(f"{p}.{money_key}: {e_row[money_key]!r} != {a_row[money_key]!r}")
            if e_row["positions"] != a_row["positions"]:
                diffs.append(f"{p}.positions: {e_row['positions']} != {a_row['positions']}")

    # Metrics (atol 1e-9)
    if set(exp["strategy_metrics"]) != set(act["strategy_metrics"]):
        diffs.append("strategy_metrics keys differ")
    else:
        for key in exp["strategy_metrics"]:
            if not _isclose(exp["strategy_metrics"][key], act["strategy_metrics"][key], METRIC_TOL):
                diffs.append(
                    f"strategy_metrics.{key}: {exp['strategy_metrics'][key]!r} "
                    f"!= {act['strategy_metrics'][key]!r}"
                )
    if set(exp["benchmark_metrics"]) != set(act["benchmark_metrics"]):
        diffs.append("benchmark_metrics keys differ")
    else:
        for name in exp["benchmark_metrics"]:
            em, am = exp["benchmark_metrics"][name], act["benchmark_metrics"][name]
            if set(em) != set(am):
                diffs.append(f"benchmark_metrics.{name} keys differ")
                continue
            for key in em:
                if not _isclose(em[key], am[key], METRIC_TOL):
                    diffs.append(f"benchmark_metrics.{name}.{key}: {em[key]!r} != {am[key]!r}")

    return diffs


def _load_approved_delta() -> dict:
    if APPROVED_DELTA_PATH.exists():
        return json.loads(APPROVED_DELTA_PATH.read_text(encoding="utf-8"))
    return {}


# ── tests ──

def test_pipeline_is_deterministic():
    """Same synthetic input must produce an identical normalized result."""
    first = _run_and_normalize()
    second = _run_and_normalize()
    assert first == second, "pipeline normalization is not deterministic"


def test_matches_pre_fix_golden():
    """Current pipeline output must match the frozen pre-fix golden.

    When Phase 0 Tasks 2-7 introduce approved correctness fixes, record each
    expected deviation in approved_delta.json (Task 8) rather than widening
    these tolerances.
    """
    if os.environ.get("PHASE0_REGEN") == "1":
        FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
        GOLDEN_PATH.write_text(
            json.dumps(_run_and_normalize(), ensure_ascii=False, indent=1, sort_keys=True),
            encoding="utf-8",
        )
        pytest.skip("golden regenerated")

    assert GOLDEN_PATH.exists(), (
        "pre_fix_golden.json missing; regenerate with PHASE0_REGEN=1 before any "
        "Phase 0 code change"
    )
    expected = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    actual = _run_and_normalize()
    diffs = compare_golden(expected, actual)

    approved = _load_approved_delta()
    if approved:
        # Task 8 populates this; until then the golden must match exactly.
        diffs = [d for d in diffs if not _is_approved(d, approved)]

    assert not diffs, "golden divergence beyond approved delta:\n" + "\n".join(diffs[:50])


def _is_approved(diff: str, approved: dict) -> bool:
    """Match a diff line against approved_delta prefixes (Task 8 mechanism).

    Key-set changes are never approved: a missing or renamed metric field must
    fail the golden even when its namespace prefix is whitelisted.
    """
    if "keys differ" in diff:
        return False
    for prefix in approved.get("approved_prefixes", []):
        if diff.startswith(prefix):
            return True
    return False
