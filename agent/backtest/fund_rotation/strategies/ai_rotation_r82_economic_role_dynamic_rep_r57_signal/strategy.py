"""R82: R81 dynamic representatives with an isolated R57 score layer."""

from __future__ import annotations

import math
import re

import numpy as np
import pandas as pd
from pydantic import BaseModel

from backtest.fund_rotation.contracts import (
    DecisionKind,
    FundRotationStrategyDescriptor,
    QualityStatus,
    StrategyArtifact,
    StrategyDataRequirements,
    StrategyDecisionContext,
    StrategyDiagnostics,
    StrategyInitializationContext,
    TargetWeightDecision,
)
from backtest.fund_rotation.risk_layers import apply_defense_asset
from backtest.fund_rotation.strategies.ai_rotation_r34_staged_reentry.strategy import (
    _append_reason,
    apply_staged_reentry,
)
from backtest.fund_rotation.strategies.ai_rotation_r39_incumbent_carry.strategy import (
    apply_incumbent_carry,
)
from backtest.fund_rotation.strategies.ai_rotation_r57_three_factor_representative.factors import (
    adjust_ohlc,
    compute_bias_momentum,
    compute_efficiency_momentum,
    compute_slope_momentum,
    score_complete_candidates,
)
from backtest.fund_rotation.strategies.correlation_all_members.signals import (
    signal_date_eligible,
)
from backtest.fund_rotation.strategies.economic_role_rotation.roles import (
    ROLE_IDS,
    classify_fund_name,
    role_rule_hash,
)
from backtest.fund_rotation.strategies.economic_role_rotation.strategy import (
    ROLE_CLASSIFIER_VERSION,
    ROLE_NAMES,
    EconomicRoleSession,
    _EconomicRoleStrategy,
    _hash_json,
    _liquidity,
)


DESCRIPTOR = FundRotationStrategyDescriptor(
    id="ai_rotation_r82_economic_role_dynamic_rep_r57_signal",
    name="Role 动态代表 R57 三因子轮动",
    description=(
        "保留 R81 经济角色动态代表选择与生命周期，仅以 R57 三因子"
        "对当前 Role 代表排序，并沿用 R34/R39/R76 下游。"
    ),
    interface_version="1.0",
    supported_universe=("etf",),
    deterministic=True,
)

_FACTOR_LOOKBACK_DAYS = 49
_BIAS_REQUIRED = 49
_SLOPE_REQUIRED = 25
_EFFICIENCY_REQUIRED = 25
_MINIMUM_COMPLETE_CANDIDATES = 2
_FACTOR_WEIGHTS = {"bias": 0.3, "slope": 0.3, "efficiency": 0.4}
_TOP_N = 3


def _validate_config(config) -> None:
    if getattr(config, "top_n", None) != _TOP_N:
        raise ValueError("R82 requires frozen top_n=3")


def _date_value(value: object) -> pd.Timestamp:
    raw = str(value).strip()
    if re.fullmatch(r"\d{8}", raw):
        return pd.Timestamp(raw)
    try:
        return pd.Timestamp(value)
    except (TypeError, ValueError):
        return pd.NaT


def _causal_frame(frame: pd.DataFrame, signal_date: str) -> pd.DataFrame:
    if not {"ts_code", "trade_date"} <= set(frame.columns):
        return frame.copy()
    result = frame.copy()
    result["ts_code"] = result["ts_code"].astype(str)
    dates = result["trade_date"].map(_date_value)
    cutoff = _date_value(signal_date)
    result = result.loc[dates.notna() & dates.le(cutoff)].copy()
    result["trade_date"] = dates.loc[result.index].dt.strftime("%Y%m%d")
    return result.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)


def _number(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _price_status(values: pd.Series, required: int) -> tuple[int, str]:
    numeric = pd.to_numeric(values, errors="coerce").to_numpy(dtype=float)
    finite = np.isfinite(numeric)
    positive = finite & (numeric > 0.0)
    count = int(positive.sum())
    if len(numeric) < required:
        return count, "INSUFFICIENT_OBSERVATIONS"
    if not finite.all():
        return count, "NONFINITE_PRICE"
    if not (numeric > 0.0).all():
        return count, "NON_POSITIVE_PRICE"
    return count, "VALID"


def _ohlc_status(values: pd.DataFrame, required: int) -> tuple[int, str]:
    numeric = values.apply(pd.to_numeric, errors="coerce").to_numpy(dtype=float)
    finite = np.isfinite(numeric).all(axis=1)
    positive = finite & (numeric > 0.0).all(axis=1)
    count = int(positive.sum())
    if len(numeric) < required:
        return count, "INSUFFICIENT_OBSERVATIONS"
    if not finite.all():
        return count, "NONFINITE_OHLC"
    if not (numeric > 0.0).all():
        return count, "NON_POSITIVE_OHLC"
    return count, "VALID"


class EconomicRoleR57Session(EconomicRoleSession):
    """Reuse R81 lifecycle state and replace only its role score layer."""

    def __init__(self, config, descriptor_id: str) -> None:
        _validate_config(config)
        super().__init__(config, descriptor_id)
        self.score_subject = "REPRESENTATIVE"
        self.representative_mode = "DYNAMIC"
        self._factor_scores: list[dict[str, object]] = []

    def _factor_rows(self, view, signal_date: str) -> dict[str, dict[str, object]]:
        rows: dict[str, dict[str, object]] = {}
        try:
            bars = _causal_frame(
                view.daily_bars(
                    ["open", "high", "low", "close", "vol", "amount"],
                    # Keep one extra row so a future row in a test view is
                    # removed by the causal cutoff without copying history.
                    lookback=_FACTOR_LOOKBACK_DAYS + 1,
                ),
                signal_date,
            )
            adjustments = _causal_frame(
                view.fund_adjustments(lookback=_FACTOR_LOOKBACK_DAYS + 1),
                signal_date,
            )
            required_bars = {"ts_code", "trade_date", "open", "high", "low", "close"}
            required_adjustments = {"ts_code", "trade_date", "adj_factor"}
            if (
                not isinstance(bars, pd.DataFrame)
                or not isinstance(adjustments, pd.DataFrame)
                or not required_bars <= set(bars.columns)
                or not required_adjustments <= set(adjustments.columns)
            ):
                raise ValueError("factor input columns are incomplete")
        except (AttributeError, KeyError, TypeError, ValueError):
            for role_id, representative in sorted(
                (role, code)
                for role, code in self._representatives.items()
                if code
            ):
                rows[str(representative)] = {
                    "ts_code": str(representative),
                    "role_id": role_id,
                    "is_representative": True,
                    "observations": 0,
                    "adjusted_observations": 0,
                    "bias_required_observations": _BIAS_REQUIRED,
                    "slope_required_observations": _SLOPE_REQUIRED,
                    "efficiency_required_observations": _EFFICIENCY_REQUIRED,
                    "bias": None,
                    "slope": None,
                    "efficiency": None,
                    "bias_status": "INVALID_INPUT",
                    "slope_status": "INVALID_INPUT",
                    "efficiency_status": "INVALID_INPUT",
                    "status_code": "INVALID_FACTOR_INPUT",
                }
            return rows
        for role_id, representative in sorted(
            (role, code)
            for role, code in self._representatives.items()
            if code
        ):
            code = str(representative)
            code_bars = (
                bars[bars["ts_code"].eq(code)]
                .sort_values("trade_date")
                .tail(_FACTOR_LOOKBACK_DAYS)
            )
            code_adjustments = (
                adjustments[adjustments["ts_code"].eq(code)]
                .sort_values("trade_date")
            )
            row: dict[str, object] = {
                "ts_code": code,
                "role_id": role_id,
                "is_representative": True,
                "observations": int(len(code_bars)),
                "adjusted_observations": 0,
                "bias_observations": 0,
                "slope_observations": 0,
                "efficiency_observations": 0,
                "bias_required_observations": _BIAS_REQUIRED,
                "slope_required_observations": _SLOPE_REQUIRED,
                "efficiency_required_observations": _EFFICIENCY_REQUIRED,
                "bias": None,
                "slope": None,
                "efficiency": None,
                "bias_status": "NOT_EVALUATED",
                "slope_status": "NOT_EVALUATED",
                "efficiency_status": "NOT_EVALUATED",
            }
            try:
                adjusted = adjust_ohlc(code_bars, code_adjustments, signal_date)
                adjusted = (
                    adjusted.sort_values("trade_date")
                    .tail(_FACTOR_LOOKBACK_DAYS)
                    .reset_index(drop=True)
                )
                row["adjusted_observations"] = int(len(adjusted))
                full_count, full_status = _ohlc_status(
                    adjusted[["open", "high", "low", "close"]],
                    _FACTOR_LOOKBACK_DAYS,
                )
                if full_status != "VALID":
                    row.update(
                        {
                            "bias_observations": full_count,
                            "slope_observations": full_count,
                            "efficiency_observations": full_count,
                            "bias_status": full_status,
                            "slope_status": full_status,
                            "efficiency_status": full_status,
                            "status_code": "INVALID_FULL_OHLC_WINDOW",
                        }
                    )
                    rows[code] = row
                    continue
                bias_count, bias_status = _price_status(
                    adjusted["close"], _BIAS_REQUIRED
                )
                slope_count, slope_status = _price_status(
                    adjusted["close"].tail(_SLOPE_REQUIRED), _SLOPE_REQUIRED
                )
                efficiency_count, efficiency_status = _ohlc_status(
                    adjusted[["open", "high", "low", "close"]].tail(
                        _EFFICIENCY_REQUIRED
                    ),
                    _EFFICIENCY_REQUIRED,
                )
                row.update(
                    {
                        "bias": _number(compute_bias_momentum(adjusted["close"])),
                        "slope": _number(compute_slope_momentum(adjusted["close"])),
                        "efficiency": _number(
                            compute_efficiency_momentum(adjusted)
                        ),
                        "bias_observations": bias_count,
                        "slope_observations": slope_count,
                        "efficiency_observations": efficiency_count,
                        "bias_status": bias_status,
                        "slope_status": slope_status,
                        "efficiency_status": efficiency_status,
                    }
                )
                for factor in ("bias", "slope", "efficiency"):
                    if row[f"{factor}_status"] == "VALID" and row[factor] is None:
                        row[f"{factor}_status"] = "INVALID_RESULT"
            except (KeyError, TypeError, ValueError):
                row.update(
                    {
                        "status_code": "INVALID_OHLC_OR_ADJUSTMENT",
                        "bias_status": "ADJUSTMENT_DATA_INVALID",
                        "slope_status": "ADJUSTMENT_DATA_INVALID",
                        "efficiency_status": "ADJUSTMENT_DATA_INVALID",
                    }
                )
            rows[code] = row
        return rows

    def _resolve_defense(
        self,
        view,
        signal_date: str,
        signal_eligible: set[str],
    ) -> tuple[str | None, dict[str, object], str]:
        del view, signal_date
        defense_code = "511010.SH" if "511010.SH" in signal_eligible else None
        return (
            defense_code,
            {"risk_layer": "fixed_short_bond"},
            "FIXED_SHORT_BOND_DEFENSE" if defense_code else "FIXED_SHORT_BOND_UNAVAILABLE",
        )

    def _score_representatives(
        self,
        view,
        signal_date: str,
        rows: dict[str, dict[str, object]],
    ) -> tuple[dict[str, float], dict[str, object]]:
        raw_scores = {
            code: {
                factor: row.get(factor)
                for factor in ("bias", "slope", "efficiency")
            }
            for code, row in rows.items()
        }
        return score_complete_candidates(
            raw_scores,
            _FACTOR_WEIGHTS,
            _MINIMUM_COMPLETE_CANDIDATES,
        )

    def _score_model_metadata(self) -> dict[str, object]:
        return {
            "id": "r57_three_factor",
            "label": "R57 Three-Factor Momentum",
            "version": "1",
            "direction": "HIGHER_BETTER",
            "lookback_days": _FACTOR_LOOKBACK_DAYS,
            "weights": dict(_FACTOR_WEIGHTS),
            "scope": "ECONOMIC_ROLE_REPRESENTATIVE",
        }

    @staticmethod
    def _enrich_rows(
        rows: dict[str, dict[str, object]],
        details: dict[str, object],
        composite: dict[str, float],
        selected_codes: set[str],
        base_weights: dict[str, float],
        final_weights: dict[str, float],
        final_cash: float,
        staged: set[str],
        incumbents: set[str],
    ) -> None:
        complete = set(details.get("complete_candidates", []))
        standardization = details.get("standardization", {})
        ranks = {code: rank for rank, code in enumerate(composite, start=1)}
        for code, row in rows.items():
            row["complete_candidate"] = code in complete
            row["composite_score"] = _number(composite.get(code))
            row["rank"] = ranks.get(code)
            for factor in ("bias", "slope", "efficiency"):
                info = standardization.get(factor, {})
                z_scores = info.get("z_scores", {})
                row[f"{factor}_mean"] = _number(info.get("mean"))
                row[f"{factor}_std"] = _number(info.get("std"))
                row[f"{factor}_zscore"] = _number(z_scores.get(code))
            row["top_3"] = code in selected_codes
            row["base_slot_weight"] = _number(base_weights.get(code, 0.0))
            row["staged"] = code in staged
            row["incumbent_carry"] = code in incumbents
            row["final_weight"] = _number(final_weights.get(code, 0.0))
            row["cash_weight"] = _number(final_cash)

    def evaluate(self, context: StrategyDecisionContext) -> TargetWeightDecision:
        cfg = self._config
        signal_date = context.signal_date
        view = context.data_view
        previous_weights = dict(self._previous_weights)
        week_index = self._week_index
        self._week_index += 1

        instruments = view.eligible_universe()
        classifications = {
            instrument.ts_code: classify_fund_name(instrument.name)
            for instrument in instruments
        }
        for code, classification in classifications.items():
            if classification.status != "MATCHED":
                self._exclusions.append(
                    {
                        "signal_date": signal_date,
                        "ts_code": code,
                        "status": classification.status,
                        "reason": classification.exclusion_reason,
                    }
                )
        current_codes = set(classifications)
        signal_codes, signal_exclusions = signal_date_eligible(
            view, sorted(current_codes), signal_date
        )
        signal_eligible = set(signal_codes)
        for record in signal_exclusions:
            self._exclusions.append(
                {
                    "signal_date": signal_date,
                    "ts_code": record.ts_code,
                    "status": "SIGNAL_DATE",
                    "reason": record.reason.value,
                }
            )
        weekly = view.returns("weekly", cfg.history_quality_lookback_weeks)
        valid_counts = {
            code: int(pd.to_numeric(weekly[code], errors="coerce").notna().sum())
            if code in weekly.columns
            else 0
            for code in current_codes
        }
        adv, hard_failed, liquidity_diagnostics = _liquidity(
            view,
            signal_date,
            cfg.representative_liquidity_window_days,
            cfg.representative_min_liquidity_observations,
        )
        regular_refresh = (
            not self._role_members
            or week_index - self._last_role_refresh_week >= cfg.refresh_interval_weeks
        )
        if regular_refresh:
            self._last_role_refresh_week = week_index
            for role_id in ROLE_IDS:
                self._role_members[role_id] = sorted(
                    code
                    for code, classification in classifications.items()
                    if classification.role_id == role_id
                    and valid_counts.get(code, 0) >= cfg.min_valid_weeks
                    and code in signal_eligible
                )
        selection_modes: dict[str, str] = {}
        for role_id in ROLE_IDS:
            previous_representative = self._representatives.get(role_id)
            selected, mode = self._select_rep(
                role_id,
                classifications,
                {
                    code
                    for code, count in valid_counts.items()
                    if count >= cfg.min_valid_weeks
                },
                signal_eligible,
                adv,
                hard_failed,
                regular_refresh=regular_refresh,
            )
            self._representatives[role_id] = selected
            selection_modes[role_id] = mode
            self._role_representatives.append(
                {
                    "signal_date": signal_date,
                    "role_id": role_id,
                    "representative": selected,
                    "selection_mode": mode,
                    "previous_representative": previous_representative,
                    "liquidity": liquidity_diagnostics.get(selected or "", {}),
                }
            )

        rows = self._factor_rows(view, signal_date)
        composite, details = self._score_representatives(
            view, signal_date, rows
        )
        complete = set(details["complete_candidates"])
        ranked_codes = list(composite)
        selected_codes = set(ranked_codes[:_TOP_N])
        role_by_code = {code: str(row["role_id"]) for code, row in rows.items()}
        selected_roles = [role_by_code[code] for code in ranked_codes if code in selected_codes]
        slot_weight = 1.0 / _TOP_N
        base_weights: dict[str, float] = {}
        vacant_roles: list[str] = []
        for role_id in selected_roles:
            representative = self._representatives.get(role_id)
            if representative:
                base_weights[representative] = base_weights.get(representative, 0.0) + slot_weight
            else:
                vacant_roles.append(role_id)
        base_cash = max(0.0, 1.0 - sum(base_weights.values()))
        staged_weights, _, staged = apply_staged_reentry(previous_weights, base_weights)
        carried_weights, carried_cash, carried_codes, incumbents = apply_incumbent_carry(
            previous_weights, staged_weights
        )
        defense_code, defense_layer, defense_reason = self._resolve_defense(
            view, signal_date, signal_eligible
        )
        final_weights, final_cash, defense_diagnostics = apply_defense_asset(
            carried_weights, carried_cash, defense_code=defense_code
        )
        self._enrich_rows(
            rows,
            details,
            composite,
            selected_codes,
            base_weights,
            final_weights,
            final_cash,
            staged,
            incumbents,
        )
        self._previous_weights = dict(final_weights)

        rank_by_role = {role: rank for rank, role in enumerate(selected_roles, start=1)}
        for role_id in ROLE_IDS:
            code = self._representatives.get(role_id)
            row = rows.get(code or "")
            self._role_diagnostics.append(
                {
                    "signal_date": signal_date,
                    "role_id": role_id,
                    "role_member_count": len(self._role_members.get(role_id, [])),
                    "score_subject": "CURRENT_REPRESENTATIVE",
                    "score": row or {"complete_candidate": False},
                    "score_rank": rank_by_role.get(role_id),
                    "representative_available": code is not None,
                    "selection_mode": selection_modes[role_id],
                    "score_available_but_execution_rep_missing": False,
                }
            )
        role_rows: list[dict[str, object]] = []
        for role_id in ROLE_IDS:
            code = self._representatives.get(role_id)
            row = dict(rows.get(code or "", {}))
            role_rows.append(
                {
                    "ts_code": code or f"role:{role_id}",
                    "role_id": role_id,
                    "role_name": ROLE_NAMES[role_id],
                    "members": list(self._role_members.get(role_id, [])),
                    "stages": {
                        "role_matched": True,
                        "representative_available": code is not None,
                        "ranking_eligible": code in composite if code else False,
                        "rank": rank_by_role.get(role_id),
                        "portfolio_selected": role_id in selected_roles and code is not None,
                    },
                    "score": row,
                    "previous_weight": float(previous_weights.get(code or "", 0.0)),
                    "target_weight": float(final_weights.get(code or "", 0.0)),
                }
            )
            self._role_history.append(
                {
                    "signal_date": signal_date,
                    "role_id": role_id,
                    "role_name": ROLE_NAMES[role_id],
                    "members": list(self._role_members.get(role_id, [])),
                    "members_as_of": signal_date,
                    "representative": code,
                    "representative_as_of": signal_date if code else None,
                    "selection_mode": selection_modes[role_id],
                }
            )
        self._decision_trace.append(
            {
                "signal_date": signal_date,
                "role_snapshot": {
                    "regular_refresh": regular_refresh,
                    "last_role_refresh_week": self._last_role_refresh_week,
                },
                "candidates": role_rows,
            }
        )
        reason = ""
        if len(complete) < _MINIMUM_COMPLETE_CANDIDATES:
            reason = "INSUFFICIENT_COMPLETE_CANDIDATES"
        if staged:
            reason = _append_reason(reason, "STAGED_REENTRY")
        if incumbents:
            reason = _append_reason(reason, "INCUMBENT_CARRY")
        reason = _append_reason(
            reason,
            defense_reason,
        )
        quality = QualityStatus.VALID if len(complete) >= _MINIMUM_COMPLETE_CANDIDATES else QualityStatus.DEGRADED
        diagnostics = {
            "role_rule_hash": role_rule_hash(),
            "role_classifier_version": ROLE_CLASSIFIER_VERSION,
            "effective_role_assignment_hash": _hash_json(
                {
                    "signal_date": signal_date,
                    "assignments": [
                        {"ts_code": code, **classification.__dict__}
                        for code, classification in sorted(classifications.items())
                    ],
                }
            ),
            "effective_universe_codes": sorted(current_codes),
            "effective_universe_hash": _hash_json(sorted(current_codes)),
            "score_model": self._score_model_metadata(),
            "selected_roles": selected_roles,
            "vacant_roles": vacant_roles,
            "filled_slots": len(selected_roles) - len(vacant_roles),
            "staged_reentry_codes": sorted(staged),
            "incumbent_carry_codes": sorted(incumbents),
            "carried_codes": sorted(carried_codes),
            "risk_layer": defense_layer["risk_layer"],
            "defense_layer": defense_layer,
            "defense_asset": defense_code,
            "defense_diagnostics": defense_diagnostics,
            "selection_modes": selection_modes,
            "history_quality_lookback_weeks": cfg.history_quality_lookback_weeks,
            "min_valid_weeks": cfg.min_valid_weeks,
            "refresh_interval_weeks": cfg.refresh_interval_weeks,
            "signal_information_cutoff": "CLOSE",
            "factor_scores": rows,
            "score_details": details,
            "complete_candidate_count": len(complete),
            "ranked_codes": ranked_codes,
            "top_n": _TOP_N,
            "slot_weight": slot_weight,
            "base_target_weights": base_weights,
            "base_cash_weight": base_cash,
            "score_model_source": "R57 signal applied after R81 representative selection",
        }
        decision = TargetWeightDecision(
            decision_id=f"{signal_date}-{self._descriptor_id}",
            signal_date=signal_date,
            action=DecisionKind.SET_TARGETS,
            target_weights=final_weights,
            cash_weight=final_cash,
            reason_code=reason,
            quality_status=quality,
            diagnostics=diagnostics,
        )
        self._decision_log.append(
            {
                "signal_date": signal_date,
                "action": decision.action.value,
                "target_weights": dict(final_weights),
                "cash_weight": final_cash,
                "reason_code": reason,
                "quality_status": quality.value,
                "diagnostics": diagnostics,
            }
        )
        self._factor_scores.append(
            {
                "signal_date": signal_date,
                "rows": [rows[code] for code in sorted(rows)],
                "complete_candidates": sorted(complete),
                "ranked_codes": ranked_codes,
                "selected_roles": selected_roles,
            }
        )
        return decision

    def finalize(self) -> StrategyDiagnostics:
        base = super().finalize()
        return StrategyDiagnostics(
            artifacts=base.artifacts
            + (StrategyArtifact("factor_scores", "application/json", self._factor_scores),),
            decision_trace=base.decision_trace,
        )


class AiRotationR82EconomicRoleDynamicRepR57SignalStrategy(_EconomicRoleStrategy):
    descriptor = DESCRIPTOR
    config_model = _EconomicRoleStrategy.config_model
    score_subject = "REPRESENTATIVE"
    representative_mode = "DYNAMIC"
    artifact_roles = (
        "role_history",
        "role_representatives",
        "role_diagnostics",
        "exclusions",
        "decisions",
        "factor_scores",
    )

    def describe_decision_pipeline(self, config: BaseModel) -> dict[str, object]:
        cfg = self.config_model.model_validate(config)
        _validate_config(cfg)
        return {
            "universe": "ECONOMIC_ROLE",
            "roles": list(ROLE_IDS),
            "representative_method": "R81 dynamic tier/ADV/code representative",
            "score_subject": "CURRENT_REPRESENTATIVE",
            "score_model": {
                "id": "r57_three_factor",
                "version": "1",
                "lookback_days": _FACTOR_LOOKBACK_DAYS,
                "weights": dict(_FACTOR_WEIGHTS),
            },
            "selection_rule": f"Top {cfg.top_n} Role slots by R57 score",
            "top_n": cfg.top_n,
            "weighting_rule": "Equal Weight Role Slots",
            "downstream": "R34 staged reentry -> R39 incumbent carry -> R76 fixed short bond",
        }

    def resolve_requirements(self, config: BaseModel) -> StrategyDataRequirements:
        _validate_config(config)
        return super().resolve_requirements(config)

    def create_session(self, initialization: StrategyInitializationContext, config: BaseModel):
        del initialization
        return EconomicRoleR57Session(config, self.descriptor.id)
