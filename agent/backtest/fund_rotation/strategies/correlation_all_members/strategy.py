"""correlation_all_members strategy — migration baseline (design §32.2).

This is the complete-strategy wrapper around the legacy fixed pipeline. The
session reproduces the legacy signal generation (data preparation →
correlation distance → iterative exclusion → hierarchical clustering →
cluster momentum Top-N → all-member equal-weight targets) from the causal
data view alone, decision date by decision date, so the common Runner can
drive it to byte-for-byte parity with the legacy pipeline (Phase 2 Task 4).
Execution is never part of the strategy.
"""

from __future__ import annotations

import pandas as pd
from pydantic import BaseModel

from backtest.fund_rotation.clustering import hierarchical_cluster
from backtest.fund_rotation.contracts import (
    DecisionKind,
    FundRotationStrategyDescriptor,
    QualityStatus,
    StrategyArtifact,
    StrategyDataRequirements,
    StrategyDiagnostics,
    StrategyInitializationContext,
    StrategyDecisionContext,
    TargetWeightDecision,
)
from backtest.fund_rotation.correlation import (
    compute_correlation_distance,
    iterative_exclude,
)
from backtest.fund_rotation.momentum import (
    build_target_weights,
    compute_cluster_momentum,
    select_top_clusters,
)
from backtest.fund_rotation.strategies.correlation_all_members.config import (
    CorrelationAllMembersConfig,
)
from backtest.fund_rotation.strategies.correlation_all_members.signals import (
    ensure_instrument_pool,
    iso_week_endings,
    signal_date_eligible,
)
from backtest.fund_rotation.universe import (
    ExclusionReason,
    ExclusionRecord,
    check_historical_eligibility,
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
    """Per-run session for the baseline strategy.

    Reproduces the legacy weekly loop (pipeline.py) decision date by decision
    date using only the causal data view:

    * weekly returns window = exactly ``correlation_lookback_weeks`` rows
      ending at the signal week (momentum window is a subset of it);
    * eligibility = historical (list_date) ∩ market (positive close and
      adj_factor on the signal date) ∩ returns columns ∩ min_valid_weeks;
    * reclustering at the legacy cadence, momentum Top-N, all-member
      equal-weight targets.

    All run state (clusters, recluster counter, diagnostics) lives here and
    never in the strategy singleton (§5).
    """

    def __init__(self, config: CorrelationAllMembersConfig) -> None:
        self._config = config
        self._week_index = 0
        self._clusters: dict[str, int] = {}
        self._last_recluster_week = -config.recluster_interval_weeks
        self._cluster_history: list[dict] = []
        self._exclusions: list = []
        self._dim_pool: pd.DataFrame | None = None

    def scheduled_dates(
        self,
        calendar: tuple[str, ...],
        simulation_start_date: str,
        evaluation_end_date: str,
    ) -> tuple[str, ...]:
        """Week-ending dates (ISO weeks) within [warmup boundary, eval end].

        The grouping matches ``compute_weekly_returns`` exactly, so this
        schedule equals the legacy signal-week index.
        """
        endings = iso_week_endings(calendar)
        return tuple(
            d for d in endings
            if simulation_start_date <= d <= evaluation_end_date
        )

    # ── data preparation (parity with the legacy pipeline's pool build) ──

    def _ensure_pool(self, context: StrategyDecisionContext) -> None:
        if self._dim_pool is not None:
            return
        self._dim_pool = ensure_instrument_pool(context.data_view)

    def _eligible_at_signal(self, view, signal_date: str) -> tuple[list[str], list]:
        """Legacy eligibility order: historical → market (close & adj), with
        exclusion records for the market step."""
        eligible, _historical_excluded = check_historical_eligibility(
            self._dim_pool, signal_date,
        )
        kept, market_excluded = signal_date_eligible(view, eligible, signal_date)
        return kept, market_excluded

    # ── decision ──

    def evaluate(self, context: StrategyDecisionContext) -> TargetWeightDecision:
        cfg = self._config
        signal_date = context.signal_date
        view = context.data_view
        self._ensure_pool(context)

        week_idx = self._week_index
        self._week_index += 1

        # Correlation window: exactly correlation_lookback_weeks weekly-return
        # rows ending at the signal week (never includes future data, §6).
        window = view.returns("weekly", cfg.correlation_lookback_weeks)

        weeks_since_recluster = week_idx - self._last_recluster_week
        reclustering = (
            weeks_since_recluster >= cfg.recluster_interval_weeks
            or not self._clusters
        )
        if reclustering:
            # Legacy recluster block records historical + market exclusions
            # before the valid-weeks gate and pairwise exclusion.
            eligible_recluster, historical_excluded = check_historical_eligibility(
                self._dim_pool, signal_date,
            )
            self._exclusions.extend(historical_excluded)
            kept_recluster, market_excluded = signal_date_eligible(
                view, eligible_recluster, signal_date,
            )
            self._exclusions.extend(market_excluded)

            valid_codes = [c for c in kept_recluster if c in window.columns]
            if cfg.min_valid_weeks > 0 and valid_codes:
                counts = window[valid_codes].notna().sum()
                qualified_codes = [
                    c for c in valid_codes if counts.get(c, 0) >= cfg.min_valid_weeks
                ]
                for code in sorted(set(valid_codes) - set(qualified_codes)):
                    self._exclusions.append(ExclusionRecord(
                        ts_code=code,
                        reason=ExclusionReason.INSUFFICIENT_VALID_WEEKS,
                        details=(
                            f"valid_weeks={int(counts.get(code, 0))}; "
                            f"required={cfg.min_valid_weeks}"
                        ),
                        signal_date=signal_date,
                    ))
                valid_codes = qualified_codes
            if len(valid_codes) < cfg.k:
                raise ValueError(
                    f"Recluster at {signal_date}: only {len(valid_codes)} eligible "
                    f"ETFs, need at least K={cfg.k}. Task failed per §18.1."
                )
            dist = compute_correlation_distance(
                window[valid_codes], min_pairwise_weeks=cfg.min_pairwise_weeks,
            )
            kept_codes, pair_excluded = iterative_exclude(dist, k=cfg.k)
            self._exclusions.extend(pair_excluded)
            sub_dist = dist.loc[kept_codes, kept_codes]
            self._clusters = hierarchical_cluster(sub_dist, k=cfg.k)
            self._last_recluster_week = week_idx
            self._cluster_history.append({
                "week": signal_date,
                "clusters": dict(self._clusters),
                "num_etfs": len(self._clusters),
            })

        # Target eligibility at the signal date (every week; the market
        # exclusions are recorded exactly as the legacy Step 6 did).
        eligible, market_excluded = self._eligible_at_signal(view, signal_date)
        self._exclusions.extend(market_excluded)

        if not self._clusters:
            # Legacy "continue" branch (no clusters yet): no target event.
            return TargetWeightDecision(
                decision_id=f"{signal_date}-correlation_all_members",
                signal_date=signal_date,
                action=DecisionKind.HOLD_TARGETS,
            )

        # Momentum window ⊂ correlation window (not additive).
        momentum_returns = window.iloc[-(cfg.momentum_window_weeks + 1):]
        momentum = compute_cluster_momentum(
            momentum_returns, self._clusters, cfg.momentum_window_weeks,
        )
        cluster_members: dict[int, list[str]] = {}
        for code, cid in self._clusters.items():
            cluster_members.setdefault(cid, []).append(code)
        selected = select_top_clusters(
            momentum, top_n=cfg.top_n, threshold=cfg.momentum_threshold,
            cluster_members=cluster_members,
        )

        eligible_set = set(eligible)
        filtered_members = {
            cid: [c for c in members if c in eligible_set]
            for cid, members in cluster_members.items()
        }
        targets = build_target_weights(selected, filtered_members, top_n=cfg.top_n)

        return TargetWeightDecision(
            decision_id=f"{signal_date}-correlation_all_members",
            signal_date=signal_date,
            action=DecisionKind.SET_TARGETS,
            target_weights=dict(targets),
            # max(0.0, ...) guards float overshoot of the slot-weight sum for
            # some top_n values (Runner contract requires cash_weight >= 0).
            cash_weight=max(0.0, 1.0 - sum(targets.values())),
            quality_status=QualityStatus.VALID,
            diagnostics={
                "num_clusters": len(self._clusters),
                # Eligible codes at the signal date feed the legacy dynamic
                # equal-weight benchmark (§10) through the compat adapter.
                "eligible_codes": list(eligible),
            },
        )

    def finalize(self) -> StrategyDiagnostics:
        # Cluster diagnostics and the exclusion trail are strategy-specific
        # artifacts (§12), kept in the session's private state until
        # publication (Phase 2 Task 5).
        return StrategyDiagnostics(artifacts=(
            StrategyArtifact(
                role="cluster_history",
                media_type="application/json",
                payload=self._cluster_history,
            ),
            StrategyArtifact(
                role="exclusions",
                media_type="application/json",
                payload=self._exclusions,
            ),
        ))


class CorrelationAllMembersStrategy:
    """Complete fund-rotation strategy plug-in (baseline)."""

    descriptor = DESCRIPTOR
    config_model = CorrelationAllMembersConfig
    # Strategy-specific diagnostic artifact roles published via finalize (§12).
    artifact_roles: tuple[str, ...] = ("cluster_history", "exclusions")

    def resolve_requirements(self, config: BaseModel) -> StrategyDataRequirements:
        """Config-derived data needs (pure function of validated config).

        Warmup = one full window of ``max(min_training_weeks,
        correlation_lookback_weeks)`` weekly returns. N weekly returns need
        N+1 week-endings ≈ (N+1)*5 trading days; the first valid decision day
        is the last of them, hence ``(N + 1) * 5 - 1``. The momentum window is
        a subset of the lookback, not additive.
        """
        cfg: CorrelationAllMembersConfig = config  # type: ignore[assignment]
        min_weeks = max(cfg.min_training_weeks, cfg.correlation_lookback_weeks)
        warmup_trade_days = (min_weeks + 1) * 5 - 1
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
