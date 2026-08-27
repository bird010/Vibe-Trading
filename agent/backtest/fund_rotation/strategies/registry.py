"""Explicit fund-rotation strategy whitelist — §16.1 (Phase 3 Task 6).

The composition root for strategy registration: an explicit, startup-fixed
whitelist of complete strategies (no directory scanning, no dynamic import
from request strings). Kept separate from the catalog machinery so the
catalog itself stays strategy-agnostic.
"""

from __future__ import annotations

from backtest.fund_rotation.strategies.correlation_all_members.strategy import (
    CorrelationAllMembersStrategy,
)
from backtest.fund_rotation.strategies.correlation_representative.strategy import (
    CorrelationRepresentativeStrategy,
)
from backtest.fund_rotation.strategies.ai_rotation_r05_mom_persist.strategy import (
    AiRotationR05MomPersistStrategy,
)
from backtest.fund_rotation.strategies.ai_rotation_r06_rank_buffer.strategy import (
    AiRotationR06RankBufferStrategy,
)
from backtest.fund_rotation.strategies.ai_rotation_r07_tail_persist.strategy import (
    AiRotationR07TailPersistStrategy,
)
from backtest.fund_rotation.strategies.ai_rotation_r11_persist_geom.strategy import (
    AiRotationR11PersistGeomStrategy,
)
from backtest.fund_rotation.strategies.ai_rotation_r12_nondecay_geom.strategy import (
    AiRotationR12NondecayGeomStrategy,
)
from backtest.fund_rotation.strategies.ai_rotation_r13_arith_persist.strategy import (
    AiRotationR13ArithPersistStrategy,
)
from backtest.fund_rotation.strategies.ai_rotation_r14_median_persist.strategy import (
    AiRotationR14MedianPersistStrategy,
)
from backtest.fund_rotation.strategies.ai_rotation_r15_weighted_persist.strategy import (
    AiRotationR15WeightedPersistStrategy,
)
from backtest.fund_rotation.strategies.ai_rotation_r16_rank_consensus.strategy import (
    AiRotationR16RankConsensusStrategy,
)
from backtest.fund_rotation.strategies.ai_rotation_r17_winsor_geom.strategy import (
    AiRotationR17WinsorGeomStrategy,
)
from backtest.fund_rotation.strategies.ai_rotation_r18_min_persist.strategy import (
    AiRotationR18MinPersistStrategy,
)
from backtest.fund_rotation.strategies.ai_rotation_r19_top2_cash.strategy import (
    AiRotationR19Top2CashStrategy,
)
from backtest.fund_rotation.strategies.ai_rotation_r20_rank_frontload.strategy import (
    AiRotationR20RankFrontloadStrategy,
)
from backtest.fund_rotation.strategies.ai_rotation_r21_harmonic_persist.strategy import (
    AiRotationR21HarmonicPersistStrategy,
)
from backtest.fund_rotation.strategies.ai_rotation_r22_path_consistency.strategy import (
    AiRotationR22PathConsistencyStrategy,
)
from backtest.fund_rotation.strategies.ai_rotation_r23_downside_geom.strategy import (
    AiRotationR23DownsideGeomStrategy,
)
from backtest.fund_rotation.strategies.ai_rotation_r24_dispersion_geom.strategy import (
    AiRotationR24DispersionGeomStrategy,
)
from backtest.fund_rotation.strategies.ai_rotation_r25_rep_persist_geom.strategy import (
    AiRotationR25RepPersistGeomStrategy,
)
from backtest.fund_rotation.strategies.ai_rotation_r26_path_vol_geom.strategy import (
    AiRotationR26PathVolGeomStrategy,
)
from backtest.fund_rotation.strategies.ai_rotation_r27_breadth_persist_geom.strategy import (
    AiRotationR27BreadthPersistGeomStrategy,
)
from backtest.fund_rotation.strategies.ai_rotation_r28_size_reliability_geom.strategy import (
    AiRotationR28SizeReliabilityGeomStrategy,
)
from backtest.fund_rotation.strategies.ai_rotation_r29_invvol_slots.strategy import (
    AiRotationR29InvvolSlotsStrategy,
)
from backtest.fund_rotation.strategies.ai_rotation_r30_endpoint_breadth_geom.strategy import (
    AiRotationR30EndpointBreadthGeomStrategy,
)
from backtest.fund_rotation.strategies.ai_rotation_r31_fast_exit.strategy import (
    AiRotationR31FastExitStrategy,
)
from backtest.fund_rotation.strategies.ai_rotation_r32_market_regime.strategy import (
    AiRotationR32MarketRegimeStrategy,
)
from backtest.fund_rotation.strategies.ai_rotation_r33_quality_fallback.strategy import (
    AiRotationR33QualityFallbackStrategy,
)
from backtest.fund_rotation.strategies.ai_rotation_r34_staged_reentry.strategy import (
    AiRotationR34StagedReentryStrategy,
)
from backtest.fund_rotation.strategies.ai_rotation_r35_short_gap_reentry.strategy import (
    AiRotationR35ShortGapReentryStrategy,
)
from backtest.fund_rotation.strategies.ai_rotation_r36_tail_slot_full_entry.strategy import (
    AiRotationR36TailSlotFullEntryStrategy,
)
from backtest.fund_rotation.strategies.ai_rotation_r37_decelerating_full_entry.strategy import (
    AiRotationR37DeceleratingFullEntryStrategy,
)
from backtest.fund_rotation.strategies.ai_rotation_r38_replacement_full_entry.strategy import (
    AiRotationR38ReplacementFullEntryStrategy,
)
from backtest.fund_rotation.strategies.ai_rotation_r39_incumbent_carry.strategy import (
    AiRotationR39IncumbentCarryStrategy,
)
from backtest.fund_rotation.strategies.ai_rotation_r40_single_name_ceiling.strategy import (
    AiRotationR40SingleNameCeilingStrategy,
)
from backtest.fund_rotation.strategies.ai_rotation_r41_breadth_gated_carry.strategy import (
    AiRotationR41BreadthGatedCarryStrategy,
)
from backtest.fund_rotation.strategies.ai_rotation_r42_single_incumbent_half_carry.strategy import (
    AiRotationR42SingleIncumbentHalfCarryStrategy,
)
from backtest.fund_rotation.strategies.ai_rotation_r43_multi_new_breadth_gate.strategy import (
    AiRotationR43MultiNewBreadthGateStrategy,
)
from backtest.fund_rotation.strategies.ai_rotation_r44_persistent_incumbent_carry.strategy import (
    AiRotationR44PersistentIncumbentCarryStrategy,
)
from backtest.fund_rotation.strategies.ai_rotation_r45_cash_floor_carry.strategy import (
    AiRotationR45CashFloorCarryStrategy,
)
from backtest.fund_rotation.strategies.ai_rotation_r45_cash_floor_carry.strategy_r46_cash_floor_tight import (
    AiRotationR46CashFloorTightCarryStrategy,
)
from backtest.fund_rotation.strategies.ai_rotation_r45_cash_floor_carry.strategy_r47_breadth_tight_floor import (
    AiRotationR47BreadthTightFloorStrategy,
)
from backtest.fund_rotation.strategies.ai_rotation_r45_cash_floor_carry.strategy_r48_cash_floor_very_tight import (
    AiRotationR48CashFloorVeryTightStrategy,
)
from backtest.fund_rotation.strategies.ai_rotation_r45_cash_floor_carry.strategy_r49_cash_floor_micro import (
    AiRotationR49CashFloorMicroStrategy,
)
from backtest.fund_rotation.strategies.ai_rotation_r45_cash_floor_carry.strategy_r50_cash_floor_nano import (
    AiRotationR50CashFloorNanoStrategy,
)
from backtest.fund_rotation.strategies.ai_rotation_r45_cash_floor_carry.strategy_r51_cash_floor_pico import (
    AiRotationR51CashFloorPicoStrategy,
)
from backtest.fund_rotation.strategies.ai_rotation_r45_cash_floor_carry.strategy_r52_cash_floor_femto import (
    AiRotationR52CashFloorFemtoStrategy,
)
from backtest.fund_rotation.strategies.ai_rotation_r45_cash_floor_carry.strategy_r53_cash_floor_atto import (
    AiRotationR53CashFloorAttoStrategy,
)
from backtest.fund_rotation.strategies.ai_rotation_r45_cash_floor_carry.strategy_r54_cash_floor_zepto import (
    AiRotationR54CashFloorZeptoStrategy,
)
from backtest.fund_rotation.strategies.ai_rotation_r45_cash_floor_carry.strategy_r55_cash_floor_yotta import (
    AiRotationR55CashFloorYottaStrategy,
)
from backtest.fund_rotation.strategies.ai_rotation_r45_cash_floor_carry.strategy_r56_cash_floor_ronna import (
    AiRotationR56CashFloorRonnaStrategy,
)
from backtest.fund_rotation.strategies.ai_rotation_r57_three_factor_representative.strategy import (
    AiRotationR57ThreeFactorRepresentativeStrategy,
)
from backtest.fund_rotation.strategies.ai_rotation_r58_r39_signal_r57.strategy import (
    AiRotationR58R39SignalR57Strategy,
)
from backtest.fund_rotation.strategies.ai_rotation_r59_r39_signal_r57_positive_slope.strategy import (
    AiRotationR59R39SignalR57PositiveSlopeStrategy,
)
from backtest.fund_rotation.strategies.ai_rotation_r60_r59_medium_trend_gate.strategy import (
    AiRotationR60R59MediumTrendGateStrategy,
)
from backtest.fund_rotation.strategies.ai_rotation_r61_r59_dual_horizon_score.strategy import (
    AiRotationR61R59DualHorizonScoreStrategy,
)
from backtest.fund_rotation.strategies.ai_rotation_r62_r59_true_invvol.strategy import (
    AiRotationR62R59TrueInvvolStrategy,
)
from backtest.fund_rotation.strategies.ai_rotation_r63_r59_rank_buffer.strategy import (
    AiRotationR63R59RankBufferStrategy,
)
from backtest.fund_rotation.strategies.ai_rotation_r64_direct_corr_diversification.strategy import (
    AiRotationR64DirectCorrDiversificationStrategy,
)
from backtest.fund_rotation.strategies.ai_rotation_r65_r64_direct_corr_rank_buffer.strategy import (
    AiRotationR65R64DirectCorrRankBufferStrategy,
)


def default_fund_rotation_strategies() -> tuple[type, ...]:
    """§16.1 — the explicit strategy whitelist."""
    return (
        CorrelationAllMembersStrategy,
        CorrelationRepresentativeStrategy,
        AiRotationR05MomPersistStrategy,
        AiRotationR06RankBufferStrategy,
        AiRotationR07TailPersistStrategy,
        AiRotationR11PersistGeomStrategy,
        AiRotationR12NondecayGeomStrategy,
        AiRotationR13ArithPersistStrategy,
        AiRotationR14MedianPersistStrategy,
        AiRotationR15WeightedPersistStrategy,
        AiRotationR16RankConsensusStrategy,
        AiRotationR17WinsorGeomStrategy,
        AiRotationR18MinPersistStrategy,
        AiRotationR19Top2CashStrategy,
        AiRotationR20RankFrontloadStrategy,
        AiRotationR21HarmonicPersistStrategy,
        AiRotationR22PathConsistencyStrategy,
        AiRotationR23DownsideGeomStrategy,
        AiRotationR24DispersionGeomStrategy,
        AiRotationR25RepPersistGeomStrategy,
        AiRotationR26PathVolGeomStrategy,
        AiRotationR27BreadthPersistGeomStrategy,
        AiRotationR28SizeReliabilityGeomStrategy,
        AiRotationR29InvvolSlotsStrategy,
        AiRotationR30EndpointBreadthGeomStrategy,
        AiRotationR31FastExitStrategy,
        AiRotationR32MarketRegimeStrategy,
        AiRotationR33QualityFallbackStrategy,
        AiRotationR34StagedReentryStrategy,
        AiRotationR35ShortGapReentryStrategy,
        AiRotationR36TailSlotFullEntryStrategy,
        AiRotationR37DeceleratingFullEntryStrategy,
        AiRotationR38ReplacementFullEntryStrategy,
        AiRotationR39IncumbentCarryStrategy,
        AiRotationR40SingleNameCeilingStrategy,
        AiRotationR41BreadthGatedCarryStrategy,
        AiRotationR42SingleIncumbentHalfCarryStrategy,
        AiRotationR43MultiNewBreadthGateStrategy,
        AiRotationR44PersistentIncumbentCarryStrategy,
        AiRotationR45CashFloorCarryStrategy,
        AiRotationR46CashFloorTightCarryStrategy,
        AiRotationR47BreadthTightFloorStrategy,
        AiRotationR48CashFloorVeryTightStrategy,
        AiRotationR49CashFloorMicroStrategy,
        AiRotationR50CashFloorNanoStrategy,
        AiRotationR51CashFloorPicoStrategy,
        AiRotationR52CashFloorFemtoStrategy,
        AiRotationR53CashFloorAttoStrategy,
        AiRotationR54CashFloorZeptoStrategy,
        AiRotationR55CashFloorYottaStrategy,
        AiRotationR56CashFloorRonnaStrategy,
        AiRotationR57ThreeFactorRepresentativeStrategy,
        AiRotationR58R39SignalR57Strategy,
        AiRotationR59R39SignalR57PositiveSlopeStrategy,
        AiRotationR60R59MediumTrendGateStrategy,
        AiRotationR61R59DualHorizonScoreStrategy,
        AiRotationR62R59TrueInvvolStrategy,
        AiRotationR63R59RankBufferStrategy,
        AiRotationR64DirectCorrDiversificationStrategy,
        AiRotationR65R64DirectCorrRankBufferStrategy,
    )
