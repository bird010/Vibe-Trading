"""Tests for residual order management — §12.2."""

import pytest

from backtest.fund_rotation.orders import OrderManager, OrderStatus, AttemptStatus


class TestOrderLifecycle:
    """§12.2 — PENDING/PARTIAL/FILLED/CANCELLED states."""

    def test_new_order_is_pending(self):
        om = OrderManager()
        om.create_orders({"A": 1000, "B": 500}, event_id="evt_001")
        order_a = om.get_order("A")
        assert order_a is not None
        assert order_a.status == OrderStatus.PENDING
        assert order_a.requested == 1000
        assert order_a.filled == 0

    def test_partial_fill(self):
        om = OrderManager()
        om.create_orders({"A": 1000}, event_id="evt_001")
        om.record_attempt("A", filled=400, attempt_status=AttemptStatus.PARTIAL)
        order_a = om.get_order("A")
        assert order_a.status == OrderStatus.PARTIAL
        assert order_a.filled == 400
        assert order_a.remaining == 600

    def test_full_fill(self):
        om = OrderManager()
        om.create_orders({"A": 1000}, event_id="evt_001")
        om.record_attempt("A", filled=1000, attempt_status=AttemptStatus.FILLED)
        order_a = om.get_order("A")
        assert order_a.status == OrderStatus.FILLED
        assert order_a.remaining == 0

    def test_partial_then_full(self):
        om = OrderManager()
        om.create_orders({"A": 1000}, event_id="evt_001")
        om.record_attempt("A", filled=400, attempt_status=AttemptStatus.PARTIAL)
        om.record_attempt("A", filled=600, attempt_status=AttemptStatus.FILLED)
        order_a = om.get_order("A")
        assert order_a.status == OrderStatus.FILLED
        assert order_a.filled == 1000


class TestNewTargetCancelsOld:
    """§12.2 — new target event cancels and replaces old pending orders."""

    def test_new_event_cancels_pending(self):
        om = OrderManager()
        om.create_orders({"A": 1000, "B": 500}, event_id="evt_001")
        om.record_attempt("A", filled=400, attempt_status=AttemptStatus.PARTIAL)
        # New target arrives
        om.create_orders({"A": 200, "C": 800}, event_id="evt_002")
        # Old B order should be cancelled
        order_b = om.get_order("B")
        assert order_b.status == OrderStatus.CANCELLED
        # Old A order cancelled, new A order created
        order_a = om.get_order("A")
        assert order_a.status == OrderStatus.PENDING
        assert order_a.requested == 200
        assert order_a.event_id == "evt_002"

    def test_filled_orders_not_cancelled(self):
        """Already filled orders stay FILLED, not overwritten."""
        om = OrderManager()
        om.create_orders({"A": 1000}, event_id="evt_001")
        om.record_attempt("A", filled=1000, attempt_status=AttemptStatus.FILLED)
        om.create_orders({"A": 500}, event_id="evt_002")
        # The filled order from evt_001 should remain in history
        history = om.get_history("A")
        assert any(o.event_id == "evt_001" and o.status == OrderStatus.FILLED for o in history)


class TestRetrySemantics:
    """§12.2 — retry only the remaining quantity."""

    def test_blocked_attempt_does_not_change_filled(self):
        om = OrderManager()
        om.create_orders({"A": 1000}, event_id="evt_001")
        om.record_attempt("A", filled=0, attempt_status=AttemptStatus.BLOCKED)
        order_a = om.get_order("A")
        assert order_a.status == OrderStatus.PENDING  # BLOCKED is not terminal
        assert order_a.filled == 0
        assert order_a.remaining == 1000

    def test_remaining_after_partial(self):
        om = OrderManager()
        om.create_orders({"A": 1000}, event_id="evt_001")
        om.record_attempt("A", filled=300, attempt_status=AttemptStatus.PARTIAL)
        assert om.get_order("A").remaining == 700


class TestExecutionPriority:
    """§12.2 — sells before buys within each execution day."""

    def test_pending_orders_sorted_sells_first(self):
        om = OrderManager()
        om.create_orders({"A": 500, "B": -300, "C": 200}, event_id="evt_001")
        pending = om.get_pending_orders()
        # Negative = sell, should come first
        actions = [o.direction for o in pending]
        # All sells before all buys
        sell_indices = [i for i, a in enumerate(actions) if a == "SELL"]
        buy_indices = [i for i, a in enumerate(actions) if a == "BUY"]
        if sell_indices and buy_indices:
            assert max(sell_indices) < min(buy_indices)
