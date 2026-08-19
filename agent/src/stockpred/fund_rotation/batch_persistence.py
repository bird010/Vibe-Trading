"""Batch idempotency and persistence primitives — Phase 4 Task 2 (§21.1/§22).

JSON-file persistence only (no database). The idempotency binding uses one
directory per hashed key created with ``mkdir(exist_ok=False)`` so concurrent
submissions of the same key have exactly one winner — never a
check-then-create race. A binding survives service restarts and is
state-agnostic: a failed batch is returned as-is for the same key/payload and
is never "continued"; recomputation requires a new key.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Sequence

from src.stockpred.fund_rotation.batch_models import BatchVariantRequest
from src.stockpred.fund_rotation.persistence import atomic_write_json


class BatchIdempotencyError(Exception):
    """Structured idempotency failure (returned before any task is created)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class VariantIdentity:
    """Persistable resolved identity of one variant (§21)."""

    variant_key: str
    strategy_id: str
    label: str | None
    resolved_config_hash: str
    resolved_requirements_hash: str
    implementation_hash: str
    resolved_config: dict[str, Any] = field(default_factory=dict)
    resolved_requirements: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ResolvedBatchIdentity:
    """Resolved reproduction identity — DISTINCT from the client idempotency
    hash (§21.1): service upgrades must not silently change results."""

    batch_id: str
    schema_version: str
    mode: str
    catalog_version: str
    framework_implementation_hash: str
    variants: tuple[VariantIdentity, ...]


def build_variant_identities(
    catalog,
    variants: Sequence[BatchVariantRequest],
) -> tuple[VariantIdentity, ...]:
    """Resolve every variant through the Catalog and derive stable keys.

    ``variant_key = strategy_id + "@" + resolved_config_hash[:12]``; display
    labels never enter identity. Duplicate (strategy, resolved-config) pairs
    are rejected before any task is created (§21).
    """
    identities: list[VariantIdentity] = []
    seen_configs: set[tuple[str, str]] = set()
    for variant in variants:
        binding = catalog.resolve(variant.strategy_id, dict(variant.params))
        spec = binding.spec
        variant_key = f"{variant.strategy_id}@{spec.resolved_config_hash[:12]}"
        identity_pair = (variant.strategy_id, spec.resolved_config_hash)
        if identity_pair in seen_configs:
            raise ValueError(
                f"duplicate variant: strategy {variant.strategy_id!r} with the "
                f"same resolved config appears more than once ({variant_key})"
            )
        seen_configs.add(identity_pair)
        identities.append(
            VariantIdentity(
                variant_key=variant_key,
                strategy_id=variant.strategy_id,
                label=variant.label,
                resolved_config_hash=spec.resolved_config_hash,
                resolved_requirements_hash=spec.resolved_requirements_hash,
                implementation_hash=spec.implementation_hash,
                resolved_config=dict(spec.resolved_config),
                resolved_requirements={
                    "required_datasets": list(
                        spec.resolved_requirements.required_datasets
                    ),
                    "required_fields": list(
                        spec.resolved_requirements.required_fields
                    ),
                    "warmup_trade_days": (
                        spec.resolved_requirements.warmup_trade_days
                    ),
                    "frequency": spec.resolved_requirements.frequency,
                    "needs_benchmark": (
                        spec.resolved_requirements.needs_benchmark
                    ),
                },
            )
        )
    return tuple(identities)


class BatchPersistence:
    """JSON-file batch store under ``batches_dir`` (no database)."""

    def __init__(self, batches_dir: Path) -> None:
        self.batches_dir = Path(batches_dir)
        self.idempotency_dir = self.batches_dir / "idempotency"

    def _slot_dir(self, idempotency_key: str) -> Path:
        digest = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
        return self.idempotency_dir / digest

    def submit(
        self,
        idempotency_key: str,
        payload_hash: str,
    ) -> tuple[dict[str, Any], bool]:
        """Atomically bind key to payload hash, batch id and creation time."""
        for _attempt in range(2):
            slot = self._slot_dir(idempotency_key)
            try:
                slot.mkdir(parents=True, exist_ok=False)
            except FileExistsError:
                record = self._read_record_with_retry(slot / "record.json")
                if record is not None:
                    if record.get("payload_hash") != payload_hash:
                        raise BatchIdempotencyError(
                            "IDEMPOTENCY_CONFLICT",
                            f"idempotency_key {idempotency_key!r} is already "
                            "bound to a different normalized request",
                        )
                    return record, False
                try:
                    for leftover in slot.iterdir():
                        if leftover.is_file():
                            leftover.unlink()
                    slot.rmdir()
                except OSError as exc:
                    raise BatchIdempotencyError(
                        "IDEMPOTENCY_CONFLICT",
                        f"idempotency slot for {idempotency_key!r} is not "
                        f"ready and cannot be reclaimed: {exc}",
                    ) from exc
                continue

            record = {
                "idempotency_key": idempotency_key,
                "payload_hash": payload_hash,
                "batch_id": uuid.uuid4().hex[:12],
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            }
            try:
                atomic_write_json(slot / "record.json", record)
            except Exception:
                try:
                    for leftover in slot.iterdir():
                        if leftover.is_file():
                            leftover.unlink()
                    slot.rmdir()
                except OSError:
                    pass
                raise
            return record, True
        raise BatchIdempotencyError(
            "IDEMPOTENCY_CONFLICT",
            f"idempotency slot for {idempotency_key!r} could not be acquired",
        )

    @staticmethod
    def _read_record_with_retry(record_path: Path) -> dict[str, Any] | None:
        """Wait briefly for a concurrent winner to finish writing its record."""
        for _ in range(50):
            try:
                return json.loads(record_path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                time.sleep(0.02)
            except json.JSONDecodeError:
                time.sleep(0.02)
        return None

    def _record_for_batch(self, batch_id: str) -> dict[str, Any] | None:
        """Recover the immutable idempotency record for one batch id."""
        if not self.idempotency_dir.exists():
            return None
        for slot in self.idempotency_dir.iterdir():
            record_path = slot / "record.json"
            if not record_path.is_file():
                continue
            try:
                record = json.loads(record_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if record.get("batch_id") == batch_id:
                return record
        return None

    def batch_dir(self, batch_id: str) -> Path:
        return self.batches_dir / batch_id

    def write_batch_request(
        self,
        batch_id: str,
        *,
        request_payload: dict[str, Any],
        identity: ResolvedBatchIdentity,
    ) -> Path:
        """Persist client request and resolved reproduction identity.

        ``created_at`` originates in the atomic idempotency record and is copied
        into ``resolved_batch.json`` so list/read APIs never infer submission
        time from evaluation dates or mutable filesystem timestamps.
        """
        batch_dir = self.batch_dir(batch_id)
        batch_dir.mkdir(parents=True, exist_ok=True)
        atomic_write_json(batch_dir / "request.json", request_payload)
        resolved = asdict(identity)
        record = self._record_for_batch(batch_id)
        if record and record.get("created_at"):
            resolved["created_at"] = str(record["created_at"])
        atomic_write_json(batch_dir / "resolved_batch.json", resolved)
        return batch_dir
