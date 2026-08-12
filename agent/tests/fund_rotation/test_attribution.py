import math

import pytest

from backtest.fund_rotation.attribution import (
    AccountDayInput,
    CashAttributionEvent,
    CorporateAction,
    Fill,
    Position,
    PricePoint,
    VariantSnapshot,
    AttributionContractError,
    classify_cash_events,
    compute_accounting_day,
    compute_concentration_metrics,
    compute_drawdown_episodes,
    compute_execution_drag,
    compute_execution_ladder_effects,
    compute_regime_attribution,
    compute_strategy_component_effects,
    reconcile_attribution,
)


def test_accounting_day_uses_executed_price_once_for_cash_nav_and_pnl_bridge():
    """Catches double-counted slippage or fees that do not enter cash at fill time."""
    result = compute_accounting_day(
        AccountDayInput(
            begin_cash=1_000.0,
            begin_positions=(Position("A", 100.0, 10.0),),
            end_positions=(Position("A", 80.0, 11.0), Position("B", 10.0, 21.0)),
            prices={
                "A": PricePoint(prior_close=10.0, open_price=10.5, close_price=11.0),
                "B": PricePoint(prior_close=20.0, open_price=20.0, close_price=21.0),
            },
            fills=(
                Fill("A", quantity=-20.0, executed_price=12.1, reference_price=12.0, commission=2.0, other_fee=1.0),
                Fill("B", quantity=10.0, executed_price=20.2, reference_price=20.0, commission=1.0, other_fee=1.0),
            ),
            cash_income=5.0,
        )
    )

    assert result.accounting_contract_version == "daily_accounting_v1"
    assert result.ending_cash == pytest.approx(1_040.0)
    assert result.ending_nav == pytest.approx(2_130.0)
    assert result.actual_nav_change == pytest.approx(130.0)

    assert result.pnl_components["overnight_pnl"] == pytest.approx(50.0)
    assert result.pnl_components["holding_intraday_pnl"] == pytest.approx(50.0)
    assert result.pnl_components["trade_day_pnl"] == pytest.approx(30.0)
    assert result.pnl_components["income"] == pytest.approx(5.0)
    assert result.pnl_components["fees"] == pytest.approx(-5.0)
    assert result.pnl_components["corporate_action_economic_effect"] == pytest.approx(0.0)
    assert result.reconciliation.residual == pytest.approx(0.0)
    assert result.quality_status == "OK"


def test_corporate_action_unit_conversion_preserves_economic_value_without_income():
    """Catches split/unit conversion being reported as ordinary income or alpha."""
    result = compute_accounting_day(
        AccountDayInput(
            begin_cash=0.0,
            begin_positions=(Position("A", 100.0, 10.0),),
            end_positions=(Position("A", 200.0, 5.0),),
            prices={"A": PricePoint(prior_close=5.0, open_price=5.0, close_price=5.0)},
            corporate_actions=(CorporateAction("A", pre_quantity=100.0, pre_price=10.0, post_quantity=200.0, post_price=5.0),),
        )
    )

    assert result.corporate_action_economic_effects["A"] == pytest.approx(0.0)
    assert result.pnl_components["corporate_action_economic_effect"] == pytest.approx(0.0)
    assert result.pnl_components["income"] == pytest.approx(0.0)
    assert result.ending_nav == pytest.approx(1_000.0)
    assert result.actual_nav_change == pytest.approx(0.0)


def test_cash_in_lieu_enters_real_cash_ledger_without_economic_effect_or_residual():
    """Catches fractional-share cash being omitted from real cash/NAV accounting."""
    result = compute_accounting_day(
        AccountDayInput(
            begin_cash=0.0,
            begin_positions=(Position("A", 10.0, 10.0),),
            end_positions=(Position("A", 9.0, 10.0),),
            prices={"A": PricePoint(prior_close=10.0, open_price=10.0, close_price=10.0)},
            corporate_actions=(
                CorporateAction("A", pre_quantity=10.0, pre_price=10.0, post_quantity=9.0, post_price=10.0, cash_in_lieu=10.0),
            ),
        )
    )

    assert result.ending_cash == pytest.approx(10.0)
    assert result.ending_nav == pytest.approx(100.0)
    assert result.actual_nav_change == pytest.approx(0.0)
    assert result.corporate_action_economic_effects["A"] == pytest.approx(0.0)
    assert result.pnl_components["corporate_action_economic_effect"] == pytest.approx(0.0)
    assert result.reconciliation.residual == pytest.approx(0.0)
    assert result.quality_status == "OK"


def test_missing_open_price_degrades_explicitly_and_missing_close_invalidates_asset_day():
    """Catches fabricated open prices and silent stale-price degradation."""
    degraded = compute_accounting_day(
        AccountDayInput(
            begin_cash=0.0,
            begin_positions=(Position("A", 100.0, 10.0),),
            end_positions=(Position("A", 100.0, 12.0),),
            prices={"A": PricePoint(prior_close=10.0, open_price=None, close_price=12.0)},
        )
    )

    assert degraded.pnl_components["holding_close_to_close_pnl"] == pytest.approx(200.0)
    assert degraded.pnl_components["overnight_pnl"] == pytest.approx(0.0)
    assert degraded.pnl_components["holding_intraday_pnl"] == pytest.approx(0.0)
    assert "DEGRADED_OPEN_PRICE_UNAVAILABLE:A" in degraded.quality_flags

    invalid = compute_accounting_day(
        AccountDayInput(
            begin_cash=0.0,
            begin_positions=(Position("A", 100.0, 10.0),),
            end_positions=(Position("A", 100.0, 10.0),),
            prices={"A": PricePoint(prior_close=10.0, open_price=10.0, close_price=None)},
        )
    )

    assert invalid.quality_status == "INVALID"
    assert "INVALID_CLOSE_PRICE_UNAVAILABLE:A" in invalid.quality_flags


def test_missing_open_price_degrades_only_that_asset_and_keeps_other_asset_pnl():
    """Catches one missing open price suppressing attribution for unrelated assets."""
    result = compute_accounting_day(
        AccountDayInput(
            begin_cash=0.0,
            begin_positions=(Position("A", 100.0, 10.0), Position("B", 50.0, 20.0)),
            end_positions=(Position("A", 100.0, 12.0), Position("B", 50.0, 22.0)),
            prices={
                "A": PricePoint(prior_close=10.0, open_price=None, close_price=12.0),
                "B": PricePoint(prior_close=20.0, open_price=21.0, close_price=22.0),
            },
        )
    )

    assert result.pnl_components["holding_close_to_close_pnl"] == pytest.approx(200.0)
    assert result.pnl_components["overnight_pnl"] == pytest.approx(50.0)
    assert result.pnl_components["holding_intraday_pnl"] == pytest.approx(50.0)
    assert result.reconciliation.residual == pytest.approx(0.0)
    assert "DEGRADED_OPEN_PRICE_UNAVAILABLE:A" in result.quality_flags
    assert "DEGRADED_OPEN_PRICE_UNAVAILABLE:B" not in result.quality_flags


def test_reconciliation_failure_gate_rejects_publishable_attribution():
    """Catches residual being hidden in an 'other' bucket."""
    ok = reconcile_attribution(actual_nav_change=10.0, attributed_components={"market": 9.99, "fees": 0.01}, tolerance=1e-9)
    failed = reconcile_attribution(actual_nav_change=10.0, attributed_components={"market": 9.0}, tolerance=0.01)

    assert ok.publishable is True
    assert ok.status == "OK"
    assert failed.publishable is False
    assert failed.status == "ATTRIBUTION_RECONCILIATION_FAILED"
    assert failed.residual == pytest.approx(1.0)


def test_execution_effect_and_drag_are_opposites_and_drag_is_not_clipped():
    """Catches effect/drag sign confusion and clipping favorable execution to zero."""
    adverse = compute_execution_drag(actual_executed_account_return=0.01, reference_price_account_return=0.015)
    favorable = compute_execution_drag(actual_executed_account_return=0.02, reference_price_account_return=0.015)

    assert adverse["execution_effect_return"] == pytest.approx(-0.005)
    assert adverse["execution_drag_return"] == pytest.approx(0.005)
    assert favorable["execution_effect_return"] == pytest.approx(0.005)
    assert favorable["execution_drag_return"] == pytest.approx(-0.005)


def test_strategy_component_chain_outputs_prefixed_effects_and_enforces_fairness_identity():
    """Catches ambiguous Return(Sn) fields and unfair counterfactual deltas."""
    identity = {
        "pit_universe": "u1",
        "snapshot": "snap1",
        "calendar": "c1",
        "signal_dates": "sig1",
        "rules": "rules1",
        "costs": "costs1",
        "initial_nav": 1_000_000,
        "oos_fold": "fold1",
        "seed": 7,
    }
    variants = {
        "S0": VariantSnapshot("S0", ideal_target_return=0.000, executable_return=0.000, identity=identity, declared_differences=()),
        "S1": VariantSnapshot("S1", ideal_target_return=0.020, executable_return=0.018, identity=identity, declared_differences=("momentum",)),
        "S2": VariantSnapshot("S2", ideal_target_return=0.030, executable_return=0.025, identity=identity, declared_differences=("clustering",)),
        "S3": VariantSnapshot("S3", ideal_target_return=0.025, executable_return=0.021, identity=identity, declared_differences=("representative_etf",)),
        "S4": VariantSnapshot("S4", ideal_target_return=0.027, executable_return=0.022, identity=identity, declared_differences=("quality_selection",)),
        "S5": VariantSnapshot("S5", ideal_target_return=0.040, executable_return=0.033, identity=identity, declared_differences=("weighting",)),
        "S6": VariantSnapshot("S6", ideal_target_return=0.035, executable_return=0.031, identity=identity, declared_differences=("risk",)),
    }

    effects = compute_strategy_component_effects(variants)

    assert effects.chain == ("S0", "S1", "S2", "S3", "S4", "S5", "S6")
    assert effects.effects["ideal_component_effect_S2"] == pytest.approx(0.010)
    assert effects.effects["executable_component_effect_S2"] == pytest.approx(0.007)
    assert effects.effects["ideal_weighting_effect"] == pytest.approx(0.013)
    assert effects.effects["executable_risk_effect"] == pytest.approx(-0.002)
    assert all(key.startswith(("ideal_", "executable_")) for key in effects.effects)
    assert not any(key.startswith("Return(") for key in effects.effects)

    unfair_identity = dict(identity, costs="changed-costs")
    unfair = dict(variants)
    unfair["S2"] = VariantSnapshot("S2", 0.030, 0.025, unfair_identity, declared_differences=("clustering",))
    with pytest.raises(AttributionContractError, match="shared identity"):
        compute_strategy_component_effects(unfair)

    missing_declaration = dict(variants)
    missing_declaration["S2"] = VariantSnapshot("S2", 0.030, 0.025, identity, declared_differences=())
    with pytest.raises(AttributionContractError, match="declared differences"):
        compute_strategy_component_effects(missing_declaration)


def test_execution_ladder_uses_fixed_x0_x5_targets_and_reports_effect_and_drag():
    """Catches variable strategy targets or missing X-ladder incremental drag."""
    identity = {"strategy_target_hash": "target-v1", "rule_version": "r1", "cost_model_version": "c1"}
    ladder = {
        "X0": VariantSnapshot("X0", ideal_target_return=0.020, executable_return=0.020, identity=identity),
        "X1": VariantSnapshot("X1", ideal_target_return=0.020, executable_return=0.018, identity=identity, declared_differences=("tradability_filter",)),
        "X2": VariantSnapshot("X2", ideal_target_return=0.020, executable_return=0.017, identity=identity, declared_differences=("limit_and_suspend_filter",)),
        "X3": VariantSnapshot("X3", ideal_target_return=0.020, executable_return=0.019, identity=identity, declared_differences=("lot_rounding",)),
        "X4": VariantSnapshot("X4", ideal_target_return=0.020, executable_return=0.016, identity=identity, declared_differences=("capacity",)),
        "X5": VariantSnapshot("X5", ideal_target_return=0.020, executable_return=0.014, identity=identity, declared_differences=("fees_and_slippage",)),
    }

    effects = compute_execution_ladder_effects(ladder)

    assert effects.chain == ("X0", "X1", "X2", "X3", "X4", "X5")
    assert effects.effects["cumulative_execution_effect_X5"] == pytest.approx(-0.006)
    assert effects.effects["incremental_execution_effect_X3"] == pytest.approx(0.002)
    assert effects.effects["incremental_execution_drag_X3"] == pytest.approx(-0.002)

    changed_target = dict(ladder)
    changed_target["X3"] = VariantSnapshot(
        "X3", ideal_target_return=0.020, executable_return=0.019, identity=dict(identity, strategy_target_hash="target-v2")
    )
    with pytest.raises(AttributionContractError, match="strategy target"):
        compute_execution_ladder_effects(changed_target)

    missing_declaration = dict(ladder)
    missing_declaration["X3"] = VariantSnapshot("X3", 0.020, 0.019, identity, declared_differences=())
    with pytest.raises(AttributionContractError, match="declared differences"):
        compute_execution_ladder_effects(missing_declaration)

    unexpected_declaration = dict(ladder)
    unexpected_declaration["X3"] = VariantSnapshot("X3", 0.020, 0.019, identity, declared_differences=("slippage",))
    with pytest.raises(AttributionContractError, match="declared differences"):
        compute_execution_ladder_effects(unexpected_declaration)


def test_cash_drawdown_regime_and_concentration_base_contracts():
    """Catches collapsed cash reasons, tradable post-hoc regimes, and missing concentration warnings."""
    cash = classify_cash_events((
        CashAttributionEvent(100.0, reason="momentum_threshold"),
        CashAttributionEvent(50.0, reason="blocked_order"),
        CashAttributionEvent(25.0, reason="capacity"),
    ))
    assert cash["intentional_cash"] == pytest.approx(100.0)
    assert cash["unintentional_cash"] == pytest.approx(75.0)

    drawdowns = compute_drawdown_episodes(
        equity=[("2024-01-01", 100.0), ("2024-01-02", 120.0), ("2024-01-03", 90.0), ("2024-01-04", 110.0), ("2024-01-05", 125.0)],
        component_contributions={
            "asset_selection": {"2024-01-03": -18.0},
            "execution_drag": {"2024-01-03": -7.0},
            "cash_effect": {"2024-01-03": -5.0},
        },
    )
    assert len(drawdowns) == 1
    assert drawdowns[0].peak == "2024-01-02"
    assert drawdowns[0].trough == "2024-01-03"
    assert drawdowns[0].recovery == "2024-01-05"
    assert drawdowns[0].max_drawdown == pytest.approx(-0.25)
    assert drawdowns[0].component_contributions["execution_drag"] == pytest.approx(-7.0)

    regimes = compute_regime_attribution(
        period_returns={"2024-01-01": 0.01, "2024-01-02": -0.02},
        labels={"2024-01-01": "Bull", "2024-01-02": "Bear"},
        regime_kind="POST_HOC_ANALYTICS_ONLY",
    )
    assert regimes["Bull"].regime_kind == "POST_HOC_ANALYTICS_ONLY"
    assert regimes["Bull"].can_drive_trading is False
    with pytest.raises(AttributionContractError, match="post-hoc"):
        compute_regime_attribution(
            {"2024-01-01": 0.01},
            {"2024-01-01": "Bull"},
            regime_kind="POST_HOC_ANALYTICS_ONLY",
            can_drive_trading=True,
        )

    concentration = compute_concentration_metrics(
        {"2024-01": 80.0, "2024-02": 10.0, "2024-03": 10.0},
        top_n=1,
        warning_threshold=0.6,
    )
    assert concentration["top_contribution_share"] == pytest.approx(0.8)
    assert concentration["quality_flag"] == "RETURN_CONCENTRATION_WARNING"
    assert math.isfinite(concentration["top_contribution_share"])
