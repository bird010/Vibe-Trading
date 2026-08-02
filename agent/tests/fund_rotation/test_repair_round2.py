import math
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import pytest

from backtest.engines.composite import CompositeEngine
from backtest.engines.china_a import ChinaAEngine
from backtest.fund_rotation.orders import AttemptStatus, OrderManager
from backtest.fund_rotation.config import FundRotationConfig
from backtest.fund_rotation.pipeline import (
    PipelineResult,
    _build_execution_context,
    _run_execution_loop,
    _serialize_orders,
)
from backtest.fund_rotation.share_adjustment import adjust_shares_for_factor_change
from src.stockpred.fund_rotation.persistence import RunDirectory
from src.stockpred.fund_rotation.service import FundRotationBacktestService


@pytest.mark.parametrize(
    ("old_factor", "new_factor", "expected"),
    [(1.0, 1.2, 1200), (2.0, 1.0, 500)],
)
def test_share_adjustment_follows_adjusted_close_identity(old_factor, new_factor, expected):
    old_shares = 1000
    new_shares, fractional = adjust_shares_for_factor_change(
        old_shares, old_factor, new_factor,
    )
    # A factor jump implies new_raw = old_raw * old/new.  The restated
    # holding must have identical value before integer cash settlement.
    old_raw = 12.0
    new_raw = old_raw * old_factor / new_factor
    assert new_shares == expected
    assert new_shares * new_raw + fractional * new_raw == pytest.approx(old_shares * old_raw)


def test_noninteger_adjustment_exposes_exact_fraction_for_cash_in_lieu():
    shares, fraction = adjust_shares_for_factor_change(101, 3.0, 4.0)
    assert shares == 134
    assert fraction == pytest.approx(2.0 / 3.0)
    assert shares + fraction == pytest.approx(101 * 4.0 / 3.0)


def test_factor_change_restates_active_residual_order():
    manager = OrderManager()
    manager.create_orders({"510300.SH": -101}, event_id="SIG-1")
    manager.record_attempt("510300.SH", 1, AttemptStatus.PARTIAL)
    manager.adjust_for_factor("510300.SH", 4.0 / 3.0)
    order = manager.get_order("510300.SH")
    assert order is not None
    assert order.requested == -134
    assert order.filled == 1
    assert order.remaining == 133


def test_multiple_factor_adjustments_preserve_attempt_facts_and_current_parent_state():
    manager = OrderManager()
    manager.create_orders({"510300.SH": 1000}, event_id="SIG-1")
    manager.record_attempt(
        "510300.SH", 200, AttemptStatus.PARTIAL,
        {"trade_date": "20240102", "reason": "capacity"},
    )
    original_attempt = dict(manager.get_order("510300.SH").attempts[0])
    manager.adjust_for_factor(
        "510300.SH", 2.0, trade_date="20240103", corporate_action_id="CA-1",
    )
    manager.adjust_for_factor(
        "510300.SH", 0.5, trade_date="20240104", corporate_action_id="CA-2",
    )
    order = manager.get_order("510300.SH")
    assert order.attempts[0] == original_attempt
    assert order.requested == 1000
    assert order.filled == 200
    assert order.remaining == 800
    rows = _serialize_orders(manager)
    assert rows[0]["attempt_filled"] == 200
    assert rows[0]["requested"] == 1000
    assert rows[0]["filled"] == 200
    assert rows[0]["remaining"] == 800
    assert rows[0]["attempt_quantity_basis"] == 1.0
    assert rows[0]["current_quantity_basis"] == 1.0
    assert '"corporate_action_id": "CA-1"' in rows[0]["corporate_action_adjustments"]
    assert '"corporate_action_id": "CA-2"' in rows[0]["corporate_action_adjustments"]


@pytest.mark.parametrize(
    ("symbol", "raw_size", "price", "direction"),
    [
        ("000001.SZ", 1234.5, 10.0, 1),
        ("BTC-USDT", 1.234567, 60000.0, -1),
        ("EUR/USD", 123456.0, 1.08, 1),
        ("ESZ4", 8.7, 5000.0, -1),
    ],
)
def test_composite_market_hooks_are_routed_by_explicit_symbol(
    symbol, raw_size, price, direction,
):
    codes = ["EUR/USD", "000001.SZ", "ESZ4", "BTC-USDT"]
    engine = CompositeEngine({"initial_cash": 1_000_000}, list(reversed(codes)))
    rule = engine._rule_for(symbol)
    expected_size = rule.round_size(symbol, raw_size, price)
    expected_slippage = rule.apply_slippage(symbol, price, direction)
    expected_commission = rule.calc_commission(symbol, max(expected_size, 1), price, direction, True)

    assert engine.round_size(symbol, raw_size, price) == expected_size
    assert engine.apply_slippage(symbol, price, direction) == pytest.approx(expected_slippage)
    assert engine.calc_commission(
        symbol, max(expected_size, 1), price, direction, True,
    ) == pytest.approx(expected_commission)
    assert math.isfinite(engine.capital) and engine.capital == 1_000_000


def test_composite_explicit_hooks_are_safe_under_concurrent_interleaving():
    symbols = ["000001.SZ", "BTC-USDT", "EUR/USD", "ESZ4"]
    prices = {"000001.SZ": 10.0, "BTC-USDT": 60_000.0, "EUR/USD": 1.08, "ESZ4": 5_000.0}
    engine = CompositeEngine({"initial_cash": 1_000_000}, symbols)

    def quote(symbol):
        price = prices[symbol]
        return (
            symbol,
            engine.apply_slippage(symbol, price, 1),
            engine.calc_commission(symbol, 2, price, 1, True),
        )

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(quote, symbols * 25))
    by_symbol = {}
    for symbol, slipped, commission in results:
        current = (slipped, commission)
        by_symbol.setdefault(symbol, current)
        assert current == by_symbol[symbol]


def test_composite_hook_exception_does_not_poison_next_symbol(monkeypatch):
    engine = CompositeEngine({"initial_cash": 1_000_000}, ["BTC-USDT", "EUR/USD"])
    crypto = engine._rule_for("BTC-USDT")
    monkeypatch.setattr(crypto, "apply_slippage", lambda *args: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError, match="boom"):
        engine.apply_slippage("BTC-USDT", 60_000.0, 1)
    expected = engine._rule_for("EUR/USD").apply_slippage("EUR/USD", 1.08, 1)
    assert engine.apply_slippage("EUR/USD", 1.08, 1) == expected


def test_mixed_market_long_short_rebalance_is_order_independent_and_conserves_capital():
    symbols = ["000001.SZ", "BTC-USDT", "EUR/USD", "ESZ4"]
    timestamp = pd.Timestamp("2024-01-03")
    prices = {"000001.SZ": 10.0, "BTC-USDT": 50_000.0, "EUR/USD": 1.1, "ESZ4": 5_000.0}
    weights = {"000001.SZ": 0.10, "BTC-USDT": -0.10, "EUR/USD": -0.10, "ESZ4": -0.10}

    def run(code_order):
        engine = CompositeEngine({"initial_cash": 1_000_000}, code_order)
        bars = {
            code: pd.DataFrame([{
                "open": price, "high": price * 1.01, "low": price * 0.99,
                "close": price, "volume": 1_000_000.0,
            }], index=[timestamp])
            for code, price in prices.items()
        }
        close = pd.DataFrame([prices], index=[timestamp])
        targets = pd.DataFrame([weights], index=[timestamp])
        engine._bar_idx = 0
        engine._rebalance_portfolio(
            timestamp, bars, close, targets, code_order, engine.initial_capital,
        )
        return engine

    first = run(symbols)
    second = run(list(reversed(symbols)))
    assert first.capital >= 0 and second.capital >= 0
    assert first.capital == pytest.approx(second.capital)
    assert {
        code: (position.direction, position.size, position.entry_price)
        for code, position in first.positions.items()
    } == pytest.approx({
        code: (position.direction, position.size, position.entry_price)
        for code, position in second.positions.items()
    })
    first_equity = first._calc_equity(pd.DataFrame([prices], index=[timestamp]), timestamp)
    assert math.isfinite(first_equity)
    assert first_equity <= first.initial_capital


def test_china_a_target_weight_base_golden_is_exact():
    """Lock the BASE single-symbol contract to exact pre-existing economics."""
    engine = ChinaAEngine({"initial_cash": 100_000, "slippage": 0.001})
    timestamp = pd.Timestamp("2024-01-03")
    bar = pd.Series({"open": 10.0, "high": 10.1, "low": 9.9, "close": 10.0}, name=timestamp)
    data = {"000001.SZ": pd.DataFrame([bar], index=[timestamp])}
    close = pd.DataFrame({"000001.SZ": [10.0]}, index=[timestamp])
    targets = pd.DataFrame({"000001.SZ": [0.5]}, index=[timestamp])
    engine._bar_idx = 0
    engine._rebalance_portfolio(timestamp, data, close, targets, ["000001.SZ"], 100_000)

    position = engine.positions["000001.SZ"]
    expected_price = 10.01
    expected_size = 4_900
    expected_commission = max(expected_size * expected_price * 0.00025, 5.0) + (
        expected_size * expected_price * 0.00001
    )
    assert position.size == expected_size
    assert position.entry_price == pytest.approx(expected_price, abs=1e-12)
    assert position.entry_commission == pytest.approx(expected_commission, abs=1e-12)
    assert engine.capital == pytest.approx(
        100_000 - (expected_size * expected_price + expected_commission), abs=1e-9,
    )


def _executable_market():
    dates = pd.bdate_range("2024-01-02", periods=16).strftime("%Y%m%d")
    daily = pd.DataFrame([
        {
            "ts_code": "510300.SH", "trade_date": date, "open": 10.0,
            "high": 10.1, "low": 9.9, "close": 10.0, "pre_close": 10.0,
            "vol": 1_000_000, "amount": 10_000_000.0,
        }
        for date in dates
    ])
    adj = pd.DataFrame([
        {"ts_code": "510300.SH", "trade_date": date, "adj_factor": 1.0}
        for date in dates
    ])
    return dates, daily, adj


def test_other_fee_rate_reaches_execution_and_holdings_are_auditable():
    dates, daily, adj = _executable_market()

    def execute(other_fee_rate):
        result = PipelineResult(weekly_targets={dates[10]: {"510300.SH": 0.5}})
        config = FundRotationConfig(
            k=1, top_n=1, initial_capital=100_000,
            adv_min_observations=2, other_fee_rate=other_fee_rate,
        )
        ctx = _build_execution_context(daily, adj, config)
        _run_execution_loop(result, config, ctx)
        return result

    base = execute(0.0)
    charged = execute(0.001)
    base_fill = next(event for event in base.trade_events if event["filled"] > 0)
    charged_fill = next(event for event in charged.trade_events if event["filled"] > 0)
    assert charged_fill["commission"] - base_fill["commission"] == pytest.approx(
        charged_fill["filled"] * charged_fill["price"] * 0.001,
    )
    holding = next(
        row for snapshot in charged.positions_history for row in snapshot["holdings"]
    )
    assert {
        "quantity", "mark_price", "market_value", "target_weight", "actual_weight",
        "adj_factor", "stale_days",
    } <= set(holding)
    assert {"cash", "signal_cash", "execution_failure_cash"} <= set(charged.positions_history[0])


def test_real_stale_close_is_restated_across_multiple_fund_adjustments():
    dates = ["20231229", "20240102", "20240103", "20240104", "20240105"]
    factors = [1.0, 1.0, 2.0, 1.0, 2.0]
    opens = [100.0, 100.0, 50.0, 100.0, 50.0]
    closes = [100.0, 100.0, None, None, 55.0]
    daily = pd.DataFrame([
        {
            "ts_code": "510300.SH", "trade_date": date,
            "open": open_price, "close": close_price,
            "high": open_price * 1.01, "low": open_price * 0.99,
            "pre_close": open_price, "vol": 1_000_000, "amount": 100_000_000.0,
        }
        for date, open_price, close_price in zip(dates, opens, closes)
    ])
    adj = pd.DataFrame([
        {"ts_code": "510300.SH", "trade_date": date, "adj_factor": factor}
        for date, factor in zip(dates, factors)
    ])
    result = PipelineResult(weekly_targets={"20240101": {"510300.SH": 0.1}})

    config = FundRotationConfig(
        k=1, top_n=1, initial_capital=100_000,
        adv_min_observations=1, max_participation_rate=1.0,
    )
    ctx = _build_execution_context(daily, adj, config)
    _run_execution_loop(result, config, ctx)

    by_date = {snapshot["trade_date"]: snapshot for snapshot in result.positions_history}
    first = by_date["20240102"]["holdings"][0]
    factor_up = by_date["20240103"]["holdings"][0]
    factor_down = by_date["20240104"]["holdings"][0]
    fresh_close = by_date["20240105"]["holdings"][0]
    assert (first["quantity"], first["mark_price"], first["market_value"]) == (100, 100.0, 10_000.0)
    assert (factor_up["quantity"], factor_up["mark_price"], factor_up["market_value"]) == (200, 50.0, 10_000.0)
    assert factor_up["stale_days"] == 1
    assert factor_up["last_valid_close_date"] == "20240102"
    assert (factor_down["quantity"], factor_down["mark_price"], factor_down["market_value"]) == (100, 100.0, 10_000.0)
    assert factor_down["stale_days"] == 2
    assert (fresh_close["quantity"], fresh_close["mark_price"], fresh_close["market_value"]) == (200, 55.0, 11_000.0)
    assert fresh_close["stale_days"] == 0
    assert fresh_close["last_valid_close_date"] == "20240105"

    adjustments = [
        event for event in result.trade_events
        if event.get("event_type") == "CORPORATE_ACTION"
    ]
    assert [(event["last_close_before"], event["last_close_after"]) for event in adjustments] == [
        (100.0, 50.0), (50.0, 100.0), (100.0, 50.0),
    ]
    assert all(event["last_valid_close_date"] == "20240102" for event in adjustments[:2])


def test_real_new_position_without_close_uses_fill_as_valuation_anchor():
    daily = pd.DataFrame([
        {
            "ts_code": "510300.SH", "trade_date": "20231229",
            "open": 100.0, "close": 100.0, "high": 101.0, "low": 99.0,
            "pre_close": 100.0, "vol": 1_000_000, "amount": 100_000_000.0,
        },
        {
            "ts_code": "510300.SH", "trade_date": "20240102",
            "open": 120.0, "close": None, "high": 121.0, "low": 119.0,
            "pre_close": 100.0, "vol": 1_000_000, "amount": 120_000_000.0,
        },
    ])
    adj = pd.DataFrame([
        {"ts_code": "510300.SH", "trade_date": date, "adj_factor": 1.0}
        for date in ("20231229", "20240102")
    ])
    result = PipelineResult(weekly_targets={"20240101": {"510300.SH": 0.5}})

    config = FundRotationConfig(
        k=1, top_n=1, initial_capital=100_000,
        adv_min_observations=1, max_participation_rate=1.0,
    )
    ctx = _build_execution_context(daily, adj, config)
    _run_execution_loop(result, config, ctx)

    snapshot = next(
        row for row in result.positions_history if row["trade_date"] == "20240102"
    )
    holding = snapshot["holdings"][0]
    fill = next(
        event for event in result.trade_events
        if event.get("trade_date") == "20240102" and event.get("filled", 0) > 0
    )
    assert holding["mark_price"] == pytest.approx(fill["price"])
    assert holding["market_value"] == pytest.approx(
        holding["quantity"] * fill["price"]
    )
    assert holding["valuation_anchor_date"] == "20240102"
    assert holding["valuation_anchor_source"] == "execution_price"
    assert holding["last_valid_close_date"] == ""
    assert result.executed_equity.loc["20240102"] > 0.99


def test_state_write_failure_cannot_publish_manifest(tmp_path, monkeypatch):
    svc = FundRotationBacktestService(tmp_path)
    run_id = "fault-state"
    run_dir = RunDirectory(tmp_path, run_id)
    run_dir.ensure()
    params = {"k": 1, "top_n": 1}

    def fake_pipeline(config, fund_daily, fund_adj, dim_fund, stage_callback):
        for stage in (
            "PREPARING_RETURNS", "CLUSTERING", "GENERATING_TARGETS",
            "EXECUTING", "COMPUTING_BENCHMARKS",
        ):
            stage_callback(stage)
        return PipelineResult()

    monkeypatch.setattr(svc, "_load_data", lambda config: (
        pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), {"datasets": {}},
    ))
    monkeypatch.setattr("backtest.fund_rotation.pipeline.run_signal_pipeline", fake_pipeline)
    original_write_state = RunDirectory.write_state

    def fail_succeeded(self, state):
        if state.get("stage") == "SUCCEEDED":
            raise OSError("injected state failure")
        return original_write_state(self, state)

    monkeypatch.setattr(RunDirectory, "write_state", fail_succeeded)
    svc._execute_run(run_id, FundRotationConfig(k=1, top_n=1), params)
    assert not (run_dir.path / "manifest.json").exists()
    assert run_dir.read_state()["stage"] == "FAILED"
