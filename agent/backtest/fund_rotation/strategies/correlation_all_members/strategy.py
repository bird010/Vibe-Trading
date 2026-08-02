"""correlation_all_members strategy — migration baseline shell (design §32.2).

This is the complete-strategy wrapper around the legacy fixed pipeline. In
Phase 1 it provides the descriptor, config model, data-requirement resolution
and a session shell; the full signal generation (clustering → momentum →
all-member equal-weight targets) is wired to the existing pure functions in
Phase 2 (Task "让 baseline 策略完整生成现有目标"). Execution is never switched
in Phase 1.
"""

from __future__ import annotations

from pydantic import BaseModel

from backtest.fund_rotation.contracts import (
    DecisionKind,
    FundRotationStrategyDescriptor,
    StrategyDataRequirements,
    StrategyDiagnostics,
    StrategyInitializationContext,
    StrategyDecisionContext,
    TargetWeightDecision,
)
from backtest.fund_rotation.strategies.correlation_all_members.config import (
    CorrelationAllMembersConfig,
)

DESCRIPTOR = FundRotationStrategyDescriptor(
    id="correlation_all_members",
    name="相关性聚类全成员等权",
    description=(
        "基线策略：PIT 周收益相关距离聚类，簇动量 Top-N，入选簇内全部 ETF 等权。"
        "作为迁移基准，封装首版固定流程行为。"
    ),
    interface_version="1.0",
    supported_universe=("etf",),
    deterministic=True,
)


class CorrelationAllMembersSession:
    """Per-run session shell for the baseline strategy.

    Holds per-run state. Full signal generation is delegated to the existing
    pure functions in Phase 2; here ``evaluate`` emits a safe HOLD_TARGETS so
    the contract is exercisable end to end.
    """

    def __init__(self, config: CorrelationAllMembersConfig) -> None:
        self._config = config

    def scheduled_dates(
        self,
        calendar: tuple[str, ...],
        simulation_start_date: str,
        evaluation_end_date: str,
    ) -> tuple[str, ...]:
        """Weekly rebalance dates within [simulation_start, evaluation_end].

        Shell: selects calendar days at the rebalance cadence. The exact
        week-ending selection is refined in Phase 2.
        """
        in_range = [
            d for d in calendar
            if simulation_start_date <= d <= evaluation_end_date
        ]
        # Weekly cadence: every 5th trading day as a placeholder schedule.
        return tuple(in_range[::5])

    def evaluate(self, context: StrategyDecisionContext) -> TargetWeightDecision:
        """Shell decision (Phase 2 wires real clustering/momentum signals)."""
        return TargetWeightDecision(
            decision_id=f"{context.signal_date}-correlation_all_members",
            signal_date=context.signal_date,
            action=DecisionKind.HOLD_TARGETS,
        )

    def finalize(self) -> StrategyDiagnostics:
        return StrategyDiagnostics()


class CorrelationAllMembersStrategy:
    """Complete fund-rotation strategy plug-in (baseline)."""

    descriptor = DESCRIPTOR
    config_model = CorrelationAllMembersConfig

    def resolve_requirements(self, config: BaseModel) -> StrategyDataRequirements:
        """Config-derived data needs (pure function of validated config)."""
        cfg: CorrelationAllMembersConfig = config  # type: ignore[assignment]
        # TODO(Phase 2): confirm whether recluster_interval_weeks must also bound
        # the warmup once real signals are wired (first recluster needs a full
        # lookback window of valid weekly returns).
        warmup_trade_days = (
            cfg.correlation_lookback_weeks + cfg.momentum_window_weeks + 1
        ) * 5
        return StrategyDataRequirements(
            required_datasets=("fund", "fact_fund_adj", "dim_fund"),
            required_fields=("open", "close", "high", "low", "pre_close", "vol", "amount", "adj_factor"),
            warmup_trade_days=warmup_trade_days,
            frequency=cfg.rebalance_freq,
            needs_benchmark=True,
        )

    def create_session(
        self,
        initialization: StrategyInitializationContext,
        config: BaseModel,
    ) -> CorrelationAllMembersSession:
        return CorrelationAllMembersSession(config)  # type: ignore[arg-type]
