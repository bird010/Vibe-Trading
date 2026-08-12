"""Testable signal -> portfolio -> risk vertical slice for fund rotation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
import math
from typing import Any

import numpy as np
import pandas as pd


INSUFFICIENT_CLUSTER_COVERAGE = "INSUFFICIENT_CLUSTER_COVERAGE"
INVALID = "INVALID"
VALID = "VALID"


@dataclass(frozen=True)
class ClusterCoveragePolicy:
    min_weekly_coverage: float
    max_low_coverage_weeks: int
    minimum_valid_members: int


@dataclass(frozen=True)
class ClusterCoverageReport:
    valid_member_counts: tuple[int, ...]
    eligible_member_counts: tuple[int, ...]
    coverage_ratios: tuple[float, ...]
    min_weekly_coverage: float
    mean_weekly_coverage: float
    low_coverage_week_count: int
    coverage_distribution: tuple[float, ...]
    is_available: bool
    reason_codes: tuple[str, ...]


def compute_cluster_coverage(
    weekly_returns: pd.DataFrame,
    cluster_members: Mapping[Any, Sequence[str]],
    eligible_by_week: Mapping[Any, set[str] | frozenset[str]],
    policy: ClusterCoveragePolicy,
) -> dict[Any, ClusterCoverageReport]:
    reports: dict[Any, ClusterCoverageReport] = {}
    for cluster_id, members in cluster_members.items():
        valid_counts: list[int] = []
        eligible_counts: list[int] = []
        ratios: list[float] = []

        for week, row in weekly_returns.iterrows():
            eligible = set(eligible_by_week.get(week, set(members)))
            eligible_members = [code for code in members if code in eligible]
            valid = 0
            for code in eligible_members:
                if code in row.index:
                    value = row[code]
                    if pd.notna(value) and math.isfinite(float(value)):
                        valid += 1
            eligible_count = len(eligible_members)
            ratio = valid / eligible_count if eligible_count else 0.0
            valid_counts.append(valid)
            eligible_counts.append(eligible_count)
            ratios.append(float(ratio))

        min_coverage = min(ratios) if ratios else 0.0
        mean_coverage = float(np.mean(ratios)) if ratios else 0.0
        low_weeks = sum(ratio < policy.min_weekly_coverage for ratio in ratios)
        min_valid = min(valid_counts) if valid_counts else 0
        available = (
            min_coverage >= policy.min_weekly_coverage
            and low_weeks <= policy.max_low_coverage_weeks
            and min_valid >= policy.minimum_valid_members
        )
        reports[cluster_id] = ClusterCoverageReport(
            valid_member_counts=tuple(valid_counts),
            eligible_member_counts=tuple(eligible_counts),
            coverage_ratios=tuple(ratios),
            min_weekly_coverage=float(min_coverage),
            mean_weekly_coverage=mean_coverage,
            low_coverage_week_count=low_weeks,
            coverage_distribution=tuple(ratios),
            is_available=available,
            reason_codes=() if available else (INSUFFICIENT_CLUSTER_COVERAGE,),
        )
    return reports


@dataclass(frozen=True)
class MomentumPolicy:
    single_window: int = 4
    families: tuple[str, ...] = ("single_window",)


@dataclass(frozen=True)
class MomentumResult:
    scores_by_family: dict[str, dict[Any, float]]


def _compound_return(values: pd.Series) -> float:
    finite = pd.to_numeric(values, errors="coerce").replace([np.inf, -np.inf], np.nan)
    finite = finite.dropna()
    if finite.empty:
        return 0.0
    return float(np.prod(1.0 + finite.to_numpy(dtype=float)) - 1.0)


def _window_for_family(series: pd.Series, family: str, single_window: int) -> pd.Series:
    windows = {"single_window": single_window, "1M": single_window, "3M": 13, "6M": 26, "12M": 52}
    if family in windows:
        return series.iloc[-windows[family]:]
    if family == "6-1":
        return series.iloc[-26:-single_window] if len(series) > single_window else series.iloc[0:0]
    if family == "12-1":
        return series.iloc[-52:-single_window] if len(series) > single_window else series.iloc[0:0]
    raise ValueError(f"unsupported momentum family: {family}")


def compute_momentum_families(
    cluster_returns: pd.DataFrame,
    policy: MomentumPolicy,
) -> MomentumResult:
    scores_by_family: dict[str, dict[Any, float]] = {}
    for family in policy.families:
        scores: dict[Any, float] = {}
        for cluster_id in cluster_returns.columns:
            series = cluster_returns[cluster_id]
            if family == "risk_adjusted":
                window = series.iloc[-policy.single_window :]
                momentum = _compound_return(window)
                vol = float(pd.to_numeric(window, errors="coerce").std(ddof=0))
                scores[cluster_id] = momentum / vol if math.isfinite(vol) and vol > 0 else 0.0
            else:
                scores[cluster_id] = _compound_return(
                    _window_for_family(series, family, policy.single_window)
                )
            if not math.isfinite(scores[cluster_id]):
                scores[cluster_id] = 0.0
        scores_by_family[family] = scores
    return MomentumResult(scores_by_family=scores_by_family)


def aggregate_momentum_rank_average(scores_by_family: Mapping[str, Mapping[Any, float]]) -> dict[Any, float]:
    clusters = sorted({cluster_id for scores in scores_by_family.values() for cluster_id in scores})
    rank_sums = {cluster_id: 0.0 for cluster_id in clusters}
    rank_counts = {cluster_id: 0 for cluster_id in clusters}

    for scores in scores_by_family.values():
        ranked = sorted(
            clusters,
            key=lambda cluster_id: (
                -_finite_or_worst(scores.get(cluster_id)),
                str(cluster_id),
            ),
        )
        for rank, cluster_id in enumerate(ranked, start=1):
            rank_sums[cluster_id] += rank
            rank_counts[cluster_id] += 1

    averaged = {
        cluster_id: rank_sums[cluster_id] / rank_counts[cluster_id]
        for cluster_id in clusters
        if rank_counts[cluster_id]
    }
    return dict(sorted(averaged.items(), key=lambda item: (item[1], str(item[0]))))


def aggregate_momentum_zscore_weighted(
    scores_by_family: Mapping[str, Mapping[Any, float]],
    weights: Mapping[str, float],
) -> dict[Any, float]:
    total_weight = float(sum(weights.values()))
    if not math.isclose(total_weight, 1.0, rel_tol=1e-9, abs_tol=1e-9):
        raise ValueError("momentum family weights must sum to 1")
    clusters = sorted({cluster_id for scores in scores_by_family.values() for cluster_id in scores})
    weighted_scores = {cluster_id: 0.0 for cluster_id in clusters}

    for family, weight in weights.items():
        raw = np.array([_finite_or_zero(scores_by_family.get(family, {}).get(cluster_id)) for cluster_id in clusters])
        mean = float(np.mean(raw)) if len(raw) else 0.0
        std = float(np.std(raw)) if len(raw) else 0.0
        zscores = np.zeros_like(raw) if std == 0.0 or not math.isfinite(std) else (raw - mean) / std
        for cluster_id, zscore in zip(clusters, zscores, strict=True):
            weighted_scores[cluster_id] += float(weight) * float(zscore)

    return dict(sorted(weighted_scores.items(), key=lambda item: (-item[1], str(item[0]))))


def _finite_or_zero(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if math.isfinite(number) else 0.0


def _finite_or_worst(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return -math.inf
    return number if math.isfinite(number) else -math.inf


@dataclass(frozen=True)
class SelectionPolicy:
    top_n: int
    exit_buffer: int = 0
    minimum_holding_weeks: int = 0
    minimum_score_improvement: float = 0.0
    minimum_entry_score: float = -math.inf


@dataclass(frozen=True)
class HeldCluster:
    weeks_held: int
    entry_score: float


@dataclass(frozen=True)
class SelectionState:
    cycle_id: str | None = None
    holdings: Mapping[Any, HeldCluster] = field(default_factory=dict)


@dataclass(frozen=True)
class HysteresisResult:
    selected_clusters: tuple[Any, ...]
    next_state: SelectionState
    reason_codes: dict[Any, tuple[str, ...]]


def apply_hysteresis(
    scores: Mapping[Any, float],
    *,
    state: SelectionState | None = None,
    cycle_id: str,
    policy: SelectionPolicy,
    hard_failures: set[Any] | frozenset[Any] | None = None,
) -> HysteresisResult:
    if policy.top_n <= 0:
        return HysteresisResult((), SelectionState(cycle_id, {}), {})

    state = state or SelectionState()
    hard_failures = hard_failures or set()
    ranked = [
        cluster_id
        for cluster_id, score in sorted(scores.items(), key=lambda item: (-_finite_or_worst(item[1]), str(item[0])))
        if (
            math.isfinite(float(score))
            and float(score) > policy.minimum_entry_score
            and cluster_id not in hard_failures
        )
    ]
    ranks = {cluster_id: rank for rank, cluster_id in enumerate(ranked, start=1)}
    reason_codes: dict[Any, tuple[str, ...]] = {}
    for cluster_id in hard_failures:
        if cluster_id in scores:
            reason_codes[cluster_id] = ("HARD_FAILURE_EXIT",)

    if state.cycle_id != cycle_id:
        selected = tuple(ranked[: policy.top_n])
        next_holdings = {
            cluster_id: HeldCluster(weeks_held=1, entry_score=float(scores[cluster_id]))
            for cluster_id in selected
        }
        for cluster_id in selected:
            reason_codes[cluster_id] = ("NEW_CLUSTERING_CYCLE", "ENTRY_TOP_N")
        return HysteresisResult(selected, SelectionState(cycle_id, next_holdings), reason_codes)

    retained: list[Any] = []
    exited_holdings: list[Any] = []
    for cluster_id, held in state.holdings.items():
        if cluster_id in hard_failures:
            reason_codes[cluster_id] = ("HARD_FAILURE_EXIT",)
            continue
        rank = ranks.get(cluster_id)
        if rank is not None and rank <= policy.top_n:
            retained.append(cluster_id)
            reason_codes[cluster_id] = (
                ("HELD_MIN_HOLDING",)
                if held.weeks_held < policy.minimum_holding_weeks
                else ("HELD_TOP_N",)
            )
        elif held.weeks_held < policy.minimum_holding_weeks:
            retained.append(cluster_id)
            reason_codes[cluster_id] = ("HELD_MIN_HOLDING",)
        elif rank is not None and rank <= policy.top_n + policy.exit_buffer:
            retained.append(cluster_id)
            reason_codes[cluster_id] = ("HELD_EXIT_BUFFER",)
        else:
            exited_holdings.append(cluster_id)
            reason_codes[cluster_id] = ("EXIT_OUTSIDE_BUFFER",)

    retained = sorted(retained, key=lambda cluster_id: (-_finite_or_worst(scores.get(cluster_id)), str(cluster_id)))
    selected = list(retained[: policy.top_n])
    slots = policy.top_n - len(selected)
    if slots > 0:
        candidates = [cluster_id for cluster_id in ranked if cluster_id not in selected and cluster_id not in hard_failures]
        baseline = (
            max(_finite_or_worst(scores.get(cluster_id, state.holdings[cluster_id].entry_score)) for cluster_id in exited_holdings)
            if exited_holdings
            else -math.inf
        )
        for cluster_id in candidates:
            if slots <= 0:
                break
            if baseline != -math.inf and float(scores[cluster_id]) - baseline < policy.minimum_score_improvement:
                reason_codes[cluster_id] = ("SKIP_INSUFFICIENT_SCORE_IMPROVEMENT",)
                continue
            selected.append(cluster_id)
            reason_codes[cluster_id] = ("ENTRY_TOP_N",)
            slots -= 1
        if slots > 0:
            fallback_holdings = sorted(
                (cluster_id for cluster_id in exited_holdings if cluster_id not in selected),
                key=lambda cluster_id: (
                    -_finite_or_worst(scores.get(cluster_id, state.holdings[cluster_id].entry_score)),
                    str(cluster_id),
                ),
            )
            for cluster_id in fallback_holdings:
                if slots <= 0:
                    break
                selected.append(cluster_id)
                reason_codes[cluster_id] = ("HELD_INSUFFICIENT_REPLACEMENT_IMPROVEMENT",)
                slots -= 1

    selected = sorted(selected, key=lambda cluster_id: (-_finite_or_worst(scores.get(cluster_id)), str(cluster_id)))
    next_holdings = {
        cluster_id: HeldCluster(
            weeks_held=state.holdings[cluster_id].weeks_held + 1
            if cluster_id in state.holdings
            else 1,
            entry_score=state.holdings[cluster_id].entry_score
            if cluster_id in state.holdings
            else float(scores[cluster_id]),
        )
        for cluster_id in selected
    }
    return HysteresisResult(tuple(selected), SelectionState(cycle_id, next_holdings), reason_codes)


@dataclass(frozen=True)
class CandidateQuality:
    code: str
    representativeness: float
    liquidity: float
    listing_age: float
    cost: float | None = None
    tracking: float | None = None
    stability: float | None = None
    hard_failure: bool = False


@dataclass(frozen=True)
class RepresentativePolicy:
    exit_score: float = 0.0
    deterioration_periods: int = 1


@dataclass(frozen=True)
class RepresentativeState:
    current: str | None = None
    deterioration_counts: Mapping[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class RepresentativeQualityResult:
    selected: str | None
    next_state: RepresentativeState
    scores: dict[str, float]
    lock_maintained: bool
    reason_codes: tuple[str, ...]


def select_representative_quality(
    candidates: Sequence[CandidateQuality],
    *,
    state: RepresentativeState | None = None,
    policy: RepresentativePolicy,
) -> RepresentativeQualityResult:
    state = state or RepresentativeState()
    scores = {candidate.code: _quality_score(candidate) for candidate in candidates}
    by_code = {candidate.code: candidate for candidate in candidates}
    viable = [candidate for candidate in candidates if not candidate.hard_failure]
    viable_sorted = sorted(viable, key=lambda candidate: (-scores[candidate.code], candidate.code))

    current = state.current
    if current and current in by_code and by_code[current].hard_failure:
        replacement = next((candidate.code for candidate in viable_sorted if candidate.code != current), None)
        return RepresentativeQualityResult(
            selected=replacement,
            next_state=RepresentativeState(replacement, {}),
            scores=scores,
            lock_maintained=False,
            reason_codes=("HARD_FAILURE_REPLACED",) if replacement else ("NO_ELIGIBLE_REPRESENTATIVE",),
        )

    if current and current in scores:
        current_score = scores[current]
        if current_score >= policy.exit_score:
            return RepresentativeQualityResult(
                selected=current,
                next_state=RepresentativeState(current, {}),
                scores=scores,
                lock_maintained=True,
                reason_codes=("QUALITY_LOCK_HELD",),
            )
        count = int(state.deterioration_counts.get(current, 0)) + 1
        if count < policy.deterioration_periods:
            return RepresentativeQualityResult(
                selected=current,
                next_state=RepresentativeState(current, {current: count}),
                scores=scores,
                lock_maintained=True,
                reason_codes=("QUALITY_LOCK_HELD", "QUALITY_DETERIORATION_OBSERVED"),
            )
        replacement = next((candidate.code for candidate in viable_sorted if candidate.code != current), None)
        return RepresentativeQualityResult(
            selected=replacement,
            next_state=RepresentativeState(replacement, {}),
            scores=scores,
            lock_maintained=False,
            reason_codes=("QUALITY_DETERIORATION_EXIT",) if replacement else ("NO_ELIGIBLE_REPRESENTATIVE",),
        )

    selected = viable_sorted[0].code if viable_sorted else None
    return RepresentativeQualityResult(
        selected=selected,
        next_state=RepresentativeState(selected, {}),
        scores=scores,
        lock_maintained=False,
        reason_codes=() if selected else ("NO_ELIGIBLE_REPRESENTATIVE",),
    )


def _quality_score(candidate: CandidateQuality) -> float:
    values = [
        candidate.representativeness,
        candidate.liquidity,
        candidate.listing_age,
        candidate.cost,
        candidate.tracking,
        candidate.stability,
    ]
    finite = [_finite_or_zero(value) for value in values if value is not None]
    return float(np.mean(finite)) if finite else 0.0


@dataclass(frozen=True)
class AssetSelection:
    code: str
    cluster_id: Any
    asset_class: str


@dataclass(frozen=True)
class PortfolioPolicy:
    enabled: bool = True
    method: str = "equal_weight"
    target_cluster_slots: int | None = None
    max_etf_weight: float = 1.0
    max_cluster_weight: float = 1.0
    max_asset_class_weight: float = 1.0
    minimum_cash_weight: float = 0.0
    maximum_one_way_turnover_per_rebalance: float = 1.0


@dataclass(frozen=True)
class PortfolioResult:
    status: str
    weights: dict[str, float]
    cash_weight: float
    one_way_turnover: float
    reason_codes: tuple[str, ...]


def build_portfolio_weights(
    assets: Sequence[AssetSelection],
    *,
    policy: PortfolioPolicy,
    volatilities: Mapping[str, float] | None = None,
    previous_weights: Mapping[str, float] | None = None,
) -> PortfolioResult:
    if not assets:
        return PortfolioResult(VALID, {}, 1.0, 0.0, ())

    investable = 1.0 - policy.minimum_cash_weight
    if investable < 0.0 or investable > 1.0:
        return PortfolioResult(INVALID, {}, 1.0, 0.0, ("INVALID_MINIMUM_CASH_WEIGHT",))

    reason_codes: list[str] = []
    if not policy.enabled:
        reason_codes.append("PORTFOLIO_WEIGHTING_DISABLED_EQUAL_WEIGHT")
        weights = _equal_weights(assets, investable)
    elif policy.method == "equal_weight":
        weights = _equal_weights(assets, investable)
    elif policy.method == "equal_weight_by_cluster_slot":
        weights = _equal_weights_by_cluster_slot(assets, investable, policy)
    elif policy.method == "inverse_volatility":
        weights = _inverse_volatility_weights(assets, investable, volatilities or {})
    else:
        return PortfolioResult(INVALID, {}, policy.minimum_cash_weight, 0.0, ("UNKNOWN_PORTFOLIO_METHOD",))

    one_way = _one_way_turnover(weights, previous_weights or {})
    constraint_reasons = _portfolio_constraint_reasons(weights, assets, policy, one_way)
    status = INVALID if constraint_reasons else VALID
    return PortfolioResult(
        status=status,
        weights=weights,
        cash_weight=policy.minimum_cash_weight,
        one_way_turnover=one_way,
        reason_codes=tuple(reason_codes + constraint_reasons),
    )


def _equal_weights(assets: Sequence[AssetSelection], investable: float) -> dict[str, float]:
    weight = investable / len(assets)
    return {asset.code: float(weight) for asset in assets}


def _equal_weights_by_cluster_slot(
    assets: Sequence[AssetSelection],
    investable: float,
    policy: PortfolioPolicy,
) -> dict[str, float]:
    by_cluster: dict[Any, list[AssetSelection]] = {}
    for asset in assets:
        by_cluster.setdefault(asset.cluster_id, []).append(asset)
    slots = policy.target_cluster_slots or len(by_cluster)
    if slots <= 0:
        return {}
    slot_weight = investable / slots
    weights: dict[str, float] = {}
    for cluster_assets in by_cluster.values():
        if not cluster_assets:
            continue
        member_weight = slot_weight / len(cluster_assets)
        for asset in cluster_assets:
            weights[asset.code] = weights.get(asset.code, 0.0) + float(member_weight)
    return weights


def _inverse_volatility_weights(
    assets: Sequence[AssetSelection],
    investable: float,
    volatilities: Mapping[str, float],
) -> dict[str, float]:
    inv: dict[str, float] = {}
    for asset in assets:
        vol = _finite_or_zero(volatilities.get(asset.code))
        inv[asset.code] = 1.0 / vol if vol > 0.0 else 0.0
    total = sum(inv.values())
    if total <= 0.0:
        return _equal_weights(assets, investable)
    return {code: float(investable * value / total) for code, value in inv.items()}


def _one_way_turnover(weights: Mapping[str, float], previous_weights: Mapping[str, float]) -> float:
    codes = set(weights) | set(previous_weights)
    return float(0.5 * sum(abs(float(weights.get(code, 0.0)) - float(previous_weights.get(code, 0.0))) for code in codes))


def _portfolio_constraint_reasons(
    weights: Mapping[str, float],
    assets: Sequence[AssetSelection],
    policy: PortfolioPolicy,
    one_way_turnover: float,
) -> list[str]:
    reasons: list[str] = []
    if any(weight > policy.max_etf_weight + 1e-12 for weight in weights.values()):
        reasons.append("MAX_ETF_WEIGHT_CONSTRAINT")

    cluster_sums: dict[Any, float] = {}
    class_sums: dict[str, float] = {}
    for asset in assets:
        weight = float(weights.get(asset.code, 0.0))
        cluster_sums[asset.cluster_id] = cluster_sums.get(asset.cluster_id, 0.0) + weight
        class_sums[asset.asset_class] = class_sums.get(asset.asset_class, 0.0) + weight
    if any(weight > policy.max_cluster_weight + 1e-12 for weight in cluster_sums.values()):
        reasons.append("MAX_CLUSTER_WEIGHT_CONSTRAINT")
    if any(weight > policy.max_asset_class_weight + 1e-12 for weight in class_sums.values()):
        reasons.append("MAX_ASSET_CLASS_WEIGHT_CONSTRAINT")
    if one_way_turnover > policy.maximum_one_way_turnover_per_rebalance + 1e-12:
        reasons.append("MAX_ONE_WAY_TURNOVER_CONSTRAINT")
    return reasons


@dataclass(frozen=True)
class RiskPolicy:
    enabled: bool = True
    target_volatility: float | None = None
    min_gross_exposure: float = 0.0
    max_gross_exposure: float = 1.0
    conservative_gross_exposure: float = 0.5
    regime_exposure: Mapping[str, float] = field(
        default_factory=lambda: {"RISK_ON": 1.0, "NEUTRAL": 0.75, "RISK_OFF": 0.5}
    )


@dataclass(frozen=True)
class RiskResult:
    gross_exposure: float
    scaled_weights: dict[str, float]
    cash_weight: float
    reason_codes: tuple[str, ...]


def apply_risk_layer(
    raw_weights: Mapping[str, float],
    *,
    upstream_cash: float,
    policy: RiskPolicy,
    estimated_portfolio_volatility: float | None = None,
    regime: str | None = None,
    upstream_reasons: tuple[str, ...] = (),
) -> RiskResult:
    if not policy.enabled:
        return RiskResult(
            gross_exposure=1.0,
            scaled_weights={code: float(weight) for code, weight in raw_weights.items()},
            cash_weight=float(upstream_cash),
            reason_codes=tuple(upstream_reasons) + ("RISK_LAYER_DISABLED_IDENTITY",),
        )

    reasons: list[str] = []
    if regime is None:
        gross = policy.conservative_gross_exposure
        reasons.append("RISK_STATE_UNAVAILABLE")
    else:
        gross = policy.max_gross_exposure
        if policy.target_volatility is not None:
            vol = _finite_or_zero(estimated_portfolio_volatility)
            if vol > 0.0:
                gross = policy.target_volatility / vol
                reasons.append("VOL_TARGET_SCALED")
            else:
                gross = policy.conservative_gross_exposure
                reasons.append("VOLATILITY_UNAVAILABLE")
        gross = min(gross, float(policy.regime_exposure.get(regime, policy.conservative_gross_exposure)))
        reasons.append(f"REGIME_{regime}")

    gross = min(max(gross, policy.min_gross_exposure), policy.max_gross_exposure, 1.0)
    scaled = {code: float(weight) * gross for code, weight in raw_weights.items()}
    cash = 1.0 - sum(scaled.values())
    return RiskResult(
        gross_exposure=float(gross),
        scaled_weights=scaled,
        cash_weight=float(cash),
        reason_codes=tuple(upstream_reasons) + tuple(reasons),
    )


@dataclass(frozen=True)
class StageRecord:
    stage: str
    input: Any
    output: Any
    policy_version: str
    reason_codes: tuple[str, ...] = ()


@dataclass(frozen=True)
class PipelineDecision:
    status: str
    reason_codes: tuple[str, ...]
    stage_records: tuple[StageRecord, ...]
    next_selection_state: SelectionState | None = None


def serialize_stage_records(records: Sequence[StageRecord]) -> list[dict[str, Any]]:
    return [
        {
            "stage": record.stage,
            "input": _json_safe(record.input),
            "output": _json_safe(record.output),
            "policy_version": record.policy_version,
            "reason_codes": list(record.reason_codes),
        }
        for record in records
    ]


def _json_safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, (str, bool, int)) or value is None:
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        number = float(value)
        return number if math.isfinite(number) else None
    return str(value)


def _representative_codes(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, Sequence):
        return tuple(str(code) for code in value if code)
    return (str(value),)


def run_decision_pipeline(
    *,
    raw_signal_scores: Mapping[Any, float],
    coverage_available: Mapping[Any, bool],
    representatives: Mapping[Any, str | Sequence[str] | None],
    asset_metadata: Mapping[str, Mapping[str, Any]],
    selection_policy: SelectionPolicy,
    portfolio_policy: PortfolioPolicy,
    risk_policy: RiskPolicy,
    policy_versions: Mapping[str, str],
    selection_state: SelectionState | None = None,
    cycle_id: str = "pipeline-cycle",
) -> PipelineDecision:
    records: list[StageRecord] = []

    raw_scores = dict(raw_signal_scores)
    records.append(StageRecord("raw_signal_scores", raw_scores, raw_scores, policy_versions["signal"], ()))

    filtered = {
        cluster_id: score
        for cluster_id, score in raw_signal_scores.items()
        if coverage_available.get(cluster_id, False)
    }
    coverage_reasons = tuple(
        INSUFFICIENT_CLUSTER_COVERAGE
        for cluster_id in raw_signal_scores
        if not coverage_available.get(cluster_id, False)
    )
    records.append(
        StageRecord(
            "coverage_filtered_scores",
            dict(raw_signal_scores),
            dict(filtered),
            policy_versions["coverage"],
            coverage_reasons,
        )
    )

    selection = apply_hysteresis(
        filtered,
        state=selection_state,
        cycle_id=cycle_id,
        policy=selection_policy,
    )
    records.append(
        StageRecord(
            "selected_clusters",
            dict(filtered),
            selection.selected_clusters,
            policy_versions["selection"],
            tuple(code for reasons in selection.reason_codes.values() for code in reasons),
        )
    )

    selected_reps: dict[Any, str | Sequence[str] | None] = {
        cluster_id: representatives.get(cluster_id) for cluster_id in selection.selected_clusters
    }
    rep_reasons = tuple(
        "NO_REPRESENTATIVE_CASH"
        for value in selected_reps.values()
        if not _representative_codes(value)
    )
    records.append(
        StageRecord(
            "selected_representatives",
            selection.selected_clusters,
            selected_reps,
            policy_versions["representative"],
            rep_reasons,
        )
    )

    assets = []
    for cluster_id, representative_value in selected_reps.items():
        codes = _representative_codes(representative_value)
        if not codes:
            continue
        for code in codes:
            metadata = asset_metadata[code]
            assets.append(
                AssetSelection(
                    code,
                    cluster_id=metadata.get("cluster_id", cluster_id),
                    asset_class=str(metadata["asset_class"]),
                )
            )
    portfolio_build_policy = portfolio_policy
    missing_representative_count = sum(
        1 for value in selected_reps.values() if not _representative_codes(value)
    )
    if selected_reps and missing_representative_count:
        investable = 1.0 - portfolio_policy.minimum_cash_weight
        if 0.0 <= investable <= 1.0:
            reserved_cash = investable * missing_representative_count / len(selected_reps)
            portfolio_build_policy = replace(
                portfolio_policy,
                minimum_cash_weight=portfolio_policy.minimum_cash_weight + reserved_cash,
            )
    portfolio = build_portfolio_weights(assets, policy=portfolio_build_policy)
    records.append(
        StageRecord(
            "raw_portfolio_weights",
            tuple(asset.code for asset in assets),
            portfolio.weights,
            policy_versions["portfolio"],
            rep_reasons + portfolio.reason_codes,
        )
    )

    risk = apply_risk_layer(
        portfolio.weights,
        upstream_cash=portfolio.cash_weight,
        policy=risk_policy,
        upstream_reasons=tuple(rep_reasons),
    )
    records.append(
        StageRecord(
            "risk_scaled_weights",
            portfolio.weights,
            risk.scaled_weights,
            policy_versions["risk"],
            risk.reason_codes,
        )
    )

    status = INVALID if portfolio.status == INVALID else VALID
    decision_reasons = tuple(dict.fromkeys(coverage_reasons + rep_reasons + portfolio.reason_codes))
    execution_output = {
        "status": status,
        "weights": risk.scaled_weights,
        "cash_weight": risk.cash_weight,
    }
    records.append(
        StageRecord(
            "execution_targets",
            risk.scaled_weights,
            execution_output,
            policy_versions["risk"],
            decision_reasons,
        )
    )
    return PipelineDecision(
        status=status,
        reason_codes=decision_reasons,
        stage_records=tuple(records),
        next_selection_state=selection.next_state,
    )
