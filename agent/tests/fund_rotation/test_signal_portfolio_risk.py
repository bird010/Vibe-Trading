from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from backtest.fund_rotation.signal_portfolio_risk import (
    AssetSelection,
    CandidateQuality,
    ClusterCoveragePolicy,
    HeldCluster,
    MomentumPolicy,
    PortfolioPolicy,
    RepresentativePolicy,
    RepresentativeState,
    RiskPolicy,
    SelectionPolicy,
    SelectionState,
    aggregate_momentum_rank_average,
    aggregate_momentum_zscore_weighted,
    apply_hysteresis,
    apply_risk_layer,
    build_portfolio_weights,
    compute_cluster_coverage,
    compute_momentum_families,
    run_decision_pipeline,
    select_representative_quality,
)


def test_cluster_coverage_counts_eligible_members_and_rejects_insufficient_window():
    weeks = pd.date_range("2026-01-02", periods=4, freq="W-FRI")
    members = [f"ETF{i:02d}" for i in range(20)]
    weekly_returns = pd.DataFrame(np.nan, index=weeks, columns=members)
    weekly_returns.loc[:, "ETF00"] = [0.01, 0.02, 0.01, 0.03]
    weekly_returns.loc[:, "ETF19"] = [np.nan, np.nan, 0.04, 0.01]
    eligible_by_week = {
        weeks[0]: set(members[:19]),  # ETF19 not listed yet; excluded from denominator.
        weeks[1]: set(members[:19]),
        weeks[2]: set(members),
        weeks[3]: set(members),
    }

    report = compute_cluster_coverage(
        weekly_returns=weekly_returns,
        cluster_members={7: members},
        eligible_by_week=eligible_by_week,
        policy=ClusterCoveragePolicy(
            min_weekly_coverage=0.20,
            max_low_coverage_weeks=1,
            minimum_valid_members=2,
        ),
    )[7]

    assert report.valid_member_counts == (1, 1, 2, 2)
    assert report.eligible_member_counts == (19, 19, 20, 20)
    assert report.coverage_ratios == pytest.approx((1 / 19, 1 / 19, 0.10, 0.10))
    assert report.min_weekly_coverage == pytest.approx(1 / 19)
    assert report.mean_weekly_coverage == pytest.approx((1 / 19 + 1 / 19 + 0.1 + 0.1) / 4)
    assert report.low_coverage_week_count == 4
    assert report.is_available is False
    assert report.reason_codes == ("INSUFFICIENT_CLUSTER_COVERAGE",)


def test_momentum_families_include_baseline_skip_month_and_finite_risk_adjusted_scores():
    clusters = [101, 102, 103]
    weeks = pd.date_range("2025-01-03", periods=60, freq="W-FRI")
    returns = pd.DataFrame(
        {
            101: np.full(60, 0.020),
            102: np.r_[np.full(20, 0.006), np.full(40, 0.012)],
            103: np.zeros(60),  # zero volatility must not create inf risk-adjusted momentum.
        },
        index=weeks,
    )

    result = compute_momentum_families(
        returns,
        MomentumPolicy(
            single_window=4,
            families=("single_window", "1M", "3M", "6M", "12M", "6-1", "12-1", "risk_adjusted"),
        ),
    )

    assert set(result.scores_by_family) == {
        "single_window",
        "1M",
        "3M",
        "6M",
        "12M",
        "6-1",
        "12-1",
        "risk_adjusted",
    }
    assert result.scores_by_family["single_window"] == result.scores_by_family["1M"]
    assert result.scores_by_family["6-1"][101] == pytest.approx(
        float(np.prod(1.0 + returns[101].iloc[-26:-4]) - 1.0)
    )
    assert result.scores_by_family["12-1"][101] == pytest.approx(
        float(np.prod(1.0 + returns[101].iloc[-52:-4]) - 1.0)
    )
    assert result.scores_by_family["risk_adjusted"][103] is None
    assert result.reason_codes_by_family["risk_adjusted"][103] == ("UNAVAILABLE_MOMENTUM",)

    average_rank = aggregate_momentum_rank_average(result.scores_by_family)
    assert list(average_rank) == [101, 102, 103]

    weighted = aggregate_momentum_zscore_weighted(
        result.scores_by_family,
        weights={"1M": 0.25, "3M": 0.25, "6M": 0.25, "12M": 0.25},
    )
    assert list(weighted) == [101, 102, 103]
    assert all(math.isfinite(score) for score in weighted.values())


def test_unavailable_momentum_is_null_reasoned_and_excluded_from_rankings():
    weeks = pd.date_range("2026-01-02", periods=4, freq="W-FRI")
    returns = pd.DataFrame(
        {
            "missing": [np.nan, np.nan, np.nan, np.nan],
            "flat": [0.0, 0.0, 0.0, 0.0],
            "winner": [0.01, 0.01, 0.01, 0.01],
        },
        index=weeks,
    )

    result = compute_momentum_families(
        returns,
        MomentumPolicy(single_window=4, families=("single_window", "risk_adjusted")),
    )

    assert result.scores_by_family["single_window"]["missing"] is None
    assert result.reason_codes_by_family["single_window"]["missing"] == ("UNAVAILABLE_MOMENTUM",)


    assert result.scores_by_family["risk_adjusted"]["flat"] is None
    assert result.reason_codes_by_family["risk_adjusted"]["flat"] == ("UNAVAILABLE_MOMENTUM",)

    average_rank = aggregate_momentum_rank_average(result.scores_by_family)
    assert list(average_rank) == ["winner", "flat"]
    assert "missing" not in average_rank

    weighted = aggregate_momentum_zscore_weighted(
        result.scores_by_family,
        weights={"single_window": 0.5, "risk_adjusted": 0.5},
    )
    assert list(weighted) == ["winner", "flat"]
    assert "missing" not in weighted


def test_nonfinite_momentum_is_unavailable_not_zero():
    result = compute_momentum_families(
        pd.DataFrame({"overflow": [1e308, 1e308]}),
        MomentumPolicy(single_window=2),
    )

    assert result.scores_by_family["single_window"]["overflow"] is None
    assert result.reason_codes_by_family["single_window"]["overflow"] == (
        "UNAVAILABLE_MOMENTUM",
    )


def test_hysteresis_respects_buffer_min_holding_score_improvement_cycle_reset_and_hard_failure():
    policy = SelectionPolicy(
        top_n=2,
        exit_buffer=1,
        minimum_holding_weeks=3,
        minimum_score_improvement=0.05,
    )
    state = SelectionState(
        cycle_id="cycle-a",
        holdings={
            "A": HeldCluster(weeks_held=5, entry_score=0.50),
            "B": HeldCluster(weeks_held=1, entry_score=0.45),
        },
    )

    # A is rank 3: inside Top-(N+buffer), so it stays. C beats B only by 0.02,
    # below the improvement threshold, so B stays despite being just outside Top-N.
    first = apply_hysteresis(
        {"C": 0.47, "B": 0.45, "A": 0.44, "D": 0.10},
        state=state,
        cycle_id="cycle-a",
        policy=policy,
    )
    assert first.selected_clusters == ("B", "A")
    assert first.reason_codes["A"] == ("HELD_EXIT_BUFFER",)
    assert first.reason_codes["B"] == ("HELD_MIN_HOLDING",)

    # Hard failures bypass the minimum holding period.
    failed = apply_hysteresis(
        {"C": 0.60, "A": 0.55, "B": 0.54},
        state=first.next_state,
        cycle_id="cycle-a",
        policy=policy,
        hard_failures={"B"},
    )
    assert failed.selected_clusters == ("C", "A")
    assert failed.reason_codes["B"] == ("HARD_FAILURE_EXIT",)

    # A new clustering cycle does not inherit old label state.
    reset = apply_hysteresis(
        {"B": 0.70, "A": 0.69, "C": 0.68},
        state=first.next_state,
        cycle_id="cycle-b",
        policy=policy,
    )
    assert reset.selected_clusters == ("B", "A")
    assert reset.next_state.holdings["B"].weeks_held == 1
    assert reset.reason_codes["B"] == ("NEW_CLUSTERING_CYCLE", "ENTRY_TOP_N")


def test_hysteresis_keeps_existing_holding_when_replacement_improvement_is_insufficient():
    result = apply_hysteresis(
        {"NEW": 0.52, "HELD": 0.50},
        state=SelectionState(
            cycle_id="cycle-a",
            holdings={"HELD": HeldCluster(weeks_held=5, entry_score=0.50)},
        ),
        cycle_id="cycle-a",
        policy=SelectionPolicy(
            top_n=1,
            exit_buffer=0,
            minimum_holding_weeks=1,
            minimum_score_improvement=0.05,
        ),
    )

    assert result.selected_clusters == ("HELD",)
    assert result.next_state.holdings["HELD"].weeks_held == 6
    assert result.reason_codes["NEW"] == ("SKIP_INSUFFICIENT_SCORE_IMPROVEMENT",)
    assert result.reason_codes["HELD"] == ("HELD_INSUFFICIENT_REPLACEMENT_IMPROVEMENT",)


def test_hysteresis_filters_hard_failures_when_clustering_cycle_changes():
    result = apply_hysteresis(
        {"FAILED": 0.90, "KEEP": 0.80, "NEXT": 0.70},
        state=SelectionState(
            cycle_id="cycle-a",
            holdings={"OLD": HeldCluster(weeks_held=5, entry_score=0.50)},
        ),
        cycle_id="cycle-b",
        policy=SelectionPolicy(top_n=2),
        hard_failures={"FAILED"},
    )

    assert result.selected_clusters == ("KEEP", "NEXT")
    assert "FAILED" not in result.next_state.holdings
    assert result.reason_codes["FAILED"] == ("HARD_FAILURE_EXIT",)


def test_representative_quality_scores_lock_until_hard_failure_or_confirmed_deterioration():
    policy = RepresentativePolicy(exit_score=0.60, deterioration_periods=2)
    candidates = [
        CandidateQuality("LOCKED", representativeness=0.70, liquidity=0.50, listing_age=0.60),
        CandidateQuality("BETTER", representativeness=0.90, liquidity=0.90, listing_age=0.90),
    ]

    locked = select_representative_quality(
        candidates,
        state=RepresentativeState(current="LOCKED"),
        policy=policy,
    )
    assert locked.selected == "LOCKED"
    assert locked.lock_maintained is True
    assert locked.scores["BETTER"] > locked.scores["LOCKED"]

    deteriorating_once = select_representative_quality(
        [CandidateQuality("LOCKED", 0.30, 0.50, 0.40), candidates[1]],
        state=locked.next_state,
        policy=policy,
    )
    assert deteriorating_once.selected == "LOCKED"
    assert deteriorating_once.reason_codes == ("QUALITY_LOCK_HELD", "QUALITY_DETERIORATION_OBSERVED")

    deteriorating_twice = select_representative_quality(
        [CandidateQuality("LOCKED", 0.30, 0.50, 0.40), candidates[1]],
        state=deteriorating_once.next_state,
        policy=policy,
    )
    assert deteriorating_twice.selected == "BETTER"
    assert "QUALITY_DETERIORATION_EXIT" in deteriorating_twice.reason_codes

    hard_failure = select_representative_quality(
        [CandidateQuality("LOCKED", 0.90, 0.90, 0.90, hard_failure=True), candidates[1]],
        state=RepresentativeState(current="LOCKED"),
        policy=policy,
    )
    assert hard_failure.selected == "BETTER"
    assert "HARD_FAILURE_REPLACED" in hard_failure.reason_codes


def test_portfolio_weighting_equal_inverse_vol_constraints_cash_and_turnover():
    assets = (
        AssetSelection("AAA", cluster_id="c1", asset_class="equity"),
        AssetSelection("BBB", cluster_id="c2", asset_class="bond"),
    )

    disabled = build_portfolio_weights(
        assets,
        policy=PortfolioPolicy(enabled=False, method="inverse_volatility", minimum_cash_weight=0.10),
        volatilities={"AAA": 0.10, "BBB": 0.30},
    )
    assert disabled.status == "VALID"
    assert disabled.weights == {"AAA": 0.45, "BBB": 0.45}
    assert disabled.cash_weight == pytest.approx(0.10)
    assert disabled.reason_codes == ("PORTFOLIO_WEIGHTING_DISABLED_EQUAL_WEIGHT",)

    inverse = build_portfolio_weights(
        assets,
        policy=PortfolioPolicy(
            enabled=True,
            method="inverse_volatility",
            max_etf_weight=0.80,
            max_cluster_weight=0.80,
            max_asset_class_weight=0.80,
            minimum_cash_weight=0.10,
            maximum_one_way_turnover_per_rebalance=1.0,
        ),
        volatilities={"AAA": 0.10, "BBB": 0.30},
        previous_weights={"AAA": 0.45, "BBB": 0.45},
    )
    assert inverse.status == "VALID"
    assert inverse.weights["AAA"] == pytest.approx(0.675)
    assert inverse.weights["BBB"] == pytest.approx(0.225)
    assert inverse.cash_weight == pytest.approx(0.10)
    assert inverse.one_way_turnover == pytest.approx(0.225)

    invalid = build_portfolio_weights(
        assets,
        policy=PortfolioPolicy(
            enabled=True,
            method="equal_weight",
            max_etf_weight=0.40,
            max_cluster_weight=0.90,
            max_asset_class_weight=0.90,
            minimum_cash_weight=0.10,
        ),
    )
    assert invalid.status == "INVALID"
    assert "MAX_ETF_WEIGHT_CONSTRAINT" in invalid.reason_codes


def test_equal_weight_by_cluster_slot_reports_vacant_fixed_slots_as_cash():
    result = build_portfolio_weights(
        (
            AssetSelection("AAA", cluster_id="c1", asset_class="equity"),
            AssetSelection("BBB", cluster_id="c2", asset_class="equity"),
        ),
        policy=PortfolioPolicy(
            enabled=True,
            method="equal_weight_by_cluster_slot",
            target_cluster_slots=3,
            minimum_cash_weight=0.0,
        ),
    )

    assert result.status == "VALID"
    assert result.weights == {"AAA": pytest.approx(1 / 3), "BBB": pytest.approx(1 / 3)}
    assert result.cash_weight == pytest.approx(1 / 3)
    assert result.reason_codes == ("VACANT_CLUSTER_SLOT_CASH",)


def test_risk_layer_scales_exposure_independently_and_disabled_mode_is_identity():
    raw_weights = {"AAA": 0.50, "BBB": 0.30}
    disabled = apply_risk_layer(
        raw_weights,
        upstream_cash=0.20,
        policy=RiskPolicy(enabled=False, target_volatility=0.05),
        estimated_portfolio_volatility=0.20,
        upstream_reasons=("NO_REPRESENTATIVE_CASH",),
    )
    assert disabled.gross_exposure == 1.0
    assert disabled.scaled_weights == raw_weights
    assert disabled.cash_weight == 0.20
    assert disabled.reason_codes == ("NO_REPRESENTATIVE_CASH", "RISK_LAYER_DISABLED_IDENTITY")

    risk_off = apply_risk_layer(
        raw_weights,
        upstream_cash=0.20,
        policy=RiskPolicy(
            enabled=True,
            target_volatility=0.10,
            min_gross_exposure=0.20,
            max_gross_exposure=1.0,
            regime_exposure={"RISK_ON": 1.0, "NEUTRAL": 0.75, "RISK_OFF": 0.50},
        ),
        estimated_portfolio_volatility=0.25,
        regime="RISK_OFF",
    )
    assert risk_off.gross_exposure == pytest.approx(0.40)
    assert risk_off.scaled_weights == {"AAA": 0.20, "BBB": 0.12}
    assert risk_off.cash_weight == pytest.approx(0.68)
    assert risk_off.reason_codes == ("VOL_TARGET_SCALED", "REGIME_RISK_OFF")

    conservative = apply_risk_layer(
        raw_weights,
        upstream_cash=0.20,
        policy=RiskPolicy(enabled=True, conservative_gross_exposure=0.35),
        regime=None,
    )
    assert conservative.gross_exposure == 0.35
    assert conservative.reason_codes == ("RISK_STATE_UNAVAILABLE",)


def test_pipeline_records_stage_inputs_outputs_policy_versions_and_invalid_constraints():
    decision = run_decision_pipeline(
        raw_signal_scores={"c1": 0.30, "c2": 0.20},
        coverage_available={"c1": True, "c2": True},
        representatives={"c1": "AAA", "c2": "BBB"},
        asset_metadata={
            "AAA": {"cluster_id": "c1", "asset_class": "equity"},
            "BBB": {"cluster_id": "c2", "asset_class": "equity"},
        },
        selection_policy=SelectionPolicy(top_n=2, exit_buffer=0),
        portfolio_policy=PortfolioPolicy(
            enabled=True,
            method="equal_weight",
            max_etf_weight=0.40,
            max_cluster_weight=0.90,
            max_asset_class_weight=0.90,
            minimum_cash_weight=0.10,
        ),
        risk_policy=RiskPolicy(enabled=False),
        policy_versions={
            "signal": "sig-v1",
            "coverage": "cov-v1",
            "selection": "sel-v1",
            "representative": "rep-v1",
            "portfolio": "port-v1",
            "risk": "risk-v1",
        },
    )

    assert decision.status == "INVALID"
    assert "MAX_ETF_WEIGHT_CONSTRAINT" in decision.reason_codes
    assert [record.stage for record in decision.stage_records] == [
        "raw_signal_scores",
        "coverage_filtered_scores",
        "selected_clusters",
        "selected_representatives",
        "raw_portfolio_weights",
        "risk_scaled_weights",
        "execution_targets",
    ]
    assert decision.stage_records[0].input == {"c1": 0.30, "c2": 0.20}
    assert decision.stage_records[0].policy_version == "sig-v1"
    portfolio_stage = next(r for r in decision.stage_records if r.stage == "raw_portfolio_weights")
    assert portfolio_stage.policy_version == "port-v1"
    assert portfolio_stage.reason_codes == ("MAX_ETF_WEIGHT_CONSTRAINT",)


def test_pipeline_excludes_unavailable_momentum_from_selection_with_reason():
    decision = run_decision_pipeline(
        raw_signal_scores={"unavailable": np.nan, "winner": 0.20},
        coverage_available={"unavailable": True, "winner": True},
        representatives={"unavailable": "BAD", "winner": "GOOD"},
        asset_metadata={
            "BAD": {"cluster_id": "unavailable", "asset_class": "equity"},
            "GOOD": {"cluster_id": "winner", "asset_class": "equity"},
        },
        selection_policy=SelectionPolicy(top_n=1, exit_buffer=0),
        portfolio_policy=PortfolioPolicy(enabled=True, method="equal_weight"),
        risk_policy=RiskPolicy(enabled=False),
        policy_versions={
            "signal": "sig-v1",
            "coverage": "cov-v1",
            "selection": "sel-v1",
            "representative": "rep-v1",
            "portfolio": "port-v1",
            "risk": "risk-v1",
        },
    )

    assert decision.reason_codes == ("UNAVAILABLE_MOMENTUM",)
    selected_stage = next(r for r in decision.stage_records if r.stage == "selected_clusters")
    assert selected_stage.output == ("winner",)
    assert selected_stage.reason_codes == ("ENTRY_TOP_N",)


def test_pipeline_keeps_missing_representative_slot_as_cash_without_renormalizing_weights():
    decision = run_decision_pipeline(
        raw_signal_scores={"c1": 0.30, "c2": 0.20},
        coverage_available={"c1": True, "c2": True},
        representatives={"c1": "AAA", "c2": None},
        asset_metadata={"AAA": {"cluster_id": "c1", "asset_class": "equity"}},
        selection_policy=SelectionPolicy(top_n=2, exit_buffer=0),
        portfolio_policy=PortfolioPolicy(
            enabled=True,
            method="equal_weight",
            minimum_cash_weight=0.10,
        ),
        risk_policy=RiskPolicy(enabled=False),
        policy_versions={
            "signal": "sig-v1",
            "coverage": "cov-v1",
            "selection": "sel-v1",
            "representative": "rep-v1",
            "portfolio": "port-v1",
            "risk": "risk-v1",
        },
    )

    assert decision.status == "VALID"
    assert decision.reason_codes == ("NO_REPRESENTATIVE_CASH",)
    assert decision.stage_records[-1].output == {
        "status": "VALID",
        "weights": {"AAA": pytest.approx(0.45)},
        "cash_weight": pytest.approx(0.55),
    }
    portfolio_stage = next(r for r in decision.stage_records if r.stage == "raw_portfolio_weights")
    assert portfolio_stage.output == {"AAA": pytest.approx(0.45)}
    assert portfolio_stage.reason_codes == ("NO_REPRESENTATIVE_CASH",)


def test_cluster_slot_pipeline_reserves_vacant_slot_only_once():
    decision = run_decision_pipeline(
        raw_signal_scores={"c1": 0.30, "c2": 0.20},
        coverage_available={"c1": True, "c2": True},
        representatives={"c1": "AAA", "c2": None},
        asset_metadata={"AAA": {"cluster_id": "c1", "asset_class": "equity"}},
        selection_policy=SelectionPolicy(top_n=2, exit_buffer=0),
        portfolio_policy=PortfolioPolicy(
            enabled=True,
            method="equal_weight_by_cluster_slot",
            target_cluster_slots=2,
            minimum_cash_weight=0.10,
        ),
        risk_policy=RiskPolicy(enabled=False),
        policy_versions={
            "signal": "sig-v1", "coverage": "cov-v1", "selection": "sel-v1",
            "representative": "rep-v1", "portfolio": "port-v1", "risk": "risk-v1",
        },
    )

    assert decision.status == "VALID"
    assert decision.stage_records[-1].output["weights"] == {"AAA": pytest.approx(0.45)}
    assert decision.stage_records[-1].output["cash_weight"] == pytest.approx(0.55)


def test_cluster_slot_pipeline_infers_selected_slots_when_policy_omits_count():
    decision = run_decision_pipeline(
        raw_signal_scores={"c1": 0.30, "c2": 0.20},
        coverage_available={"c1": True, "c2": True},
        representatives={"c1": "AAA", "c2": None},
        asset_metadata={"AAA": {"cluster_id": "c1", "asset_class": "equity"}},
        selection_policy=SelectionPolicy(top_n=2, exit_buffer=0),
        portfolio_policy=PortfolioPolicy(enabled=True, method="equal_weight_by_cluster_slot", minimum_cash_weight=0.10),
        risk_policy=RiskPolicy(enabled=False),
        policy_versions={
            "signal": "sig-v1", "coverage": "cov-v1", "selection": "sel-v1",
            "representative": "rep-v1", "portfolio": "port-v1", "risk": "risk-v1",
        },
    )

    assert decision.stage_records[-1].output["weights"] == {"AAA": pytest.approx(0.45)}
