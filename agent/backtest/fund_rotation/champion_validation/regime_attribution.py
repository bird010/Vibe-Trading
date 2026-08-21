"""Causal, post-hoc regime and concentration attribution for Champion validation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import math
from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np

from ..attribution import compute_concentration_metrics


REGIME_KIND = "POST_HOC_ANALYTICS_ONLY"
INCONCLUSIVE = "INCONCLUSIVE"


@dataclass(frozen=True)
class RegimeLabel:
    """A decision-time-known label; it is deliberately not tradable."""

    date: Any
    fold: str
    market: str
    volatility: str
    trend: str
    correlation: str
    thresholds: Mapping[str, float]
    can_drive_trading: bool = False

    @property
    def label(self) -> str:
        return self.market

    @property
    def regime(self) -> str:
        return "|".join((self.market, self.volatility, self.trend, self.correlation))

    @property
    def labels(self) -> dict[str, str]:
        return {
            "market": self.market,
            "volatility": self.volatility,
            "trend": self.trend,
            "correlation": self.correlation,
        }

    @property
    def regime_kind(self) -> str:
        return REGIME_KIND


def classify_regimes(
    features: Sequence[Mapping[str, Any]] | Mapping[Any, Mapping[str, Any]] | Any,
    train_masks: Mapping[str, Sequence[bool] | Mapping[Any, bool]] | Sequence[bool],
) -> tuple[RegimeLabel, ...]:
    """Fit fold thresholds on train rows and classify validation rows.

    The input features must already be lagged to the decision timestamp.  If a
    row supplies ``available_at``, it is checked against ``date`` to make an
    accidental look-ahead fail loudly.
    """

    rows = _as_records(features)
    if not rows:
        return ()
    masks = _normalise_masks(train_masks, rows)
    results: list[RegimeLabel] = []
    for index, row in enumerate(rows):
        row_date = row.get("date", row.get("timestamp", index))
        if "available_at" in row and _time_key(row["available_at"]) > _time_key(row_date):
            raise ValueError("feature is not available at decision time")
        fold = str(row.get("fold", next(iter(masks))))
        mask = masks.get(fold) or masks.get("__default__")
        if mask is None:
            raise ValueError(f"missing train mask for fold {fold}")
        train_rows = [candidate for candidate, is_train in zip(rows, mask, strict=True) if is_train]
        if not train_rows:
            raise ValueError(f"fold {fold} has no training observations")
        thresholds = {
            key: _median(train_rows, key)
            for key in ("volatility_13w", "trend_strength", "correlation")
        }
        benchmark_return = _value(row, "benchmark_return_26w", "benchmark_return", "market_return_26w")
        volatility = _value(row, "volatility_13w", "volatility")
        trend = _value(row, "trend_strength", "trend")
        correlation = _value(row, "correlation", "average_correlation", "mean_correlation")
        results.append(
            RegimeLabel(
                date=row_date,
                fold=fold,
                market="Bull" if benchmark_return > 0 else "Bear",
                volatility="High Vol" if volatility > thresholds["volatility_13w"] else "Low Vol",
                trend="Trend" if trend > thresholds["trend_strength"] else "Range",
                correlation="High Correlation" if correlation > thresholds["correlation"] else "Low Correlation",
                thresholds=thresholds,
            )
        )
    return tuple(results)


def compute_regime_and_concentration(
    observations: Sequence[Mapping[str, Any]] | Mapping[Any, Mapping[str, Any]] | None = None,
    *,
    period_returns: Mapping[Any, float] | Sequence[float] | None = None,
    regimes: Mapping[Any, str | RegimeLabel] | Sequence[str | RegimeLabel] | None = None,
    excess_returns: Mapping[Any, float] | Sequence[float] | None = None,
    cost_after_alpha: float | None = None,
    low_vol_cash_threshold: float = 0.5,
) -> dict[str, Any]:
    """Return regime metrics and concentration evidence with three-state gating."""

    rows = _normalise_observations(observations, period_returns, regimes, excess_returns)
    if not rows:
        return {
            "status": INCONCLUSIVE,
            "reason_codes": ["NO_OBSERVATIONS"],
            "groups": {name: {} for name in ("regime", "etf", "year", "fold", "cluster")},
            "concentration": {"pnl_hhi": 0.0, "effective_sources": 0.0},
        }

    groups = {
        dimension: _group_metrics(rows, dimension)
        for dimension in ("regime", "etf", "year", "fold", "cluster")
    }
    contributions = {
        dimension: _positive_contributions(rows, dimension)
        for dimension in ("etf", "year", "fold", "cluster", "trade_id")
    }
    concentration = {
        "by_etf": _concentration(contributions["etf"]),
        "top_5_etf": _top_share(contributions["etf"], 5),
        "top_10_trades": _top_share(contributions["trade_id"], 10),
        "by_year": _concentration(contributions["year"]),
        "by_fold": _concentration(contributions["fold"]),
        "by_cluster": _concentration(contributions["cluster"]),
    }
    concentration["etf"] = concentration["by_etf"]
    concentration["year"] = concentration["by_year"]
    concentration["fold"] = concentration["by_fold"]
    concentration["cluster"] = concentration["by_cluster"]
    shares = _shares(contributions["etf"])
    hhi = sum(share * share for share in shares)
    concentration["pnl_hhi"] = hhi
    concentration["effective_sources"] = 1.0 / hhi if hhi > 0 else 0.0

    reason_codes: list[str] = []
    if any(
        _max_group_share(values) > 0.5
        for values in (contributions["etf"], contributions["year"], contributions["fold"], contributions["cluster"], contributions["trade_id"])
    ):
        reason_codes.append("CONCENTRATION_OVER_50_PERCENT")
    regime_values = [metric["excess_return"] for metric in groups["regime"].values()]
    if len(regime_values) > 1 and sum(value > 0 for value in regime_values) == 1:
        reason_codes.append("EXCESS_RETURN_ONLY_ONE_REGIME")
    if regime_values and sum(value < 0 for value in regime_values) > len(regime_values) / 2:
        reason_codes.append("MORE_THAN_HALF_REGIMES_NEGATIVE")
    low_vol = [row for row in rows if "Low Vol" in str(row["regime"])]
    if low_vol and cost_after_alpha is not None and cost_after_alpha <= 0:
        if np.mean([float(row["cash_ratio"]) for row in low_vol]) >= low_vol_cash_threshold:
            reason_codes.append("LOW_VOL_EXPLAINED_BY_CASH")
    return {
        "status": INCONCLUSIVE if reason_codes else "PASS",
        "reason_codes": reason_codes,
        "groups": groups,
        "regime": groups["regime"],
        "etf": groups["etf"],
        "year": groups["year"],
        "fold": groups["fold"],
        "cluster": groups["cluster"],
        "concentration": concentration,
        "regime_kind": REGIME_KIND,
        "can_drive_trading": False,
    }


def _as_records(features: Any) -> list[dict[str, Any]]:
    if hasattr(features, "to_dict"):
        return [dict(row) for row in features.to_dict("records")]
    if isinstance(features, Mapping):
        return [dict(value, date=key) for key, value in features.items()]
    return [dict(row) for row in features]


def _normalise_masks(train_masks: Any, rows: list[dict[str, Any]]) -> dict[str, list[bool]]:
    if isinstance(train_masks, Mapping):
        if train_masks and all(isinstance(value, bool) for value in train_masks.values()):
            return {"__default__": [bool(train_masks.get(row.get("date"), False)) for row in rows]}
        result: dict[str, list[bool]] = {}
        for fold, mask in train_masks.items():
            if isinstance(mask, Mapping):
                result[str(fold)] = [bool(mask.get(row.get("date"), False)) for row in rows]
            else:
                values = list(mask)
                if len(values) != len(rows):
                    raise ValueError(f"train mask for fold {fold} must match feature length")
                result[str(fold)] = [bool(value) for value in values]
        return result
    values = list(train_masks)
    if len(values) != len(rows):
        raise ValueError("train mask must match feature length")
    return {"__default__": [bool(value) for value in values]}


def _value(row: Mapping[str, Any], *names: str) -> float:
    for name in names:
        if name in row and row[name] is not None:
            value = float(row[name])
            if math.isfinite(value):
                return value
    raise ValueError(f"missing finite regime feature: {names[0]}")


def _median(rows: Sequence[Mapping[str, Any]], key: str) -> float:
    aliases = {
        "volatility_13w": ("volatility_13w", "volatility"),
        "trend_strength": ("trend_strength", "trend"),
        "correlation": ("correlation", "average_correlation", "mean_correlation"),
    }
    values = [_value(row, *aliases[key]) for row in rows]
    return float(np.median(values))


def _time_key(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)


def _normalise_observations(observations: Any, period_returns: Any, regimes: Any, excess_returns: Any) -> list[dict[str, Any]]:
    if observations is not None:
        raw = _as_records(observations)
    else:
        if period_returns is None:
            return []
        if isinstance(period_returns, Mapping):
            keys = list(period_returns)
            values = list(period_returns.values())
        else:
            values = list(period_returns)
            keys = list(range(len(values)))
        raw = []
        for index, (key, value) in enumerate(zip(keys, values, strict=True)):
            regime = _lookup(regimes, key, index, "Unknown")
            raw.append({"date": key, "return": value, "excess_return": _lookup(excess_returns, key, index, value), "regime": regime})
    result = []
    for index, row in enumerate(raw):
        date_value = row.get("date", row.get("timestamp", index))
        regime = row.get("regime", row.get("regime_label", "Unknown"))
        if isinstance(regime, RegimeLabel):
            regime = regime.regime
        contribution = float(row.get("pnl_contribution", row.get("contribution", row.get("return", 0.0))))
        result.append({
            "date": date_value,
            "return": float(row.get("return", 0.0)),
            "excess_return": float(row.get("excess_return", row.get("return", 0.0))),
            "regime": str(regime),
            "etf": str(row.get("etf", row.get("symbol", "Unknown"))),
            "year": str(row.get("year", str(date_value)[:4])),
            "fold": str(row.get("fold", "Unknown")),
            "cluster": str(row.get("cluster", "Unknown")),
            "trade_id": str(row.get("trade_id", row.get("trade", index))),
            "pnl_contribution": contribution,
            "exposure": float(row.get("exposure", 0.0)),
            "cash_ratio": float(row.get("cash_ratio", row.get("cash_exposure", 0.0))),
        })
    return result


def _lookup(values: Any, key: Any, index: int, default: Any) -> Any:
    if values is None:
        return default
    if isinstance(values, Mapping):
        return values.get(key, default)
    sequence = list(values)
    return sequence[index]


def _group_metrics(rows: Sequence[Mapping[str, Any]], dimension: str) -> dict[str, dict[str, float]]:
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row[dimension]), []).append(row)
    result: dict[str, dict[str, float]] = {}
    for key, values in grouped.items():
        returns = np.asarray([row["return"] for row in values], dtype=float)
        excess = np.asarray([row["excess_return"] for row in values], dtype=float)
        wealth = np.cumprod(1.0 + returns)
        years = len(returns) / 52.0
        cagr = float(wealth[-1] ** (1.0 / years) - 1.0) if years and wealth[-1] > 0 else -1.0
        volatility = float(np.std(returns, ddof=1)) if len(returns) > 1 else 0.0
        sharpe = float(np.mean(returns) / volatility * np.sqrt(52.0)) if volatility > 0 else 0.0
        drawdown = wealth / np.maximum.accumulate(wealth) - 1.0
        result[key] = {
            "cagr": cagr,
            "sharpe": sharpe,
            "mdd": float(np.min(drawdown)),
            "win_rate": float(np.mean(returns > 0)),
            "excess_return": float(np.sum(excess)),
            "exposure": float(np.mean([row["exposure"] for row in values])),
            "cash_ratio": float(np.mean([row["cash_ratio"] for row in values])),
            "pnl_contribution": float(np.sum([row["pnl_contribution"] for row in values])),
        }
    return result


def _positive_contributions(rows: Sequence[Mapping[str, Any]], dimension: str) -> dict[str, float]:
    result: dict[str, float] = {}
    for row in rows:
        key = str(row[dimension])
        result[key] = result.get(key, 0.0) + max(float(row["pnl_contribution"]), 0.0)
    return result


def _shares(contributions: Mapping[str, float]) -> list[float]:
    total = sum(contributions.values())
    return [value / total for value in contributions.values()] if total > 0 else []


def _max_group_share(contributions: Mapping[str, float]) -> float:
    return max(_shares(contributions), default=0.0)


def _top_share(contributions: Mapping[str, float], top_n: int) -> dict[str, float | str]:
    positive = {key: value for key, value in contributions.items() if value > 0}
    if not positive:
        return {"top_contribution_share": 0.0, "quality_flag": "OK"}
    total = sum(positive.values())
    share = sum(sorted(positive.values(), reverse=True)[:top_n]) / total
    return {
        "top_contribution_share": share,
        "quality_flag": "RETURN_CONCENTRATION_WARNING" if share > 0.5 else "OK",
    }


def _concentration(contributions: Mapping[str, float]) -> dict[str, float | str]:
    # Use the existing attribution primitive for the common top-one accounting convention.
    positive = {key: value for key, value in contributions.items() if value > 0}
    return compute_concentration_metrics(positive, top_n=1, warning_threshold=0.5)
