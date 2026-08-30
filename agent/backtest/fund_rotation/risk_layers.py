"""Small, fail-closed risk-layer helpers for research-only strategies."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence


def _finite(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def apply_volatility_target(
    target_weights: Mapping[str, object],
    *,
    portfolio_volatility: object,
    target_volatility: object,
) -> tuple[dict[str, float], float, dict[str, object]]:
    """Scale existing long-only targets by ``min(1, target / volatility)``."""
    target = _finite(target_volatility)
    volatility = _finite(portfolio_volatility)
    if target is None or target <= 0.0:
        return {}, 1.0, {
            "exposure": 0.0,
            "leverage": False,
            "reason": "target_volatility_unavailable",
        }
    if volatility is None or volatility <= 0.0:
        return {}, 1.0, {
            "exposure": 0.0,
            "leverage": False,
            "reason": "portfolio_volatility_unavailable",
        }

    validated: dict[str, float] = {}
    for code, raw_weight in target_weights.items():
        weight = _finite(raw_weight)
        if not isinstance(code, str) or not code or weight is None or weight < 0.0:
            return {}, 1.0, {
                "exposure": 0.0,
                "leverage": False,
                "reason": "target_weights_invalid",
            }
        validated[code] = weight
    if sum(validated.values()) > 1.0 + 1e-12:
        return {}, 1.0, {
            "exposure": 0.0,
            "leverage": False,
            "reason": "target_weights_invalid",
        }
    exposure = min(1.0, target / volatility)
    scaled = {code: weight * exposure for code, weight in validated.items()}
    cash = max(0.0, 1.0 - sum(scaled.values()))
    return scaled, cash, {
        "exposure": exposure,
        "target_volatility": target,
        "portfolio_volatility": volatility,
        "leverage": False,
        "reason": None,
    }


def compute_portfolio_volatility(weekly_returns: object, target_weights: Mapping[str, object]) -> float | None:
    """Compute annualized population volatility from causal weekly returns."""
    if not hasattr(weekly_returns, "columns") or not hasattr(weekly_returns, "iloc"):
        return None
    try:
        columns = list(target_weights)
        if not columns or any(code not in weekly_returns.columns for code in columns):
            return None
        if not columns or len(weekly_returns) < 2:
            return None
        weights = {code: _finite(target_weights[code]) for code in columns}
        if any(value is None or value < 0.0 for value in weights.values()):
            return None
        frame = weekly_returns[columns].apply(lambda value: value.astype(float))
        if frame.isna().any().any():
            return None
        portfolio_returns = frame.mul([weights[code] for code in columns], axis=1).sum(axis=1)
        values = [float(value) for value in portfolio_returns.tolist()]
    except (AttributeError, KeyError, TypeError, ValueError):
        return None
    if len(values) < 2 or any(not math.isfinite(value) for value in values):
        return None
    mean = sum(values) / len(values)
    volatility = math.sqrt(sum((value - mean) ** 2 for value in values) / len(values)) * math.sqrt(52.0)
    return volatility if math.isfinite(volatility) and volatility > 0.0 else None


def select_defense_asset(
    arm: str,
    *,
    fixed_short_bond: str,
    relative_scores: Mapping[str, object],
) -> str | None:
    """Select one predeclared defense asset; invalid relative scores mean cash."""
    if arm == "cash":
        return None
    if arm == "fixed_short_bond":
        return fixed_short_bond if isinstance(fixed_short_bond, str) and fixed_short_bond else None
    if arm != "relative_momentum":
        raise ValueError(f"unknown defense arm: {arm}")
    candidates = []
    for raw_code, raw_score in relative_scores.items():
        code = str(raw_code)
        score = _finite(raw_score)
        if score is not None and score > 0.0:
            candidates.append((code, score))
    candidates.sort(key=lambda item: (-item[1], item[0]))
    return candidates[0][0] if candidates else None


def apply_defense_asset(
    target_weights: Mapping[str, object],
    cash_weight: object,
    *,
    defense_code: str | None,
) -> tuple[dict[str, float], float, dict[str, object]]:
    """Move only existing cash to one selected defense asset."""
    cash = _finite(cash_weight)
    if cash is None or cash < 0.0:
        return {}, 1.0, {"reason": "cash_weight_invalid", "defense_code": None}
    weights = {str(code): float(weight) for code, weight in target_weights.items()}
    if defense_code is None or cash == 0.0:
        return weights, cash, {"reason": "cash_fallback", "defense_code": defense_code}
    weights[defense_code] = weights.get(defense_code, 0.0) + cash
    return weights, 0.0, {"reason": None, "defense_code": defense_code}


def count_identity_breadth(
    positive_codes: Sequence[str],
    available_codes: Sequence[str],
    identity_by_code: Mapping[str, object],
) -> dict[str, object]:
    """Count positive and available breadth by independent U1 identities."""
    available = {str(code) for code in available_codes}
    missing = [
        code
        for code in available
        if not isinstance(identity_by_code.get(code), str) or not identity_by_code.get(code)
    ]
    if not available or missing:
        return {
            "positive_identity_count": None,
            "available_identity_count": None,
            "breadth": None,
            "status": "UNAVAILABLE",
        }
    identities = {identity_by_code[code] for code in available}
    positive_identities = {
        identity_by_code.get(str(code)) for code in positive_codes if str(code) in available
    }
    positive_identities.discard(None)
    return {
        "positive_identity_count": len(positive_identities),
        "available_identity_count": len(identities),
        "breadth": len(positive_identities) / len(identities),
        "status": "VALID",
    }
