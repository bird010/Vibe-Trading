"""Causal V2 rotation evidence builders."""

from __future__ import annotations

import pandas as pd

from backtest.fund_rotation.contracts import DecisionKind, TargetWeightDecision
from backtest.fund_rotation.runner import FundRotationRunResult, SubRunStatus
from src.stockpred.fund_rotation.decision_evidence import (
    build_holdings_timeline,
    build_rebalance_evidence,
    build_strategy_evidence,
)


def test_build_holdings_timeline_keeps_cash_and_actual_weight_separate():
    result = build_holdings_timeline(
        positions_history=[
            {
                "trade_date": "20240102",
                "equity": 1000,
                "cash": 500,
                "holdings": [
                    {
                        "ts_code": "510300.SH",
                        "actual_weight": 0.5,
                        "target_weight": 0.8,
                        "market_value": 500,
                    }
                ],
            },
            {
                "trade_date": "20240103",
                "equity": 1000,
                "cash": 400,
                "holdings": [
                    {
                        "ts_code": "510300.SH",
                        "actual_weight": 0.6,
                        "target_weight": 0.8,
                        "market_value": 600,
                    }
                ],
            },
        ],
        decisions=(),
        evaluation_dates=("20240102", "20240103"),
    )

    cash = next(row for row in result["intervals"] if row["ts_code"] == "_CASH")
    etf = next(row for row in result["intervals"] if row["ts_code"] == "510300.SH")
    assert cash["actual_weight"] == 0.45
    assert etf["actual_weight"] == 0.55
    assert etf["target_weight"] == 0.8


def test_build_holdings_timeline_splits_exit_and_reentry():
    result = build_holdings_timeline(
        positions_history=[
            {
                "trade_date": "20240102",
                "equity": 1000,
                "cash": 500,
                "holdings": [{"ts_code": "510300.SH", "actual_weight": 0.5}],
            },
            {"trade_date": "20240103", "equity": 1000, "cash": 1000, "holdings": []},
            {
                "trade_date": "20240104",
                "equity": 1000,
                "cash": 500,
                "holdings": [{"ts_code": "510300.SH", "actual_weight": 0.5}],
            },
        ],
        decisions=(),
        evaluation_dates=("20240102", "20240103", "20240104"),
    )

    intervals = [
        row for row in result["intervals"] if row["ts_code"] == "510300.SH"
    ]
    assert [(row["start_date"], row["end_date"]) for row in intervals] == [
        ("20240102", "20240102"),
        ("20240104", "20240104"),
    ]


def test_build_holdings_timeline_starts_new_position_on_first_observed_date():
    result = build_holdings_timeline(
        positions_history=[
            {
                "trade_date": "20240102",
                "equity": 1000,
                "cash": 1000,
                "holdings": [],
            },
            {
                "trade_date": "20240103",
                "equity": 1000,
                "cash": 500,
                "holdings": [
                    {"ts_code": "159915.SZ", "actual_weight": 0.5}
                ],
            },
        ],
        decisions=(),
        evaluation_dates=("20240102", "20240103"),
    )

    intervals = [
        row for row in result["intervals"] if row["ts_code"] == "159915.SZ"
    ]
    assert [(row["start_date"], row["end_date"]) for row in intervals] == [
        ("20240103", "20240103"),
    ]


def test_build_rebalance_evidence_before_uses_signal_day_account_state():
    decisions = (
        TargetWeightDecision(
            decision_id="d-1",
            signal_date="20240102",
            action=DecisionKind.SET_TARGETS,
            target_weights={"510300.SH": 1.0},
            cash_weight=0.0,
        ),
        TargetWeightDecision(
            decision_id="d-2",
            signal_date="20240105",
            action=DecisionKind.SET_TARGETS,
            target_weights={"159915.SZ": 1.0},
            cash_weight=0.0,
        ),
    )
    result = FundRotationRunResult(
        status=SubRunStatus.SUCCEEDED,
        decisions=decisions,
        weekly_targets={
            "20240102": {"510300.SH": 1.0},
            "20240105": {"159915.SZ": 1.0},
        },
        positions_history=[
            {
                "trade_date": "20240104",
                "equity": 1000,
                "cash": 700,
                "holdings": [{"ts_code": "510300.SH", "actual_weight": 0.3}],
            },
            {
                "trade_date": "20240105",
                "equity": 1000,
                "cash": 0,
                "holdings": [{"ts_code": "510300.SH", "actual_weight": 0.34}],
            },
        ],
    )

    evidence = build_rebalance_evidence(
        result=result,
        evaluation_dates=("20240102", "20240104", "20240105"),
        strategy_metadata={"ranking_metric": "momentum"},
        decision_trace=(),
    )
    bundle = evidence["items"]["20240105"]
    assert bundle["before"]["as_of_date"] == "20240105"
    assert bundle["before"]["weights"] == {"510300.SH": 0.34}
    assert bundle["after_target"]["as_of_signal_date"] == "20240105"
    assert bundle["after_target"]["weights"] == {"159915.SZ": 1.0}


def test_build_rebalance_evidence_before_uses_actual_account_state_not_previous_target():
    decisions = (
        TargetWeightDecision(
            decision_id="d-1",
            signal_date="20240105",
            action=DecisionKind.SET_TARGETS,
            target_weights={"510300.SH": 1.0},
            cash_weight=0.0,
        ),
        TargetWeightDecision(
            decision_id="d-2",
            signal_date="20240112",
            action=DecisionKind.SET_TARGETS,
            target_weights={"159915.SZ": 1.0},
            cash_weight=0.0,
        ),
    )
    result = FundRotationRunResult(
        status=SubRunStatus.SUCCEEDED,
        decisions=decisions,
        positions_history=[
            {
                "trade_date": "20240111",
                "equity": 1000,
                "cash": 300,
                "holdings": [{"ts_code": "510300.SH", "actual_weight": 0.7}],
            }
        ],
    )

    evidence = build_rebalance_evidence(
        result=result,
        evaluation_dates=("20240105", "20240111", "20240112"),
        strategy_metadata={},
        decision_trace=(),
    )

    bundle = evidence["items"]["20240112"]
    assert bundle["before"]["as_of_date"] == "20240111"
    assert bundle["before"]["weights"] == {"510300.SH": 0.7, "_CASH": 0.3}
    assert bundle["before"]["weights"] != {"510300.SH": 1.0}
    assert bundle["execution"]["summary"]["target_changed_positions"] == 2
    assert bundle["execution"]["summary"]["required_changed_positions"] == 3
    assert bundle["execution"]["summary"]["actual_changed_positions"] == 0


def test_execution_metrics_use_fills_not_required_target_turnover():
    decision = TargetWeightDecision(
        decision_id="decision-blocked",
        signal_date="20240105",
        action=DecisionKind.SET_TARGETS,
        target_weights={"159915.SZ": 1.0},
        cash_weight=0.0,
    )
    result = FundRotationRunResult(
        status=SubRunStatus.SUCCEEDED,
        decisions=(decision,),
        positions_history=[
            {
                "trade_date": "20240105",
                "equity": 1000,
                "cash": 0,
                "holdings": [{"ts_code": "510300.SH", "actual_weight": 1.0}],
            }
        ],
        orders=[
            {
                "decision_id": "decision-blocked",
                "ts_code": "159915.SZ",
                "status": "BLOCKED",
            }
        ],
        trade_events=[],
    )

    evidence = build_rebalance_evidence(
        result=result,
        evaluation_dates=("20240105", "20240108"),
        strategy_metadata={},
        decision_trace=(),
    )

    summary = evidence["items"]["20240105"]["execution"]["summary"]
    assert summary["required_changed_positions"] == 2
    assert summary["required_turnover"] == 1.0
    assert summary["executed_changed_positions"] == 0
    assert summary["actual_changed_positions"] == 0
    assert summary["execution_turnover"] == 0.0


def test_build_rebalance_evidence_joins_execution_by_decision_id_across_dates():
    decision = TargetWeightDecision(
        decision_id="decision-friday",
        signal_date="20240105",
        action=DecisionKind.SET_TARGETS,
        target_weights={"510300.SH": 1.0},
        cash_weight=0.0,
    )
    result = FundRotationRunResult(
        status=SubRunStatus.SUCCEEDED,
        decisions=(decision,),
        positions_history=[
            {"trade_date": "20240104", "equity": 1000, "cash": 1000, "holdings": []}
        ],
        orders=[
            {"order_id": "o-1", "decision_id": "decision-friday", "created_date": "20240105", "status": "SUBMITTED"},
            {"order_id": "legacy", "signal_date": "20240105", "status": "REJECTED"},
        ],
        trade_events=[
            {"fill_id": "f-1", "decision_id": "decision-friday", "trade_date": "20240108", "status": "FILLED", "commission": 1.5},
            {"fill_id": "wrong-date", "signal_date": "20240105", "trade_date": "20240108", "status": "FILLED"},
        ],
    )

    evidence = build_rebalance_evidence(
        result=result,
        evaluation_dates=("20240104", "20240105", "20240108"),
        strategy_metadata={},
        decision_trace=(),
    )

    execution = evidence["items"]["20240105"]["execution"]
    assert [row["order_id"] for row in execution["orders"]] == ["o-1"]
    assert [row["fill_id"] for row in execution["fills"]] == ["f-1"]
    assert execution["summary"]["filled"] == 1


def test_turnover_uses_one_way_contract_without_counting_cash_as_a_second_leg():
    decision = TargetWeightDecision(
        decision_id="decision-cash-to-etf",
        signal_date="20240105",
        action=DecisionKind.SET_TARGETS,
        target_weights={"159915.SZ": 1.0},
        cash_weight=0.0,
    )
    result = FundRotationRunResult(
        status=SubRunStatus.SUCCEEDED,
        decisions=(decision,),
        positions_history=[
            {"trade_date": "20240105", "equity": 1000, "cash": 1000, "holdings": []}
        ],
        trade_events=[
            {
                "decision_id": "decision-cash-to-etf",
                "trade_date": "20240108",
                "ts_code": "159915.SZ",
                "status": "FILLED",
                "filled": 100,
                "price": 10,
                "notional": 1000,
            }
        ],
    )

    evidence = build_rebalance_evidence(
        result=result,
        evaluation_dates=("20240105", "20240108"),
        strategy_metadata={},
        decision_trace=(),
    )

    summary = evidence["items"]["20240105"]["execution"]["summary"]
    assert summary["target_turnover"] == 0.5
    assert summary["required_turnover"] == 0.5
    assert summary["execution_turnover"] == 0.5


def test_anchor_execution_turnover_uses_initial_capital_when_before_snapshot_is_absent():
    decision = TargetWeightDecision(
        decision_id="anchor",
        signal_date="20240105",
        action=DecisionKind.SET_TARGETS,
        target_weights={"159915.SZ": 1.0},
        cash_weight=0.0,
    )
    result = FundRotationRunResult(
        status=SubRunStatus.SUCCEEDED,
        decisions=(decision,),
        trade_events=[
            {
                "decision_id": "anchor",
                "trade_date": "20240108",
                "ts_code": "159915.SZ",
                "status": "FILLED",
                "filled": 100,
                "price": 10,
                "notional": 1000,
            }
        ],
    )

    evidence = build_rebalance_evidence(
        result=result,
        evaluation_dates=("20240105", "20240108"),
        strategy_metadata={},
        decision_trace=(),
        initial_capital=1000.0,
    )

    assert evidence["items"]["20240105"]["execution"]["summary"]["execution_turnover"] == 0.5


def test_holdings_timeline_anchor_marker_uses_initial_capital():
    decision = TargetWeightDecision(
        decision_id="timeline-anchor",
        signal_date="20240105",
        action=DecisionKind.SET_TARGETS,
        target_weights={"159915.SZ": 1.0},
        cash_weight=0.0,
    )

    timeline = build_holdings_timeline(
        positions_history=[],
        decisions=(decision,),
        evaluation_dates=("20240105", "20240108"),
        trade_events=(
            {
                "decision_id": "timeline-anchor",
                "trade_date": "20240108",
                "ts_code": "159915.SZ",
                "status": "FILLED",
                "filled": 100,
                "price": 10,
                "notional": 1000,
            },
        ),
        initial_capital=1000.0,
    )

    assert timeline["rebalance_markers"][0]["execution_turnover"] == 0.5


def test_build_strategy_evidence_keeps_runtime_metrics_and_normalizes_benchmark():
    result = FundRotationRunResult(
        status=SubRunStatus.SUCCEEDED,
        benchmark_equity={
            "buy_hold": pd.Series([100.0, 105.0], index=["20240102", "20240103"])
        },
    )
    evidence = build_strategy_evidence(
        result=result,
        run_id="run-1",
        decision_trace=(
            {
                "signal_date": "20240102",
                "candidates": [
                    {
                        "ts_code": "510300.SH",
                        "primary_metric": {
                            "id": "cluster_momentum",
                            "label": "Momentum",
                            "value": 0.82,
                        },
                    }
                ],
            },
        ),
    )

    instrument = evidence["instruments"]["510300.SH"]
    assert instrument["indicators"][0]["formula_id"] == "strategy.cluster_momentum"
    assert instrument["indicators"][0]["points"] == [{"date": "20240102", "value": 0.82}]
    assert instrument["benchmark"]["normalized_price"][-1] == {"date": "20240103", "value": 1.05}


def test_build_strategy_evidence_v2_publishes_generic_representative_score_only():
    result = FundRotationRunResult(status=SubRunStatus.SUCCEEDED)
    evidence = build_strategy_evidence(
        result=result,
        run_id="run-v2",
        decision_trace=(
            {
                "signal_date": "20240105",
                "candidates": [
                    {
                        "ts_code": "510300.SH",
                        "stages": {
                            "cluster_id": 3,
                            "cluster_representative": True,
                            "ranking_eligible": True,
                            "rank": 2,
                            "portfolio_selected": True,
                        },
                        "score": {
                            "id": "primary_score",
                            "label": "策略得分（周频）",
                            "value": 0.081,
                            "frequency": "WEEKLY",
                            "direction": "HIGHER_BETTER",
                            "scope": "CLUSTER",
                            "subject_id": "cluster:3",
                            "model_id": "cluster_momentum",
                            "model_version": "1",
                            "components": {"momentum": 0.081},
                        },
                    },
                    {
                        "ts_code": "159915.SZ",
                        "stages": {
                            "cluster_id": 3,
                            "cluster_representative": False,
                            "ranking_eligible": False,
                            "rank": None,
                        },
                        "score": None,
                        "primary_metric": {"id": "cluster_momentum", "label": "Momentum", "value": 0.2},
                    },
                ],
            },
            {
                "signal_date": "20240112",
                "candidates": [
                    {
                        "ts_code": "510300.SH",
                        "stages": {
                            "cluster_id": 3,
                            "cluster_representative": True,
                            "ranking_eligible": True,
                            "rank": 1,
                            "portfolio_selected": True,
                        },
                        "score": {
                            "id": "primary_score",
                            "label": "策略得分（周频）",
                            "value": 0.093,
                            "frequency": "WEEKLY",
                            "direction": "HIGHER_BETTER",
                            "scope": "CLUSTER",
                            "subject_id": "cluster:3",
                            "model_id": "cluster_momentum",
                            "model_version": "1",
                            "components": {"momentum": 0.093},
                        },
                    }
                ],
            },
        ),
    )

    instrument = evidence["instruments"]["510300.SH"]
    assert evidence["schema_version"] == "2"
    assert instrument["score"]["scope"] == "CLUSTER"
    assert "subject_id" not in instrument["score"]
    assert instrument["score"]["points"] == [
        {"date": "20240105", "value": 0.081, "eligible": True, "rank": 2, "selected": True, "subject_id": "cluster:3"},
        {"date": "20240112", "value": 0.093, "eligible": True, "rank": 1, "selected": True, "subject_id": "cluster:3"},
    ]
    assert "159915.SZ" not in evidence["instruments"]
    assert instrument["score_components"]["momentum"]["points"][0]["value"] == 0.081


def test_build_strategy_evidence_keeps_finite_ineligible_score_point():
    result = FundRotationRunResult(status=SubRunStatus.SUCCEEDED)
    evidence = build_strategy_evidence(
        result=result,
        run_id="run-ineligible",
        decision_trace=(
            {
                "signal_date": "20240112",
                "candidates": [
                    {
                        "ts_code": "510300.SH",
                        "stages": {
                            "cluster_id": 7,
                            "cluster_representative": True,
                            "ranking_eligible": False,
                            "rank": None,
                            "portfolio_selected": False,
                        },
                        "score": {
                            "id": "primary_score",
                            "label": "Strategy Score",
                            "value": -0.03,
                            "eligible": False,
                            "frequency": "WEEKLY",
                            "direction": "HIGHER_BETTER",
                            "scope": "CLUSTER",
                            "subject_id": "cluster:7",
                            "model_id": "cluster_momentum",
                            "model_version": "1",
                            "components": {"momentum": -0.03},
                        },
                    }
                ],
            },
        ),
    )

    point = evidence["instruments"]["510300.SH"]["score"]["points"][0]
    assert point == {
        "date": "20240112",
        "value": -0.03,
        "eligible": False,
        "rank": None,
        "selected": False,
        "subject_id": "cluster:7",
    }


def test_build_strategy_evidence_keeps_subject_identity_at_point_level_when_cluster_changes():
    result = FundRotationRunResult(status=SubRunStatus.SUCCEEDED)
    evidence = build_strategy_evidence(
        result=result,
        run_id="run-cluster-change",
        decision_trace=(
            {
                "signal_date": "20240105",
                "candidates": [
                    {
                        "ts_code": "510300.SH",
                        "stages": {"ranking_eligible": True, "rank": 1, "portfolio_selected": True},
                        "score": {
                            "id": "primary_score",
                            "label": "Cluster Momentum",
                            "value": 0.1,
                            "eligible": True,
                            "subject_id": "cluster:1",
                        },
                    }
                ],
            },
            {
                "signal_date": "20240112",
                "candidates": [
                    {
                        "ts_code": "510300.SH",
                        "stages": {"ranking_eligible": True, "rank": 1, "portfolio_selected": True},
                        "score": {
                            "id": "primary_score",
                            "label": "Cluster Momentum",
                            "value": 0.2,
                            "eligible": True,
                            "subject_id": "cluster:2",
                        },
                    }
                ],
            },
        ),
    )

    score = evidence["instruments"]["510300.SH"]["score"]
    assert "subject_id" not in score
    assert [point["subject_id"] for point in score["points"]] == ["cluster:1", "cluster:2"]
