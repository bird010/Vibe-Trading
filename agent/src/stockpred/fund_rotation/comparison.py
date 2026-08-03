"""Strict common-calendar comparisons — Phase 4 Task 5 (§22/§27).

Only technically successful sub-runs whose equity index EXACTLY equals the
shared evaluation calendar participate; the interval is never shortened by
date intersection. Metrics are recomputed from each variant's RAW equity and
the common ``initial_nav=1.0`` — display fields are never reused.

Research quality is four-valued (VALID/DEGRADED/INVALID/FAILED): only
VALID/DEGRADED rank; a technically successful INVALID-quality run keeps its
full NAV and diagnostics with a warning but never ranks; FAILED never enters
the comparison with zero returns (§9/§27).

The comparison contract fingerprint has exactly eight components and never
includes strategy implementation/config identities; each variant's identity
hash travels separately (§27).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Sequence

import pandas as pd

from backtest.fund_rotation.metrics import compute_performance_metrics

# Contract component versions (bumped when the corresponding policy changes).
CONTRACT_VERSIONS = {
    "universe_policy_version": "v1",
    "return_policy_version": "v1",      # pct_change(fill_method=None), PIT
    "execution_contract": "v1",         # ETF lots/ADV20/fees (§12)
    "benchmark_contract_version": "v1",
    "metric_contract_version": "v1",    # 244 periods/year, initial_nav anchor
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

# Stable exclusion reason codes.
EXCLUDED_TECHNICAL_FAILURE = "TECHNICAL_FAILURE"
EXCLUDED_CANCELED = "CANCELED"
EXCLUDED_DECISION_INVALID = "DECISION_INVALID"
EXCLUDED_CALENDAR_MISMATCH = "CALENDAR_MISMATCH"
EXCLUDED_NO_EQUITY = "NO_EQUITY"


@dataclass(frozen=True)
class VariantComparisonInput:
    """One sub-run's comparison evidence (raw equity, not display fields)."""

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


def evaluation_calendar_hash(calendar: Sequence[str]) -> str:
    """Order-independent hash of the shared evaluation calendar."""
    canonical = json.dumps(sorted(str(d) for d in calendar), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def comparison_contract_fingerprint(
    *,
    framework_implementation_hash: str,
    data_snapshot_fingerprint: str,
    evaluation_calendar: Sequence[str],
) -> tuple[dict[str, str], str]:
    """§27 — the eight comparison-contract components and their fingerprint.

    Strategy implementation/config identities are deliberately NOT components.
    """
    components = {
        "framework_implementation_hash": framework_implementation_hash,
        "data_snapshot_fingerprint": data_snapshot_fingerprint,
        "evaluation_calendar_hash": evaluation_calendar_hash(evaluation_calendar),
        **CONTRACT_VERSIONS,
    }
    canonical = json.dumps(components, sort_keys=True, ensure_ascii=False)
    return components, hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_comparison(
    inputs: Sequence[VariantComparisonInput],
    *,
    evaluation_calendar: Sequence[str],
    framework_implementation_hash: str,
    data_snapshot_fingerprint: str,
    initial_nav: float = 1.0,
) -> ComparisonOutcome:
    """Build the strict comparison across eligible sub-runs.

    Eligibility: status SUCCEEDED, no decision action INVALID, and an equity
    index EXACTLY equal to the evaluation calendar (no intersection).
    INVALID-quality runs stay displayed (equity + metrics + warning) but are
    never ranked; ranking covers VALID/DEGRADED only, by annual_return desc.
    """
    components, fingerprint = comparison_contract_fingerprint(
        framework_implementation_hash=framework_implementation_hash,
        data_snapshot_fingerprint=data_snapshot_fingerprint,
        evaluation_calendar=evaluation_calendar,
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
        if not equity.index.equals(calendar_index):
            excluded.append(
                {"variant_key": key, "reason": EXCLUDED_CALENDAR_MISMATCH}
            )
            continue

        # Recompute metrics from the raw equity and the common anchor.
        metrics[key] = compute_performance_metrics(
            equity, periods_per_year=244, initial_nav=initial_nav,
        )
        displayed[key] = equity

        if variant.decision_quality == "INVALID":
            quality_warnings.append({
                "variant_key": key,
                "reason": "RESEARCH_QUALITY_INVALID",
                "message": (
                    "research quality INVALID: NAV preserved for display, "
                    "excluded from ranking"
                ),
            })
            continue
        if variant.decision_quality not in ("VALID", "DEGRADED"):
            excluded.append(
                {"variant_key": key, "reason": EXCLUDED_TECHNICAL_FAILURE}
            )
            continue
        ranked_entries.append({
            "variant_key": key,
            "strategy_id": variant.strategy_id,
            "run_id": variant.run_id,
            "quality_status": variant.decision_quality,
            "annual_return": metrics[key].get("annual_return", 0.0),
        })

    ranked_entries.sort(
        key=lambda e: (-e["annual_return"], e["variant_key"]),
    )
    for rank, entry in enumerate(ranked_entries, start=1):
        entry["rank"] = rank

    equity_frame = pd.DataFrame(displayed) if displayed else pd.DataFrame(
        index=calendar_index,
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
