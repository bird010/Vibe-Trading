from __future__ import annotations

import json
import math
from types import SimpleNamespace

import pytest

from src.stockpred.fund_rotation import batch_child_runtime


def test_summary_projects_turnover_from_execution_diagnostics_v2() -> None:
    result = SimpleNamespace(
        execution_diagnostics={
            "metric_contract_version": "execution_diagnostics_v2",
            "attempts": {"blocked_attempt_rate": 0.125},
            "trades": {
                "one_way_turnover": 0.42,
                "annualized_one_way_turnover": 10.584,
                "commission": 12.5,
                "explicit_fee": 1.25,
                "slippage_opportunity_cost": 3.75,
            },
        }
    )

    projected = batch_child_runtime.project_execution_summary_metrics(result)

    assert projected == {
        "metric_contract_version": "execution_diagnostics_v2",
        "execution_metrics_status": "available",
        "turnover": 0.42,
        "one_way_turnover": 0.42,
        "annualized_one_way_turnover": 10.584,
        "blocked_attempt_rate": 0.125,
        "commission": 12.5,
        "explicit_fee": 1.25,
        "slippage_opportunity_cost": 3.75,
    }


def test_missing_v2_metrics_are_none_and_json_serializable() -> None:
    result = SimpleNamespace(
        execution_diagnostics={
            "metric_contract_version": "execution_diagnostics_v2",
            "attempts": {},
            "trades": {},
        }
    )

    projected = batch_child_runtime.project_execution_summary_metrics(result)

    assert projected["execution_metrics_status"] == "unavailable"
    for key in (
        "turnover",
        "one_way_turnover",
        "annualized_one_way_turnover",
        "blocked_attempt_rate",
        "commission",
        "explicit_fee",
        "slippage_opportunity_cost",
    ):
        assert projected[key] is None
    json.dumps(projected, allow_nan=False)


def test_none_and_non_finite_v2_metrics_are_not_available() -> None:
    result = SimpleNamespace(
        execution_diagnostics={
            "metric_contract_version": "execution_diagnostics_v2",
            "attempts": {"blocked_attempt_rate": math.nan},
            "trades": {
                "one_way_turnover": None,
                "annualized_one_way_turnover": math.inf,
                "commission": 1.0,
                "explicit_fee": 0.0,
                "slippage_opportunity_cost": 2.0,
            },
        }
    )

    projected = batch_child_runtime.project_execution_summary_metrics(result)

    assert projected["execution_metrics_status"] == "partial"
    assert projected["turnover"] is None
    assert projected["annualized_one_way_turnover"] is None
    assert projected["blocked_attempt_rate"] is None
    assert projected["commission"] == 1.0
    assert projected["slippage_opportunity_cost"] == 2.0
    json.dumps(projected, allow_nan=False)


@pytest.mark.parametrize("invalid_value", ["0.5", True, False, 10**400])
def test_string_and_boolean_metrics_are_not_coerced(
    invalid_value: object,
) -> None:
    result = SimpleNamespace(
        execution_diagnostics={
            "metric_contract_version": "execution_diagnostics_v2",
            "attempts": {"blocked_attempt_rate": 0.1},
            "trades": {
                "one_way_turnover": invalid_value,
                "annualized_one_way_turnover": 1.0,
                "commission": 3.0,
                "explicit_fee": 0.0,
                "slippage_opportunity_cost": 4.0,
            },
        }
    )

    projected = batch_child_runtime.project_execution_summary_metrics(result)

    assert projected["turnover"] is None
    assert projected["execution_metrics_status"] == "partial"
