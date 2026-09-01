"""Minimal v4.3 Economic Role strategies.

The module owns only Role grouping, representative lifecycle and Role evidence.
R11 scoring mathematics and the R34/R39/R76 downstream functions remain the
single implementations used by the existing strategies.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import replace

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
from backtest.fund_rotation.evaluation import iso_week_endings
from backtest.fund_rotation.risk_layers import apply_defense_asset
from backtest.fund_rotation.scoring.contracts import StrategyScore, rank_scores
from backtest.fund_rotation.strategies.ai_rotation_r34_staged_reentry.strategy import (
    _append_reason,
    apply_staged_reentry,
)
from backtest.fund_rotation.strategies.ai_rotation_r39_incumbent_carry.strategy import (
    apply_incumbent_carry,
)
from backtest.fund_rotation.strategies.economic_role_rotation.config import (
    EconomicRoleConfig,
)
from backtest.fund_rotation.strategies.economic_role_rotation.roles import (
    BOND,
    CN_DEFENSIVE_EQUITY,
    ROLE_IDS,
    RoleClassification,
    classify_fund_name,
    select_dynamic_representative,
    select_fixed_representative,
    role_rule_hash,
)
from backtest.fund_rotation.strategies.correlation_all_members.signals import (
    signal_date_eligible,
)


ROLE_NAMES = {
    "CN_DEFENSIVE_EQUITY": "中国防守权益",
    "CN_GROWTH_EQUITY": "中国成长权益",
    "OVERSEAS_GROWTH_EQUITY": "海外成长权益",
    "GOLD": "黄金ETF",
    "BOND": "债券",
}
ROLE_CLASSIFIER_VERSION = "1"
ROLE_SCORE_MODEL_ID = "persistent_geometric_role_momentum"
ROLE_SCORE_MODEL_VERSION = "1"
_ZERO_TURN_STREAK_DAYS = 5


def _finite(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def persistent_geometric_role_score(
    current_momentum: object,
    lagged_momentum: object,
    role_id: str = CN_DEFENSIVE_EQUITY,
) -> StrategyScore:
    """Reuse R11's M0/M1 formula with Role-specific audit metadata."""
    current = _finite(current_momentum)
    lagged = _finite(lagged_momentum)
    eligible = (
        current is not None
        and lagged is not None
        and current > 0.0
        and lagged > 0.0
    )
    value = math.sqrt((1.0 + current) * (1.0 + lagged)) - 1.0 if eligible else None
    return StrategyScore(
        value=value,
        eligible=eligible,
        subject_id=f"role:{role_id}",
        display_label="持续几何 Role 动量",
        model_label="Persistent Geometric Role Momentum",
        frequency="WEEKLY",
        scope="ECONOMIC_ROLE",
        model_id=ROLE_SCORE_MODEL_ID,
        model_version=ROLE_SCORE_MODEL_VERSION,
        components={
            "current_momentum": current,
            "lagged_momentum": lagged,
            "persistent_geometric_momentum": value,
        },
    )


def _role_returns(
    weekly_returns: pd.DataFrame,
    members: Sequence[str],
) -> tuple[list[float | None], list[int]]:
    """Average finite member returns cross-sectionally for each week."""
    values: list[float | None] = []
    counts: list[int] = []
    for _, row in weekly_returns.iterrows():
        finite = [
            number
            for code in members
            if code in row.index
            for number in [_finite(row[code])]
            if number is not None
        ]
        values.append(sum(finite) / len(finite) if finite else None)
        counts.append(len(finite))
    return values, counts


def compute_role_score(
    weekly_returns: pd.DataFrame,
    members: Sequence[str],
    role_id: str,
    momentum_window: int = 4,
) -> tuple[StrategyScore, dict[str, object]]:
    """Compute 5-week Role Return input, M0/M1 and persistent score."""
    tail = weekly_returns.iloc[-(momentum_window + 1):]
    role_returns, counts = _role_returns(tail, members)
    if len(role_returns) < momentum_window + 1:
        current = lagged = None
    else:
        lagged_window = role_returns[:momentum_window]
        current_window = role_returns[1:momentum_window + 1]
        lagged = (
            math.prod(1.0 + value for value in lagged_window) - 1.0
            if all(value is not None for value in lagged_window)
            else None
        )
        current = (
            math.prod(1.0 + value for value in current_window) - 1.0
            if all(value is not None for value in current_window)
            else None
        )
    score = persistent_geometric_role_score(current, lagged, role_id)
    diagnostics = {
        "role_return": role_returns,
        "valid_member_count": counts,
        "coverage_ratio": [count / len(members) if members else 0.0 for count in counts],
        "M0": current,
        "M1": lagged,
        "score": score.value,
        "score_eligible": score.eligible,
    }
    return score, diagnostics


def _serialize_score(score: StrategyScore) -> dict[str, object]:
    return {
        "id": "primary_score",
        "label": score.label,
        "display_label": score.display_label,
        "model_label": score.model_label,
        "value": score.value,
        "eligible": score.eligible,
        "direction": score.direction.value,
        "frequency": score.frequency,
        "scope": score.scope,
        "subject_id": score.subject_id,
        "model_id": score.model_id,
        "model_version": score.model_version,
        "components": dict(score.components),
    }


def _liquidity(
    view,
    signal_date: str,
    window_days: int,
    min_observations: int,
) -> tuple[dict[str, float], set[str], dict[str, dict[str, object]]]:
    bars = view.daily_bars(["amount"], lookback=window_days)
    adv: dict[str, float] = {}
    hard_failed: set[str] = set()
    diagnostics: dict[str, dict[str, object]] = {}
    for code, group in bars.sort_values("trade_date").groupby("ts_code"):
        amounts = pd.to_numeric(group.tail(window_days)["amount"], errors="coerce")
        positive = amounts[amounts > 0]
        streak = amounts.tail(_ZERO_TURN_STREAK_DAYS)
        hard_fail = len(streak) >= _ZERO_TURN_STREAK_DAYS and (streak.fillna(0.0) <= 0).all()
        if hard_fail:
            hard_failed.add(str(code))
        if len(positive) >= min_observations and not hard_fail:
            adv[str(code)] = float(positive.mean())
        diagnostics[str(code)] = {
            "signal_date": signal_date,
            "window_days": window_days,
            "valid_days": int(len(positive)),
            "adv": adv.get(str(code)),
            "hard_failure": bool(hard_fail),
        }
    return adv, hard_failed, diagnostics


class EconomicRoleSession:
    """Stateful Role lifecycle shared by the three Phase-A groups."""

    score_subject = "REPRESENTATIVE"
    representative_mode = "FIXED"

    def __init__(self, config: EconomicRoleConfig, descriptor_id: str) -> None:
        self._config = config
        self._descriptor_id = descriptor_id
        self._week_index = 0
        self._last_role_refresh_week = -config.refresh_interval_weeks
        self._role_members: dict[str, list[str]] = {}
        self._representatives: dict[str, str | None] = {}
        self._role_history: list[dict[str, object]] = []
        self._role_representatives: list[dict[str, object]] = []
        self._role_diagnostics: list[dict[str, object]] = []
        self._exclusions: list[dict[str, object]] = []
        self._decision_log: list[dict[str, object]] = []
        self._decision_trace: list[dict[str, object]] = []
        self._previous_weights: dict[str, float] = {}

    def scheduled_dates(self, calendar, decision_start_date, evaluation_end_date):
        return tuple(
            date for date in iso_week_endings(calendar)
            if decision_start_date <= date <= evaluation_end_date
        )

    def _select_rep(
        self,
        role_id: str,
        classifications: Mapping[str, RoleClassification],
        quality_eligible: set[str],
        signal_eligible: set[str],
        adv: Mapping[str, float],
        hard_failed: set[str],
        *,
        regular_refresh: bool,
    ) -> tuple[str | None, str]:
        valid = {
            code for code, classification in classifications.items()
            if classification.role_id == role_id
            and code in quality_eligible
            and code in signal_eligible
            and code in adv
            and code not in hard_failed
        }
        current = self._representatives.get(role_id)
        if not regular_refresh and current in valid:
            return current, "LOCK_MAINTENANCE"
        if regular_refresh:
            mode = "REGULAR_REFRESH"
        else:
            mode = "HARD_FAILURE_FALLBACK"
        if self.representative_mode == "DYNAMIC":
            candidates = {
                code: (int(classifications[code].tier or 99), float(adv[code]))
                for code in valid
            }
            selected = select_dynamic_representative(candidates, eligible=valid)
        else:
            candidates = {
                code for code, classification in classifications.items()
                if classification.role_id == role_id
            }
            selected = select_fixed_representative(
                self._config.fixed_role_manifest[role_id],
                candidates=candidates,
                eligible=valid,
                adv=adv,
            )
        return selected, mode if selected is not None else f"{mode}_NO_AVAILABLE"

    def evaluate(self, context: StrategyDecisionContext) -> TargetWeightDecision:
        cfg = self._config
        signal_date = context.signal_date
        view = context.data_view
        week_index = self._week_index
        self._week_index += 1

        instruments = view.eligible_universe()
        classifications = {
            instrument.ts_code: classify_fund_name(instrument.name)
            for instrument in instruments
        }
        for code, classification in classifications.items():
            if classification.status != "MATCHED":
                self._exclusions.append({
                    "signal_date": signal_date,
                    "ts_code": code,
                    "status": classification.status,
                    "reason": classification.exclusion_reason,
                })
        current_codes = set(classifications)
        signal_codes, signal_exclusions = signal_date_eligible(
            view, sorted(current_codes), signal_date,
        )
        signal_eligible = set(signal_codes)
        for record in signal_exclusions:
            self._exclusions.append({
                "signal_date": signal_date,
                "ts_code": record.ts_code,
                "status": "SIGNAL_DATE",
                "reason": record.reason.value,
            })
        weekly = view.returns("weekly", cfg.history_quality_lookback_weeks)
        valid_counts = {
            code: int(pd.to_numeric(weekly[code], errors="coerce").notna().sum())
            if code in weekly.columns else 0
            for code in current_codes
        }
        adv, hard_failed, liquidity_diagnostics = _liquidity(
            view, signal_date, cfg.representative_liquidity_window_days,
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
                    code for code, classification in classifications.items()
                    if classification.role_id == role_id
                    and valid_counts.get(code, 0) >= cfg.min_valid_weeks
                    and code in signal_eligible
                )

        selection_modes: dict[str, str] = {}
        for role_id in ROLE_IDS:
            previous_representative = self._representatives.get(role_id)
            selected, mode = self._select_rep(
                role_id, classifications, {
                    code for code, count in valid_counts.items()
                    if count >= cfg.min_valid_weeks
                },
                signal_eligible, adv, hard_failed, regular_refresh=regular_refresh,
            )
            self._representatives[role_id] = selected
            selection_modes[role_id] = mode
            self._role_representatives.append({
                "signal_date": signal_date,
                "role_id": role_id,
                "representative": selected,
                "selection_mode": mode,
                "previous_representative": previous_representative,
                "liquidity": liquidity_diagnostics.get(selected or "", {}),
            })

        scores: dict[str, StrategyScore] = {}
        score_diagnostics: dict[str, dict[str, object]] = {}
        for role_id in ROLE_IDS:
            if self.score_subject == "MEMBERS":
                subjects = list(self._role_members.get(role_id, []))
            else:
                representative = self._representatives.get(role_id)
                subjects = [representative] if representative else []
            score, diagnostics = compute_role_score(
                weekly, subjects, role_id, cfg.momentum_window_weeks,
            )
            scores[role_id] = score
            score_diagnostics[role_id] = diagnostics

        ranked = rank_scores(scores, cluster_members={
            role_id: self._role_members.get(role_id, []) for role_id in ROLE_IDS
        })
        selected_roles = ranked[:cfg.top_n]
        base_weights: dict[str, float] = {}
        vacant_roles: list[str] = []
        slot_weight = 1.0 / cfg.top_n
        for role_id in selected_roles:
            representative = self._representatives.get(role_id)
            if representative:
                base_weights[representative] = base_weights.get(representative, 0.0) + slot_weight
            else:
                vacant_roles.append(role_id)
        base_cash = max(0.0, 1.0 - sum(base_weights.values()))

        staged_weights, staged_cash, staged = apply_staged_reentry(
            self._previous_weights, base_weights,
        )
        carried_weights, carried_cash, carried_codes, incumbents = apply_incumbent_carry(
            self._previous_weights, staged_weights,
        )
        final_weights, final_cash, defense_diagnostics = apply_defense_asset(
            carried_weights, carried_cash, defense_code="511010.SH",
        )
        reason = _append_reason("", "STAGED_REENTRY" if staged else "")
        reason = _append_reason(reason, "INCUMBENT_CARRY" if incumbents else "")
        reason = _append_reason(reason, "FIXED_SHORT_BOND_DEFENSE")
        quality = QualityStatus.VALID if all(scores[role].eligible for role in selected_roles) else QualityStatus.DEGRADED
        role_rows: list[dict[str, object]] = []
        rank_by_role = {role_id: index for index, role_id in enumerate(ranked, start=1)}
        for role_id in ROLE_IDS:
            score = scores[role_id]
            representative = self._representatives.get(role_id)
            role_rows.append({
                "ts_code": representative or f"role:{role_id}",
                "role_id": role_id,
                "role_name": ROLE_NAMES[role_id],
                "members": list(self._role_members.get(role_id, [])),
                "stages": {
                    "role_matched": True,
                    "representative_available": representative is not None,
                    "ranking_eligible": score.eligible,
                    "rank": rank_by_role.get(role_id),
                    "portfolio_selected": role_id in selected_roles and representative is not None,
                },
                "primary_metric": None,
                "score": _serialize_score(score),
                "previous_weight": float(self._previous_weights.get(representative or "", 0.0)),
                "target_weight": float(final_weights.get(representative or "", 0.0)),
                "exclusion_reason": (
                    "SCORE_AVAILABLE_BUT_EXECUTION_REP_MISSING"
                    if score.eligible and representative is None else None
                ),
            })
            self._role_history.append({
                "signal_date": signal_date,
                "role_id": role_id,
                "role_name": ROLE_NAMES[role_id],
                "members": list(self._role_members.get(role_id, [])),
                "members_as_of": signal_date,
                "representative": representative,
                "representative_as_of": signal_date if representative else None,
                "selection_mode": selection_modes[role_id],
            })
            self._role_diagnostics.append({
                "signal_date": signal_date,
                "role_id": role_id,
                "role_member_count": len(self._role_members.get(role_id, [])),
                "score_subject": "FROZEN_ROLE_MEMBERS" if self.score_subject == "MEMBERS" else "CURRENT_REPRESENTATIVE",
                "score": score_diagnostics[role_id],
                "persistent_score": _serialize_score(score),
                "score_rank": rank_by_role.get(role_id),
                "representative_available": representative is not None,
                "selection_mode": selection_modes[role_id],
                "liquidity": liquidity_diagnostics.get(representative or "", {}),
                "score_available_but_execution_rep_missing": score.eligible and representative is None,
            })

        diagnostics = {
            "role_rule_hash": role_rule_hash(),
            "role_classifier_version": ROLE_CLASSIFIER_VERSION,
            "effective_role_assignment_hash": _hash_json({
                "signal_date": signal_date,
                "assignments": [
                    {"ts_code": code, **classification.__dict__}
                    for code, classification in sorted(classifications.items())
                ],
            }),
            "effective_universe_codes": sorted(current_codes),
            "effective_universe_hash": _hash_json(sorted(current_codes)),
            "score_model": {
                "id": ROLE_SCORE_MODEL_ID,
                "version": ROLE_SCORE_MODEL_VERSION,
                "scope": "ECONOMIC_ROLE",
            },
            "selected_roles": selected_roles,
            "vacant_roles": vacant_roles,
            "filled_slots": len(selected_roles) - len(vacant_roles),
            "staged_reentry_codes": sorted(staged),
            "incumbent_carry_codes": sorted(incumbents),
            "carried_codes": sorted(carried_codes),
            "risk_layer": "fixed_short_bond",
            "defense_asset": "511010.SH",
            "defense_diagnostics": defense_diagnostics,
            "selection_modes": selection_modes,
            "history_quality_lookback_weeks": cfg.history_quality_lookback_weeks,
            "min_valid_weeks": cfg.min_valid_weeks,
            "refresh_interval_weeks": cfg.refresh_interval_weeks,
            "signal_information_cutoff": "CLOSE",
        }
        decision = TargetWeightDecision(
            decision_id=f"{signal_date}-{self._descriptor_id}",
            signal_date=signal_date,
            action=DecisionKind.SET_TARGETS,
            target_weights=dict(final_weights),
            cash_weight=final_cash,
            reason_code=reason,
            quality_status=quality,
            diagnostics=diagnostics,
        )
        self._decision_log.append({
            "signal_date": signal_date,
            "action": decision.action.value,
            "target_weights": dict(final_weights),
            "cash_weight": final_cash,
            "reason_code": reason,
            "quality_status": quality.value,
            "diagnostics": diagnostics,
        })
        self._decision_trace.append({
            "signal_date": signal_date,
            "role_snapshot": {
                "regular_refresh": regular_refresh,
                "last_role_refresh_week": self._last_role_refresh_week,
            },
            "candidates": role_rows,
        })
        self._previous_weights = dict(final_weights)
        return decision

    def finalize(self) -> StrategyDiagnostics:
        return StrategyDiagnostics(
            artifacts=(
                StrategyArtifact("role_history", "application/json", self._role_history),
                StrategyArtifact("role_representatives", "application/json", self._role_representatives),
                StrategyArtifact("role_diagnostics", "application/json", self._role_diagnostics),
                StrategyArtifact("exclusions", "application/json", self._exclusions),
                StrategyArtifact("decisions", "application/json", self._decision_log),
            ),
            decision_trace=tuple(self._decision_trace),
        )


def _hash_json(value: object) -> str:
    import hashlib
    import json

    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


class _EconomicRoleStrategy:
    config_model = EconomicRoleConfig
    score_subject = "REPRESENTATIVE"
    representative_mode = "FIXED"
    descriptor: FundRotationStrategyDescriptor

    artifact_roles = (
        "role_history", "role_representatives", "role_diagnostics", "exclusions", "decisions",
    )

    def describe_decision_pipeline(self, config: BaseModel) -> dict[str, object]:
        cfg: EconomicRoleConfig = config  # type: ignore[assignment]
        return {
            "universe": "ECONOMIC_ROLE",
            "roles": list(ROLE_IDS),
            "representative_method": self.representative_mode,
            "score_subject": self.score_subject,
            "score_model": {
                "id": ROLE_SCORE_MODEL_ID,
                "version": ROLE_SCORE_MODEL_VERSION,
                "scope": "ECONOMIC_ROLE",
            },
            "selection_rule": f"Top {cfg.top_n} Role slots",
            "top_n": cfg.top_n,
            "weighting_rule": "Equal Weight Role Slots",
            "downstream": "R34 staged reentry -> R39 incumbent carry -> R76 fixed short bond",
        }

    def resolve_requirements(self, config: BaseModel) -> StrategyDataRequirements:
        cfg: EconomicRoleConfig = config  # type: ignore[assignment]
        return StrategyDataRequirements(
            required_datasets=("fund", "fact_fund_adj", "dim_fund"),
            required_fields=(
                "ts_code", "trade_date", "name", "list_date", "open", "close",
                "high", "low", "pre_close", "vol", "amount", "adj_factor",
            ),
            warmup_trade_days=cfg.warmup_trade_days,
            frequency="weekly",
            needs_benchmark=False,
        )

    def resolve_candidate_universe_codes(self, snapshot) -> tuple[str, ...]:
        """Expose the Role pool through the Runner's generic universe hook."""
        return tuple(
            getattr(snapshot, "role_universe_codes", ())
            or snapshot.universe_codes
        )

    def create_session(self, initialization: StrategyInitializationContext, config: BaseModel):
        del initialization
        session = EconomicRoleSession(config, self.descriptor.id)  # type: ignore[arg-type]
        session.score_subject = self.score_subject
        session.representative_mode = self.representative_mode
        return session


class AiRotationR79EconomicRoleMembersStrategy(_EconomicRoleStrategy):
    descriptor = FundRotationStrategyDescriptor(
        id="ai_rotation_r79_economic_role_members",
        name="Role 成员评分固定代表诊断",
        description="Economic Role frozen members momentum with fixed representative execution; diagnostic only.",
        interface_version="1.0", supported_universe=("etf",), deterministic=True,
    )
    score_subject = "MEMBERS"
    representative_mode = "FIXED"


class AiRotationR80EconomicRoleFixedRepresentativeStrategy(_EconomicRoleStrategy):
    descriptor = FundRotationStrategyDescriptor(
        id="ai_rotation_r80_economic_role_fixed_rep",
        name="Role 固定代表动量",
        description="Economic Role fixed-manifest representative momentum with R76 downstream.",
        interface_version="1.0", supported_universe=("etf",), deterministic=True,
    )
    score_subject = "REPRESENTATIVE"
    representative_mode = "FIXED"


class AiRotationR81EconomicRoleDynamicRepresentativeStrategy(_EconomicRoleStrategy):
    descriptor = FundRotationStrategyDescriptor(
        id="ai_rotation_r81_economic_role_dynamic_rep",
        name="Role 动态代表动量",
        description="Economic Role tier/ADV deterministic representative momentum with R76 downstream.",
        interface_version="1.0", supported_universe=("etf",), deterministic=True,
    )
    score_subject = "REPRESENTATIVE"
    representative_mode = "DYNAMIC"
