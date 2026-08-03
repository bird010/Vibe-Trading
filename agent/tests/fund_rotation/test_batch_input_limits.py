"""Request-boundary tests for finite JSON and canonical payload limits."""

from __future__ import annotations

import math

import pytest
from pydantic import ValidationError

from src.stockpred.fund_rotation.batch_models import (
    MAX_BATCH_PAYLOAD_BYTES,
    MAX_VARIANT_PARAMS_BYTES,
    StrategyBatchRequest,
)


def _payload(*, variants=None):
    return {
        "schema_version": "1",
        "idempotency_key": "payload-limit-test",
        "mode": "RESEARCH_ONLY",
        "evaluation_start_date": "20240102",
        "evaluation_end_date": "20241231",
        "execution": {},
        "variants": variants
        or [
            {
                "strategy_id": "correlation_all_members",
                "params": {},
            }
        ],
    }


def test_variant_params_must_be_json_serializable():
    with pytest.raises(ValidationError, match="JSON-serializable"):
        StrategyBatchRequest.model_validate(
            _payload(
                variants=[
                    {
                        "strategy_id": "correlation_all_members",
                        "params": {"opaque": object()},
                    }
                ]
            )
        )


def test_variant_params_reject_non_finite_numbers():
    for value in (math.nan, math.inf, -math.inf):
        with pytest.raises(ValidationError, match="finite JSON"):
            StrategyBatchRequest.model_validate(
                _payload(
                    variants=[
                        {
                            "strategy_id": "correlation_all_members",
                            "params": {"threshold": value},
                        }
                    ]
                )
            )


def test_single_variant_params_size_is_bounded():
    oversized = "x" * (MAX_VARIANT_PARAMS_BYTES + 1)
    with pytest.raises(ValidationError, match="variant params exceed"):
        StrategyBatchRequest.model_validate(
            _payload(
                variants=[
                    {
                        "strategy_id": "correlation_all_members",
                        "params": {"payload": oversized},
                    }
                ]
            )
        )


def test_total_batch_payload_size_is_bounded():
    chunk = "x" * 60_000
    variants = [
        {
            "strategy_id": "correlation_all_members",
            "label": f"variant-{index}",
            "params": {"payload": chunk, "index": index},
        }
        for index in range(20)
    ]
    assert sum(len(item["params"]["payload"]) for item in variants) > (
        MAX_BATCH_PAYLOAD_BYTES
    )
    with pytest.raises(ValidationError, match="batch request exceeds"):
        StrategyBatchRequest.model_validate(_payload(variants=variants))
