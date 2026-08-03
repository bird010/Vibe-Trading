"""Strict common-calendar comparisons — Phase 4 Task 5 (§22/§27).

Only technically successful sub-runs whose equity index exactly equals the
shared evaluation calendar participate. Metrics are recomputed from raw equity
and the comparison fingerprint binds the actual resolved execution contract,
not a static version label.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

import pandas as pd

from backtest.fund_rotation.metrics import compute_performance_metrics


CONTRACT_VERSIONS = {
    "universe_policy_version": "v1",
    "return_policy_version": "v1",
    "benchmark_contract_version": "v1",
    "metric_contract_version": "v1",
}

CONTRACT_COMPONENT_KEYS = {
    "framework_implementation_hash",
    "data_snapshot_fingerprint",
    "evaluation_calendar_hash",
    "universe_policy_version",
    "return_policy_version",
    "execution_contract",
    "benchmark_contract_version",
    "metric_contract_version",
}

EXCLUDED_TECHNICAL_FAILURE = "TECHNICAL_FAILURE"
EXCLUDED_CANCELED = "CANCELED"
EXCLUDED_DECISION_INVALID = "DECISION_INVALID"
EXCLUDED_CALENDAR_MISMATCH = "CALENDAR_MISMATCH"
EXCLUDED_NO_EQUITY = "NO_EQUITY"


@dataclass(frozen=True)
class VariantComparisonInput:
    variant_key: str
    strategy_id: str
    run_id: str
    status: str
    equity: pd.Series
    decision_quality: str = "VALID"
    has_invalid_action: bool = False


@dataclass
class ComparisonOutcome:
    contract_components: dict[str, str]
    contract_fingerprint: str
    equity_frame: pd.DataFrame
    metrics: dict[str, dict[str, float]]
    ranking: list[dict[str, Any]] = field(default_factory=list)
    excluded: list[dict[str, str]] = field(default_factory=list)
    quality_warnings: list[dict[str, str]] = field(default_factory=list)


def _canonical_hash(value: object) -> str:
    canonical = json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def evaluation_calendar_hash(calendar: Sequence[str]) -> str:
    """Hash the exact ordered evaluation calendar."""
    canonical = json.dumps(
        [str(date) for date in calendar],
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def comparison_contract_fingerprint(
    *,
    framework_implementation_hash: str,
    data_snapshot_fingerprint: str,
    evaluation_calendar: Sequence[str],
    execution_contract: Mapping[str, object] | None = None,
) -> tuple[dict[str, str], str]:
    """Return the eight comparison-contract components and fingerprint.

    ``execution_contract`` must be the fully resolved common execution config,
    including server defaults. The component stores its canonical hash while
    the resolved document is persisted separately in child specifications.
    """
    resolved_execution = dict(execution_contract or {"version": "v1"})
    components = {
        "framework_implementation_hash": framework_implementation_hash,
        "data_snapshot_fingerprint": data_snapshot_fingerprint,
        "evaluation_calendar_hash": evaluation_calendar_hash(
            evaluation_calendar
        ),
        "execution_contract": _canonical_hash(resolved_execution),
        **CONTRACT_VERSIONS,
    }
    if set(components) != CONTRACT_COMPONENT_KEYS:
        raise ValueError(
            "comparison contract registry mismatch: "
            f"expected {sorted(CONTRACT_COMPONENT_KEYS)}, "
            f"got {sorted(components)}"
        )
    return components, _canonical_hash(components)


def build_comparison(
    inputs: Sequence[VariantComparisonInput],
    *,
    evaluation_calendar: Sequence[str],
    framework_implementation_hash: str,
    data_snapshot_fingerprint: str,
    execution_contract: Mapping[str, object] | None = None,
    initial_nav: float = 1.0,
) -> ComparisonOutcome:
    components, fingerprint = comparison_contract_fingerprint(
        framework_implementation_hash=framework_implementation_hash,
        data_snapshot_fingerprint=data_snapshot_fingerprint,
        evaluation_calendar=evaluation_calendar,
        execution_contract=execution_contract,
    )

    calendar_index = pd.Index(list(evaluation_calendar))
    ranked_entries: list[dict[str, Any]] = []
    metrics: dict[str, dict[str, float]] = {}
    excluded: list[dict[str, str]] = []
    quality_warnings: list[dict[str, str]] = []
    displayed: dict[str, pd.Series] = {}

    for variant in inputs:
        key = variant.variant_key
        if variant.status == "CANCELED":
            excluded.append({"variant_key": key, "reason": EXCLUDED_CANCELED})
            continue
        if variant.status != "SUCCEEDED":
            excluded.append(
                {"variant_key": key, "reason": EXCLUDED_TECHNICAL_FAILURE}
            )
            continue
        if variant.has_invalid_action:
            excluded.append(
                {"variant_key": key, "reason": EXCLUDED_DECISION_INVALID}
            )
            continue
        equity = variant.equity
        if equity is None or equity.empty:
            excluded.append({"variant_key": key, "reason": EXCLUDED_NO_EQUITY})
            continue
        normalized_index = pd.Index([str(value) for value in equity.index])
        if not normalized_index.equals(calendar_index):
            excluded.append(
                {"variant_key": key, "reason": EXCLUDED_CALENDAR_MISMATCH}
            )
            continue

        if variant.decision_quality not in ("VALID", "DEGRADED", "INVALID"):
            excluded.append(
                {"variant_key": key, "reason": EXCLUDED_TECHNICAL_FAILURE}
            )
            continue

        normalized_equity = equity.copy()
        normalized_equity.index = normalized_index
        metrics[key] = compute_performance_metrics(
            normalized_equity,
            periods_per_year=244,
            initial_nav=initial_nav,
        )
        displayed[key] = normalized_equity

        if variant.decision_quality == "INVALID":
            quality_warnings.append(
                {
                    "variant_key": key,
                    "reason": "RESEARCH_QUALITY_INVALID",
                    "message": (
                        "research quality INVALID: NAV preserved for display, "
                        "excluded from ranking"
                    ),
                }
            )
            continue

        variant_metrics = metrics[key]
        ranked_entries.append(
            {
                "variant_key": key,
                "strategy_id": variant.strategy_id,
                "run_id": variant.run_id,
                "quality_status": variant.decision_quality,
                "annual_return": variant_metrics.get("annual_return", 0.0),
                "total_return": variant_metrics.get("total_return", 0.0),
                "sharpe": variant_metrics.get("sharpe", 0.0),
                "max_drawdown": variant_metrics.get("max_drawdown", 0.0),
                "calmar": variant_metrics.get("calmar", 0.0),
            }
        )

    ranked_entries.sort(
        key=lambda entry: (-entry["annual_return"], entry["variant_key"])
    )
    for rank, entry in enumerate(ranked_entries, start=1):
        entry["rank"] = rank

    equity_frame = (
        pd.DataFrame(displayed)
        if displayed
        else pd.DataFrame(index=calendar_index)
    )

    return ComparisonOutcome(
        contract_components=components,
        contract_fingerprint=fingerprint,
        equity_frame=equity_frame,
        metrics=metrics,
        ranking=ranked_entries,
        excluded=excluded,
        quality_warnings=quality_warnings,
    )
