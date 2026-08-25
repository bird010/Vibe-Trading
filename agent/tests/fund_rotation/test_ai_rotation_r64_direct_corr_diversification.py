import pytest
from backtest.fund_rotation.strategies.ai_rotation_r64_direct_corr_diversification.strategy import select_direct_correlation_diversified
from backtest.fund_rotation.strategies.ai_rotation_r59_r39_signal_r57_positive_slope.strategy import AiRotationR59R39SignalR57PositiveSlopeSession as AiRotationR59R39SignalR57Session
from backtest.fund_rotation.strategies.ai_rotation_r64_direct_corr_diversification.strategy import AiRotationR64DirectCorrDiversificationStrategy
from pydantic import ValidationError
from stockpred.fund_rotation.api_models import CandidateDecisionRow

def test_r64_uses_strict_pairwise_threshold_and_allows_negative_corr():
    selected, diagnostics = select_direct_correlation_diversified(
        ["A", "B", "C"],
        {"A|B": 0.80, "A|C": -0.90, "B|C": 0.10},
        {"A|B": 20, "A|C": 20, "B|C": 20},
    )
    assert selected == ["A", "C"]
    assert diagnostics["correlation_rejected_candidates"]["B"] == "PAIRWISE_CORRELATION_TOO_HIGH"

def test_r64_skips_unknown_pairwise_correlation():
    selected, diagnostics = select_direct_correlation_diversified(["A", "B"], {}, {})
    assert selected == ["A"]
    assert diagnostics["correlation_rejected_candidates"]["B"] == "PAIRWISE_CORRELATION_UNAVAILABLE"

def test_r64_default_session_is_independent_of_cluster_session():
    session = AiRotationR64DirectCorrDiversificationStrategy().create_session(
        None, AiRotationR64DirectCorrDiversificationStrategy().config_model()
    )
    assert not isinstance(session, AiRotationR59R39SignalR57Session)

def test_r64_first_version_configuration_is_frozen():
    strategy = AiRotationR64DirectCorrDiversificationStrategy()
    with pytest.raises(ValidationError):
        strategy.config_model(top_n=4)

def test_r64_candidate_trace_uses_common_evidence_schema():
    candidate = {
        "ts_code": "A",
        "stages": {"universe_eligible": True, "ranking_eligible": True, "rank": 1, "portfolio_selected": True},
        "primary_metric": {"id": "r57_three_factor", "label": "R57 Three-Factor Momentum", "value": 1.0},
        "score": {"id": "primary_score", "model_id": "r57_three_factor", "value": 1.0, "components": {}},
        "previous_weight": 0.0,
        "before_weight": 0.0,
        "target_weight": 1 / 3,
    }
    parsed = CandidateDecisionRow.model_validate(candidate)
    assert parsed.score["model_id"] == "r57_three_factor"
    assert parsed.previous_weight == 0.0
