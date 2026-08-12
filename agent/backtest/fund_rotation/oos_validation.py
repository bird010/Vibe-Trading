"""OOS and walk-forward research validation contracts.

This module is intentionally standalone and deterministic.  It defines the
minimum auditable slice for separating validation selection from sealed OOS
evidence without touching the existing batch runner/comparison path.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from types import MappingProxyType
from typing import Any, Callable, Mapping, Sequence


COMPONENT_CHAIN_STAGES: tuple[str, ...] = ("S0", "S1", "S2", "S3", "S4", "S5", "S6")
QUALIFIED_OOS_EVIDENCE = "QUALIFIED_OOS_EVIDENCE"
RESEARCH_ONLY = "RESEARCH_ONLY"
CONSUMED_AS_RESEARCH_INPUT = "CONSUMED_AS_RESEARCH_INPUT"
_FORBIDDEN_OOS_EVIDENCE_KEYS = frozenset({"winner", "rank", "selection_rank", "selection_winner"})
_FORBIDDEN_SELECTION_METRIC_TOKENS = frozenset({"proxy"})
_DEFAULT_ACCOUNTING_CONTRACT_VERSION = "accounting-v1"
_DEFAULT_FRAMEWORK_IMPLEMENTATION_HASH = "framework-default-v1"
_DEFAULT_KNOWLEDGE_CUTOFF = "knowledge-cutoff-default-v1"
_DEFAULT_MARKET_RULE_POLICY_HASH = "market-rule-default-v1"
_DEFAULT_BENCHMARK_POLICY_HASH = "benchmark-default-v1"
_DEFAULT_ALLOWED_SELECTION_METRICS = frozenset(
    {
        "variant_key",
        "complexity",
        "sharpe",
        "max_drawdown",
        "turnover",
    }
)
_SEALED_SELECTION_TOKENS = frozenset(
    {
        "holdout",
        "oos",
        "posthoc",
        "sealed",
        "test",
        "untouched",
        "validation",
        "regime",
    }
)
_SEALED_SELECTION_COMPACT_PHRASES = frozenset({"outofsample", "posthoc"})
_SEALED_SELECTION_COMPACT_PREFIXES = frozenset({"holdout", "oos", "regime", "sealed", "untouched", "validation"})
_CAMEL_CASE_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_NON_ALNUM_UNDERSCORE = re.compile(r"[^A-Za-z0-9_]+")
_FORBIDDEN_OOS_EVIDENCE_TOKENS = frozenset({"rank", "ranking", "select", "selected", "selection", "winner"})
_DESIGNED_FORMAL_NON_CLUSTER_BASELINE_STRATEGY_FAMILIES = frozenset(
    {
        "etf_absolute_momentum",
        "etf_dual_momentum",
        "etf_trend_momentum",
    }
)
_LEGACY_FORMAL_NON_CLUSTER_BASELINE_STRATEGY_FAMILIES = frozenset({"independent_alpha_baseline"})
_FORMAL_NON_CLUSTER_BASELINE_STRATEGY_FAMILIES = (
    _DESIGNED_FORMAL_NON_CLUSTER_BASELINE_STRATEGY_FAMILIES
    | _LEGACY_FORMAL_NON_CLUSTER_BASELINE_STRATEGY_FAMILIES
)
_FORMAL_BASELINE_REQUIRED_FIELDS = frozenset(
    {
        "baseline_id",
        "strategy_family",
        "uses_clustering",
        "oos_qualification_label",
        "strategy_implementation_hash",
        "framework_implementation_hash",
        "resolved_config_hash",
        "data_snapshot_fingerprint",
        "knowledge_cutoff",
        "universe_id",
        "execution_contract_version",
        "execution_policy_hash",
        "accounting_contract_version",
        "accounting_policy_hash",
        "market_rule_policy_hash",
        "evaluation_calendar_hash",
        "benchmark_policy_hash",
        "qualification_policy_hash",
        "research_experiment_id",
    }
)
_FORMAL_BASELINE_FIELD_ALIASES = MappingProxyType(
    {field.replace("_", ""): field for field in _FORMAL_BASELINE_REQUIRED_FIELDS}
)


def _immutable_mapping(value: Mapping[str, Any] | None) -> Mapping[str, Any]:
    return freeze_for_identity(value or {})


def freeze_for_identity(value: Any) -> Any:
    """Recursively freeze identity-bearing values.

    Mappings are copied into MappingProxyType and sequences become tuples so
    later caller-side mutation cannot drift a sealed experiment identity.
    """

    if hasattr(value, "to_identity_dict"):
        return freeze_for_identity(value.to_identity_dict())
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): freeze_for_identity(item) for key, item in value.items()})
    if isinstance(value, (tuple, list)):
        return tuple(freeze_for_identity(item) for item in value)
    if isinstance(value, set):
        return tuple(freeze_for_identity(item) for item in sorted(value, key=lambda item: json.dumps(_canonicalize(item), sort_keys=True)))
    return value


def _canonicalize(value: Any) -> Any:
    if hasattr(value, "to_identity_dict"):
        return _canonicalize(value.to_identity_dict())
    if isinstance(value, Mapping):
        return {str(key): _canonicalize(value[key]) for key in sorted(value)}
    if isinstance(value, (tuple, list)):
        return [_canonicalize(item) for item in value]
    if isinstance(value, set):
        return [_canonicalize(item) for item in sorted(value)]
    return value


def _identity_hash(value: Any) -> str:
    payload = json.dumps(_canonicalize(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def canonical_identity_hash(value: Any) -> str:
    return _identity_hash(value)


def assert_identity_matches(name: str, value: Any, expected_hash: str) -> None:
    actual_hash = canonical_identity_hash(value)
    if actual_hash != expected_hash:
        raise ValueError(f"{name} identity drift: expected {expected_hash}, got {actual_hash}")


@dataclass(frozen=True)
class BenchmarkPolicy:
    primary_benchmark: str
    secondary_benchmarks: tuple[str, ...]
    cash_benchmark: str
    universe_equal_weight_benchmark: str
    benchmark_data_version: str

    def to_identity_dict(self) -> dict[str, Any]:
        return {
            "primary_benchmark": self.primary_benchmark,
            "secondary_benchmarks": self.secondary_benchmarks,
            "cash_benchmark": self.cash_benchmark,
            "universe_equal_weight_benchmark": self.universe_equal_weight_benchmark,
            "benchmark_data_version": self.benchmark_data_version,
        }


@dataclass(frozen=True)
class QualificationPolicy:
    min_oos_weeks: int = 104
    min_oos_fraction: float = 0.20
    require_non_cluster_baseline: bool = True

    @property
    def policy_hash(self) -> str:
        return _identity_hash(self)

    def to_identity_dict(self) -> dict[str, Any]:
        return {
            "min_oos_weeks": self.min_oos_weeks,
            "min_oos_fraction": self.min_oos_fraction,
            "require_non_cluster_baseline": self.require_non_cluster_baseline,
        }


@dataclass(frozen=True)
class SelectionPolicy:
    primary_metric: str = "sharpe"
    tie_breakers: tuple[str, ...] = (
        "max_drawdown",
        "turnover",
        "complexity",
        "variant_key",
    )
    allowed_validation_metrics: tuple[str, ...] = ("sharpe",)

    def __post_init__(self) -> None:
        object.__setattr__(self, "tie_breakers", tuple(self.tie_breakers))
        object.__setattr__(self, "allowed_validation_metrics", tuple(self.allowed_validation_metrics))
        for metric in self.allowed_validation_metrics:
            _validate_selection_metric_semantics(metric)
        allowed = self.allowed_metric_names
        _validate_selection_metric_name(self.primary_metric, allowed)
        for metric in self.tie_breakers:
            _validate_selection_metric_name(metric, allowed)

    @property
    def allowed_metric_names(self) -> frozenset[str]:
        return _DEFAULT_ALLOWED_SELECTION_METRICS.union(self.allowed_validation_metrics)

    def to_identity_dict(self) -> dict[str, Any]:
        return {
            "primary_metric": self.primary_metric,
            "tie_breakers": self.tie_breakers,
            "allowed_validation_metrics": self.allowed_validation_metrics,
        }


def _semantic_tokens(name: str) -> tuple[str, ...]:
    normalized = _NON_ALNUM_UNDERSCORE.sub("_", name)
    tokens: list[str] = []
    for chunk in normalized.split("_"):
        tokens.extend(token.lower() for token in _CAMEL_CASE_BOUNDARY.sub("_", chunk).split("_") if token)
    return tuple(tokens)


def _compact_semantic_name(name: str) -> str:
    return "".join(_semantic_tokens(name))


def _canonical_semantic_aliases(name: str, aliases: frozenset[str]) -> frozenset[str]:
    tokens = _semantic_tokens(name)
    canonical_aliases: set[str] = set()
    for token in tokens:
        if token in aliases:
            canonical_aliases.add(token)
    return frozenset(canonical_aliases)


def _compact_prefix_semantic_aliases(name: str, aliases: frozenset[str]) -> frozenset[str]:
    compact = _compact_semantic_name(name)
    return frozenset(alias for alias in aliases if compact.startswith(alias))


def _compact_phrase_semantic_aliases(name: str, aliases: frozenset[str]) -> frozenset[str]:
    compact = _compact_semantic_name(name)
    return frozenset(alias for alias in aliases if alias in compact)


def _compact_boundary_semantic_aliases(name: str, aliases: frozenset[str]) -> frozenset[str]:
    compact = _compact_semantic_name(name)
    canonical_aliases: set[str] = set()
    for alias in aliases:
        if compact.startswith(alias) or compact.endswith(alias):
            canonical_aliases.add(alias)
    return frozenset(canonical_aliases)


def _canonical_formal_baseline_field_name(name: str) -> str:
    tokens = _semantic_tokens(name)
    canonical = "_".join(tokens)
    compact = _compact_semantic_name(name)
    return str(_FORMAL_BASELINE_FIELD_ALIASES.get(compact, canonical))


def _is_sealed_metric_name(metric: str) -> bool:
    return bool(
        _canonical_semantic_aliases(metric, _SEALED_SELECTION_TOKENS)
        or _compact_prefix_semantic_aliases(metric, _SEALED_SELECTION_COMPACT_PREFIXES)
        or _compact_phrase_semantic_aliases(metric, _SEALED_SELECTION_COMPACT_PHRASES)
    )


def _is_forbidden_selection_metric_name(metric: str) -> bool:
    return bool(
        _canonical_semantic_aliases(metric, _FORBIDDEN_SELECTION_METRIC_TOKENS)
        or _compact_boundary_semantic_aliases(metric, _FORBIDDEN_SELECTION_METRIC_TOKENS)
    )


def _validate_selection_metric_semantics(metric: str) -> None:
    if _is_sealed_metric_name(metric):
        raise ValueError(f"sealed OOS/posthoc/test metric is not allowed for validation selection: {metric}")
    if _is_forbidden_selection_metric_name(metric):
        raise ValueError(f"proxy metric is not allowed for validation selection: {metric}")


def _validate_selection_metric_name(metric: str, allowed_metric_names: frozenset[str]) -> None:
    _validate_selection_metric_semantics(metric)
    if metric not in allowed_metric_names:
        raise ValueError(f"unknown validation selection metric: {metric}")


def _validate_selection_row(row: Mapping[str, Any], selection_policy: SelectionPolicy) -> None:
    allowed = selection_policy.allowed_metric_names
    for key in row:
        _validate_selection_metric_semantics(str(key))
        if key not in allowed:
            raise ValueError(f"unknown validation selection metric: {key}")


@dataclass(frozen=True)
class TemporalSplitPolicy:
    train_weeks: tuple[str, ...]
    validation_weeks: tuple[str, ...]
    oos_weeks: tuple[str, ...]

    def __init__(
        self,
        train_weeks: Sequence[str],
        validation_weeks: Sequence[str],
        oos_weeks: Sequence[str],
    ) -> None:
        object.__setattr__(self, "train_weeks", tuple(train_weeks))
        object.__setattr__(self, "validation_weeks", tuple(validation_weeks))
        object.__setattr__(self, "oos_weeks", tuple(oos_weeks))
        self._validate()

    def _validate(self) -> None:
        if not self.train_weeks or not self.validation_weeks or not self.oos_weeks:
            raise ValueError("Train, Validation and OOS splits must all be non-empty")
        train = set(self.train_weeks)
        validation = set(self.validation_weeks)
        oos = set(self.oos_weeks)
        if train & validation or train & oos or validation & oos:
            raise ValueError("Train, Validation and OOS splits must not overlap")
        if not (self.train_weeks[-1] < self.validation_weeks[0] and self.validation_weeks[-1] < self.oos_weeks[0]):
            raise ValueError("Temporal splits must be strictly ordered Train -> Validation -> OOS")

    @property
    def total_weeks(self) -> int:
        return len(self.train_weeks) + len(self.validation_weeks) + len(self.oos_weeks)

    def has_qualified_oos(self, qualification_policy: QualificationPolicy | None = None) -> bool:
        policy = qualification_policy or QualificationPolicy()
        return (
            len(self.oos_weeks) >= policy.min_oos_weeks
            and len(self.oos_weeks) / self.total_weeks >= policy.min_oos_fraction
        )

    def oos_qualification_label(self, qualification_policy: QualificationPolicy | None = None) -> str:
        if self.has_qualified_oos(qualification_policy):
            return QUALIFIED_OOS_EVIDENCE
        return RESEARCH_ONLY

    def require_qualified_oos_evidence(self, qualification_policy: QualificationPolicy | None = None) -> str:
        label = self.oos_qualification_label(qualification_policy)
        if label != QUALIFIED_OOS_EVIDENCE:
            raise ValueError("OOS split cannot be marked QUALIFIED_OOS_EVIDENCE")
        return label

    def to_identity_dict(self) -> dict[str, Any]:
        return {
            "train_weeks": self.train_weeks,
            "validation_weeks": self.validation_weeks,
            "oos_weeks": self.oos_weeks,
        }


@dataclass(frozen=True)
class WalkForwardFold:
    fold_index: int
    train_weeks: tuple[str, ...]
    validation_weeks: tuple[str, ...]
    test_weeks: tuple[str, ...]
    frozen_parameter_cutoff: str


@dataclass(frozen=True)
class RollingWalkForwardPolicy:
    train_weeks: int = 156
    validation_weeks: int = 52
    test_weeks: int = 52
    step_weeks: int = 52

    def __post_init__(self) -> None:
        if min(self.train_weeks, self.validation_weeks, self.test_weeks, self.step_weeks) <= 0:
            raise ValueError("Walk-forward windows and step must be positive")

    def generate_folds(self, weeks: Sequence[str]) -> tuple[WalkForwardFold, ...]:
        ordered_weeks = tuple(weeks)
        width = self.train_weeks + self.validation_weeks + self.test_weeks
        folds: list[WalkForwardFold] = []
        fold_index = 0
        for start in range(0, len(ordered_weeks) - width + 1, self.step_weeks):
            train_end = start + self.train_weeks
            validation_end = train_end + self.validation_weeks
            test_end = validation_end + self.test_weeks
            validation = ordered_weeks[train_end:validation_end]
            folds.append(
                WalkForwardFold(
                    fold_index=fold_index,
                    train_weeks=ordered_weeks[start:train_end],
                    validation_weeks=validation,
                    test_weeks=ordered_weeks[validation_end:test_end],
                    frozen_parameter_cutoff=validation[-1],
                )
            )
            fold_index += 1
        return tuple(folds)

    def to_identity_dict(self) -> dict[str, Any]:
        return {
            "train_weeks": self.train_weeks,
            "validation_weeks": self.validation_weeks,
            "test_weeks": self.test_weeks,
            "step_weeks": self.step_weeks,
        }


@dataclass(frozen=True)
class WalkForwardAccountState:
    cash: float
    positions: Mapping[str, float]
    cost_basis: Mapping[str, float]
    residual_orders: tuple[str, ...]
    corporate_action_state: Mapping[str, Any]
    last_valuation_date: str
    last_nav: float
    accounting_contract_version: str = _DEFAULT_ACCOUNTING_CONTRACT_VERSION
    daily_accounting_event_order: tuple[str, ...] = (
        "corporate_actions",
        "fills",
        "valuation",
        "residual_order_carry",
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "positions", _immutable_mapping(self.positions))
        object.__setattr__(self, "cost_basis", _immutable_mapping(self.cost_basis))
        object.__setattr__(self, "residual_orders", tuple(self.residual_orders))
        object.__setattr__(self, "corporate_action_state", _immutable_mapping(self.corporate_action_state))

    def replace(self, **changes: Any) -> "WalkForwardAccountState":
        return replace(self, **changes)


@dataclass(frozen=True)
class WalkForwardFoldRun:
    fold: WalkForwardFold
    start_state: WalkForwardAccountState
    end_state: WalkForwardAccountState


def run_walk_forward_state_chain(
    folds: Sequence[WalkForwardFold],
    initial_state: WalkForwardAccountState,
    transition: Callable[[WalkForwardFold, WalkForwardAccountState], WalkForwardAccountState],
) -> tuple[WalkForwardFoldRun, ...]:
    current_state = initial_state
    runs: list[WalkForwardFoldRun] = []
    accounting_contract_version = initial_state.accounting_contract_version
    daily_accounting_event_order = initial_state.daily_accounting_event_order
    for fold in folds:
        start_state = current_state
        _validate_fold_start_continuity(fold, start_state)
        end_state = transition(fold, start_state)
        if end_state.accounting_contract_version != accounting_contract_version:
            raise ValueError("accounting_contract_version must remain consistent across walk-forward folds")
        if end_state.daily_accounting_event_order != daily_accounting_event_order:
            raise ValueError("daily_accounting_event_order must remain consistent across walk-forward folds")
        if end_state.last_valuation_date != fold.test_weeks[-1]:
            raise ValueError("fold end last_valuation_date must equal the final test week")
        runs.append(WalkForwardFoldRun(fold=fold, start_state=start_state, end_state=end_state))
        current_state = end_state
    return tuple(runs)


def _validate_fold_start_continuity(fold: WalkForwardFold, start_state: WalkForwardAccountState) -> None:
    expected_first_test = _next_valuation_label(start_state.last_valuation_date)
    if expected_first_test is not None and expected_first_test != fold.test_weeks[0]:
        raise ValueError(
            "walk-forward folds must be continuous: "
            f"state last_valuation_date {start_state.last_valuation_date} is not immediately before {fold.test_weeks[0]}"
        )


def _next_valuation_label(label: str) -> str | None:
    if len(label) >= 2 and label[0] == "w" and label[1:].isdigit():
        return f"w{int(label[1:]) + 1:0{len(label) - 1}d}"
    try:
        parsed = date.fromisoformat(label)
    except ValueError:
        return None
    return (parsed + timedelta(days=7)).isoformat()


@dataclass(frozen=True)
class VariantSpec:
    variant_key: str
    strategy_family: str
    parameters: Mapping[str, Any]
    component_stage: str
    component_toggles: Mapping[str, Any]
    uses_clustering: bool
    universe_id: str
    execution_contract_version: str
    data_identity_hash: str
    code_identity_hash: str
    evaluation_identity_hash: str
    strategy_implementation_hash: str = ""
    framework_implementation_hash: str = ""
    resolved_config_hash: str = ""
    knowledge_cutoff: str = ""
    execution_policy_hash: str = ""
    accounting_policy_hash: str = ""
    market_rule_policy_hash: str = ""
    evaluation_calendar_hash: str = ""
    benchmark_policy_hash: str = ""

    def __post_init__(self) -> None:
        if self.component_stage not in COMPONENT_CHAIN_STAGES:
            raise ValueError(f"Unknown component stage: {self.component_stage}")
        object.__setattr__(self, "parameters", _immutable_mapping(self.parameters))
        object.__setattr__(self, "component_toggles", _immutable_mapping(self.component_toggles))
        if not self.strategy_implementation_hash:
            object.__setattr__(self, "strategy_implementation_hash", self.code_identity_hash)
        if not self.framework_implementation_hash:
            object.__setattr__(self, "framework_implementation_hash", _DEFAULT_FRAMEWORK_IMPLEMENTATION_HASH)
        if not self.resolved_config_hash:
            object.__setattr__(self, "resolved_config_hash", _identity_hash(self.parameters))
        if not self.knowledge_cutoff:
            object.__setattr__(self, "knowledge_cutoff", _DEFAULT_KNOWLEDGE_CUTOFF)
        if not self.execution_policy_hash:
            object.__setattr__(self, "execution_policy_hash", _identity_hash(self.execution_contract_version))
        if not self.accounting_policy_hash:
            object.__setattr__(self, "accounting_policy_hash", _identity_hash(_DEFAULT_ACCOUNTING_CONTRACT_VERSION))
        if not self.market_rule_policy_hash:
            object.__setattr__(self, "market_rule_policy_hash", _identity_hash(_DEFAULT_MARKET_RULE_POLICY_HASH))
        if not self.evaluation_calendar_hash:
            object.__setattr__(self, "evaluation_calendar_hash", self.evaluation_identity_hash)
        if not self.benchmark_policy_hash:
            object.__setattr__(self, "benchmark_policy_hash", _identity_hash(_DEFAULT_BENCHMARK_POLICY_HASH))

    @property
    def sealed_identity_hash(self) -> str:
        return _identity_hash(self)

    def to_identity_dict(self) -> dict[str, Any]:
        return {
            "variant_key": self.variant_key,
            "strategy_family": self.strategy_family,
            "parameters": self.parameters,
            "component_stage": self.component_stage,
            "component_toggles": self.component_toggles,
            "uses_clustering": self.uses_clustering,
            "universe_id": self.universe_id,
            "execution_contract_version": self.execution_contract_version,
            "data_identity_hash": self.data_identity_hash,
            "code_identity_hash": self.code_identity_hash,
            "evaluation_identity_hash": self.evaluation_identity_hash,
            "strategy_implementation_hash": self.strategy_implementation_hash,
            "framework_implementation_hash": self.framework_implementation_hash,
            "resolved_config_hash": self.resolved_config_hash,
            "knowledge_cutoff": self.knowledge_cutoff,
            "execution_policy_hash": self.execution_policy_hash,
            "accounting_policy_hash": self.accounting_policy_hash,
            "market_rule_policy_hash": self.market_rule_policy_hash,
            "evaluation_calendar_hash": self.evaluation_calendar_hash,
            "benchmark_policy_hash": self.benchmark_policy_hash,
        }


@dataclass(frozen=True)
class ResearchExperiment:
    experiment_id: str
    hypothesis: str
    primary_metric: str
    secondary_metrics: tuple[str, ...]
    parameter_space: Mapping[str, Any]
    selection_policy: SelectionPolicy
    split_policy: TemporalSplitPolicy
    benchmark_policy: BenchmarkPolicy
    qualification_policy_hash: str
    candidate_variants: tuple[VariantSpec, ...]
    sealed_candidate_identity_hashes: tuple[str, ...]
    walk_forward_policy: RollingWalkForwardPolicy | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "parameter_space", _immutable_mapping(self.parameter_space))
        object.__setattr__(self, "secondary_metrics", tuple(self.secondary_metrics))
        object.__setattr__(self, "candidate_variants", tuple(self.candidate_variants))
        object.__setattr__(self, "sealed_candidate_identity_hashes", tuple(self.sealed_candidate_identity_hashes))
        expected_experiment_id = _identity_hash(self.to_identity_dict())
        if self.experiment_id != expected_experiment_id:
            raise ValueError(
                f"experiment_id does not match canonical identity: expected {expected_experiment_id}, got {self.experiment_id}"
            )

    def to_identity_dict(self) -> dict[str, Any]:
        return {
            "hypothesis": self.hypothesis,
            "primary_metric": self.primary_metric,
            "secondary_metrics": self.secondary_metrics,
            "parameter_space": self.parameter_space,
            "selection_policy": self.selection_policy,
            "split_policy": self.split_policy,
            "benchmark_policy": self.benchmark_policy,
            "qualification_policy_hash": self.qualification_policy_hash,
            "sealed_candidate_identity_hashes": self.sealed_candidate_identity_hashes,
            "walk_forward_policy": self.walk_forward_policy,
        }


def create_research_experiment(
    *,
    hypothesis: str,
    primary_metric: str,
    secondary_metrics: Sequence[str],
    parameter_space: Mapping[str, Any],
    selection_policy: SelectionPolicy,
    split_policy: TemporalSplitPolicy,
    benchmark_policy: BenchmarkPolicy,
    qualification_policy: QualificationPolicy,
    candidate_variants: Sequence[VariantSpec],
    walk_forward_policy: RollingWalkForwardPolicy | None = None,
) -> ResearchExperiment:
    variants = tuple(candidate_variants)
    if not variants:
        raise ValueError("ResearchExperiment requires at least one candidate variant")
    sealed_hashes = tuple(variant.sealed_identity_hash for variant in variants)
    identity_payload = {
        "hypothesis": hypothesis,
        "primary_metric": primary_metric,
        "secondary_metrics": tuple(secondary_metrics),
        "parameter_space": parameter_space,
        "selection_policy": selection_policy,
        "split_policy": split_policy,
        "benchmark_policy": benchmark_policy,
        "qualification_policy_hash": qualification_policy.policy_hash,
        "sealed_candidate_identity_hashes": sealed_hashes,
        "walk_forward_policy": walk_forward_policy,
    }
    experiment_id = _identity_hash(identity_payload)
    return ResearchExperiment(
        experiment_id=experiment_id,
        hypothesis=hypothesis,
        primary_metric=primary_metric,
        secondary_metrics=tuple(secondary_metrics),
        parameter_space=parameter_space,
        selection_policy=selection_policy,
        split_policy=split_policy,
        benchmark_policy=benchmark_policy,
        qualification_policy_hash=qualification_policy.policy_hash,
        candidate_variants=variants,
        sealed_candidate_identity_hashes=sealed_hashes,
        walk_forward_policy=walk_forward_policy,
    )


def rank_validation_candidates(
    candidate_results: Sequence[Mapping[str, Any]],
    selection_policy: SelectionPolicy,
) -> tuple[dict[str, Any], ...]:
    _validate_selection_metric_name(selection_policy.primary_metric, selection_policy.allowed_metric_names)
    for row in candidate_results:
        _validate_selection_row(row, selection_policy)

    def sort_key(row: Mapping[str, Any]) -> tuple[Any, ...]:
        primary = float(row[selection_policy.primary_metric])
        max_drawdown = float(row.get("max_drawdown", 0.0))
        turnover = float(row.get("turnover", 0.0))
        complexity = float(row.get("complexity", 0.0))
        return (-primary, -max_drawdown, turnover, complexity, str(row["variant_key"]))

    ranked: list[dict[str, Any]] = []
    for index, row in enumerate(sorted(candidate_results, key=sort_key), start=1):
        validation_only = {
            key: value
            for key, value in row.items()
            if key in selection_policy.allowed_metric_names
        }
        validation_only["selection_rank"] = index
        ranked.append(validation_only)
    return tuple(ranked)


def build_oos_evidence_table(
    experiment: ResearchExperiment,
    candidate_oos_metrics: Mapping[str, Mapping[str, Any]],
) -> tuple[Mapping[str, Any], ...]:
    rows: list[Mapping[str, Any]] = []
    for variant, candidate_hash in zip(experiment.candidate_variants, experiment.sealed_candidate_identity_hashes, strict=True):
        metrics = dict(candidate_oos_metrics.get(variant.variant_key, {}))
        forbidden = {key for key in metrics if _is_forbidden_oos_evidence_key(str(key))}
        if forbidden:
            raise ValueError(
                "OOS evidence cannot contain winner, rank, selection, 选优 or 排名 fields: "
                f"{sorted(forbidden)}"
            )
        row = {
            "experiment_id": experiment.experiment_id,
            "variant_key": variant.variant_key,
            "candidate_identity_hash": candidate_hash,
        }
        row.update(metrics)
        rows.append(freeze_for_identity(row))
    return tuple(rows)


def _is_forbidden_oos_evidence_key(key: str) -> bool:
    if _canonical_semantic_aliases(key, _FORBIDDEN_OOS_EVIDENCE_KEYS):
        return True
    if _canonical_semantic_aliases(key, _FORBIDDEN_OOS_EVIDENCE_TOKENS):
        return True
    if _compact_prefix_semantic_aliases(key, _FORBIDDEN_OOS_EVIDENCE_TOKENS):
        return True
    return any(term in key for term in ("选优", "排名"))


def require_qualified_oos_evidence(
    experiment: ResearchExperiment,
    *,
    qualification_policy: QualificationPolicy | None = None,
    non_cluster_baseline_results: Sequence[Mapping[str, Any]] | None = None,
) -> str:
    policy = qualification_policy or QualificationPolicy()
    label = experiment.split_policy.require_qualified_oos_evidence(policy)
    if not policy.require_non_cluster_baseline:
        return label

    baseline_results = tuple(non_cluster_baseline_results or ())
    if not baseline_results:
        raise ValueError("QUALIFIED_OOS_EVIDENCE requires at least one completed non-cluster baseline")

    for result in baseline_results:
        normalized_result = _normalize_formal_baseline_contract_fields(result)
        if not _is_formal_non_cluster_baseline_result(normalized_result):
            continue
        if not _same_oos_contract_identity(normalized_result, experiment):
            continue
        return label

    raise ValueError("QUALIFIED_OOS_EVIDENCE requires a formal non-cluster baseline completed under the same OOS contract")


def _is_formal_non_cluster_baseline_result(result: Mapping[str, Any]) -> bool:
    if any(field not in result for field in _FORMAL_BASELINE_REQUIRED_FIELDS):
        return False
    if not result.get("baseline_id"):
        return False
    if result.get("strategy_family") not in _FORMAL_NON_CLUSTER_BASELINE_STRATEGY_FAMILIES:
        return False
    if result.get("uses_clustering") is not False:
        return False
    return result.get("oos_qualification_label") == QUALIFIED_OOS_EVIDENCE


def _normalize_formal_baseline_contract_fields(result: Mapping[str, Any]) -> dict[str, Any]:
    normalized = dict(result)
    for raw_field, value in result.items():
        canonical_field = _canonical_formal_baseline_field_name(str(raw_field))
        if canonical_field not in _FORMAL_BASELINE_REQUIRED_FIELDS:
            continue
        if canonical_field in normalized and _canonicalize(normalized[canonical_field]) != _canonicalize(value):
            raise ValueError(f"conflicting baseline contract aliases for {canonical_field}")
        normalized[canonical_field] = value
    return normalized


def _same_oos_contract_identity(result: Mapping[str, Any], experiment: ResearchExperiment) -> bool:
    expected = _experiment_oos_contract_identity(experiment)
    return all(result.get(field) == expected[field] for field in expected)


def _experiment_oos_contract_identity(experiment: ResearchExperiment) -> dict[str, Any]:
    return {
        "strategy_implementation_hash": _common_variant_field(experiment, "strategy_implementation_hash"),
        "framework_implementation_hash": _common_variant_field(experiment, "framework_implementation_hash"),
        "resolved_config_hash": _common_variant_field(experiment, "resolved_config_hash"),
        "data_snapshot_fingerprint": _common_variant_field(experiment, "data_identity_hash"),
        "knowledge_cutoff": _common_variant_field(experiment, "knowledge_cutoff"),
        "universe_id": _common_variant_field(experiment, "universe_id"),
        "execution_contract_version": _common_variant_field(experiment, "execution_contract_version"),
        "execution_policy_hash": _common_variant_field(experiment, "execution_policy_hash"),
        "accounting_contract_version": _DEFAULT_ACCOUNTING_CONTRACT_VERSION,
        "accounting_policy_hash": _common_variant_field(experiment, "accounting_policy_hash"),
        "market_rule_policy_hash": _common_variant_field(experiment, "market_rule_policy_hash"),
        "evaluation_calendar_hash": _identity_hash(tuple(experiment.split_policy.oos_weeks)),
        "benchmark_policy_hash": _identity_hash(experiment.benchmark_policy),
        "qualification_policy_hash": experiment.qualification_policy_hash,
        "research_experiment_id": experiment.experiment_id,
    }


def _common_variant_field(experiment: ResearchExperiment, field_name: str) -> Any:
    values = {getattr(variant, field_name) for variant in experiment.candidate_variants}
    if len(values) != 1:
        raise ValueError(f"candidate variants must share one {field_name}")
    return next(iter(values))


def _common_execution_contract_version(experiment: ResearchExperiment) -> str | None:
    return _common_variant_field(experiment, "execution_contract_version")


@dataclass(frozen=True)
class OOSConsumptionLedger:
    entries: tuple[Mapping[str, Any], ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "entries", tuple(freeze_for_identity(entry) for entry in self.entries))

    def consume(
        self,
        *,
        source_experiment_id: str,
        consumed_at: datetime,
        derived_experiment_ids: Sequence[str],
    ) -> "OOSConsumptionLedger":
        entry = {
            "source_experiment_id": source_experiment_id,
            "status": CONSUMED_AS_RESEARCH_INPUT,
            "consumed_at": consumed_at.isoformat(),
            "derived_experiment_ids": tuple(derived_experiment_ids),
        }
        return OOSConsumptionLedger(entries=self.entries + (freeze_for_identity(entry),))


@dataclass(frozen=True)
class ComponentChainStage:
    stage_id: str
    description: str
    universe_id: str
    execution_contract_version: str


@dataclass(frozen=True)
class NonClusterBaselineMetadata:
    strategy_family: str
    uses_clustering: bool
    baseline_role: str


@dataclass(frozen=True)
class ComponentChainMetadata:
    component_chain: tuple[ComponentChainStage, ...]
    non_cluster_baselines: tuple[NonClusterBaselineMetadata, ...]
    execution_ladder_included: bool = False


def build_component_chain_metadata(
    *,
    universe_id: str,
    execution_contract_version: str,
) -> ComponentChainMetadata:
    descriptions = {
        "S0": "Cash / no-alpha baseline",
        "S1": "ETF direct momentum ranking",
        "S2": "S1 + correlation clustering",
        "S3": "S2 + representative ETF",
        "S4": "S3 + ETF quality selection",
        "S5": "S4 + portfolio weighting",
        "S6": "S5 + portfolio risk layer",
    }
    chain = tuple(
        ComponentChainStage(
            stage_id=stage_id,
            description=descriptions[stage_id],
            universe_id=universe_id,
            execution_contract_version=execution_contract_version,
        )
        for stage_id in COMPONENT_CHAIN_STAGES
    )
    baselines = (
        NonClusterBaselineMetadata("etf_absolute_momentum", False, "direct ETF absolute momentum baseline"),
        NonClusterBaselineMetadata("etf_dual_momentum", False, "direct ETF dual momentum baseline"),
        NonClusterBaselineMetadata("etf_trend_momentum", False, "direct ETF trend momentum baseline"),
    )
    return ComponentChainMetadata(
        component_chain=chain,
        non_cluster_baselines=baselines,
        execution_ladder_included=False,
    )


def validate_component_chain_variants(variants: Sequence[VariantSpec]) -> None:
    ordered = tuple(variants)
    stages = tuple(variant.component_stage for variant in ordered)
    if stages != COMPONENT_CHAIN_STAGES:
        raise ValueError("component chain variants must appear exactly once in fixed S0 through S6 order")
    _require_common_component_field(ordered, "universe_id", "universe_id")
    _require_common_component_field(ordered, "execution_contract_version", "execution_contract_version")
    _require_common_component_field(ordered, "accounting_policy_hash", "accounting policy")
    _require_common_component_field(ordered, "evaluation_calendar_hash", "evaluation calendar")
    _require_common_component_field(ordered, "benchmark_policy_hash", "benchmark policy")
    _require_common_component_field(ordered, "data_identity_hash", "data snapshot")

    by_stage = {variant.component_stage: variant for variant in ordered}
    if by_stage["S0"].uses_clustering is not False or by_stage["S0"].component_toggles.get("clustering") not in {False, None}:
        raise ValueError("S0 clustering must be disabled")
    if by_stage["S1"].uses_clustering is not False or by_stage["S1"].component_toggles.get("clustering") is not False:
        raise ValueError("S1 clustering must be disabled")
    if by_stage["S2"].uses_clustering is not True or by_stage["S2"].component_toggles.get("clustering") is not True:
        raise ValueError("S2 clustering must be enabled")

    for previous, current in zip(ordered[:-1], ordered[1:], strict=True):
        diff_keys = _toggle_diff_keys(previous.component_toggles, current.component_toggles)
        if len(diff_keys) != 1:
            raise ValueError(
                f"{previous.component_stage}/{current.component_stage} must differ by exactly one registered toggle"
            )
        diff_key = next(iter(diff_keys))
        if previous.component_stage == "S4" and current.component_stage == "S5" and not _is_weighting_toggle_key(diff_key):
            raise ValueError("S4/S5 boundary may only differ by weighting")
        if previous.component_stage == "S5" and current.component_stage == "S6" and not _is_risk_toggle_key(diff_key):
            raise ValueError("S5/S6 boundary may only differ by risk")


def _require_common_component_field(variants: Sequence[VariantSpec], field_name: str, label: str) -> None:
    values = {getattr(variant, field_name) for variant in variants}
    if len(values) != 1:
        raise ValueError(f"component chain variants must share one {label}")


def _is_weighting_toggle_key(key: str) -> bool:
    normalized = key.lower()
    has_weighting_semantics = any(token in normalized for token in ("weight", "allocation", "position_size"))
    has_risk_semantics = any(token in normalized for token in ("risk", "vol", "drawdown", "exposure"))
    return has_weighting_semantics and not has_risk_semantics


def _is_risk_toggle_key(key: str) -> bool:
    normalized = key.lower()
    has_risk_semantics = any(token in normalized for token in ("risk", "vol", "drawdown", "exposure"))
    has_weighting_semantics = any(token in normalized for token in ("weight", "allocation", "position_size"))
    return has_risk_semantics and not has_weighting_semantics


def _toggle_diff_keys(left: Mapping[str, Any], right: Mapping[str, Any]) -> frozenset[str]:
    keys = set(left).union(right)
    return frozenset(key for key in keys if _canonicalize(left.get(key)) != _canonicalize(right.get(key)))
