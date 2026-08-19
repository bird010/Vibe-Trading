"""ETF execution rules — §13.2.

China onshore ETF market rules: T+1, long-only, 100-share lots,
tick 0.001, commission 2.5 bps min 5 CNY, no stamp tax.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ChinaETFExecutionRules:
    """§13.2 — Default ETF execution rules."""

    lot_size: int = 100
    tick_size: float = 0.001
    commission_rate: float = 0.00025  # 2.5 bps
    commission_min: float = 5.0  # CNY
    stamp_tax: float = 0.0
    transfer_fee: float = 0.0
    other_fee_rate: float = 0.0
    leverage: float = 1.0
    allow_short: bool = False
    price_limit_pct: float | None = 0.10  # None means an explicit no-limit rule
    settlement: str = "T+1"

    def round_buy_size(self, raw_size: float) -> int:
        """Round down to lot_size integer units for buying."""
        return int(raw_size // self.lot_size) * self.lot_size

    def round_sell_size(self, raw_size: float) -> int:
        """Sell allows odd lots (from adj factor changes)."""
        return int(raw_size)

    def apply_tick(self, price: float, direction: int) -> float:
        """Round price to tick size against the trader.

        Buy (direction=1): round up (pay more).
        Sell (direction=-1): round down (receive less).
        """
        ticks = price / self.tick_size
        if direction > 0:
            rounded = math.ceil(round(ticks, 6)) * self.tick_size
        else:
            rounded = math.floor(round(ticks, 6)) * self.tick_size
        return round(rounded, 6)

    def calc_commission(self, size: float, price: float) -> float:
        """Commission = max(notional * rate, min). Zero size -> zero."""
        if size <= 0 or price <= 0:
            return 0.0
        notional = size * price
        comm = notional * self.commission_rate
        comm = max(comm, self.commission_min)
        comm += notional * (self.transfer_fee + self.other_fee_rate)
        return comm

    def can_buy(self, bar: dict) -> bool:
        """Check if buying is possible given bar data."""
        if not self._has_valid_price(bar):
            return False
        if self._is_zero_volume(bar):
            return False
        if self._is_limit_up(bar):
            return False
        return True

    def can_sell(self, bar: dict) -> bool:
        """Check if selling is possible given bar data."""
        if not self._has_valid_price(bar):
            return False
        if self._is_zero_volume(bar):
            return False
        if self._is_limit_down(bar):
            return False
        return True

    def can_sell_today(self, entry_date: str, current_date: str) -> bool:
        """Apply the PIT settlement rule for same-day selling."""
        settlement = self.settlement.strip().upper().replace(" ", "")
        if settlement in {"T+0", "T0"}:
            return True
        if settlement not in {"T+1", "T1"}:
            raise ValueError(f"unsupported settlement rule: {self.settlement}")
        return current_date > entry_date

    # ── Private helpers ──

    def _has_valid_price(self, bar: dict) -> bool:
        open_price = bar.get("open")
        if open_price is None:
            return False
        try:
            return float(open_price) > 0
        except (TypeError, ValueError):
            return False

    def _is_zero_volume(self, bar: dict) -> bool:
        vol = bar.get("vol", 0)
        try:
            return float(vol) <= 0
        except (TypeError, ValueError):
            return True

    def _is_limit_up(self, bar: dict) -> bool:
        """Single-price limit-up: high==low and at +limit from pre_close."""
        if self.price_limit_pct is None:
            return False
        pre_close = bar.get("pre_close")
        if pre_close is None:
            return False
        high = bar.get("high")
        low = bar.get("low")
        close = bar.get("close")
        if high is None or low is None or close is None:
            return False
        try:
            h, l, c, pc = float(high), float(low), float(close), float(pre_close)
        except (TypeError, ValueError):
            return False
        if h != l:
            return False
        # Single price day at limit up
        pct = (c - pc) / pc if pc > 0 else 0
        return pct >= self.price_limit_pct - 0.001

    def _is_limit_down(self, bar: dict) -> bool:
        """Single-price limit-down: high==low and at -limit from pre_close."""
        if self.price_limit_pct is None:
            return False
        pre_close = bar.get("pre_close")
        if pre_close is None:
            return False
        high = bar.get("high")
        low = bar.get("low")
        close = bar.get("close")
        if high is None or low is None or close is None:
            return False
        try:
            h, l, c, pc = float(high), float(low), float(close), float(pre_close)
        except (TypeError, ValueError):
            return False
        if h != l:
            return False
        pct = (c - pc) / pc if pc > 0 else 0
        return pct <= -self.price_limit_pct + 0.001
