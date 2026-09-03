"""R87: R86 with deterministic Top3-entry/Top4-exit role hysteresis."""

from __future__ import annotations

from dataclasses import replace

from pydantic import BaseModel

from backtest.fund_rotation.contracts import (
    FundRotationStrategyDescriptor,
    StrategyDecisionContext,
    StrategyInitializationContext,
)
from backtest.fund_rotation.strategies.ai_rotation_r86_r81_transition_cap_50.strategy import (
    AiRotationR86R81TransitionCap50Strategy,
    EconomicRoleR81TransitionCap50Session,
)


DESCRIPTOR = FundRotationStrategyDescriptor(
    id="ai_rotation_r87_r81_role_rank_buffer",
    name="R86 Role Top3 入场 Top4 退出排名缓冲",
    description="R86 上游加经济角色评分 Top3 入场/Top4 退出滞后。",
    interface_version="1.0",
    supported_universe=("etf",),
    deterministic=True,
)


def select_rank_buffer_roles(
    ranked_roles: list[str],
    previous_selected: set[str],
    valid_roles: set[str],
    top_n: int = 3,
    exit_rank: int = 4,
    *,
    epoch_reset: bool = False,
) -> tuple[list[str], dict[str, object]]:
    current_rank = {role: index + 1 for index, role in enumerate(ranked_roles)}
    prior = set() if epoch_reset else set(previous_selected)
    retained = sorted(
        (role for role in prior if role in valid_roles and current_rank.get(role, exit_rank + 1) <= exit_rank),
        key=lambda role: (current_rank[role], role),
    )
    fillers = [role for role in ranked_roles if role not in retained and role in valid_roles]
    selected = (retained + fillers)[:top_n]
    return selected, {
        "entry_rank": top_n,
        "exit_rank": exit_rank,
        "previous_selected_roles": sorted(previous_selected),
        "retained_roles": retained,
        "forced_exit_roles": sorted(set(previous_selected) - set(retained)),
        "current_rank_by_role": current_rank,
        "epoch_reset": epoch_reset,
    }


class EconomicRoleR81RoleRankBufferSession(EconomicRoleR81TransitionCap50Session):
    """R86 session with role-only state applied before R86 post-processing."""

    STRATEGY_ID = DESCRIPTOR.id

    def __init__(self, config) -> None:
        super().__init__(config)
        self._previous_selected_roles: set[str] = set()
        self._last_rank_buffer_diagnostics: dict[str, object] = {}

    def _rank_roles(self, scores):
        ranked = super()._rank_roles(scores)
        valid_roles = {role for role, score in scores.items() if score.eligible}
        epoch_reset = (
            not self._role_members
            or self._week_index - self._last_role_refresh_week >= self._config.refresh_interval_weeks
        )
        selected, diagnostics = select_rank_buffer_roles(
            list(ranked), self._previous_selected_roles, valid_roles,
            top_n=self._config.top_n, exit_rank=self._config.top_n + 1,
            epoch_reset=epoch_reset,
        )
        self._previous_selected_roles = set(selected)
        self._last_rank_buffer_diagnostics = diagnostics
        return selected + [role for role in ranked if role not in selected]

    def evaluate(self, context: StrategyDecisionContext):
        decision = super().evaluate(context)
        diagnostics = dict(decision.diagnostics)
        diagnostics["role_rank_buffer"] = dict(self._last_rank_buffer_diagnostics)
        self._decision_log[-1]["diagnostics"] = diagnostics
        return replace(decision, diagnostics=diagnostics)


class AiRotationR87R81RoleRankBufferStrategy(AiRotationR86R81TransitionCap50Strategy):
    descriptor = DESCRIPTOR

    def describe_decision_pipeline(self, config: BaseModel) -> dict[str, object]:
        pipeline = super().describe_decision_pipeline(config)
        pipeline["selection_rule"] = "role ranking with entry Top3 and exit Top4 rank buffer"
        return pipeline

    def create_session(self, initialization: StrategyInitializationContext, config: BaseModel):
        del initialization
        return EconomicRoleR81RoleRankBufferSession(config)
