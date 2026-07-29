from __future__ import annotations

from src.stockpred.graph import advisor
from src.stockpred.graph.advisor import generate_advisory
from src.stockpred.graph.market_regime import detect_market_regime
from src.stockpred.graph.predictor import predict_batch_vectorized

from agent.tests.stockpred.graph._fixtures import make_features_df


def test_advisory_adds_bounded_decision_fields() -> None:
    predictions = predict_batch_vectorized(make_features_df())
    result = generate_advisory(predictions)

    assert {"confidence", "stop_loss_pct", "take_profit_pct", "position_weight", "action"}.issubset(result)
    assert result["confidence"].between(0.0, 1.0).all()
    assert result["stop_loss_pct"].between(-8.0, -3.0).all()
    assert set(result["action"]).issubset({"买入", "增持", "持有", "减持", "卖出"})


def test_market_regime_uses_supplied_prediction_frame() -> None:
    predictions = predict_batch_vectorized(make_features_df())
    regime = detect_market_regime(predictions)

    assert regime["regime"] in {"牛市", "熊市", "震荡市"}
    assert 0.0 <= regime["confidence"] <= 1.0


def test_advisor_does_not_expose_legacy_html_renderer() -> None:
    assert not hasattr(advisor, "generate_html_report")
