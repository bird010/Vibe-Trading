"""Pre-registered one-factor stress scenarios and frozen gate evaluation."""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class StressScenario:
    scenario_id: str
    dimension: str
    value: object
    unit: str
    one_factor: bool = True

    def as_dict(self) -> dict[str, object]:
        return {
            "scenario_id": self.scenario_id,
            "dimension": self.dimension,
            "value": self.value,
            "unit": self.unit,
            "one_factor": self.one_factor,
        }


@dataclass(frozen=True)
class StressEvaluation:
    status: str
    gates: dict[str, bool]
    break_even_transaction_cost_bps: float | None
    technical_failures: tuple[str, ...]
    reason_codes: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "gates": dict(self.gates),
            "break_even_transaction_cost_bps": self.break_even_transaction_cost_bps,
            "technical_failures": list(self.technical_failures),
            "reason_codes": list(self.reason_codes),
        }


def build_stress_scenarios() -> tuple[StressScenario, ...]:
    """Return all frozen one-factor scenarios in stable order."""

    scenarios: list[StressScenario] = [
        StressScenario(f"slippage_{bps}bps", "slippage", bps, "bps")
        for bps in (5, 10, 20, 30, 50)
    ]
    scenarios.extend(
        (
            StressScenario("delay_next_open", "delay", "next_open", "fill_timing"),
            StressScenario("delay_next_close", "delay", "next_close", "fill_timing"),
            StressScenario("delay_extra_1d", "delay", "extra_1d", "trading_day"),
        )
    )
    scenarios.extend(
        StressScenario(
            f"adv_participation_{int(rate * 100)}pct",
            "adv_participation",
            rate,
            "fraction",
        )
        for rate in (0.01, 0.02, 0.05)
    )
    scenarios.extend(
        (
            StressScenario("fees_baseline", "fees", "baseline", "fee_policy"),
            StressScenario("fees_double_commission", "fees", "double_commission", "fee_policy"),
            StressScenario("fees_minimum_commission", "fees", "minimum_commission", "fee_policy"),
        )
    )
    scenarios.extend(
        (
            StressScenario("tradability_suspended", "tradability", "suspended", "execution_rule"),
            StressScenario("tradability_limit_up_down", "tradability", "limit_up_down", "execution_rule"),
            StressScenario("tradability_missing_adv", "tradability", "missing_adv", "execution_rule"),
        )
    )
    return tuple(scenarios)


def _finite(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _excess(result: Mapping[str, object]) -> float | None:
    for key in ("annualized_excess_return", "excess_return", "excess_return_annualized"):
        value = _finite(result.get(key))
        if value is not None:
            return value
    return None


def _quality_failed(result: Mapping[str, object]) -> bool:
    if bool(result.get("execution_quality_failed", False)):
        return True
    quality = result.get("execution_quality_status")
    if quality is None or not str(quality).strip():
        return True
    quality = str(quality).upper()
    return quality not in {"PASS", "OK", "VALID", "COMPLETE"}


_SCENARIO_ALIASES = {
    "slippage_20_bps": "slippage_20bps",
    "one_day_delay": "delay_extra_1d",
    "adv_1pct": "adv_participation_1pct",
}


def _derive_break_even(results: Iterable[Mapping[str, object]]) -> float | None:
    explicit = [
        _finite(result.get("break_even_transaction_cost_bps"))
        for result in results
        if _finite(result.get("break_even_transaction_cost_bps")) is not None
    ]
    if explicit:
        return explicit[0]

    points: list[tuple[float, float]] = []
    for result in results:
        raw_scenario_id = str(result.get("scenario_id", ""))
        scenario_id = _SCENARIO_ALIASES.get(raw_scenario_id, raw_scenario_id)
        if not scenario_id.startswith("slippage_"):
            continue
        try:
            bps = float(scenario_id.removeprefix("slippage_").removesuffix("bps"))
        except ValueError:
            continue
        excess = _excess(result)
        if excess is not None:
            points.append((bps, excess))
    points.sort()
    for (left_cost, left_excess), (right_cost, right_excess) in zip(points, points[1:]):
        if left_excess == 0.0:
            return left_cost
        if left_excess * right_excess < 0.0:
            fraction = -left_excess / (right_excess - left_excess)
            return left_cost + fraction * (right_cost - left_cost)
    return None


def evaluate_stress_results(
    results: Iterable[Mapping[str, object]],
) -> StressEvaluation:
    """Apply the 20 bps, one-day delay and 1% ADV gates without tuning."""

    materialized = tuple(results)
    preregistered = {scenario.scenario_id for scenario in build_stress_scenarios()}
    by_id: dict[str, Mapping[str, object]] = {}
    gates: dict[str, bool] = {}
    technical_failures: list[str] = []
    reasons: list[str] = []

    for result in materialized:
        raw_id = str(result.get("scenario_id", ""))
        scenario_id = _SCENARIO_ALIASES.get(raw_id, raw_id)
        if scenario_id not in preregistered:
            technical_failures.append(f"UNREGISTERED_SCENARIO:{raw_id}")
            continue
        if scenario_id in by_id:
            technical_failures.append(f"DUPLICATE_SCENARIO:{scenario_id}")
            continue
        by_id[scenario_id] = result

    for scenario_id in sorted(preregistered - by_id.keys()):
        technical_failures.append(f"MISSING_SCENARIO:{scenario_id}")

    for scenario_id, result in by_id.items():
        quality_value = result.get("execution_quality_status")
        if quality_value is None or not str(quality_value).strip():
            technical_failures.append(f"MISSING_EXECUTION_QUALITY:{scenario_id}")
        elif _quality_failed(result):
            reasons.append("EXECUTION_QUALITY_FAILURE")

    required = ("slippage_20bps", "delay_extra_1d", "adv_participation_1pct")
    for scenario_id in required:
        result = by_id.get(scenario_id)
        excess = _excess(result) if result is not None else None
        if result is None or excess is None:
            technical_failures.append(f"MISSING_OR_NONFINITE:{scenario_id}")
            gates[scenario_id] = False
            continue
        if scenario_id == "adv_participation_1pct":
            gate = excess > 0.0 and not _quality_failed(result)
            if _quality_failed(result):
                reasons.append("EXECUTION_QUALITY_FAILURE")
        else:
            gate = excess > 0.0
        gates[scenario_id] = gate
        if not gate and scenario_id != "adv_participation_1pct":
            reasons.append(f"{scenario_id.upper()}_EXCESS_NOT_POSITIVE")

    if technical_failures:
        reasons.append("TECHNICAL_FAILURE")
    elif not all(gates.values()):
        reasons.append("STRESS_GATE_FAILED")

    status = "FAIL" if technical_failures or reasons else "PASS"
    return StressEvaluation(
        status=status,
        gates=gates,
        break_even_transaction_cost_bps=_derive_break_even(materialized),
        technical_failures=tuple(technical_failures),
        reason_codes=tuple(dict.fromkeys(reasons)),
    )
