"""Cohort target builder: Top-N selection with fee pre-reservation.

Implements design §8.3. Pure Top-N, no buffer_retain_rank, no previous holdings.
"""

from __future__ import annotations

from backtest.stockpred.cohort.contracts import SignalSnapshot, TargetSnapshot
from backtest.stockpred.execution.costs import CostPolicy, DEFAULT_COST_POLICY

# Estimated entry fee rate for pre-reservation (conservative)
_ESTIMATED_ENTRY_FEE_RATE = 0.003  # 30 bps


def build_cohort_targets(
    signal_snapshot: SignalSnapshot,
    *,
    committed_capital: float,
    top_n: int,
    fee_policy: CostPolicy = DEFAULT_COST_POLICY,
    cohort_id: str = "",
) -> TargetSnapshot:
    """Build frozen target portfolio from signal snapshot.

    Rules (§8.3, §27.3):
    - Pure Top-N by score, deterministic tie-break by ts_code ascending
    - Equal weight = 1 / actual_count (not 1/top_n if fewer available)
    - Fee pre-reservation: target_value adjusted so that
      target_value + estimated_fee <= committed_capital / actual_count
    - No buffer_retain_rank, no previous holdings
    - Target frozen before execution (immutable)
    """
    signals = signal_snapshot.signals
    if not signals:
        return TargetSnapshot(
            cohort_id=cohort_id,
            evaluation_date=signal_snapshot.evaluation_date,
            committed_capital=committed_capital,
        )

    # Sort by score descending, tie-break by ts_code ascending
    ranked = sorted(signals, key=lambda s: (-float(s.get("score", 0)), str(s.get("ts_code", ""))))

    # Select Top-N
    actual_count = min(top_n, len(ranked))
    selected = ranked[:actual_count]
    selected_codes = tuple(str(s["ts_code"]) for s in selected)

    if actual_count == 0:
        return TargetSnapshot(
            cohort_id=cohort_id,
            evaluation_date=signal_snapshot.evaluation_date,
            committed_capital=committed_capital,
        )

    # Equal weight
    weight = 1.0 / actual_count

    # Fee pre-reservation: reduce target value to leave room for entry fees
    # target_value + estimated_fee = capital_per_slot
    # estimated_fee ≈ target_value * fee_rate
    # target_value * (1 + fee_rate) = capital_per_slot
    # target_value = capital_per_slot / (1 + fee_rate)
    capital_per_slot = committed_capital / actual_count
    target_value_per_slot = capital_per_slot / (1.0 + _ESTIMATED_ENTRY_FEE_RATE)

    target_weights = {code: weight for code in selected_codes}
    target_values = {code: target_value_per_slot for code in selected_codes}

    return TargetSnapshot(
        cohort_id=cohort_id,
        evaluation_date=signal_snapshot.evaluation_date,
        committed_capital=committed_capital,
        selected_codes=selected_codes,
        target_weights=target_weights,
        target_values=target_values,
        selection_reason=f"top_{top_n}_equal_weight",
    )
