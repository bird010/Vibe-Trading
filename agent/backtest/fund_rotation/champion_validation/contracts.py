"""冻结的 R11 Champion Validation 契约与可审计工件原语。

本模块只描述验证边界，不注册策略、不修改公共回测语义，也不做任何候选
选择。所有值对象尽量保持不可变，方便恢复运行时重新计算并比较身份哈希。
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Iterable, Mapping, Iterator
from dataclasses import dataclass, field, fields, is_dataclass
from datetime import date, datetime, timezone
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Any


SCHEMA_VERSION = "1"
EXPERIMENT_ID = "ai_fund_rotation_r11_validation_20260821"
EXPERIMENT_TYPE = "CHAMPION_VALIDATION"
SUBJECT_STRATEGY = "ai_rotation_r11_persist_geom"
CANDIDATE_SELECTION_ENABLED = False
PROMOTION_ENABLED = False
SUBJECT_STATUS = "FROZEN_RESEARCH_CANDIDATE"
TRIAL_COUNT = 30
RESEARCH_START_DATE = "2017-07-07"
RESEARCH_END_DATE = "2022-07-29"
CONSUMED_CONFIRMATION_START = "2022-08-01"
CONSUMED_CONFIRMATION_END = "2026-08-01"
BENCHMARK_IDS = ("B0", "B1", "B2", "B3", "B4", "B5")
ABLATION_VARIANTS = ("A", "B", "C", "D", "E")
MOMENTUM_WINDOWS = (3, 4, 6, 8, 12)
TOP_N_VALUES = (2, 3, 4)
RECLUSTER_WEEKS = (13, 26, 52)
STABILITY_GRID_SIZE = len(MOMENTUM_WINDOWS) * len(TOP_N_VALUES) * len(RECLUSTER_WEEKS)
_DEFAULT_STRESS_SCENARIOS = (
    "slippage_5_bps",
    "slippage_10_bps",
    "slippage_20_bps",
    "slippage_30_bps",
    "slippage_50_bps",
    "delay_next_open",
    "delay_next_close",
    "delay_plus_one_day",
    "adv_1_percent",
    "adv_2_percent",
    "adv_5_percent",
    "commission_baseline",
    "commission_double",
    "commission_minimum_sensitive",
    "strict_halt_rejection",
    "strict_limit_rejection",
    "strict_missing_adv_rejection",
)
STRESS_SCENARIOS = _DEFAULT_STRESS_SCENARIOS
_DEFAULT_THRESHOLDS = {
    "economic_value": {
        "annualized_excess_return_gt": 0.0,
        "information_ratio_gt": 0.0,
        "cost_after_alpha_gt": 0.0,
        "max_drawdown_worsening_lte": 0.01,
    },
    "stability": {
        "positive_grid_fraction_gte": 0.60,
        "positive_neighborhood_fraction_gte": 0.60,
        "neighborhood_sharpe_fraction_of_r11_gte": 0.80,
    },
    "stress": {
        "slippage_bps": 20,
        "delay_days": 1,
        "adv_participation": 0.01,
    },
    "statistics": {
        "bootstrap_samples": 10000,
        "seed": 20260821,
        "dsr_probability_gte": 0.95,
        "spa_p_value_lte": 0.10,
    },
}
_DEFAULT_STRESS_SCENARIOS = (
    "slippage_5_bps",
    "slippage_10_bps",
    "slippage_20_bps",
    "slippage_30_bps",
    "slippage_50_bps",
    "delay_next_open",
    "delay_next_close",
    "delay_plus_one_day",
    "adv_1_percent",
    "adv_2_percent",
    "adv_5_percent",
    "commission_baseline",
    "commission_double",
    "commission_minimum_sensitive",
    "strict_halt_rejection",
    "strict_limit_rejection",
    "strict_missing_adv_rejection",
)


class StageStatus(str, Enum):
    PASS = "PASS"
    INCONCLUSIVE = "INCONCLUSIVE"
    FAIL = "FAIL"


def _canonicalize(value: Any) -> Any:
    """Convert supported values to a strict, deterministic JSON shape."""
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("canonical JSON cannot contain a non-finite float")
        return value
    if isinstance(value, Enum):
        return _canonicalize(value.value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value):
        return {
            item.name: _canonicalize(getattr(value, item.name))
            for item in fields(value)
        }
    if isinstance(value, Mapping):
        return {
            str(key): _canonicalize(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_canonicalize(item) for item in value]
    if isinstance(value, (set, frozenset)):
        items = [_canonicalize(item) for item in value]
        return sorted(items, key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True))
    raise TypeError(f"unsupported value for canonical JSON: {type(value).__name__}")


def canonical_json(value: Any) -> str:
    """Serialize a value with stable key ordering and no whitespace drift."""
    return json.dumps(
        _canonicalize(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def canonical_hash(value: Any) -> str:
    """Return the SHA-256 digest of the canonical JSON representation."""
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _as_date(value: date | str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(value)


@dataclass(frozen=True)
class DateInterval:
    start: date | str
    end: date | str

    def __post_init__(self) -> None:
        start = _as_date(self.start)
        end = _as_date(self.end)
        if start > end:
            raise ValueError("interval start must not be after interval end")
        object.__setattr__(self, "start", start)
        object.__setattr__(self, "end", end)

    def overlaps(self, other: DateInterval) -> bool:
        return self.start <= other.end and other.start <= self.end

    def to_dict(self) -> dict[str, str]:
        return {"start": self.start.isoformat(), "end": self.end.isoformat()}


CONSUMED_CONFIRMATION_INTERVAL = DateInterval(
    CONSUMED_CONFIRMATION_START,
    CONSUMED_CONFIRMATION_END,
)
RESEARCH_INTERVAL = DateInterval(RESEARCH_START_DATE, RESEARCH_END_DATE)


class ConfirmationIntervalViolation(ValueError):
    code = "CONFIRMATION_INTERVAL_OVERLAP"


def validate_confirmation_interval(
    start: DateInterval | date | str,
    end: date | str | None = None,
    *,
    consumed_interval: DateInterval = CONSUMED_CONFIRMATION_INTERVAL,
) -> bool:
    """Reject any requested interval that touches the consumed interval.

    Intervals are closed at both ends because the research contract names dates,
    not timestamps. This makes the boundary conservative and auditable.
    """
    requested = start if isinstance(start, DateInterval) else DateInterval(start, start if end is None else end)
    if requested.overlaps(consumed_interval):
        raise ConfirmationIntervalViolation(
            f"{requested.start.isoformat()}..{requested.end.isoformat()} overlaps consumed "
            f"confirmation interval {consumed_interval.start.isoformat()}..{consumed_interval.end.isoformat()}"
        )
    return True


@dataclass(frozen=True)
class FrozenIdentity(Mapping[str, str]):
    input_checksum: str
    data_hash: str
    framework_hash: str
    strategy_hash: str
    execution_hash: str
    spec_hash: str
    identity_hash: str

    def to_dict(self) -> dict[str, str]:
        return {
            "input_checksum": self.input_checksum,
            "data_hash": self.data_hash,
            "framework_hash": self.framework_hash,
            "strategy_hash": self.strategy_hash,
            "execution_hash": self.execution_hash,
            "spec_hash": self.spec_hash,
            "identity_hash": self.identity_hash,
        }

    def __getitem__(self, key: str) -> str:
        return self.to_dict()[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self.to_dict())

    def __len__(self) -> int:
        return 7


def freeze_identity(
    source: Any | None = None,
    *,
    input_data: Any | None = None,
    data_snapshot: Any | None = None,
    framework: Any | None = None,
    strategy: Any | None = None,
    execution: Any | None = None,
    spec: Any | None = None,
    **components: Any,
) -> FrozenIdentity:
    """Freeze component hashes without depending on mapping insertion order."""
    data_value = data_snapshot if data_snapshot is not None else input_data
    if data_value is None:
        data_value = components.pop("data", None)
    if source is not None and all(value is None for value in (data_value, framework, strategy, execution, spec)):
        spec = source
    framework = {} if framework is None else framework
    strategy = {} if strategy is None else strategy
    execution = {} if execution is None else execution
    spec = {} if spec is None else spec
    data_value = {} if data_value is None else data_value
    extra = components
    component_values = {
        "data": data_value,
        "framework": framework,
        "strategy": strategy,
        "execution": execution,
        "spec": spec,
        "extra": extra,
    }
    data_hash = canonical_hash(data_value)
    framework_hash = canonical_hash(framework)
    strategy_hash = canonical_hash(strategy)
    execution_hash = canonical_hash(execution)
    spec_hash = canonical_hash(spec)
    input_checksum = canonical_hash(component_values)
    identity_hash = canonical_hash(
        {
            "input_checksum": input_checksum,
            "data_hash": data_hash,
            "framework_hash": framework_hash,
            "strategy_hash": strategy_hash,
            "execution_hash": execution_hash,
            "spec_hash": spec_hash,
        }
    )
    return FrozenIdentity(
        input_checksum=input_checksum,
        data_hash=data_hash,
        framework_hash=framework_hash,
        strategy_hash=strategy_hash,
        execution_hash=execution_hash,
        spec_hash=spec_hash,
        identity_hash=identity_hash,
    )


def _proxy(value: Mapping[str, Any]) -> Mapping[str, Any]:
    return _deep_freeze(value)


def _deep_freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _deep_freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_deep_freeze(item) for item in value)
    if isinstance(value, tuple):
        return tuple(_deep_freeze(item) for item in value)
    if isinstance(value, set):
        return frozenset(_deep_freeze(item) for item in value)
    return value


@dataclass(frozen=True)
class ValidationContract:
    schema_version: str = SCHEMA_VERSION
    experiment_id: str = EXPERIMENT_ID
    experiment_type: str = EXPERIMENT_TYPE
    subject_strategy: str = SUBJECT_STRATEGY
    candidate_selection_enabled: bool = CANDIDATE_SELECTION_ENABLED
    promotion_enabled: bool = PROMOTION_ENABLED
    subject_status: str = SUBJECT_STATUS
    trial_count: int = TRIAL_COUNT
    research_interval: DateInterval = RESEARCH_INTERVAL
    consumed_confirmation_interval: DateInterval = CONSUMED_CONFIRMATION_INTERVAL
    benchmark_ids: tuple[str, ...] = BENCHMARK_IDS
    ablation_variants: tuple[str, ...] = ABLATION_VARIANTS
    momentum_windows: tuple[int, ...] = MOMENTUM_WINDOWS
    top_n_values: tuple[int, ...] = TOP_N_VALUES
    recluster_weeks: tuple[int, ...] = RECLUSTER_WEEKS
    thresholds: Mapping[str, Any] = field(
        default_factory=lambda: _proxy(_DEFAULT_THRESHOLDS)
    )
    stress_scenarios: tuple[str, ...] = _DEFAULT_STRESS_SCENARIOS

    def __post_init__(self) -> None:
        if self.trial_count != 30:
            raise ValueError("trial_count is frozen at 30")
        validate_confirmation_interval(
            self.research_interval,
            consumed_interval=self.consumed_confirmation_interval,
        )
        if self.candidate_selection_enabled or self.promotion_enabled:
            raise ValueError("champion validation cannot select candidates or promote them")
        for field_name, expected in (
            ("momentum_windows", MOMENTUM_WINDOWS),
            ("top_n_values", TOP_N_VALUES),
            ("recluster_weeks", RECLUSTER_WEEKS),
            ("stress_scenarios", STRESS_SCENARIOS),
        ):
            actual = getattr(self, field_name)
            if actual != expected:
                raise ValueError(f"{field_name} is pre-registered and frozen")
            object.__setattr__(self, field_name, expected)
        thresholds = _proxy(self.thresholds)
        if canonical_hash(thresholds) != canonical_hash(_DEFAULT_THRESHOLDS):
            raise ValueError("thresholds are pre-registered and frozen")
        object.__setattr__(self, "thresholds", thresholds)

    @property
    def stability_grid_size(self) -> int:
        return len(self.momentum_windows) * len(self.top_n_values) * len(self.recluster_weeks)

    def frozen_spec(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "experiment_id": self.experiment_id,
            "experiment_type": self.experiment_type,
            "subject_strategy": self.subject_strategy,
            "candidate_selection_enabled": self.candidate_selection_enabled,
            "promotion_enabled": self.promotion_enabled,
            "subject_status": self.subject_status,
            "trial_count": self.trial_count,
            "research_interval": self.research_interval.to_dict(),
            "consumed_confirmation_interval": self.consumed_confirmation_interval.to_dict(),
            "benchmark_ids": self.benchmark_ids,
            "ablation_variants": self.ablation_variants,
            "momentum_windows": self.momentum_windows,
            "top_n_values": self.top_n_values,
            "recluster_weeks": self.recluster_weeks,
            "thresholds": _canonicalize(self.thresholds),
            "stress_scenarios": self.stress_scenarios,
        }

    def to_dict(self) -> dict[str, Any]:
        return self.frozen_spec()


@dataclass(frozen=True)
class StageResult:
    stage: str
    status: StageStatus
    reason_codes: tuple[str, ...] = ()
    metrics: Mapping[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=_utc_timestamp)
    schema_version: str = SCHEMA_VERSION
    experiment_id: str = EXPERIMENT_ID
    input_checksum: str = ""
    data_hash: str = ""
    framework_hash: str = ""
    strategy_hash: str = ""
    execution_hash: str = ""
    spec_hash: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", StageStatus(self.status))
        object.__setattr__(self, "reason_codes", tuple(self.reason_codes))
        object.__setattr__(self, "metrics", _proxy(self.metrics))

    @property
    def identity_complete(self) -> bool:
        return bool(self.schema_version.strip() and self.experiment_id.strip()) and all(
            _is_hash(value) for value in self.identity_values
        )

    @property
    def has_complete_identity(self) -> bool:
        return self.identity_complete

    @property
    def identity_context(self) -> tuple[str, ...]:
        return (self.schema_version, self.experiment_id, *self.identity_values)

    @property
    def identity_values(self) -> tuple[str, ...]:
        return tuple(getattr(self, field_name) for field_name in _IDENTITY_FIELDS)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "experiment_id": self.experiment_id,
            "timestamp": self.timestamp,
            "input_checksum": self.input_checksum,
            "data_hash": self.data_hash,
            "framework_hash": self.framework_hash,
            "strategy_hash": self.strategy_hash,
            "execution_hash": self.execution_hash,
            "spec_hash": self.spec_hash,
            "status": self.status.value,
            "reason_codes": list(self.reason_codes),
            "stage": self.stage,
            "metrics": dict(self.metrics),
        }


@dataclass(frozen=True)
class ValidationLedger:
    entries: tuple[Mapping[str, Any], ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "entries", tuple(dict(entry) for entry in self.entries))


def _reject_selection_fields(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = re.sub(r"[^a-z0-9]+", "_", str(key).strip().lower()).strip("_")
            compact = normalized.replace("_", "")
            if "winner" in compact or "recommend" in compact:
                raise ValueError(f"selection field is forbidden in validation artifacts: {key}")
            _reject_selection_fields(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _reject_selection_fields(item)


def _entry_record(entry: Any, sequence: int, previous_hash: str) -> dict[str, Any]:
    if isinstance(entry, StageResult):
        record = entry.to_dict()
    elif isinstance(entry, Mapping):
        record = dict(entry)
    else:
        raise TypeError("ledger entry must be a mapping or StageResult")
    _reject_selection_fields(record)
    payload = record.get("payload")
    if isinstance(payload, Mapping):
        # Keep the canonical payload intact while projecting non-reserved fields
        # for line-oriented audit queries (for example, ``ledger.stage``).
        reserved = set(record)
        for key, value in payload.items():
            normalized_key = str(key)
            if normalized_key not in reserved:
                record[normalized_key] = value
    record["sequence"] = sequence
    record["previous_entry_hash"] = previous_hash
    record["entry_hash"] = _entry_hash(record)
    return record


_IDENTITY_FIELDS = (
    "input_checksum",
    "data_hash",
    "framework_hash",
    "strategy_hash",
    "execution_hash",
    "spec_hash",
)


def _is_hash(value: Any) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return True


def _entry_hash(record: Mapping[str, Any]) -> str:
    return canonical_hash({key: value for key, value in record.items() if key != "entry_hash"})


def _read_ledger_entries(
    ledger: ValidationLedger | str | Path | Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    if isinstance(ledger, ValidationLedger):
        return [dict(entry) for entry in ledger.entries]
    if not isinstance(ledger, (str, Path)):
        return [dict(entry) for entry in ledger]
    path = Path(ledger)
    if not path.exists():
        return []
    entries: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            decoded = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid ledger JSON at line {line_number}") from exc
        if not isinstance(decoded, dict):
            raise ValueError(f"ledger line {line_number} is not an object")
        entries.append(decoded)
    return entries


def verify_ledger_chain(
    ledger: ValidationLedger | str | Path | Iterable[Mapping[str, Any]],
) -> bool:
    """Return whether every ledger record and predecessor link is valid."""
    try:
        entries = _read_ledger_entries(ledger)
        previous_hash = ""
        for sequence, record in enumerate(entries, 1):
            _reject_selection_fields(record)
            if record.get("sequence") != sequence:
                return False
            if record.get("previous_entry_hash") != previous_hash:
                return False
            if not _is_hash(record.get("entry_hash")) or record["entry_hash"] != _entry_hash(record):
                return False
            previous_hash = record["entry_hash"]
        return True
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return False


validate_ledger_chain = verify_ledger_chain


def append_ledger_entry(
    ledger: ValidationLedger | str | Path,
    entry: Mapping[str, Any] | StageResult,
) -> ValidationLedger | dict[str, Any]:
    """Append one entry, never rewrite an existing ledger line.

    For an in-memory ledger a new immutable value is returned. For a path a
    JSONL record is appended and the written record is returned.
    """
    if isinstance(ledger, ValidationLedger):
        if not verify_ledger_chain(ledger):
            raise ValueError("ledger chain is invalid")
        previous_hash = ledger.entries[-1]["entry_hash"] if ledger.entries else ""
        record = _entry_record(entry, len(ledger.entries) + 1, previous_hash)
        return ValidationLedger(entries=ledger.entries + (record,))

    path = Path(ledger)
    existing = _read_ledger_entries(path)
    if not verify_ledger_chain(path):
        raise ValueError("ledger chain is invalid")
    previous_hash = existing[-1]["entry_hash"] if existing else ""
    record = _entry_record(entry, len(existing) + 1, previous_hash)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(canonical_json(record) + "\n")
    return record


def build_structured_artifact(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Lazy compatibility export; implementation lives in ``report.py``."""
    from .report import build_structured_artifact as builder

    return builder(*args, **kwargs)
