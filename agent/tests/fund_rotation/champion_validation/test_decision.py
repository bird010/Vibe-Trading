import pytest

from backtest.fund_rotation.champion_validation.contracts import StageResult, StageStatus
from backtest.fund_rotation.champion_validation.decision import (
    FinalAction,
    ValidationState,
    ValidationStateMachine,
    evaluate_final_decision,
)


IDENTITY = {
    "input_checksum": "1" * 64,
    "data_hash": "2" * 64,
    "framework_hash": "3" * 64,
    "strategy_hash": "4" * 64,
    "execution_hash": "5" * 64,
    "spec_hash": "6" * 64,
}


def result(stage: str, status: StageStatus, **identity: str) -> StageResult:
    return StageResult(
        stage=stage,
        status=status,
        reason_codes=(stage.lower(),),
        **{**IDENTITY, **identity},
    )


def all_passing_results() -> list[StageResult]:
    return [
        result("preflight", StageStatus.PASS),
        result("universe", StageStatus.PASS),
        result("economic", StageStatus.PASS),
        result("mechanism", StageStatus.PASS),
        result("robustness", StageStatus.PASS),
        result("statistics", StageStatus.PASS),
    ]


def test_state_machine_allows_only_the_frozen_main_path():
    machine = ValidationStateMachine()
    machine = machine.advance(result("preflight", StageStatus.PASS))
    machine = machine.advance(result("universe", StageStatus.PASS))

    assert machine.state is ValidationState.UNIVERSE_VERIFIED

    with pytest.raises(ValueError, match="economic"):
        machine.advance(result("statistics", StageStatus.PASS))


def test_all_history_layers_passing_authorizes_only_p2_research():
    decision = evaluate_final_decision(all_passing_results())

    assert decision.action is FinalAction.P2_RESEARCH_AUTHORIZED


def test_empty_or_incomplete_stage_identity_cannot_authorize_p2():
    results = all_passing_results()
    results[-1] = result("statistics", StageStatus.PASS, spec_hash="")

    decision = evaluate_final_decision(results)

    assert decision.action is not FinalAction.P2_RESEARCH_AUTHORIZED


def test_mismatched_stage_identity_is_blocked_from_p2():
    results = all_passing_results()
    results[-1] = result("statistics", StageStatus.PASS, strategy_hash="f" * 64)

    decision = evaluate_final_decision(results)

    assert decision.action is FinalAction.STOP_CURRENT_ARCHITECTURE
    assert decision.state is ValidationState.BLOCKED_IDENTITY_DRIFT


def test_mismatched_stage_experiment_context_is_blocked_from_p2():
    results = all_passing_results()
    results[-1] = result("statistics", StageStatus.PASS, experiment_id="other-experiment")

    decision = evaluate_final_decision(results)

    assert decision.action is FinalAction.STOP_CURRENT_ARCHITECTURE
    assert decision.state is ValidationState.BLOCKED_IDENTITY_DRIFT


def test_economic_failure_stops_current_architecture():
    decision = evaluate_final_decision(
        [result("universe", StageStatus.PASS), result("economic", StageStatus.FAIL)]
    )

    assert decision.action is FinalAction.STOP_CURRENT_ARCHITECTURE


def test_robustness_or_statistics_inconclusive_allows_forward_shadow_only():
    decision = evaluate_final_decision(
        [
            result("universe", StageStatus.PASS),
            result("economic", StageStatus.PASS),
            result("robustness", StageStatus.INCONCLUSIVE),
            result("statistics", StageStatus.PASS),
        ]
    )

    assert decision.action is FinalAction.FORWARD_SHADOW_ONLY


@pytest.mark.parametrize("stage", ["universe", "robustness", "statistics"])
def test_explicit_failure_never_becomes_a_research_authorization(stage):
    decision = evaluate_final_decision(
        [result("universe", StageStatus.PASS), result("economic", StageStatus.PASS), result(stage, StageStatus.FAIL)]
    )

    assert decision.action is FinalAction.STOP_CURRENT_ARCHITECTURE


def test_duplicate_canonical_stage_cannot_have_later_pass_cover_earlier_fail():
    results = all_passing_results()
    results.insert(2, result("economic-value", StageStatus.FAIL))
    results.insert(3, result("economic", StageStatus.PASS))

    decision = evaluate_final_decision(results)

    assert decision.action is FinalAction.STOP_CURRENT_ARCHITECTURE
    assert "ECONOMIC_FAIL" in decision.reason_codes


@pytest.mark.parametrize("failed_stage", ["robustness", "stability", "stress", "attribution"])
def test_robustness_failure_is_not_overwritten_by_a_later_pass(failed_stage):
    results = all_passing_results()
    results.insert(4, result(failed_stage, StageStatus.FAIL))

    decision = evaluate_final_decision(results)

    assert decision.action is FinalAction.STOP_CURRENT_ARCHITECTURE
    assert decision.state is ValidationState.ROBUSTNESS_FAILED


def test_statistics_failure_maps_to_statistical_failed():
    results = all_passing_results()
    results[-1] = result("statistics", StageStatus.FAIL)

    decision = evaluate_final_decision(results)

    assert decision.action is FinalAction.STOP_CURRENT_ARCHITECTURE
    assert decision.state is ValidationState.STATISTICAL_FAILED
