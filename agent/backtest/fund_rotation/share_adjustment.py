"""Economic share adjustment — §11.

When adj_factor changes between periods (due to distributions/splits),
holding shares must be adjusted to maintain consistent economic value.
This is the "distribution auto-reinvestment" research accounting assumption.
"""

from __future__ import annotations


def adjust_shares_for_factor_change(
    current_shares: int,
    old_adj_factor: float,
    new_adj_factor: float,
) -> tuple[int, float]:
    """Adjust holding shares when adj_factor changes.

    Under the economic share assumption:
    - adjusted_value = shares * price * adj_factor / latest_factor
    - When factor changes from old to new, shares scale by new/old
      to preserve the same adjusted economic value.

    Args:
        current_shares: Shares held before factor change.
        old_adj_factor: Previous adj_factor.
        new_adj_factor: New adj_factor (e.g. after distribution).

    Returns:
        (new_shares, fractional_remainder) where new_shares is integer
        and fractional_remainder is the sub-share portion that may
        generate an odd lot for future clearing.
    """
    if old_adj_factor <= 0 or new_adj_factor <= 0:
        return current_shares, 0.0

    if old_adj_factor == new_adj_factor:
        return current_shares, 0.0

    # adjusted_close = raw_close * adj_factor / terminal_factor.  Therefore a
    # factor jump from old to new is accompanied by the inverse raw-price move,
    # and economic shares must scale by new/old to preserve holding value.
    scale = new_adj_factor / old_adj_factor
    raw_shares = current_shares * scale
    new_shares = int(raw_shares)
    fractional = raw_shares - new_shares

    return new_shares, fractional
