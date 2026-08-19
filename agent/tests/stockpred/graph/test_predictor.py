from __future__ import annotations

import numpy as np

from src.stockpred.graph.predictor import predict_batch, predict_batch_vectorized

from agent.tests.stockpred.graph._fixtures import make_features_df


def test_vectorized_predictor_matches_batch_scoring_path() -> None:
    features = make_features_df()
    batch = predict_batch(features, top_n_evidence=0).sort_values("ts_code").reset_index(drop=True)
    vectorized = predict_batch_vectorized(features).sort_values("ts_code").reset_index(drop=True)

    np.testing.assert_allclose(batch["score"], vectorized["score"], rtol=1e-8, atol=1e-10)
    assert batch[["direction", "stage"]].equals(vectorized[["direction", "stage"]])


def test_predictor_emits_non_idle_stages_and_required_columns() -> None:
    result = predict_batch_vectorized(make_features_df(n=200))

    assert {"score", "direction", "stage", "base_score", "risk_overlay_penalty"}.issubset(result)
    assert set(result["stage"]) - {"无行情", "退潮"}
