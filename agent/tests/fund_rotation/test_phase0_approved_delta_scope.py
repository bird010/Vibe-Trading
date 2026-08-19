"""Guard the Phase 0 golden approval list against namespace-wide masking."""

from __future__ import annotations

import json
from pathlib import Path


APPROVED_DELTA = (
    Path(__file__).parent
    / "fixtures"
    / "phase0"
    / "approved_delta.json"
)


def test_approved_delta_prefixes_are_field_scoped() -> None:
    approved = json.loads(APPROVED_DELTA.read_text(encoding="utf-8"))
    prefixes = approved.get("approved_prefixes", [])
    assert prefixes, "approved_delta.json must not silently become empty"
    assert len(prefixes) == len(set(prefixes)), "duplicate approval prefixes"

    forbidden_exact = {
        "strategy_metrics.",
        "benchmark_metrics.",
        "positions_summary:",
    }
    for prefix in prefixes:
        assert prefix not in forbidden_exact, (
            f"namespace-wide golden approval is forbidden: {prefix!r}"
        )
        assert "*" not in prefix and "?" not in prefix, (
            f"glob-style golden approval is forbidden: {prefix!r}"
        )
        if prefix.startswith("strategy_metrics."):
            assert prefix.count(".") == 1 and prefix.endswith(":"), prefix
        if prefix.startswith("benchmark_metrics."):
            assert prefix.count(".") == 2 and prefix.endswith(":"), prefix


def test_approved_added_keys_are_explicit_and_non_glob() -> None:
    approved = json.loads(APPROVED_DELTA.read_text(encoding="utf-8"))
    added_keys = approved.get("approved_added_keys", {})
    assert added_keys == {
        "orders": [
            "attempt_id",
            "corporate_action_id",
            "decision_id",
            "parent_order_id",
            "replacement_chain_id",
            "replacement_of_order_id",
            "rule_version",
            "source_record_id",
        ],
        "trade_events": ["code", "rule_version", "source_record_id"],
    }
    for section_keys in added_keys.values():
        assert len(section_keys) == len(set(section_keys))
        assert all("*" not in key and "?" not in key for key in section_keys)
