"""Unified target weight portfolio executor — §12.1.

Sell-first-buy-later, common proportional scaling, order-independent.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from backtest.fund_rotation.etf_rules import ChinaETFExecutionRules


@dataclass
class RebalanceResult:
    """Result of one rebalance execution."""

    pre_equity: float
    cash: float
    final_positions: dict[str, dict]
    events: list[dict] = field(default_factory=list)
    scale_factor: float = 1.0


class PortfolioExecutor:
    """§12.1 — Portfolio-level target weight executor.

    Processes one rebalance event per call:
    1. Compute equity anchor from open prices.
    2. Compute deltas (target - current).
    3. Execute all sells/reductions first.
    4. Execute buys with common scaling factor.
    """

    def __init__(self, cash: float, rules: ChinaETFExecutionRules) -> None:
        self.cash = cash
        self.rules = rules
        self._positions: dict[str, dict] = {}
        self._last_close: dict[str, float] = {}
        self._last_close_date: dict[str, str] = {}
        self._last_close_source: dict[str, str] = {}

    def set_positions(self, positions: dict[str, dict]) -> None:
        """Set current positions. Each: {"size": int, "entry_date": str}."""
        self._positions = {k: dict(v) for k, v in positions.items()}

    def set_last_close(self, prices: dict[str, float], trade_date: str = "") -> None:
        """Set last known close prices for stale valuation."""
        self._last_close = dict(prices)
        if trade_date:
            self._last_close_date = {code: trade_date for code in prices}
            self._last_close_source = {code: "close" for code in prices}

    def execute_rebalance(
        self,
        target_weights: dict[str, float],
        bars: dict[str, dict],
        trade_date: str,
    ) -> RebalanceResult:
        """Execute one rebalance event.

        Args:
            target_weights: ts_code -> target weight (0-1). Missing = 0.
            bars: ts_code -> bar dict with open/close/vol/high/low/pre_close.
            trade_date: Current execution date (YYYYMMDD).

        Returns:
            RebalanceResult with final state.
        """
        events: list[dict] = []
        all_codes = sorted(set(list(target_weights.keys()) + list(self._positions.keys())))

        # Step 1: Equity anchor
        pre_equity = self._compute_equity_anchor(all_codes, bars)

        # Step 2: Compute deltas
        sells: list[tuple[str, int]] = []  # (code, shares_to_sell)
        buys: list[tuple[str, float]] = []  # (code, target_notional)

        for code in all_codes:
            current_size = self._positions.get(code, {}).get("size", 0)
            tw = target_weights.get(code, 0.0)
            target_notional = tw * pre_equity
            price = self._get_execution_price(code, bars)
            if price <= 0:
                continue
            target_size_approx = target_notional / price
            delta = target_size_approx - current_size

            if delta < -0.5:  # Need to sell
                sell_shares = min(int(-delta), current_size)
                if sell_shares > 0:
                    sells.append((code, sell_shares))
            elif delta > 0.5:  # Need to buy
                buys.append((code, target_notional - current_size * price))

        # Step 3: Execute all sells first
        for code, shares in sells:
            bar = bars.get(code, {})
            if not self.rules.can_sell(bar):
                events.append({"code": code, "action": "SELL", "status": "BLOCKED",
                               "requested": shares, "filled": 0, "reason": "market_blocked"})
                continue
            # T+1 check
            entry_date = self._positions.get(code, {}).get("entry_date", "")
            if not self.rules.can_sell_today(entry_date, trade_date):
                events.append({"code": code, "action": "SELL", "status": "BLOCKED",
                               "requested": shares, "filled": 0, "reason": "t_plus_1"})
                continue

            price = self._get_execution_price(code, bars)
            sell_price = self.rules.apply_tick(price, direction=-1)
            sell_shares = self.rules.round_sell_size(shares)
            commission = self.rules.calc_commission(sell_shares, sell_price)
            proceeds = sell_shares * sell_price - commission

            self.cash += proceeds
            pos = self._positions.get(code, {})
            pos["size"] = pos.get("size", 0) - sell_shares
            if pos["size"] <= 0:
                self._positions.pop(code, None)
            else:
                self._positions[code] = pos

            events.append({"code": code, "action": "SELL", "status": "FILLED",
                           "requested": shares, "filled": sell_shares,
                           "price": sell_price, "commission": commission})

        # Step 4-7: Execute buys with common scaling
        if buys:
            scale = self._find_max_scale(buys, bars, pre_equity)
            for code, target_notional in buys:
                bar = bars.get(code, {})
                if not self.rules.can_buy(bar):
                    events.append({"code": code, "action": "BUY", "status": "BLOCKED",
                                   "requested": int(target_notional / max(self._get_execution_price(code, bars), 0.001)),
                                   "filled": 0, "reason": "market_blocked"})
                    continue

                price = self._get_execution_price(code, bars)
                buy_price = self.rules.apply_tick(price, direction=1)
                scaled_notional = target_notional * scale
                raw_size = scaled_notional / buy_price if buy_price > 0 else 0
                size = self.rules.round_buy_size(raw_size)

                if size <= 0:
                    events.append({"code": code, "action": "BUY", "status": "BLOCKED",
                                   "requested": int(raw_size), "filled": 0,
                                   "reason": "insufficient_cash_after_commission_and_lot"})
                    continue

                commission = self.rules.calc_commission(size, buy_price)
                cost = size * buy_price + commission
                if cost > self.cash + 1e-9:
                    events.append({"code": code, "action": "BUY", "status": "BLOCKED",
                                   "requested": size, "filled": 0,
                                   "reason": "insufficient_cash_after_commission_and_lot"})
                    continue

                self.cash -= cost
                pos = self._positions.get(code, {"size": 0, "entry_date": trade_date})
                was_empty = pos.get("size", 0) <= 0
                pos["size"] = pos.get("size", 0) + size
                pos["entry_date"] = trade_date
                self._positions[code] = pos
                close_price = float(bar.get("close", 0.0) or 0.0)
                if was_empty and (not math.isfinite(close_price) or close_price <= 0):
                    self._last_close[code] = buy_price
                    self._last_close_date[code] = trade_date
                    self._last_close_source[code] = "execution_price"

                events.append({"code": code, "action": "BUY", "status": "FILLED",
                               "requested": int(raw_size), "filled": size,
                               "price": buy_price, "commission": commission})
        else:
            scale = 1.0

        return RebalanceResult(
            pre_equity=pre_equity,
            cash=self.cash,
            final_positions={k: dict(v) for k, v in self._positions.items()},
            events=events,
            scale_factor=scale if buys else 1.0,
        )

    # ── Private helpers ──

    def _compute_equity_anchor(self, codes: list[str], bars: dict[str, dict]) -> float:
        """Step 1: equity = cash + sum(position_value at open)."""
        equity = self.cash
        for code in codes:
            pos = self._positions.get(code)
            if not pos or pos.get("size", 0) <= 0:
                continue
            price = self._get_anchor_price(code, bars)
            equity += pos["size"] * price
        return equity

    def _get_anchor_price(self, code: str, bars: dict[str, dict]) -> float:
        """Open price, or last close if no open."""
        bar = bars.get(code, {})
        open_price = bar.get("open")
        if open_price is not None:
            try:
                p = float(open_price)
                if p > 0:
                    return p
            except (TypeError, ValueError):
                pass
        return self._last_close.get(code, 0.0)

    def _get_execution_price(self, code: str, bars: dict[str, dict]) -> float:
        """Execution price = open price (for actual trades)."""
        bar = bars.get(code, {})
        open_price = bar.get("open")
        if open_price is not None:
            try:
                p = float(open_price)
                if p > 0:
                    return p
            except (TypeError, ValueError):
                pass
        return 0.0

    def _find_max_scale(
        self,
        buys: list[tuple[str, float]],
        bars: dict[str, dict],
        equity: float,
    ) -> float:
        """§12.1 step 7: binary search for max feasible scale in [0, 1].

        Constraint: sum(lot_floor(notional_i * s / price_i) * price_i + commission_i) <= cash
        s=0 is always feasible.
        """
        available = self.cash
        if available <= 0:
            return 0.0

        # Pre-compute prices
        priced_buys: list[tuple[str, float, float]] = []
        for code, notional in buys:
            price = self._get_execution_price(code, bars)
            bar = bars.get(code, {})
            if price <= 0 or not self.rules.can_buy(bar):
                continue
            buy_price = self.rules.apply_tick(price, direction=1)
            priced_buys.append((code, notional, buy_price))

        if not priced_buys:
            return 0.0

        def total_cost_at_scale(s: float) -> float:
            total = 0.0
            for _, notional, price in priced_buys:
                raw = notional * s / price
                size = self.rules.round_buy_size(raw)
                if size > 0:
                    total += size * price + self.rules.calc_commission(size, price)
            return total

        # Binary search
        lo, hi = 0.0, 1.0
        for _ in range(60):  # sufficient precision
            mid = (lo + hi) / 2.0
            if total_cost_at_scale(mid) <= available:
                lo = mid
            else:
                hi = mid

        return lo
