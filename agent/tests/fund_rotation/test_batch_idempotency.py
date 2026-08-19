"""Phase 4 Task 2 — batch request normalization and idempotency tests.

Covers §21/§21.1: required fields and RESEARCH_ONLY enforcement, canonical
client-payload hashing, variant identity, duplicate rejection, atomic key
binding and conflict semantics.
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from backtest.fund_rotation.catalog import FundRotationStrategyCatalog
from backtest.fund_rotation.strategies.registry import (
    default_fund_rotation_strategies,
)
from src.stockpred.fund_rotation.batch_models import (
    StrategyBatchRequest,
    canonical_payload_hash,
    validate_research_mode,
)
from src.stockpred.fund_rotation.batch_persistence import (
    BatchIdempotencyError,
    BatchPersistence,
    ResolvedBatchIdentity,
    VariantIdentity,
    build_variant_identities,
)


def _payload(**overrides) -> dict:
    base = {
        "schema_version": "1",
        "idempotency_key": "key-1",
        "mode": "RESEARCH_ONLY",
        "evaluation_start_date": "20240101",
        "evaluation_end_date": "20240630",
        "execution": {"initial_capital": 1_000_000.0},
        "variants": [
            {"strategy_id": "correlation_all_members", "params": {"k": 4}},
        ],
    }
    base.update(overrides)
    return base


def _catalog() -> FundRotationStrategyCatalog:
    return FundRotationStrategyCatalog(list(default_fund_rotation_strategies()))


class TestRequestValidation:
    def test_valid_request_parses(self):
        request = StrategyBatchRequest(**_payload())
        assert request.mode == "RESEARCH_ONLY"
        assert len(request.variants) == 1

    def test_empty_idempotency_key_or_schema_version_rejected(self):
        with pytest.raises(ValidationError):
            StrategyBatchRequest(**_payload(idempotency_key=""))
        with pytest.raises(ValidationError):
            StrategyBatchRequest(**_payload(schema_version=""))

    def test_missing_idempotency_key_rejected(self):
        payload = _payload()
        payload.pop("idempotency_key")
        with pytest.raises(ValidationError):
            StrategyBatchRequest(**payload)

    def test_mode_must_be_exactly_research_only(self):
        payload = _payload()
        payload.pop("mode")
        with pytest.raises(ValidationError):
            StrategyBatchRequest(**payload)
        for bad in ("research_only", "Research_Only", "LIVE", "PAPER", ""):
            with pytest.raises(ValidationError):
                StrategyBatchRequest(**_payload(mode=bad))

    def test_validate_research_mode_helper_is_structured(self):
        with pytest.raises(ValueError) as exc_info:
            validate_research_mode("LIVE")
        assert "RESEARCH_ONLY" in str(exc_info.value)
        assert validate_research_mode("RESEARCH_ONLY") == "RESEARCH_ONLY"

    def test_empty_variants_rejected(self):
        with pytest.raises(ValidationError):
            StrategyBatchRequest(**_payload(variants=[]))

    def test_unknown_request_field_rejected(self):
        with pytest.raises(ValidationError):
            StrategyBatchRequest(**_payload(not_a_field=1))


class TestCanonicalPayloadHash:
    def test_object_key_order_does_not_change_hash(self):
        reordered = _payload()
        reordered["variants"] = [{
            "params": {"top_n": 2, "k": 4},
            "strategy_id": "correlation_all_members",
        }]
        original = _payload()
        original["variants"] = [{
            "params": {"k": 4, "top_n": 2},
            "strategy_id": "correlation_all_members",
        }]
        b = StrategyBatchRequest(**reordered)
        c = StrategyBatchRequest(**original)
        assert canonical_payload_hash(b) == canonical_payload_hash(c)

    def test_different_payload_different_hash(self):
        a = StrategyBatchRequest(**_payload())
        b = StrategyBatchRequest(**_payload(idempotency_key="key-2"))
        assert canonical_payload_hash(a) != canonical_payload_hash(b)

    def test_unsupported_schema_version_rejected_before_hashing(self):
        with pytest.raises(ValidationError, match="schema_version"):
            StrategyBatchRequest(**_payload(schema_version="2"))

    def test_label_does_not_participate_in_identity(self):
        catalog = _catalog()
        a = build_variant_identities(
            catalog, StrategyBatchRequest(**_payload()).variants,
        )
        with_label = _payload()
        with_label["variants"][0]["label"] = "my favorite"
        b = build_variant_identities(
            catalog, StrategyBatchRequest(**with_label).variants,
        )
        assert a[0].variant_key == b[0].variant_key


class TestVariantIdentity:
    def test_variant_key_format(self):
        catalog = _catalog()
        request = StrategyBatchRequest(**_payload())
        identities = build_variant_identities(catalog, request.variants)
        identity = identities[0]
        assert identity.variant_key.startswith("correlation_all_members@")
        suffix = identity.variant_key.split("@", 1)[1]
        assert len(suffix) == 12
        assert suffix == identity.resolved_config_hash[:12]
        assert identity.implementation_hash
        assert identity.resolved_requirements_hash

    def test_same_strategy_same_params_duplicate_rejected(self):
        catalog = _catalog()
        payload = _payload(variants=[
            {"strategy_id": "correlation_all_members", "params": {"k": 4}},
            {
                "strategy_id": "correlation_all_members",
                "label": "again",
                "params": {"k": 4},
            },
        ])
        request = StrategyBatchRequest(**payload)
        with pytest.raises(ValueError, match="variant"):
            build_variant_identities(catalog, request.variants)

    def test_same_strategy_different_params_allowed(self):
        catalog = _catalog()
        payload = _payload(variants=[
            {"strategy_id": "correlation_all_members", "params": {"k": 4}},
            {"strategy_id": "correlation_all_members", "params": {"k": 6}},
        ])
        request = StrategyBatchRequest(**payload)
        identities = build_variant_identities(catalog, request.variants)
        assert identities[0].variant_key != identities[1].variant_key

    def test_explicit_default_params_share_variant_key_with_empty_params(self):
        from backtest.fund_rotation.strategies.correlation_all_members.config import (
            CorrelationAllMembersConfig,
        )

        catalog = _catalog()
        explicit_defaults = CorrelationAllMembersConfig().model_dump(mode="json")
        payload = _payload(variants=[
            {"strategy_id": "correlation_all_members", "params": {}},
        ])
        explicit_payload = _payload(variants=[
            {
                "strategy_id": "correlation_all_members",
                "params": explicit_defaults,
            },
        ])
        omitted = build_variant_identities(
            catalog, StrategyBatchRequest(**payload).variants,
        )
        explicit = build_variant_identities(
            catalog, StrategyBatchRequest(**explicit_payload).variants,
        )
        assert omitted[0].variant_key == explicit[0].variant_key

    def test_different_strategies_get_distinct_variant_keys(self):
        catalog = _catalog()
        payload = _payload(variants=[
            {"strategy_id": "correlation_all_members", "params": {}},
            {"strategy_id": "correlation_representative", "params": {}},
        ])
        request = StrategyBatchRequest(**payload)
        identities = build_variant_identities(catalog, request.variants)
        assert len({identity.variant_key for identity in identities}) == 2

    def test_unknown_strategy_is_structured(self):
        catalog = _catalog()
        payload = _payload(variants=[
            {"strategy_id": "nope", "params": {}},
        ])
        request = StrategyBatchRequest(**payload)
        with pytest.raises(Exception) as exc_info:
            build_variant_identities(catalog, request.variants)
        assert getattr(exc_info.value, "code", "") == (
            "FUND_ROTATION_STRATEGY_NOT_FOUND"
        )


class TestIdempotency:
    def test_first_submission_creates_and_replay_returns_original(self, tmp_path):
        store = BatchPersistence(tmp_path)
        request = StrategyBatchRequest(**_payload())
        payload_hash = canonical_payload_hash(request)

        record, created = store.submit("key-1", payload_hash)
        assert created is True
        assert record["batch_id"]
        assert record["payload_hash"] == payload_hash

        again, created_again = store.submit("key-1", payload_hash)
        assert created_again is False
        assert again["batch_id"] == record["batch_id"]

    def test_same_key_different_payload_conflicts(self, tmp_path):
        store = BatchPersistence(tmp_path)
        request = StrategyBatchRequest(**_payload())
        store.submit("key-1", canonical_payload_hash(request))

        other = StrategyBatchRequest(**_payload(evaluation_end_date="20241231"))
        with pytest.raises(BatchIdempotencyError) as exc_info:
            store.submit("key-1", canonical_payload_hash(other))
        assert exc_info.value.code == "IDEMPOTENCY_CONFLICT"

    def test_different_key_same_payload_creates_new_batch(self, tmp_path):
        store = BatchPersistence(tmp_path)
        request = StrategyBatchRequest(**_payload())
        payload_hash = canonical_payload_hash(request)
        first, _ = store.submit("key-1", payload_hash)
        second, created = store.submit("key-2", payload_hash)
        assert created is True
        assert second["batch_id"] != first["batch_id"]

    def test_binding_survives_restart(self, tmp_path):
        request = StrategyBatchRequest(**_payload())
        payload_hash = canonical_payload_hash(request)
        first, _ = BatchPersistence(tmp_path).submit("key-1", payload_hash)
        reopened = BatchPersistence(tmp_path)
        record, created = reopened.submit("key-1", payload_hash)
        assert created is False
        assert record["batch_id"] == first["batch_id"]
        reopened.submit("key-1", payload_hash)
        index_files = list((tmp_path / "idempotency").rglob("record.json"))
        assert index_files
        stored = json.loads(index_files[0].read_text(encoding="utf-8"))
        assert stored["batch_id"] == first["batch_id"]

    def test_concurrent_same_key_single_winner(self, tmp_path):
        import threading
        from concurrent.futures import ThreadPoolExecutor

        request = StrategyBatchRequest(**_payload())
        payload_hash = canonical_payload_hash(request)
        barrier = threading.Barrier(4)

        def race_submit():
            barrier.wait()
            store = BatchPersistence(tmp_path)
            return store.submit("key-race", payload_hash)

        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: race_submit(), range(4)))

        created_flags = [created for _, created in results]
        assert created_flags.count(True) == 1
        batch_ids = {record["batch_id"] for record, _ in results}
        assert len(batch_ids) == 1

    def test_batch_request_and_identity_written_atomically(self, tmp_path):
        store = BatchPersistence(tmp_path)
        request = StrategyBatchRequest(**_payload())
        payload_hash = canonical_payload_hash(request)
        record, _ = store.submit("key-1", payload_hash)
        batch_id = record["batch_id"]

        identity = ResolvedBatchIdentity(
            batch_id=batch_id,
            schema_version="1",
            mode="RESEARCH_ONLY",
            catalog_version="cat-1",
            framework_implementation_hash="fw-1",
            variants=(
                VariantIdentity(
                    variant_key="correlation_all_members@abc123abc123",
                    strategy_id="correlation_all_members",
                    label=None,
                    resolved_config_hash="abc123abc123full",
                    resolved_requirements_hash="req-1",
                    implementation_hash="impl-1",
                ),
            ),
        )
        batch_dir = store.write_batch_request(
            batch_id,
            request_payload=request.model_dump(mode="json"),
            identity=identity,
        )
        stored_request = json.loads(
            (batch_dir / "request.json").read_text(encoding="utf-8")
        )
        stored_identity = json.loads(
            (batch_dir / "resolved_batch.json").read_text(encoding="utf-8")
        )
        assert stored_request["idempotency_key"] == "key-1"
        assert stored_identity["batch_id"] == batch_id
        assert stored_identity["variants"][0]["variant_key"] == (
            "correlation_all_members@abc123abc123"
        )
        assert stored_identity["created_at"] == record["created_at"]
        assert "idempotency_key" not in json.dumps(stored_identity)
