import pytest
from backtest.fund_rotation.strategies.ai_rotation_r61_r59_dual_horizon_score.strategy import dual_horizon_scores

def test_r61_standardizes_each_horizon_before_equal_fusion():
    scores, details = dual_horizon_scores({"A": 1.0, "B": 2.0}, {"A": 0.2, "B": 0.1})
    assert scores["A"] == pytest.approx(-scores["B"])
    assert details["short_z"]["A"] < 0 < details["short_z"]["B"]
    assert details["medium_z"]["A"] > 0 > details["medium_z"]["B"]

def test_r61_zero_variance_is_zero_not_nan():
    scores, details = dual_horizon_scores({"A": 1.0, "B": 1.0}, {"A": 0.1, "B": 0.2})
    assert details["short_z"] == {"A": 0.0, "B": 0.0}
    assert all(value == value for value in scores.values())
