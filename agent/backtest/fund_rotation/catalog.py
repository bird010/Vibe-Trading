"""Explicit whitelist strategy Catalog — Phase 1 Task 3 (design §16).

The Catalog is built once at service startup from an explicit whitelist of
complete strategies (no directory scanning, no dynamic import from request
strings). It validates configs, resolves config-dependent data requirements,
computes stable hashes, and binds the startup-fixed implementation snapshot.
"""

from __future__ import annotations

import hashlib
import inspect
import json
from dataclasses import dataclass
from typing import Callable, Mapping, Sequence

from pydantic import BaseModel, ValidationError

from backtest.fund_rotation.contracts import (
    FundRotationStrategy,
    FundRotationStrategyDescriptor,
    StrategyDataRequirements,
)

# ── Error codes (design §16.3) ──
FUND_ROTATION_STRATEGY_NOT_FOUND = "FUND_ROTATION_STRATEGY_NOT_FOUND"
FUND_ROTATION_CONFIG_INVALID = "FUND_ROTATION_CONFIG_INVALID"
FUND_ROTATION_INTERFACE_INCOMPATIBLE = "FUND_ROTATION_INTERFACE_INCOMPATIBLE"
FUND_ROTATION_DUPLICATE_STRATEGY_ID = "FUND_ROTATION_DUPLICATE_STRATEGY_ID"
FUND_ROTATION_STRATEGY_SNAPSHOT_INVALID = "FUND_ROTATION_STRATEGY_SNAPSHOT_INVALID"

SUPPORTED_INTERFACE_VERSION = "1.0"

# The explicit strategy whitelist lives in ``strategies.registry`` (kept out
# of the catalog machinery so the catalog stays strategy-agnostic, §16.1).


class CatalogError(Exception):
    """Structured catalog error with a stable code (returned before any
    background task is created)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# ── Value objects ──

@dataclass(frozen=True)
class StrategyImplementationSnapshot:
    """Startup-fixed implementation snapshot (full hashing in Task 5)."""

    implementation_hash: str
    source_files: tuple[str, ...] = ()


@dataclass(frozen=True)
class RegisteredFundRotationStrategy:
    """In-memory registration. ``factory`` never enters JSON/request/hash."""

    descriptor: FundRotationStrategyDescriptor
    config_model: type[BaseModel]
    factory: Callable[[], FundRotationStrategy]
    implementation_snapshot: StrategyImplementationSnapshot


@dataclass(frozen=True)
class StrategyCatalogEntry:
    """Public, persistable catalog listing entry (no factory/snapshot internals)."""

    strategy_id: str
    name: str
    description: str
    interface_version: str
    implementation_hash: str
    supported_universe: tuple[str, ...]


@dataclass(frozen=True)
class ResolvedFundRotationStrategySpec:
    """Persistable resolved spec (design §16.2) — no factory, no in-memory only
    fields."""

    strategy_id: str
    interface_version: str
    implementation_hash: str
    config_schema_version: str
    config_schema_hash: str
    resolved_config: Mapping[str, object]
    resolved_config_hash: str
    resolved_requirements: StrategyDataRequirements
    resolved_requirements_hash: str


@dataclass(frozen=True)
class ResolvedStrategyBinding:
    """Binds the in-memory registration to the persistable resolved spec and an
    instantiated strategy."""

    registered: RegisteredFundRotationStrategy
    spec: ResolvedFundRotationStrategySpec
    strategy: FundRotationStrategy


# ── Hashing helpers ──

def _canonical_hash(obj: object) -> str:
    """Stable SHA-256 over a canonical (sorted-key) JSON serialization."""
    canonical = json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _snapshot_strategy(strategy_cls: type) -> StrategyImplementationSnapshot:
    """Fix the strategy's source files at startup and hash them (design §19.1).

    Basic snapshot for Task 3; Task 5 extends this to the full strategy +
    framework snapshot machinery.
    """
    files: list[str] = []
    seen: set[str] = set()
    for cls in (strategy_cls, getattr(strategy_cls, "config_model", None)):
        if cls is None:
            continue
        try:
            path = inspect.getfile(cls)
        except TypeError:
            continue
        if path not in seen:
            seen.add(path)
            files.append(path)
    hasher = hashlib.sha256()
    for path in sorted(files):
        try:
            with open(path, "rb") as fh:
                hasher.update(fh.read())
        except OSError as exc:  # pragma: no cover - defensive
            raise CatalogError(
                FUND_ROTATION_STRATEGY_SNAPSHOT_INVALID,
                f"cannot read strategy source {path}: {exc}",
            ) from exc
    return StrategyImplementationSnapshot(
        implementation_hash=hasher.hexdigest(),
        source_files=tuple(sorted(files)),
    )


# ── Catalog ──

class FundRotationStrategyCatalog:
    """Explicit-whitelist catalog of complete fund-rotation strategies."""

    def __init__(self, strategies: Sequence[type]) -> None:
        self._registry: dict[str, RegisteredFundRotationStrategy] = {}
        for strategy_cls in strategies:
            instance = strategy_cls()
            descriptor: FundRotationStrategyDescriptor = instance.descriptor
            sid = descriptor.id
            if sid in self._registry:
                raise CatalogError(
                    FUND_ROTATION_DUPLICATE_STRATEGY_ID,
                    f"strategy id registered more than once: {sid}",
                )
            if descriptor.interface_version != SUPPORTED_INTERFACE_VERSION:
                raise CatalogError(
                    FUND_ROTATION_INTERFACE_INCOMPATIBLE,
                    f"strategy {sid} interface_version {descriptor.interface_version} "
                    f"is not compatible with {SUPPORTED_INTERFACE_VERSION}",
                )
            snapshot = _snapshot_strategy(strategy_cls)
            self._registry[sid] = RegisteredFundRotationStrategy(
                descriptor=descriptor,
                config_model=instance.config_model,
                factory=strategy_cls,
                implementation_snapshot=snapshot,
            )

    def list(self) -> tuple[StrategyCatalogEntry, ...]:
        """Catalog entries sorted stably by strategy_id."""
        entries = [
            StrategyCatalogEntry(
                strategy_id=r.descriptor.id,
                name=r.descriptor.name,
                description=r.descriptor.description,
                interface_version=r.descriptor.interface_version,
                implementation_hash=r.implementation_snapshot.implementation_hash,
                supported_universe=r.descriptor.supported_universe,
            )
            for r in self._registry.values()
        ]
        return tuple(sorted(entries, key=lambda e: e.strategy_id))

    def require(self, strategy_id: str) -> RegisteredFundRotationStrategy:
        registered = self._registry.get(strategy_id)
        if registered is None:
            raise CatalogError(
                FUND_ROTATION_STRATEGY_NOT_FOUND,
                f"unknown strategy id: {strategy_id}",
            )
        return registered

    def resolve(
        self,
        strategy_id: str,
        raw_params: Mapping[str, object],
    ) -> ResolvedStrategyBinding:
        """Full resolve flow (design §16.3): validate → fill defaults →
        cross-field → normalize → hash → resolve requirements → bind snapshot."""
        registered = self.require(strategy_id)

        # 1-4. Pydantic validation (rejects unknown fields), default fill and
        # cross-field constraints.
        try:
            config = registered.config_model.model_validate(dict(raw_params))
        except ValidationError as exc:
            raise CatalogError(
                FUND_ROTATION_CONFIG_INVALID,
                f"invalid config for {strategy_id}: {exc.errors()}",
            ) from exc

        # 5. Normalize to canonical JSON (defaults filled, none kept).
        resolved_config = config.model_dump(
            mode="json", exclude_none=False, exclude_unset=False,
        )

        # 6. Schema and resolved-config hashes.
        config_schema = registered.config_model.model_json_schema()
        config_schema_hash = _canonical_hash(config_schema)
        resolved_config_hash = _canonical_hash(resolved_config)

        # 7. Resolve and hash config-dependent data requirements (pure function).
        strategy = registered.factory()
        requirements = strategy.resolve_requirements(config)
        resolved_requirements_hash = _canonical_hash(
            {
                "required_datasets": list(requirements.required_datasets),
                "required_fields": list(requirements.required_fields),
                "warmup_trade_days": requirements.warmup_trade_days,
                "frequency": requirements.frequency,
                "needs_benchmark": requirements.needs_benchmark,
            }
        )

        # 8-9. Bind the startup-fixed implementation snapshot.
        spec = ResolvedFundRotationStrategySpec(
            strategy_id=strategy_id,
            interface_version=registered.descriptor.interface_version,
            implementation_hash=registered.implementation_snapshot.implementation_hash,
            config_schema_version=SUPPORTED_INTERFACE_VERSION,
            config_schema_hash=config_schema_hash,
            resolved_config=resolved_config,
            resolved_config_hash=resolved_config_hash,
            resolved_requirements=requirements,
            resolved_requirements_hash=resolved_requirements_hash,
        )
        return ResolvedStrategyBinding(
            registered=registered, spec=spec, strategy=strategy,
        )
