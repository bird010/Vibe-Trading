from __future__ import annotations

from backtest.fund_rotation.champion_validation.stress_tests import (
    _derive_break_even,
    build_stress_scenarios,
    evaluate_stress_results,
)


def _complete_stress_results():
    results = []
    for scenario in build_stress_scenarios():
        result = {
            "scenario_id": scenario.scenario_id,
            "execution_quality_status": "PASS",
        }
        if scenario.scenario_id in {
            "slippage_20bps",
            "delay_extra_1d",
            "adv_participation_1pct",
        }:
            result["annualized_excess_return"] = 0.01
        results.append(result)
    return results


def test_stress_scenarios_cover_all_preregistered_one_factor_dimensions():
    scenarios = build_stress_scenarios()

    assert {scenario.dimension for scenario in scenarios} == {
        "slippage",
        "delay",
        "adv_participation",
        "fees",
        "tradability",
    }
    assert {scenario.value for scenario in scenarios if scenario.dimension == "slippage"} == {
        5, 10, 20, 30, 50
    }
    assert {scenario.value for scenario in scenarios if scenario.dimension == "adv_participation"} == {
        0.01, 0.02, 0.05
    }
    assert len(scenarios) == 17


def test_stress_evaluation_applies_three_gates_and_reports_break_even_cost():
    results = _complete_stress_results()
    results[2]["break_even_transaction_cost_bps"] = 17.5

    evaluated = evaluate_stress_results(results)

    assert evaluated.status == "PASS"
    assert evaluated.break_even_transaction_cost_bps == 17.5
    assert evaluated.gates == {
        "slippage_20bps": True,
        "delay_extra_1d": True,
        "adv_participation_1pct": True,
    }
    assert not hasattr(evaluated, "winner")
    assert not hasattr(evaluated, "recommended_parameter")


def test_stress_technical_failure_blocks_pass_even_if_excess_is_positive():
    results = _complete_stress_results()
    results[8]["execution_quality_failed"] = True
    results[8]["execution_quality_status"] = "FAIL"
    evaluated = evaluate_stress_results(results)

    assert evaluated.status == "FAIL"
    assert "EXECUTION_QUALITY_FAILURE" in evaluated.reason_codes


def test_stress_evaluation_accepts_semantic_aliases_for_frozen_gate_scenarios():
    results = _complete_stress_results()
    results[2]["scenario_id"] = "slippage_20_bps"
    results[7]["scenario_id"] = "one_day_delay"
    results[8]["scenario_id"] = "adv_1pct"
    evaluated = evaluate_stress_results(results)

    assert evaluated.status == "PASS"


def test_break_even_normalizes_registered_slippage_aliases():
    break_even = _derive_break_even(
        [
            {"scenario_id": "slippage_5bps", "annualized_excess_return": 0.01},
            {"scenario_id": "slippage_20_bps", "annualized_excess_return": -0.01},
        ]
    )

    assert break_even == 12.5


def test_stress_inconclusive_execution_quality_is_not_a_pass():
    results = _complete_stress_results()
    results[8]["execution_quality_status"] = "INCONCLUSIVE"
    evaluated = evaluate_stress_results(results)

    assert evaluated.status == "FAIL"
    assert "EXECUTION_QUALITY_FAILURE" in evaluated.reason_codes


def test_stress_evaluation_requires_all_preregistered_scenarios():
    results = _complete_stress_results()
    results.pop()

    evaluated = evaluate_stress_results(results)

    assert evaluated.status == "FAIL"
    assert "MISSING_SCENARIO:tradability_missing_adv" in evaluated.technical_failures


def test_stress_evaluation_rejects_duplicate_scenarios():
    results = _complete_stress_results()
    results.append(dict(results[0]))

    evaluated = evaluate_stress_results(results)

    assert evaluated.status == "FAIL"
    assert "DUPLICATE_SCENARIO:slippage_5bps" in evaluated.technical_failures


def test_stress_evaluation_requires_explicit_good_execution_quality_for_each_scenario():
    results = _complete_stress_results()
    del results[0]["execution_quality_status"]

    evaluated = evaluate_stress_results(results)

    assert evaluated.status == "FAIL"
    assert "MISSING_EXECUTION_QUALITY:slippage_5bps" in evaluated.technical_failures
