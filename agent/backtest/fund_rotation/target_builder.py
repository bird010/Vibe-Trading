"""Target weight builder — §12.3.

Converts direction/score signals into portfolio target weights.
This is the adapter layer that allows existing direction-based strategies
to work with the unified target weight executor.
"""

from __future__ import annotations


def direction_to_target_weights(signals: dict[str, float]) -> dict[str, float]:
    """Convert direction signals to normalized target weights.

    Rules (§12.3):
    - Positive signals → long weight (proportional to signal magnitude).
    - Zero or negative signals → no position (long-only).
    - Weights are normalized so sum = 1.0 (fully invested among positives).
    - Empty or all-zero signals → empty dict (all cash).

    Args:
        signals: ts_code -> signal value (positive=long, zero/negative=flat).

    Returns:
        ts_code -> target_weight (sum <= 1.0).
    """
    # Filter to positive signals only (long-only)
    positive = {code: val for code, val in signals.items() if val > 0}

    if not positive:
        return {}

    # Normalize: equal weight among positive signals
    total = sum(positive.values())
    if total <= 0:
        return {}

    return {code: val / total for code, val in positive.items()}
