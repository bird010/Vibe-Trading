import json
from types import SimpleNamespace

import pytest

from backtest.fund_rotation.execution_ledger_v2 import (
    AttemptStatus,
    CorporateActionRecord,
    CorporateActionType,
    CostScenario,
    ExecutedTradeRecord,
    ExecutionCostModel,
    ExecutionCostEstimate,
    ExecutionAttemptRecord,
    ExecutionLedger,
    FundInstrumentVersion,
    InMemoryPITMarketRuleSource,
    MarketObservation,
    MarketRuleResolver,
    OrderDirection,
    ParentOrderRecord,
    ParentOrderStatus,
    PITQueryMode,
    UnknownExecutionRule,
    build_execution_ledger_from_pipeline_result,
    compute_attempt_diagnostics,
    compute_corporate_action_diagnostics,
    compute_execution_diagnostics_v2,
    compute_order_diagnostics,
    compute_trade_diagnostics,
    _replacement_residual_quantity,
)


def _parent(
    *,
    order_id: str = "O-1",
    requested: int = 1_000,
    filled: int = 0,
    remaining: int | None = None,
    status: ParentOrderStatus = ParentOrderStatus.OPEN,
    replacement_of_order_id: str = "",
    replacement_chain_id: str = "",
    corporate_action_id: str = "",
    cancel_reason: str = "",
    quantity_basis_id: str = "510300.SH:shares:v1",
) -> ParentOrderRecord:
    return ParentOrderRecord(
        order_id=order_id,
        decision_id="D-1",
        ts_code="510300.SH",
        direction=OrderDirection.BUY,
        created_date="20240102",
        original_requested_quantity=requested,
        cumulative_filled_quantity=filled,
        remaining_quantity=requested - filled if remaining is None else remaining,
        quantity_basis_id=quantity_basis_id,
        replacement_of_order_id=replacement_of_order_id,
        replacement_chain_id=replacement_chain_id,
        corporate_action_id=corporate_action_id,
        status=status,
        cancel_reason=cancel_reason,
    )


def _attempt(
    *,
    attempt_id: str,
    order_id: str = "O-1",
    attempt_number: int = 1,
    requested: int,
    filled: int,
    status: AttemptStatus,
    quantity_basis_id: str = "510300.SH:shares:v1",
    trade_date: str | None = None,
) -> ExecutionAttemptRecord:
    return ExecutionAttemptRecord(
        attempt_id=attempt_id,
        order_id=order_id,
        attempt_number=attempt_number,
        trade_date=trade_date or f"2024010{attempt_number + 1}",
        requested_quantity=requested,
        filled_quantity=filled,
        unfilled_quantity=requested - filled,
        quantity_basis_id=quantity_basis_id,
        raw_price=10.0,
        executed_price=10.01 if filled else 0.0,
        commission=1.0 if filled else 0.0,
        explicit_fee=0.5 if filled else 0.0,
        slippage_cost=filled * 0.01,
        participation_rate=0.01 if filled else 0.0,
        status=status,
    )


def _trade(
    *,
    trade_id: str,
    attempt_id: str,
    order_id: str = "O-1",
    quantity: int,
    price: float = 10.0,
    quantity_basis_id: str = "510300.SH:shares:v1",
    trade_date: str = "20240102",
    ts_code: str = "510300.SH",
    direction: OrderDirection = OrderDirection.BUY,
) -> ExecutedTradeRecord:
    return ExecutedTradeRecord(
        trade_id=trade_id,
        attempt_id=attempt_id,
        order_id=order_id,
        ts_code=ts_code,
        direction=direction,
        quantity=quantity,
        quantity_basis_id=quantity_basis_id,
        price=price,
        notional=quantity * price,
        commission=0.0,
        explicit_fee=0.0,
        slippage_cost=0.0,
        trade_date=trade_date,
    )


def test_residual_parent_fill_rate_is_separate_from_attempt_fill_rate():
    ledger = ExecutionLedger(
        parent_orders=(
            _parent(filled=1_000, remaining=0, status=ParentOrderStatus.FILLED),
        ),
        attempts=(
            _attempt(
                attempt_id="A-1",
                requested=1_000,
                filled=500,
                status=AttemptStatus.PARTIALLY_FILLED,
            ),
            _attempt(
                attempt_id="A-2",
                attempt_number=2,
                requested=500,
                filled=500,
                status=AttemptStatus.FILLED,
            ),
        ),
        trades=(
            _trade(trade_id="T-1", attempt_id="A-1", quantity=500),
            _trade(
                trade_id="T-2",
                attempt_id="A-2",
                quantity=500,
                trade_date="20240103",
            ),
        ),
        corporate_actions=(),
    )

    order_diagnostics = compute_order_diagnostics(ledger)
    attempt_diagnostics = compute_attempt_diagnostics(ledger)
    combined = compute_execution_diagnostics_v2(ledger, average_portfolio_nav=100_000.0)

    assert order_diagnostics["order_count"] == 1
    assert order_diagnostics["fully_filled_order_count"] == 1
    assert order_diagnostics["mean_parent_fill_rate"] == pytest.approx(1.0)
    assert "blocked_order_count" not in order_diagnostics

    assert attempt_diagnostics["attempt_count"] == 2
    assert attempt_diagnostics["attempt_quantity_fill_rate"] == pytest.approx(1_000 / 1_500)
    assert attempt_diagnostics["partial_attempt_count"] == 1
    assert attempt_diagnostics["filled_attempt_count"] == 1

    assert combined["metric_contract_version"] == "execution_diagnostics_v2"
    assert combined["orders"]["mean_parent_fill_rate"] == pytest.approx(1.0)
    assert combined["attempts"]["attempt_quantity_fill_rate"] == pytest.approx(1_000 / 1_500)


def test_blocked_is_only_an_attempt_status_not_a_parent_order_metric():
    ledger = ExecutionLedger(
        parent_orders=(_parent(status=ParentOrderStatus.OPEN),),
        attempts=(
            _attempt(attempt_id="A-1", requested=1_000, filled=0, status=AttemptStatus.BLOCKED),
            _attempt(
                attempt_id="A-2",
                attempt_number=2,
                requested=1_000,
                filled=0,
                status=AttemptStatus.BLOCKED,
            ),
            _attempt(
                attempt_id="A-3",
                attempt_number=3,
                requested=1_000,
                filled=0,
                status=AttemptStatus.BLOCKED,
            ),
        ),
        trades=(),
        corporate_actions=(),
    )

    order_diagnostics = compute_order_diagnostics(ledger)
    attempt_diagnostics = compute_attempt_diagnostics(ledger)

    assert order_diagnostics["open_order_count"] == 1
    assert "blocked_order_count" not in order_diagnostics
    assert attempt_diagnostics["blocked_attempt_count"] == 3
    assert attempt_diagnostics["blocked_attempt_rate"] == pytest.approx(1.0)
    assert attempt_diagnostics["attempt_quantity_fill_rate"] == pytest.approx(0.0)

    with pytest.raises(ValueError, match="BLOCKED is not a parent order status"):
        ParentOrderRecord(
            **{
                **_parent().__dict__,
                "status": "BLOCKED",
            }
        )


def test_trade_costs_use_executed_price_without_double_counting_slippage():
    trade = ExecutedTradeRecord(
        trade_id="T-1",
        attempt_id="A-1",
        order_id="O-1",
        ts_code="510300.SH",
        direction=OrderDirection.BUY,
        quantity=1_000,
        quantity_basis_id="510300.SH:shares:v1",
        price=10.10,
        notional=10_100.0,
        commission=5.0,
        explicit_fee=1.0,
        slippage_cost=100.0,
        trade_date="20240102",
    )
    ledger = ExecutionLedger(
        parent_orders=(_parent(filled=1_000, remaining=0, status=ParentOrderStatus.FILLED),),
        attempts=(_attempt(attempt_id="A-1", requested=1_000, filled=1_000, status=AttemptStatus.FILLED),),
        trades=(trade,),
        corporate_actions=(
            CorporateActionRecord(
                corporate_action_id="CA-1",
                ts_code="510300.SH",
                action_type=CorporateActionType.CASH_DIVIDEND,
                effective_date="20240103",
                old_quantity=1_000,
                new_quantity=1_000,
                old_cost_basis=10.0,
                new_cost_basis=10.0,
                adjustment_factor=1.0,
            ),
        ),
    )

    diagnostics = compute_trade_diagnostics(
        ledger,
        average_portfolio_nav=100_000.0,
        evaluation_days=10,
    )

    assert diagnostics["executed_trade_count"] == 1
    assert diagnostics["buy_trade_count"] == 1
    assert diagnostics["total_notional"] == pytest.approx(10_100.0)
    assert diagnostics["explicit_cash_cost"] == pytest.approx(6.0)
    assert diagnostics["slippage_opportunity_cost"] == pytest.approx(100.0)
    assert diagnostics["buy_cash_out"] == pytest.approx(10_106.0)
    assert diagnostics["buy_cash_out"] != pytest.approx(10_206.0)
    assert diagnostics["gross_traded_notional_ratio"] == pytest.approx(0.101)
    assert diagnostics["one_way_turnover"] == pytest.approx(0.0505)
    assert diagnostics["annualized_one_way_turnover"] == pytest.approx(0.0505 * 252 / 10)
    assert "turnover" not in diagnostics
    assert "total_execution_cost" not in diagnostics


def test_corporate_actions_are_audit_facts_and_replacement_lineage_is_validated():
    corporate_action = CorporateActionRecord(
        corporate_action_id="CA-SPLIT",
        ts_code="510300.SH",
        action_type=CorporateActionType.SHARE_SPLIT,
        effective_date="20240110",
        old_quantity=500,
        new_quantity=1_000,
        old_cost_basis=10.0,
        new_cost_basis=5.0,
        adjustment_factor=2.0,
    )
    old_parent = _parent(
        requested=1_000,
        filled=500,
        status=ParentOrderStatus.CANCELED,
        cancel_reason="CORPORATE_ACTION_REPLACED",
    )
    replacement_parent = _parent(
        order_id="O-2",
        requested=1_000,
        filled=0,
        status=ParentOrderStatus.OPEN,
        replacement_of_order_id="O-1",
        replacement_chain_id="CHAIN-1",
        corporate_action_id="CA-SPLIT",
        quantity_basis_id="510300.SH:shares:v2",
    )
    ledger = ExecutionLedger(
        parent_orders=(old_parent, replacement_parent),
        attempts=(_attempt(attempt_id="A-1", requested=1_000, filled=500, status=AttemptStatus.PARTIALLY_FILLED),),
        trades=(
            ExecutedTradeRecord(
                trade_id="T-1",
                attempt_id="A-1",
                order_id="O-1",
                ts_code="510300.SH",
                direction=OrderDirection.BUY,
                quantity=500,
                quantity_basis_id="510300.SH:shares:v1",
                price=10.0,
                notional=5_000.0,
                commission=1.0,
                explicit_fee=0.0,
                slippage_cost=0.0,
                trade_date="20240102",
            ),
        ),
        corporate_actions=(corporate_action,),
    )

    order_diagnostics = compute_order_diagnostics(ledger)
    action_diagnostics = compute_corporate_action_diagnostics(ledger)
    trade_diagnostics = compute_trade_diagnostics(ledger, average_portfolio_nav=100_000.0)

    assert order_diagnostics["order_count"] == 2
    assert order_diagnostics["replacement_order_count"] == 1
    assert order_diagnostics["canceled_order_count"] == 1
    assert order_diagnostics["parent_fill_rates_by_order_id"] == {
        "O-1": pytest.approx(0.5),
        "O-2": pytest.approx(0.0),
    }
    assert action_diagnostics == {
        "corporate_action_count": 1,
        "share_adjustment_count": 1,
        "adjusted_position_count": 1,
    }
    assert trade_diagnostics["executed_trade_count"] == 1
    assert trade_diagnostics["total_notional"] == pytest.approx(5_000.0)

    with pytest.raises(ValueError, match="replacement lineage"):
        ExecutionLedger(
            parent_orders=(
                old_parent,
                _parent(order_id="O-3", replacement_of_order_id="O-1"),
            ),
            attempts=(),
            trades=(),
            corporate_actions=(corporate_action,),
        )


def test_replacement_parent_must_follow_share_adjusted_old_residual_contract():
    old_parent = _parent(
        requested=1_000,
        filled=500,
        status=ParentOrderStatus.CANCELED,
        cancel_reason="CORPORATE_ACTION_REPLACED",
    )
    split = CorporateActionRecord(
        corporate_action_id="CA-SPLIT",
        ts_code="510300.SH",
        action_type=CorporateActionType.SHARE_SPLIT,
        effective_date="20240110",
        old_quantity=500,
        new_quantity=1_000,
        old_cost_basis=10.0,
        new_cost_basis=5.0,
        adjustment_factor=2.0,
    )

    ExecutionLedger(
        parent_orders=(
            old_parent,
            _parent(
                order_id="O-2",
                requested=1_000,
                status=ParentOrderStatus.OPEN,
                replacement_of_order_id="O-1",
                replacement_chain_id="CHAIN-1",
                corporate_action_id="CA-SPLIT",
                quantity_basis_id="510300.SH:shares:v2",
            ),
        ),
        attempts=(
            _attempt(
                attempt_id="A-1",
                requested=1_000,
                filled=500,
                status=AttemptStatus.PARTIALLY_FILLED,
            ),
        ),
        trades=(_trade(trade_id="T-1", attempt_id="A-1", quantity=500),),
        corporate_actions=(split,),
    )

    with pytest.raises(ValueError, match="adjusted residual"):
        ExecutionLedger(
            parent_orders=(
                old_parent,
                _parent(
                    order_id="O-FULL",
                    requested=2_000,
                    status=ParentOrderStatus.OPEN,
                    replacement_of_order_id="O-1",
                    replacement_chain_id="CHAIN-1",
                    corporate_action_id="CA-SPLIT",
                    quantity_basis_id="510300.SH:shares:v2",
                ),
            ),
            attempts=(
                _attempt(
                    attempt_id="A-1",
                    requested=1_000,
                    filled=500,
                    status=AttemptStatus.PARTIALLY_FILLED,
                ),
            ),
            trades=(_trade(trade_id="T-1", attempt_id="A-1", quantity=500),),
            corporate_actions=(split,),
        )

    cash_dividend = CorporateActionRecord(
        corporate_action_id="CA-CASH",
        ts_code="510300.SH",
        action_type=CorporateActionType.CASH_DIVIDEND,
        effective_date="20240110",
        old_quantity=500,
        new_quantity=500,
        old_cost_basis=10.0,
        new_cost_basis=10.0,
        adjustment_factor=1.0,
    )
    with pytest.raises(ValueError, match="share adjustment"):
        ExecutionLedger(
            parent_orders=(
                old_parent,
                _parent(
                    order_id="O-CASH",
                    requested=500,
                    status=ParentOrderStatus.OPEN,
                    replacement_of_order_id="O-1",
                    replacement_chain_id="CHAIN-1",
                    corporate_action_id="CA-CASH",
                    quantity_basis_id="510300.SH:shares:v2",
                ),
            ),
            attempts=(
                _attempt(
                    attempt_id="A-1",
                    requested=1_000,
                    filled=500,
                    status=AttemptStatus.PARTIALLY_FILLED,
                ),
            ),
            trades=(_trade(trade_id="T-1", attempt_id="A-1", quantity=500),),
            corporate_actions=(cash_dividend,),
        )

    with pytest.raises(ValueError, match="CORPORATE_ACTION_REPLACED"):
        ExecutionLedger(
            parent_orders=(
                _parent(
                    requested=1_000,
                    filled=500,
                    status=ParentOrderStatus.CANCELED,
                    cancel_reason="USER_CANCELED",
                ),
                _parent(
                    order_id="O-BAD-OLD",
                    requested=1_000,
                    status=ParentOrderStatus.OPEN,
                    replacement_of_order_id="O-1",
                    replacement_chain_id="CHAIN-1",
                    corporate_action_id="CA-SPLIT",
                    quantity_basis_id="510300.SH:shares:v2",
                ),
            ),
            attempts=(
                _attempt(
                    attempt_id="A-1",
                    requested=1_000,
                    filled=500,
                    status=AttemptStatus.PARTIALLY_FILLED,
                ),
            ),
            trades=(_trade(trade_id="T-1", attempt_id="A-1", quantity=500),),
            corporate_actions=(split,),
        )

    with pytest.raises(ValueError, match="quantity_basis_id"):
        ExecutionLedger(
            parent_orders=(
                old_parent,
                _parent(
                    order_id="O-SAME-BASIS",
                    requested=1_000,
                    status=ParentOrderStatus.OPEN,
                    replacement_of_order_id="O-1",
                    replacement_chain_id="CHAIN-1",
                    corporate_action_id="CA-SPLIT",
                ),
            ),
            attempts=(
                _attempt(
                    attempt_id="A-1",
                    requested=1_000,
                    filled=500,
                    status=AttemptStatus.PARTIALLY_FILLED,
                ),
            ),
            trades=(_trade(trade_id="T-1", attempt_id="A-1", quantity=500),),
            corporate_actions=(split,),
        )


def test_pipeline_adapter_preserves_replacement_identity_and_lot_rounded_quantity():
    order_id = "SIG-20240105-0001-510300.SH"
    adjustment = {
        "corporate_action_id": "CA-20240109-510300.SH",
        "trade_date": "20240109",
        "scale": 1.5,
        "before": {
            "requested": 150,
            "filled": 50,
            "remaining": 100,
            "quantity_basis": 1.0,
        },
        "after": {
            "requested": 225,
            "filled": 75,
            "remaining": 150,
            "quantity_basis": 1.5,
        },
    }
    result = SimpleNamespace(
        orders=[
            {
                "order_id": order_id,
                "event_id": "SIG-20240105-0001",
                "ts_code": "510300.SH",
                "direction": "BUY",
                "requested": 225,
                "filled": 75,
                "remaining": 150,
                "attempt_number": 1,
                "trade_date": "20240108",
                "attempt_filled": 50,
                "attempt_status": "PARTIAL",
                "attempt_quantity_basis": 1.0,
                "current_quantity_basis": 1.5,
                "lot_size": 50,
                "final_status": "PENDING",
                "corporate_action_adjustments": json.dumps([adjustment]),
            }
        ],
        trade_events=[
            {
                "trade_date": "20240108",
                "signal_week": "20240105",
                "signal_event_id": "SIG-20240105-0001",
                "order_id": order_id,
                "attempt_id": f"{order_id}-A1",
                "ts_code": "510300.SH",
                "action": "BUY",
                "status": "PARTIAL",
                "requested": 150,
                "filled": 50,
                "unfilled": 100,
                "price": 10.0,
                "commission": 1.0,
                "explicit_fee": 0.0,
                "slippage_bps": 5.0,
                "participation_rate": 0.01,
            },
            {
                "event_type": "CORPORATE_ACTION",
                "corporate_action_id": "CA-20240109-510300.SH",
                "trade_date": "20240109",
                "ts_code": "510300.SH",
                "requested": 100,
                "filled": 150,
                "old_adj_factor": 1.0,
                "new_adj_factor": 1.5,
                "last_close_before": 10.0,
                "last_close_after": 6.6666667,
            },
        ],
        executed_equity=None,
    )

    ledger = build_execution_ledger_from_pipeline_result(result)

    assert [parent.order_id for parent in ledger.parent_orders] == [
        order_id,
        f"{order_id}-R1",
    ]
    old_parent, replacement = ledger.parent_orders
    assert old_parent.ts_code == "510300.SH"
    assert old_parent.direction is OrderDirection.BUY
    assert old_parent.created_date == "20240105"
    assert old_parent.original_requested_quantity == 150
    assert old_parent.cumulative_filled_quantity == 50
    assert old_parent.remaining_quantity == 100
    assert old_parent.status is ParentOrderStatus.CANCELED
    assert old_parent.cancel_reason == "CORPORATE_ACTION_REPLACED"

    assert replacement.ts_code == old_parent.ts_code
    assert replacement.direction is old_parent.direction
    assert replacement.created_date == "20240109"
    assert replacement.replacement_of_order_id == order_id
    assert replacement.replacement_chain_id == order_id
    assert replacement.corporate_action_id == "CA-20240109-510300.SH"
    assert replacement.original_requested_quantity == 150
    assert replacement.remaining_quantity == 150
    assert replacement.lot_size == 50
    assert replacement.quantity_basis_id == "510300.SH:shares:1.5"

    attempt = ledger.attempts[0]
    trade = ledger.trades[0]
    assert attempt.order_id == order_id
    assert attempt.quantity_basis_id == old_parent.quantity_basis_id
    assert trade.order_id == order_id
    assert trade.attempt_id == attempt.attempt_id
    assert trade.quantity == 50
    assert trade.quantity_basis_id == old_parent.quantity_basis_id
    assert trade.trade_date == "20240108"


def test_replacement_rounding_uses_order_lot_size():
    assert _replacement_residual_quantity(OrderDirection.BUY, 100, 1.5, lot_size=50) == 150
    assert _replacement_residual_quantity(OrderDirection.BUY, 100, 1.5, lot_size=100) == 100


def test_replacement_parent_can_be_validated_after_follow_up_fill():
    old_parent = _parent(
        requested=1_000,
        filled=500,
        status=ParentOrderStatus.CANCELED,
        cancel_reason="CORPORATE_ACTION_REPLACED",
    )
    split = CorporateActionRecord(
        corporate_action_id="CA-SPLIT-AFTER-FILL",
        ts_code="510300.SH",
        action_type=CorporateActionType.SHARE_SPLIT,
        effective_date="20240110",
        old_quantity=500,
        new_quantity=1_000,
        old_cost_basis=10.0,
        new_cost_basis=5.0,
        adjustment_factor=2.0,
    )
    replacement = _parent(
        order_id="O-1-R1",
        requested=1_000,
        filled=500,
        remaining=500,
        status=ParentOrderStatus.PARTIALLY_FILLED,
        replacement_of_order_id="O-1",
        replacement_chain_id="O-1",
        corporate_action_id="CA-SPLIT-AFTER-FILL",
        quantity_basis_id="510300.SH:shares:v2",
    )

    ledger = ExecutionLedger(
        parent_orders=(old_parent, replacement),
        attempts=(
            _attempt(
                attempt_id="A-OLD",
                requested=1_000,
                filled=500,
                status=AttemptStatus.PARTIALLY_FILLED,
                trade_date="20240108",
            ),
            _attempt(
                attempt_id="A-REPLACEMENT",
                order_id="O-1-R1",
                requested=1_000,
                filled=500,
                status=AttemptStatus.PARTIALLY_FILLED,
                quantity_basis_id="510300.SH:shares:v2",
                trade_date="20240111",
            ),
        ),
        trades=(
            _trade(trade_id="T-OLD", attempt_id="A-OLD", quantity=500, trade_date="20240108"),
            _trade(
                trade_id="T-REPLACEMENT",
                attempt_id="A-REPLACEMENT",
                order_id="O-1-R1",
                quantity=500,
                quantity_basis_id="510300.SH:shares:v2",
                trade_date="20240111",
            ),
        ),
        corporate_actions=(split,),
    )

    assert ledger.parent_orders[1].cumulative_filled_quantity == 500


def test_pipeline_adapter_rejects_replacement_overfill():
    order_id = "SIG-20240105-OVERFILL-510300.SH"
    result = SimpleNamespace(
        orders=[{
            "order_id": order_id, "event_id": "SIG-20240105-OVERFILL",
            "ts_code": "510300.SH", "direction": "BUY", "requested": 150,
            "filled": 50, "remaining": 100, "attempt_number": 1,
            "trade_date": "20240108", "attempt_filled": 50, "attempt_status": "PARTIAL",
            "attempt_quantity_basis": 1.0, "current_quantity_basis": 1.5,
            "lot_size": 50, "final_status": "PENDING",
            "corporate_action_adjustments": json.dumps([{
                "corporate_action_id": "CA-OVERFILL", "trade_date": "20240109", "scale": 1.5,
                "before": {"requested": 150, "filled": 50, "remaining": 100, "quantity_basis": 1.0},
                "after": {"quantity_basis": 1.5},
            }]),
        }, {
            "order_id": order_id, "event_id": "SIG-20240105-OVERFILL",
            "ts_code": "510300.SH", "direction": "BUY", "requested": 150,
            "filled": 200, "remaining": 0, "attempt_number": 2,
            "trade_date": "20240111", "attempt_filled": 200, "attempt_status": "PARTIAL",
            "attempt_quantity_basis": 1.5, "current_quantity_basis": 1.5,
            "lot_size": 50, "final_status": "PENDING",
            "corporate_action_adjustments": json.dumps([{
                "corporate_action_id": "CA-OVERFILL", "trade_date": "20240109", "scale": 1.5,
                "before": {"requested": 150, "filled": 50, "remaining": 100, "quantity_basis": 1.0},
                "after": {"quantity_basis": 1.5},
            }]),
        }],
        trade_events=[{
            "trade_date": "20240108", "order_id": order_id,
            "attempt_id": f"{order_id}-A1", "ts_code": "510300.SH", "action": "BUY",
            "status": "PARTIAL", "requested": 150, "filled": 50, "price": 10.0,
        }],
        executed_equity=None,
    )
    with pytest.raises(ValueError, match="replacement filled quantity exceeds"):
        build_execution_ledger_from_pipeline_result(result)


def test_corporate_action_requires_a_symbol():
    with pytest.raises(ValueError, match="corporate_action ts_code is required"):
        CorporateActionRecord(
            corporate_action_id="CA-MISSING-SYMBOL",
            ts_code="",
            action_type=CorporateActionType.SHARE_SPLIT,
            effective_date="20240110",
            old_quantity=100,
            new_quantity=200,
            old_cost_basis=10.0,
            new_cost_basis=5.0,
            adjustment_factor=2.0,
        )


def test_share_corporate_action_requires_quantity_and_cost_conservation():
    with pytest.raises(ValueError, match="quantity conservation"):
        CorporateActionRecord(
            corporate_action_id="CA-BAD-QUANTITY",
            ts_code="510300.SH",
            action_type=CorporateActionType.SHARE_SPLIT,
            effective_date="20240110",
            old_quantity=500,
            new_quantity=900,
            old_cost_basis=10.0,
            new_cost_basis=5.0,
            adjustment_factor=2.0,
        )

    with pytest.raises(ValueError, match="cost conservation"):
        CorporateActionRecord(
            corporate_action_id="CA-BAD-COST",
            ts_code="510300.SH",
            action_type=CorporateActionType.SHARE_SPLIT,
            effective_date="20240110",
            old_quantity=500,
            new_quantity=1_000,
            old_cost_basis=10.0,
            new_cost_basis=6.0,
            adjustment_factor=2.0,
        )

    with pytest.raises(ValueError, match="quantity must be an integer"):
        CorporateActionRecord(
            corporate_action_id="CA-FRACTIONAL-QUANTITY",
            ts_code="510300.SH",
            action_type=CorporateActionType.SHARE_SPLIT,
            effective_date="20240110",
            old_quantity=100.5,
            new_quantity=201,
            old_cost_basis=10.0,
            new_cost_basis=5.0,
            adjustment_factor=2.0,
        )

    with pytest.raises(ValueError, match="cost conservation"):
        CorporateActionRecord(
            corporate_action_id="CA-MISSING-COST",
            ts_code="510300.SH",
            action_type=CorporateActionType.SHARE_SPLIT,
            effective_date="20240110",
            old_quantity=100,
            new_quantity=200,
            old_cost_basis=10.0,
            new_cost_basis=0.0,
            adjustment_factor=2.0,
        )


def test_share_corporate_action_accepts_whole_shares_plus_cash_in_lieu():
    action = CorporateActionRecord(
        corporate_action_id="CA-FRACTIONAL-CASH",
        ts_code="510300.SH",
        action_type=CorporateActionType.SHARE_CONVERSION,
        effective_date="20240110",
        old_quantity=101,
        new_quantity=50,
        old_cost_basis=10.0,
        new_cost_basis=20.0,
        adjustment_factor=0.5,
        economic_new_quantity=50.5,
        fractional_quantity=0.5,
        cash_in_lieu=10.0,
    )

    assert action.economic_new_quantity == pytest.approx(50.5)
    assert action.fractional_quantity == pytest.approx(0.5)
    assert action.cash_in_lieu == pytest.approx(10.0)


def test_ledger_requires_parent_attempt_and_trade_quantity_closure():
    parent = _parent(filled=500, status=ParentOrderStatus.PARTIALLY_FILLED)

    with pytest.raises(ValueError, match="must equal parent cumulative_filled_quantity"):
        ExecutionLedger(
            parent_orders=(parent,),
            attempts=(
                _attempt(
                    attempt_id="A-1",
                    requested=1_000,
                    filled=400,
                    status=AttemptStatus.PARTIALLY_FILLED,
                ),
            ),
            trades=(_trade(trade_id="T-1", attempt_id="A-1", quantity=400),),
            corporate_actions=(),
        )

    attempt = _attempt(
        attempt_id="A-1",
        requested=1_000,
        filled=500,
        status=AttemptStatus.PARTIALLY_FILLED,
    )
    with pytest.raises(ValueError, match="must equal attempt filled_quantity"):
        ExecutionLedger(
            parent_orders=(parent,),
            attempts=(attempt,),
            trades=(_trade(trade_id="T-1", attempt_id="A-1", quantity=400),),
            corporate_actions=(),
        )

    ExecutionLedger(
        parent_orders=(_parent(status=ParentOrderStatus.OPEN),),
        attempts=(
            _attempt(
                attempt_id="A-BLOCKED",
                requested=1_000,
                filled=0,
                status=AttemptStatus.BLOCKED,
            ),
            _attempt(
                attempt_id="A-INVALID",
                attempt_number=2,
                requested=1_000,
                filled=0,
                status=AttemptStatus.INVALID,
            ),
        ),
        trades=(),
        corporate_actions=(),
    )


def test_parent_cannot_have_attempts_after_it_is_filled():
    with pytest.raises(ValueError, match="FILLED parent cannot have later attempts"):
        ExecutionLedger(
            parent_orders=(
                _parent(filled=1_000, remaining=0, status=ParentOrderStatus.FILLED),
            ),
            attempts=(
                _attempt(
                    attempt_id="A-1",
                    requested=1_000,
                    filled=1_000,
                    status=AttemptStatus.FILLED,
                ),
                _attempt(
                    attempt_id="A-2",
                    attempt_number=2,
                    requested=1_000,
                    filled=0,
                    status=AttemptStatus.BLOCKED,
                ),
            ),
            trades=(_trade(trade_id="T-1", attempt_id="A-1", quantity=1_000),),
            corporate_actions=(),
        )


def test_multi_parent_attempt_fill_rate_is_reported_by_order_id_not_global_scalar():
    ledger = ExecutionLedger(
        parent_orders=(
            _parent(order_id="O-1", filled=500, status=ParentOrderStatus.PARTIALLY_FILLED),
            _parent(order_id="O-2", filled=250, status=ParentOrderStatus.PARTIALLY_FILLED),
        ),
        attempts=(
            _attempt(
                attempt_id="A-1",
                order_id="O-1",
                requested=1_000,
                filled=500,
                status=AttemptStatus.PARTIALLY_FILLED,
            ),
            _attempt(
                attempt_id="A-2",
                order_id="O-2",
                requested=500,
                filled=250,
                status=AttemptStatus.PARTIALLY_FILLED,
            ),
        ),
        trades=(
            _trade(trade_id="T-1", attempt_id="A-1", order_id="O-1", quantity=500),
            _trade(trade_id="T-2", attempt_id="A-2", order_id="O-2", quantity=250),
        ),
        corporate_actions=(),
    )

    diagnostics = compute_attempt_diagnostics(ledger)

    assert diagnostics["attempt_quantity_fill_rate"] is None
    assert diagnostics["attempt_quantity_fill_rate_by_order_id"] == {
        "O-1": pytest.approx(0.5),
        "O-2": pytest.approx(0.5),
    }


def test_ledger_rejects_quantity_breaks_or_untraceable_trade_facts():
    with pytest.raises(ValueError, match="quantity conservation"):
        _parent(requested=1_000, filled=400, remaining=700)

    parent = _parent(filled=1_000, remaining=0, status=ParentOrderStatus.FILLED)
    attempt = _attempt(attempt_id="A-1", requested=1_000, filled=1_000, status=AttemptStatus.FILLED)

    with pytest.raises(ValueError, match="filled > 0"):
        ExecutedTradeRecord(
            trade_id="T-ZERO",
            attempt_id="A-1",
            order_id="O-1",
            ts_code="510300.SH",
            direction=OrderDirection.BUY,
            quantity=0,
            quantity_basis_id="510300.SH:shares:v1",
            price=10.0,
            notional=0.0,
            commission=0.0,
            explicit_fee=0.0,
            slippage_cost=0.0,
            trade_date="20240102",
        )

    with pytest.raises(ValueError, match="exceeds parent"):
        ExecutionLedger(
            parent_orders=(parent,),
            attempts=(
                attempt,
                _attempt(
                    attempt_id="A-2",
                    attempt_number=2,
                    requested=1,
                    filled=1,
                    status=AttemptStatus.FILLED,
                ),
            ),
            trades=(),
            corporate_actions=(),
        )

    with pytest.raises(ValueError, match="unknown attempt"):
        ExecutionLedger(
            parent_orders=(parent,),
            attempts=(attempt,),
            trades=(
                ExecutedTradeRecord(
                    trade_id="T-UNKNOWN",
                    attempt_id="A-UNKNOWN",
                    order_id="O-1",
                    ts_code="510300.SH",
                    direction=OrderDirection.BUY,
                    quantity=1,
                    quantity_basis_id="510300.SH:shares:v1",
                    price=10.0,
                    notional=10.0,
                    commission=0.0,
                    explicit_fee=0.0,
                    slippage_cost=0.0,
                    trade_date="20240102",
                ),
            ),
            corporate_actions=(),
        )


def test_market_rule_resolver_rejects_unknown_instrument_type_without_defaulting():
    resolver = MarketRuleResolver(
        InMemoryPITMarketRuleSource(
            [
                {
                    "ts_code": "510300.SH",
                    "instrument_type": "domestic_equity_etf",
                    "valid_from": "2024-01-01",
                    "valid_to": None,
                    "known_from": "2024-01-01T00:00:00",
                    "snapshot_version": 7,
                    "revision_id": "r1",
                    "revision_order": 1,
                    "source_record_id": "src-510300-r1",
                    "settlement": "T+1",
                    "lot_size": 100,
                    "tick_size": 0.001,
                    "price_limit_pct": 0.10,
                    "short_allowed": False,
                    "currency": "CNY",
                    "rule_version": "rules-v1",
                },
                {
                    "ts_code": "511990.SH",
                    "instrument_type": "money_market_etf",
                    "valid_from": "2024-01-01",
                    "valid_to": None,
                    "known_from": "2024-01-01T00:00:00",
                    "snapshot_version": 7,
                    "revision_id": "r1",
                    "revision_order": 1,
                    "source_record_id": "src-511990-r1",
                    "settlement": "T+0",
                    "lot_size": 100,
                    "tick_size": 0.001,
                    "price_limit_pct": None,
                    "short_allowed": False,
                    "currency": "CNY",
                    "rule_version": "rules-v1",
                },
            ]
        )
    )

    domestic_rules = resolver.resolve(
        FundInstrumentVersion(
            ts_code="510300.SH",
            instrument_type="domestic_equity_etf",
            version="rules-v1",
        ),
        trade_date="20240102",
        knowledge_cutoff="20240101T000000",
        snapshot_version=7,
        mode=PITQueryMode.AS_WAS_KNOWN,
    )
    money_market_rules = resolver.resolve(
        FundInstrumentVersion(
            ts_code="511990.SH",
            instrument_type="money_market_etf",
            version="rules-v1",
        ),
        trade_date="20240102",
        knowledge_cutoff="20240101T000000",
        snapshot_version=7,
        mode=PITQueryMode.AS_WAS_KNOWN,
    )

    assert domestic_rules.lot_size == 100
    assert domestic_rules.instrument_type == "domestic_equity_etf"
    assert domestic_rules.trade_date == "2024-01-02"
    assert domestic_rules.knowledge_cutoff == "2024-01-01T00:00:00"
    assert domestic_rules.snapshot_version == 7
    assert domestic_rules.source_record_id == "src-510300-r1"
    assert domestic_rules.rule_version == "rules-v1"
    assert money_market_rules.instrument_type == "money_market_etf"
    assert money_market_rules.settlement != domestic_rules.settlement

    with pytest.raises(ValueError, match="trade_date and knowledge_cutoff"):
        resolver.resolve(
            FundInstrumentVersion(
                ts_code="510300.SH",
                instrument_type="domestic_equity_etf",
                version="rules-v1",
            ),
            trade_date="",
            knowledge_cutoff="20240101T000000",
            snapshot_version=7,
            mode=PITQueryMode.AS_WAS_KNOWN,
        )

    with pytest.raises(UnknownExecutionRule, match="UNKNOWN_EXECUTION_RULE"):
        resolver.resolve(
            FundInstrumentVersion(
                ts_code="UNKNOWN.SH",
                instrument_type="levered_crypto_etf",
                version="rules-v1",
            ),
            trade_date="20240102",
            knowledge_cutoff="20240101T000000",
            snapshot_version=7,
            mode=PITQueryMode.AS_WAS_KNOWN,
        )


def test_execution_cost_model_requires_and_passes_versioned_context():
    estimate = ExecutionCostModel().estimate(
        _parent(requested=1_000, filled=250),
        MarketObservation(
            reference_price=10.0,
            trade_date="20240102",
            knowledge_cutoff="20240101T000000",
            rule_version="rules-v1",
        ),
        CostScenario(
            scenario_id="base",
            commission_rate=0.001,
            explicit_fee_rate=0.0001,
            slippage_bps=5.0,
            market_impact_bps=2.0,
        ),
    )

    assert isinstance(estimate, ExecutionCostEstimate)
    assert estimate.trade_date == "20240102"
    assert estimate.knowledge_cutoff == "20240101T000000"
    assert estimate.rule_version == "rules-v1"
    assert estimate.model_version == "execution_cost_model_v1"
    assert estimate.scenario_id == "base"
    assert estimate.commission == pytest.approx(7.5)

    with pytest.raises(ValueError, match="trade_date, knowledge_cutoff, rule_version, and scenario_id"):
        ExecutionCostModel().estimate(
            _parent(),
            MarketObservation(
                reference_price=10.0,
                trade_date="20240102",
                knowledge_cutoff="",
                rule_version="rules-v1",
            ),
            CostScenario(scenario_id="base"),
        )
