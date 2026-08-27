"""Explicit-whitelist strategy Catalog — Phase 1 Task 3 (design §16).

The Catalog is built once at service startup from an explicit whitelist of
complete strategies. It validates configs, resolves config-dependent data
requirements, and binds a complete startup-fixed strategy source snapshot.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, replace
from typing import Callable, Mapping, Sequence

from pydantic import BaseModel, ValidationError

from backtest.fund_rotation.contracts import (
    FundRotationStrategy,
    FundRotationStrategyDescriptor,
    StrategyDataRequirements,
)

FUND_ROTATION_STRATEGY_NOT_FOUND = "FUND_ROTATION_STRATEGY_NOT_FOUND"
FUND_ROTATION_CONFIG_INVALID = "FUND_ROTATION_CONFIG_INVALID"
FUND_ROTATION_INTERFACE_INCOMPATIBLE = "FUND_ROTATION_INTERFACE_INCOMPATIBLE"
FUND_ROTATION_DUPLICATE_STRATEGY_ID = "FUND_ROTATION_DUPLICATE_STRATEGY_ID"
FUND_ROTATION_STRATEGY_SNAPSHOT_INVALID = "FUND_ROTATION_STRATEGY_SNAPSHOT_INVALID"

SUPPORTED_INTERFACE_VERSION = "1.0"


def _display_name(strategy_id: str, name: str) -> str:
    """Prefix AI rotation names with their canonical public strategy code."""
    match = re.match(r"^ai_rotation_(r\d+)_", strategy_id)
    if match is None:
        return name
    code = match.group(1).upper()
    body = re.sub(r"^R\d+\s*", "", name)
    return f"{code} {body}"


class CatalogError(Exception):
    """Structured catalog error returned before any background task starts."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class StrategyImplementationSnapshot:
    implementation_hash: str
    source_files: tuple[str, ...] = ()
    file_hashes: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class RegisteredFundRotationStrategy:
    descriptor: FundRotationStrategyDescriptor
    config_model: type[BaseModel]
    factory: Callable[[], FundRotationStrategy]
    implementation_snapshot: StrategyImplementationSnapshot


@dataclass(frozen=True)
class StrategyCatalogEntry:
    strategy_id: str
    name: str
    description: str
    interface_version: str
    implementation_hash: str
    supported_universe: tuple[str, ...]


@dataclass(frozen=True)
class ResolvedFundRotationStrategySpec:
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
    registered: RegisteredFundRotationStrategy
    spec: ResolvedFundRotationStrategySpec
    strategy: FundRotationStrategy


def _canonical_hash(obj: object) -> str:
    canonical = json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _snapshot_strategy(strategy_cls: type) -> StrategyImplementationSnapshot:
    """Capture the complete strategy package and imported strategy helpers.

    Snapshot construction runs during catalog startup and must fail through the
    catalog's structured error boundary regardless of whether the underlying
    filesystem/inspection failure is an ``OSError``, a mocked runtime failure,
    or another ordinary exception. Process-control exceptions are deliberately
    not caught because ``Exception`` excludes ``KeyboardInterrupt`` and
    ``SystemExit``.
    """
    from src.stockpred.fund_rotation.strategy_snapshot import (
        snapshot_strategy_package,
    )

    try:
        snapshot = snapshot_strategy_package(strategy_cls)
    except Exception as exc:
        raise CatalogError(
            FUND_ROTATION_STRATEGY_SNAPSHOT_INVALID,
            f"cannot snapshot strategy {strategy_cls!r}: {exc}",
        ) from exc
    return StrategyImplementationSnapshot(
        implementation_hash=snapshot.implementation_hash,
        source_files=snapshot.relative_paths,
        file_hashes=snapshot.file_hashes,
    )


class FundRotationStrategyCatalog:
    """Explicit-whitelist catalog of complete fund-rotation strategies."""

    def __init__(self, strategies: Sequence[type]) -> None:
        self._registry: dict[str, RegisteredFundRotationStrategy] = {}
        for strategy_cls in strategies:
            instance = strategy_cls()
            descriptor: FundRotationStrategyDescriptor = replace(
                instance.descriptor,
                name=_display_name(instance.descriptor.id, instance.descriptor.name),
            )
            strategy_id = descriptor.id
            if strategy_id in self._registry:
                raise CatalogError(
                    FUND_ROTATION_DUPLICATE_STRATEGY_ID,
                    f"strategy id registered more than once: {strategy_id}",
                )
            if descriptor.interface_version != SUPPORTED_INTERFACE_VERSION:
                raise CatalogError(
                    FUND_ROTATION_INTERFACE_INCOMPATIBLE,
                    f"strategy {strategy_id} interface_version "
                    f"{descriptor.interface_version} is not compatible with "
                    f"{SUPPORTED_INTERFACE_VERSION}",
                )
            snapshot = _snapshot_strategy(strategy_cls)
            self._registry[strategy_id] = RegisteredFundRotationStrategy(
                descriptor=descriptor,
                config_model=instance.config_model,
                factory=strategy_cls,
                implementation_snapshot=snapshot,
            )

    def list(self) -> tuple[StrategyCatalogEntry, ...]:
        entries = [
            StrategyCatalogEntry(
                strategy_id=registered.descriptor.id,
                name=registered.descriptor.name,
                description=registered.descriptor.description,
                interface_version=registered.descriptor.interface_version,
                implementation_hash=(
                    registered.implementation_snapshot.implementation_hash
                ),
                supported_universe=registered.descriptor.supported_universe,
            )
            for registered in self._registry.values()
        ]
        return tuple(sorted(entries, key=lambda entry: entry.strategy_id))

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
        """Validate, normalize, hash and bind an immutable resolved spec."""
        registered = self.require(strategy_id)
        try:
            config = registered.config_model.model_validate(dict(raw_params))
        except ValidationError as exc:
            raise CatalogError(
                FUND_ROTATION_CONFIG_INVALID,
                f"invalid config for {strategy_id}: {exc.errors()}",
            ) from exc

        resolved_config = config.model_dump(
            mode="json",
            exclude_none=False,
            exclude_unset=False,
        )
        config_schema = registered.config_model.model_json_schema()
        config_schema_hash = _canonical_hash(config_schema)
        resolved_config_hash = _canonical_hash(resolved_config)

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

        spec = ResolvedFundRotationStrategySpec(
            strategy_id=strategy_id,
            interface_version=registered.descriptor.interface_version,
            implementation_hash=(
                registered.implementation_snapshot.implementation_hash
            ),
            config_schema_version=SUPPORTED_INTERFACE_VERSION,
            config_schema_hash=config_schema_hash,
            resolved_config=resolved_config,
            resolved_config_hash=resolved_config_hash,
            resolved_requirements=requirements,
            resolved_requirements_hash=resolved_requirements_hash,
        )
        return ResolvedStrategyBinding(
            registered=registered,
            spec=spec,
            strategy=strategy,
        )
