from __future__ import annotations

import pandas as pd

from src.stockpred.graph import local_risk_features as risk
from src.stockpred.graph.local_risk_features import build_local_risk_features
from src.stockpred.graph.pattern_exposure import predictable_reason_codes


def test_risk_features_return_nan_when_coverage_is_not_proven() -> None:
    eval_rows = pd.DataFrame({"ts_code": ["000001.SZ"], "trade_date": ["20260105"]})
    result = build_local_risk_features(eval_rows, {"raw_pledge_detail": pd.DataFrame()})

    assert result["pledge_amount_180d"].isna().all()
    assert result["pledge_amount_180d_missing"].all()


def test_risk_module_has_no_direct_storage_reader() -> None:
    assert not hasattr(risk, "load_risk_table")
    assert not hasattr(risk, "LANCE_MARKET_CORE")
    assert not hasattr(risk, "LANCE_SOURCE_RAW")


def test_pattern_reason_codes_do_not_read_forward_outcome() -> None:
    exposed = {"pledge_amount_180d": 100.0}

    assert predictable_reason_codes({**exposed, "fwd_ret_20d": -0.2}) == (
        "pledge_pressure",
    )
    assert predictable_reason_codes({**exposed, "fwd_ret_20d": 0.2}) == (
        "pledge_pressure",
    )
