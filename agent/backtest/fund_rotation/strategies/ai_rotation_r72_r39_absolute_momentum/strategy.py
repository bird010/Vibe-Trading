"""Round 72: R39 with a strict positive 126-day absolute momentum gate."""

from __future__ import annotations

import copy
import math
from collections.abc import Mapping, Sequence
from dataclasses import replace

import pandas as pd
from pydantic import BaseModel

from backtest.fund_rotation.contracts import (
    DecisionKind,
    FundRotationStrategyDescriptor,
    StrategyDataRequirements,
    StrategyDecisionContext,
    StrategyInitializationContext,
    TargetWeightDecision,
)
from backtest.fund_rotation.strategies.ai_rotation_r34_staged_reentry.strategy import (
    AiRotationR34StagedReentrySession,
)
from backtest.fund_rotation.strategies.ai_rotation_r39_incumbent_carry.strategy import (
    AiRotationR39IncumbentCarrySession,
    AiRotationR39IncumbentCarryStrategy,
)
from backtest.fund_rotation.strategies.correlation_representative.strategy import (
    CorrelationRepresentativeSession,
)


ABSOLUTE_MOMENTUM_LOOKBACK_DAYS = 126
_WINDOW_SIZE = ABSOLUTE_MOMENTUM_LOOKBACK_DAYS + 1

DESCRIPTOR = FundRotationStrategyDescriptor(
    id="ai_rotation_r72_r39_absolute_momentum",
    name="R72 R39 126 日绝对动量门",
    description=(
        "完全沿用 R39；仅要求每个 R39 目标的 126 日 adjusted-close 收益严格大于零，"
        "失败候选释放为现金，不重排、不重分配。"
    ),
    interface_version="1.0",
    supported_universe=("etf",),
    deterministic=True,
)


def _date_key(value: object) -> str | None:
    try:
        return pd.Timestamp(value).strftime("%Y%m%d")
    except (TypeError, ValueError, OverflowError):
        return None


def _finite_number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _causal_frame(adjusted_closes: object, signal_date: str) -> pd.DataFrame | None:
    if not isinstance(adjusted_closes, pd.DataFrame):
        return None
    signal_key = _date_key(signal_date)
    if signal_key is None:
        return None
    try:
        rows = [
            index
            for index in adjusted_closes.index
            if (index_key := _date_key(index)) is not None and index_key <= signal_key
        ]
        rows.sort(key=lambda index: _date_key(index) or "")
        return adjusted_closes.loc[rows]
    except (KeyError, TypeError, ValueError):
        return None


def _momentum_observations(
    adjusted_closes: object,
    *,
    signal_date: str,
    codes: Sequence[str],
) -> dict[str, tuple[str, float | None]]:
    observations = {str(code): ("missing_window", None) for code in codes}
    frame = _causal_frame(adjusted_closes, signal_date)
    if frame is None:
        return observations
    for code in observations:
        if code not in frame.columns:
            continue
        series = pd.to_numeric(frame[code], errors="coerce")
        if len(series) < _WINDOW_SIZE:
            continue
        window = series.iloc[-_WINDOW_SIZE:]
        raw_values = [_finite_number(value) for value in window]
        if any(value is None for value in raw_values):
            observations[code] = ("non_finite", None)
            continue
        values = [value for value in raw_values if value is not None]
        if any(value <= 0.0 for value in values):
            observations[code] = ("non_positive_price", None)
            continue
        change = values[-1] / values[0] - 1.0
        if not math.isfinite(change):
            observations[code] = ("non_finite", None)
        elif change == 0.0:
            observations[code] = ("zero_return", change)
        elif change < 0.0:
            observations[code] = ("negative_return", change)
        else:
            observations[code] = ("positive_return", change)
    return observations


def compute_absolute_momentum_returns(
    adjusted_closes: object,
    *,
    signal_date: str,
    codes: Sequence[str],
) -> dict[str, float | None]:
    """Compute causal 126-day returns from rows no later than ``signal_date``."""
    result = {str(code): None for code in codes}
    if not isinstance(adjusted_closes, pd.DataFrame):
        return result
    observations = _momentum_observations(
        adjusted_closes,
        signal_date=signal_date,
        codes=tuple(result),
    )
    for code, (_, change) in observations.items():
        result[code] = change
    return result


def apply_absolute_momentum_gate(
    target_weights: Mapping[str, float],
    cash_weight: float,
    momentum_returns: Mapping[str, object],
) -> tuple[dict[str, float], float, set[str], set[str]]:
    """Release failed target candidates to cash without reallocating survivors."""
    try:
        base_cash = float(cash_weight)
    except (TypeError, ValueError, OverflowError):
        return {}, 1.0, set(target_weights), set()
    if (
        isinstance(cash_weight, bool)
        or not math.isfinite(base_cash)
        or base_cash < 0.0
        or base_cash > 1.0
    ):
        return {}, 1.0, set(target_weights), set()
    kept: dict[str, float] = {}
    missing: set[str] = set()
    negative: set[str] = set()
    released = 0.0
    for code, raw_weight in target_weights.items():
        value = momentum_returns.get(code)
        if value is None or isinstance(value, bool):
            missing.add(code)
            released += float(raw_weight)
            continue
        try:
            momentum = float(value)
        except (TypeError, ValueError, OverflowError):
            missing.add(code)
            released += float(raw_weight)
            continue
        if not math.isfinite(momentum):
            missing.add(code)
            released += float(raw_weight)
        elif momentum <= 0.0:
            negative.add(code)
            released += float(raw_weight)
        else:
            kept[code] = raw_weight
    new_cash = base_cash + released
    if not math.isfinite(new_cash) or new_cash < 0.0:
        return dict(target_weights), cash_weight, set(), set()
    return kept, new_cash, missing, negative


def _append_reason(reason: str, code: str) -> str:
    return f"{reason}|{code}" if reason else code


class AiRotationR72R39AbsoluteMomentumSession(AiRotationR39IncumbentCarrySession):
    """R39 session with only the R126d positive gate added."""

    def __init__(self, config):
        super().__init__(config)
        self._pending_log_args = None

    def _log_decision(
        self,
        decision: TargetWeightDecision,
        *,
        scores=None,
        ranked_subjects=None,
    ) -> None:
        """Buffer base output until all R39/R72 overlays have completed."""
        self._pending_log_args = (scores, ranked_subjects)

    def _patch_artifacts(self, decision: TargetWeightDecision) -> None:
        """Defer R39's artifact mutation until the R72 gate is final."""
        del decision

    def _commit_final_artifacts(self, decision: TargetWeightDecision) -> None:
        pending = self._pending_log_args
        self._pending_log_args = None
        if pending is not None:
            scores, ranked_subjects = pending
            CorrelationRepresentativeSession._log_decision(
                self,
                decision,
                scores=scores,
                ranked_subjects=ranked_subjects,
            )
        else:
            AiRotationR34StagedReentrySession._patch_artifacts(self, decision)

    def evaluate(self, context: StrategyDecisionContext) -> TargetWeightDecision:
        state_before = copy.deepcopy(self.__dict__)
        state_before["_pending_log_args"] = None
        self._pending_log_args = None
        try:
            return self._evaluate_transaction(context)
        except BaseException:
            self.__dict__.clear()
            self.__dict__.update(state_before)
            raise

    def _evaluate_transaction(
        self,
        context: StrategyDecisionContext,
    ) -> TargetWeightDecision:
        decision = super().evaluate(context)
        if decision.action is not DecisionKind.SET_TARGETS or not decision.target_weights:
            self._commit_final_artifacts(decision)
            return decision
        try:
            adjusted_closes = context.data_view.adjusted_closes(lookback=_WINDOW_SIZE)
        except (AttributeError, KeyError, TypeError, ValueError):
            adjusted_closes = None
        momentum_returns = compute_absolute_momentum_returns(
            adjusted_closes,
            signal_date=context.signal_date,
            codes=tuple(decision.target_weights),
        )
        observations = _momentum_observations(
            adjusted_closes,
            signal_date=context.signal_date,
            codes=tuple(decision.target_weights),
        )
        target_weights, cash_weight, _, _ = apply_absolute_momentum_gate(
            decision.target_weights,
            decision.cash_weight,
            momentum_returns,
        )
        diagnostics = dict(decision.diagnostics)
        missing_window_codes = sorted(
            code for code, (status, _) in observations.items()
            if status == "missing_window"
        )
        non_finite_codes = sorted(
            code for code, (status, _) in observations.items()
            if status == "non_finite"
        )
        non_positive_price_codes = sorted(
            code for code, (status, _) in observations.items()
            if status == "non_positive_price"
        )
        zero_return_codes = sorted(
            code for code, (status, _) in observations.items()
            if status == "zero_return"
        )
        negative_return_codes = sorted(
            code for code, (status, _) in observations.items()
            if status == "negative_return"
        )
        diagnostics.update(
            {
                "absolute_momentum_lookback_days": ABSOLUTE_MOMENTUM_LOOKBACK_DAYS,
                "absolute_momentum_returns": momentum_returns,
                "absolute_momentum_missing_window_codes": missing_window_codes,
                "absolute_momentum_negative_trend_codes": negative_return_codes,
                "absolute_momentum_non_finite_codes": non_finite_codes,
                "absolute_momentum_non_positive_price_codes": non_positive_price_codes,
                "absolute_momentum_zero_return_codes": zero_return_codes,
                "absolute_momentum_rule": "R126d_strictly_positive_else_cash",
            }
        )
        reason = decision.reason_code
        if missing_window_codes:
            reason = _append_reason(reason, "ABSOLUTE_MOMENTUM_MISSING_WINDOW")
        if non_finite_codes:
            reason = _append_reason(reason, "ABSOLUTE_MOMENTUM_NON_FINITE")
        if non_positive_price_codes:
            reason = _append_reason(reason, "ABSOLUTE_MOMENTUM_NON_POSITIVE_PRICE")
        if zero_return_codes:
            reason = _append_reason(reason, "ABSOLUTE_MOMENTUM_ZERO_RETURN")
        if negative_return_codes:
            reason = _append_reason(reason, "ABSOLUTE_MOMENTUM_NEGATIVE_TREND")
        final_decision = replace(
            decision,
            decision_id=f"{context.signal_date}-{DESCRIPTOR.id}",
            target_weights=target_weights,
            cash_weight=cash_weight,
            reason_code=reason,
            diagnostics=diagnostics,
        )
        self._commit_final_artifacts(final_decision)
        return final_decision


class AiRotationR72R39AbsoluteMomentumStrategy(AiRotationR39IncumbentCarryStrategy):
    """Complete R72 strategy plug-in."""

    descriptor = DESCRIPTOR

    def describe_decision_pipeline(self, config: BaseModel) -> dict[str, object]:
        pipeline = super().describe_decision_pipeline(config)
        pipeline["selection_rule"] += "; require R126d > 0 per target, else cash"
        pipeline["absolute_momentum_lookback_days"] = ABSOLUTE_MOMENTUM_LOOKBACK_DAYS
        return pipeline

    def resolve_requirements(self, config: BaseModel) -> StrategyDataRequirements:
        requirements = super().resolve_requirements(config)
        return replace(
            requirements,
            warmup_trade_days=max(requirements.warmup_trade_days, _WINDOW_SIZE),
        )

    def create_session(
        self,
        initialization: StrategyInitializationContext,
        config: BaseModel,
    ) -> AiRotationR72R39AbsoluteMomentumSession:
        del initialization
        return AiRotationR72R39AbsoluteMomentumSession(config)  # type: ignore[arg-type]
