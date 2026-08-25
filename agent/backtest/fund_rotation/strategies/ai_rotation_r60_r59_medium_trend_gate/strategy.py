"""Round 60: R59 plus a causal 126-trading-day trend gate."""

from __future__ import annotations

import math

import pandas as pd
from pydantic import BaseModel

from backtest.fund_rotation.contracts import (
    FundRotationStrategyDescriptor,
    StrategyDecisionContext,
    StrategyInitializationContext,
)
from backtest.fund_rotation.strategies.ai_rotation_r59_r39_signal_r57_positive_slope.strategy import (
    AiRotationR59R39SignalR57PositiveSlopeSession,
    AiRotationR59R39SignalR57PositiveSlopeStrategy,
)
from backtest.fund_rotation.strategies.ai_rotation_r57_three_factor_representative.factors import (
    adjust_ohlc,
)

DESCRIPTOR = FundRotationStrategyDescriptor(
    id="ai_rotation_r60_r59_medium_trend_gate",
    name="R59 + 126日中期正趋势门禁",
    description="R59 的唯一新增机制是截至 signal close 的 126 日复权收益正趋势 gate。",
    interface_version="1.0",
    supported_universe=("etf",),
    deterministic=True,
)


def _causal(frame: pd.DataFrame, signal_date: str) -> pd.DataFrame:
    if frame.empty or not {"ts_code", "trade_date"} <= set(frame.columns):
        return frame.copy()
    out = frame.copy()
    out["ts_code"] = out["ts_code"].astype(str)
    dates = pd.to_datetime(out["trade_date"], errors="coerce")
    out = out.loc[dates <= pd.to_datetime(signal_date)].copy()
    out["trade_date"] = dates.loc[out.index].dt.strftime("%Y%m%d")
    return out.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)


def compute_adjusted_return_126d(
    bars: pd.DataFrame, adjustments: pd.DataFrame, signal_date: str
) -> dict[str, object]:
    """Return a strict 127-observation, signal-date-causal adjusted return."""
    try:
        adjusted = adjust_ohlc(
            _causal(bars, signal_date),
            _causal(adjustments, signal_date),
            pd.Timestamp(signal_date).strftime("%Y%m%d"),
        ).sort_values("trade_date").tail(127)
        closes = pd.to_numeric(adjusted["close"], errors="coerce")
    except (KeyError, TypeError, ValueError):
        return {"return_126d": None, "observations": 0, "status": "INVALID_DATA"}
    observations = int(closes.notna().sum())
    if len(adjusted) < 127:
        return {"return_126d": None, "observations": observations, "status": "INSUFFICIENT_OBSERVATIONS"}
    if not closes.notna().all() or (closes <= 0).any():
        return {"return_126d": None, "observations": observations, "status": "INVALID_PRICE"}
    result = float(closes.iloc[-1] / closes.iloc[0] - 1.0)
    if not math.isfinite(result):
        return {"return_126d": None, "observations": 127, "status": "NONFINITE_RETURN"}
    return {"return_126d": result, "observations": 127, "status": "VALID"}


class AiRotationR60R59MediumTrendGateSession(
    AiRotationR59R39SignalR57PositiveSlopeSession
):
    def _factor_rows(self, view, signal_date: str):
        rows = super()._factor_rows(view, signal_date)
        bars = _causal(
            view.daily_bars(
                ["open", "high", "low", "close", "vol", "amount"],
                lookback=127,
            ),
            signal_date,
        )
        adjustments = _causal(view.fund_adjustments(lookback=127), signal_date)
        for code, row in rows.items():
            result = compute_adjusted_return_126d(
                bars[bars["ts_code"].eq(str(code))],
                adjustments[adjustments["ts_code"].eq(str(code))],
                signal_date,
            )
            value = result["return_126d"]
            row.update(
                {
                    "medium_return_126d": value,
                    "medium_return_observations": result["observations"],
                    "medium_return_required_observations": 127,
                    "medium_return_status": result["status"],
                    "medium_trend_positive": bool(value is not None and float(value) > 0.0),
                    "r57_complete_candidate": all(row.get(name) is not None for name in ("bias", "slope", "efficiency")),
                    "r59_positive_slope_candidate": row.get("raw_slope_25d") is not None and float(row["raw_slope_25d"]) > 0.0,
                }
            )
        return rows

    @staticmethod
    def _apply_positive_slope_filter(factor_rows, composite, score_details):
        filtered, details = AiRotationR59R39SignalR57PositiveSlopeSession._apply_positive_slope_filter(factor_rows, composite, score_details)
        before = list(details.get("complete_candidates", []))
        qualified = {code for code in before if factor_rows.get(code, {}).get("medium_trend_positive") is True}
        details = dict(details)
        details.update(
            {
                "r59_candidates_before_medium_gate": before,
                "medium_trend_qualified_candidates": sorted(qualified),
                "medium_trend_rule": "adjusted_return_126d > 0",
                "complete_candidates": [code for code in before if code in qualified],
            }
        )
        return {code: value for code, value in filtered.items() if code in qualified}, details


class AiRotationR60R59MediumTrendGateStrategy(AiRotationR59R39SignalR57PositiveSlopeStrategy):
    descriptor = DESCRIPTOR
    artifact_roles = AiRotationR59R39SignalR57PositiveSlopeStrategy.artifact_roles

    def describe_decision_pipeline(self, config: BaseModel):
        pipeline = super().describe_decision_pipeline(config)
        pipeline["selection_rule"] = "R59 positive slope gate plus adjusted_return_126d > 0"
        return pipeline

    def create_session(self, initialization: StrategyInitializationContext, config: BaseModel):
        del initialization
        return AiRotationR60R59MediumTrendGateSession(config)
