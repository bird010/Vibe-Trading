"""Pure, causal factor and selection functions for R57."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping

import numpy as np
import pandas as pd


def _finite_positive(values: pd.Series) -> bool:
    numeric = pd.to_numeric(values, errors="coerce").to_numpy(dtype=float)
    return bool(len(numeric) and np.isfinite(numeric).all() and (numeric > 0).all())


def _date_key(value: object) -> str:
    raw = str(value).strip()
    if re.fullmatch(r"\d{8}", raw):
        return raw
    try:
        return pd.Timestamp(value).strftime("%Y%m%d")
    except (TypeError, ValueError):
        raise ValueError(f"invalid trade date: {value!r}") from None


def adjust_ohlc(bars: pd.DataFrame, adjustments: pd.DataFrame, signal_date: str) -> pd.DataFrame:
    required = {"ts_code", "trade_date", "open", "high", "low", "close"}
    if not required <= set(bars.columns) or not {"ts_code", "trade_date", "adj_factor"} <= set(adjustments.columns):
        raise ValueError("bars and adjustments have incompatible columns")
    left, right = bars.copy(), adjustments.copy()
    keys = ["ts_code", "trade_date"]
    if left.duplicated(keys).any() or right.duplicated(keys).any():
        raise ValueError("duplicate ts_code/trade_date keys")
    left["ts_code"] = left["ts_code"].astype(str)
    right["ts_code"] = right["ts_code"].astype(str)
    left["trade_date"] = left["trade_date"].map(_date_key)
    right["trade_date"] = right["trade_date"].map(_date_key)
    signal_key = _date_key(signal_date)
    left = left[left["trade_date"] <= signal_key].copy()
    right = right[right["trade_date"] <= signal_key].copy()
    if left.empty or right.empty:
        raise ValueError("no causal adjustment data through signal date")
    factors = right[right["trade_date"].eq(signal_key)][["ts_code", "adj_factor"]].copy()
    if factors.empty or factors["ts_code"].duplicated().any():
        raise ValueError("missing signal-date adjustment factor")
    factors["adj_factor"] = pd.to_numeric(factors["adj_factor"], errors="coerce")
    if factors["adj_factor"].isna().any() or (factors["adj_factor"] <= 0).any():
        raise ValueError("invalid signal-date adjustment factor")
    merged = left.merge(right[keys + ["adj_factor"]], on=keys, how="left", validate="one_to_one")
    merged = merged.merge(factors.rename(columns={"adj_factor": "signal_adj_factor"}), on="ts_code", how="left", validate="many_to_one")
    if merged["adj_factor"].isna().any() or merged["signal_adj_factor"].isna().any():
        raise ValueError("missing adjustment coverage")
    ratio = merged["adj_factor"] / merged["signal_adj_factor"]
    for field in ("open", "high", "low", "close"):
        merged[field] = pd.to_numeric(merged[field], errors="coerce") * ratio
    return merged.drop(columns=["adj_factor", "signal_adj_factor"])


def _ols_slope_r2(values: np.ndarray, x: np.ndarray) -> tuple[float, float] | None:
    if len(values) != len(x) or len(values) < 2 or not np.isfinite(values).all():
        return None
    slope, intercept = np.polyfit(x, values, 1)
    if not math.isfinite(float(slope)) or not math.isfinite(float(intercept)):
        return None
    ss_tot = float(np.dot(values - values.mean(), values - values.mean()))
    if ss_tot == 0:
        return 0.0, 0.0
    residual = values - (intercept + slope * x)
    r2 = 1.0 - float(np.dot(residual, residual)) / ss_tot
    return float(slope), float(min(1.0, max(0.0, r2)))


def compute_bias_momentum(adjusted_close: pd.Series, ma_days: int = 25, regression_days: int = 25) -> float | None:
    values = pd.to_numeric(adjusted_close, errors="coerce").to_numpy(dtype=float)
    required = ma_days + regression_days - 1
    if len(values) < required or not np.isfinite(values).all() or (values <= 0).any():
        return None
    bias = pd.Series(values) / pd.Series(values).rolling(ma_days).mean()
    recent = bias.iloc[-regression_days:].to_numpy(dtype=float)
    if not np.isfinite(recent).all() or (recent <= 0).any():
        return None
    result = _ols_slope_r2(recent / recent[0], np.arange(regression_days, dtype=float))
    return None if result is None else 10000.0 * result[0]


def compute_slope_momentum(adjusted_close: pd.Series, lookback_days: int = 25) -> float | None:
    values = pd.to_numeric(adjusted_close, errors="coerce").to_numpy(dtype=float)
    if len(values) < lookback_days:
        return None
    values = values[-lookback_days:]
    if not np.isfinite(values).all() or (values <= 0).any():
        return None
    result = _ols_slope_r2(values / values[0], np.arange(1, lookback_days + 1, dtype=float))
    return None if result is None else 10000.0 * result[0] * result[1]


def compute_efficiency_momentum(adjusted_ohlc: pd.DataFrame, lookback_days: int = 25) -> float | None:
    if not {"open", "high", "low", "close"} <= set(adjusted_ohlc.columns) or len(adjusted_ohlc) < lookback_days:
        return None
    data = adjusted_ohlc[["open", "high", "low", "close"]].apply(pd.to_numeric, errors="coerce").tail(lookback_days)
    if any(not _finite_positive(data[col]) for col in data.columns):
        return None
    log_pivot = np.log(data.mean(axis=1).to_numpy(dtype=float))
    changes = np.diff(log_pivot)
    volatility = float(np.abs(changes).sum())
    direction = abs(float(log_pivot[-1] - log_pivot[0]))
    er = 0.0 if volatility == 0 else direction / volatility
    if er < -1e-12 or er > 1 + 1e-12 or not math.isfinite(er):
        return None
    er = min(1.0, max(0.0, er))
    return 100.0 * float(log_pivot[-1] - log_pivot[0]) * er


def score_complete_candidates(raw_scores: Mapping[str, Mapping[str, float | None]], weights: Mapping[str, float], minimum_candidates: int = 2) -> tuple[dict[str, float], dict[str, object]]:
    factors = ("bias", "slope", "efficiency")
    if set(weights) != set(factors) or any(
        not math.isfinite(float(weights[name])) or float(weights[name]) < 0
        for name in factors
    ) or abs(sum(float(weights[name]) for name in factors) - 1.0) > 1e-12:
        raise ValueError("factor weights must be finite, non-negative, and sum to 1")
    complete = sorted(code for code, row in raw_scores.items() if all(row.get(name) is not None and math.isfinite(float(row[name])) for name in factors))
    details: dict[str, object] = {"complete_candidates": complete, "standardization": {}}
    if len(complete) < minimum_candidates:
        return {}, details
    composite: dict[str, float] = {code: 0.0 for code in complete}
    for name in factors:
        values = np.array([float(raw_scores[code][name]) for code in complete], dtype=float)
        mean, std = float(values.mean()), float(values.std(ddof=0))
        z = np.zeros(len(values)) if abs(std) <= 1e-12 else (values - mean) / std
        details["standardization"][name] = {"mean": mean, "std": std, "z_scores": {code: float(z[i]) for i, code in enumerate(complete)}}
        for i, code in enumerate(complete):
            composite[code] += float(weights[name]) * float(z[i])
    return dict(sorted(composite.items(), key=lambda item: (-item[1], item[0]))), details


def apply_rebalance_threshold(ranked_scores: Mapping[str, float], previous_target: str | None, threshold: float = 1.5) -> tuple[str | None, str, dict[str, object]]:
    ranked = sorted(((str(code), float(score)) for code, score in ranked_scores.items()), key=lambda item: (-item[1], item[0]))
    if not ranked:
        return None, "SET_TARGETS", {"threshold": threshold, "threshold_passed": False, "negative_threshold_case": False}
    challenger, challenger_score = ranked[0]
    if previous_target is None or previous_target not in ranked_scores:
        return challenger, "SET_TARGETS", {"threshold": threshold, "threshold_passed": True, "negative_threshold_case": False, "held_score": None, "challenger_score": challenger_score, "threshold_right_side": None}
    held_score = float(ranked_scores[previous_target])
    if challenger == previous_target:
        return challenger, "HOLD_TARGETS", {"threshold": threshold, "threshold_passed": False, "negative_threshold_case": False, "held_score": held_score, "challenger_score": challenger_score, "threshold_right_side": threshold * held_score}
    right_side = threshold * held_score
    passed = challenger_score > right_side
    return (challenger if passed else previous_target), ("SET_TARGETS" if passed else "HOLD_TARGETS"), {"threshold": threshold, "threshold_passed": passed, "negative_threshold_case": bool(held_score < 0 or challenger_score < 0 or right_side < 0), "held_score": held_score, "challenger_score": challenger_score, "threshold_right_side": right_side}
