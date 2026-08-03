"""Phase 3 Task 2 — internal clustering & quality gates tests.

Covers clustering determinism/label normalization, pairwise exclusion, gate
PASS/WARN/REJECT semantics with stable codes/thresholds/actuals/affected
codes, and the critical distinction: a gate REJECT emits SET_TARGETS-to-cash
with quality INVALID (sub-run still succeeds), while a decision action
INVALID terminates the sub-run (design §7.3/§9).
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from backtest.fund_rotation.contracts import DecisionKind, QualityStatus
from backtest.fund_rotation.strategies.correlation_representative.clustering import (
    correlation_cluster,
    normalize_cluster_labels,
)
from backtest.fund_rotation.strategies.correlation_representative.config import (
    CorrelationRepresentativeConfig,
)
from backtest.fund_rotation.strategies.correlation_representative.gates import (
    GateStatus,
    cluster_quality_rejection_decision,
    evaluate_cluster_gates,
)


def _block_returns(
    blocks: dict[str, list[str]], n_weeks: int = 60, seed: int = 11,
) -> pd.DataFrame:
    """Weekly returns with a shared factor per block (high within-block corr)."""
    rng = np.random.default_rng(seed)
    data: dict[str, np.ndarray] = {}
    for _, members in sorted(blocks.items()):
        factor = rng.normal(0.0, 0.02, n_weeks)
        for code in members:
            noise = rng.normal(0.0, 0.002, n_weeks)
            data[code] = factor + noise
    index = [f"2023{w:04d}" for w in range(1, n_weeks + 1)]
    return pd.DataFrame(data, index=index)


# ── clustering determinism & normalization ──

class TestClustering:
    def test_same_input_gives_identical_normalized_labels(self):
        window = _block_returns({"a": ["A1", "A2", "A3"], "b": ["B1", "B2", "B3"]})
        first = correlation_cluster(
            window, list(window.columns), k=2, min_pairwise_weeks=10,
        )
        second = correlation_cluster(
            window, list(window.columns), k=2, min_pairwise_weeks=10,
        )
        assert first.clusters == second.clusters
        assert set(first.clusters) == set(window.columns)

    def test_labels_are_normalized_by_size_then_member(self):
        # The largest cluster gets label 1 regardless of raw fcluster labels.
        raw = {"X": 7, "Y": 7, "Z": 7, "W": 2}
        normalized = normalize_cluster_labels(raw)
        assert {normalized["X"], normalized["Y"], normalized["Z"]} == {1}
        assert normalized["W"] == 2

    def test_identical_assets_still_deterministic_and_complete(self):
        rng = np.random.default_rng(3)
        series = rng.normal(0.0, 0.02, 40)
        window = pd.DataFrame(
            {c: series for c in ("C1", "C2", "C3", "C4")},
            index=[f"2023{w:04d}" for w in range(1, 41)],
        )
        outcome = correlation_cluster(
            window, list(window.columns), k=2, min_pairwise_weeks=10,
        )
        assert set(outcome.clusters) == {"C1", "C2", "C3", "C4"}
        again = correlation_cluster(
            window, list(window.columns), k=2, min_pairwise_weeks=10,
        )
        assert outcome.clusters == again.clusters

    def test_block_structure_is_recovered(self):
        window = _block_returns(
            {"a": ["A1", "A2", "A3"], "b": ["B1", "B2", "B3"]},
        )
        outcome = correlation_cluster(
            window, list(window.columns), k=2, min_pairwise_weeks=10,
        )
        assert outcome.clusters["A1"] == outcome.clusters["A2"] == outcome.clusters["A3"]
        assert outcome.clusters["B1"] == outcome.clusters["B2"] == outcome.clusters["B3"]
        assert outcome.clusters["A1"] != outcome.clusters["B1"]

    def test_incomplete_pairs_are_iteratively_excluded_with_reason(self):
        window = _block_returns({"a": ["A1", "A2", "A3"], "b": ["B1", "B2", "B3"]})
        # A3 has almost no overlapping history -> invalid pairs with everyone.
        window["A3"] = np.nan
        window.iloc[0:3, window.columns.get_loc("A3")] = 0.01
        outcome = correlation_cluster(
            window, list(window.columns), k=2, min_pairwise_weeks=10,
        )
        assert "A3" not in outcome.kept_codes
        assert "A3" not in outcome.clusters
        excluded_codes = [r.ts_code for r in outcome.pairwise_excluded]
        assert "A3" in excluded_codes
        record = next(r for r in outcome.pairwise_excluded if r.ts_code == "A3")
        assert record.reason.value == "pairwise_exclusion"
        assert record.details  # exclusion reason recorded

    def test_fragmented_clusters_produce_singletons(self):
        rng = np.random.default_rng(5)
        data = {
            f"S{i}": rng.normal(0.0, 0.02, 50) for i in range(6)
        }
        window = pd.DataFrame(data, index=[f"2023{w:04d}" for w in range(1, 51)])
        outcome = correlation_cluster(
            window, list(window.columns), k=6, min_pairwise_weeks=10,
        )
        assert len(set(outcome.clusters.values())) == 6


# ── quality gates ──

class TestGates:
    def test_balanced_clusters_pass(self):
        clusters = {"A": 1, "B": 1, "C": 2, "D": 2, "E": 3, "F": 3, "G": 4, "H": 4}
        evaluation = evaluate_cluster_gates(clusters, CorrelationRepresentativeConfig())
        assert evaluation.overall is GateStatus.PASS
        assert all(r.status is GateStatus.PASS for r in evaluation.results)

    def test_single_big_cluster_rejects_with_code_thresholds_and_affected(self):
        clusters = {f"C{i}": 1 for i in range(9)}
        clusters["OUT"] = 2
        evaluation = evaluate_cluster_gates(clusters, CorrelationRepresentativeConfig())
        assert evaluation.overall is GateStatus.REJECT

        share = next(r for r in evaluation.results if r.code == "MAX_CLUSTER_SHARE")
        assert share.status is GateStatus.REJECT
        assert share.actual == pytest.approx(0.9)
        assert share.warn_threshold == 0.50
        assert share.reject_threshold == 0.80
        assert set(share.affected_codes) == {f"C{i}" for i in range(9)}

        eff = next(
            (r for r in evaluation.results if r.code == "EFFECTIVE_CLUSTER_COUNT")
        )
        # p=(0.9,0.1) -> N_eff = exp(-(0.9ln0.9+0.1ln0.1)) ≈ 1.384 < 2.5
        assert eff.status is GateStatus.REJECT
        expected_eff = math.exp(-(0.9 * math.log(0.9) + 0.1 * math.log(0.1)))
        assert eff.actual == pytest.approx(expected_eff)
        assert eff.warn_threshold == 4.0
        assert eff.reject_threshold == 2.5

    def test_moderate_dominance_warns_but_does_not_reject(self):
        # p = (0.6, 0.1, 0.1, 0.1, 0.1): share 0.6 in (0.5, 0.8] -> WARN.
        clusters = {f"D{i}": 1 for i in range(6)}
        for i, single in enumerate(("S1", "S2", "S3", "S4")):
            clusters[single] = 2 + i
        evaluation = evaluate_cluster_gates(clusters, CorrelationRepresentativeConfig())
        share = next(r for r in evaluation.results if r.code == "MAX_CLUSTER_SHARE")
        assert share.status is GateStatus.WARN
        eff = next(
            (r for r in evaluation.results if r.code == "EFFECTIVE_CLUSTER_COUNT")
        )
        # N_eff = exp(-(0.6ln0.6 + 4*0.1ln0.1)) ≈ 3.41 -> WARN (< 4.0).
        expected_eff = math.exp(-(0.6 * math.log(0.6) + 4 * 0.1 * math.log(0.1)))
        assert eff.actual == pytest.approx(expected_eff)
        assert eff.status is GateStatus.WARN
        assert evaluation.overall is GateStatus.WARN

    def test_effective_count_warn_band(self):
        # sizes (7,1,1,1) of 10 -> N_eff ≈ 2.56 in [2.5, 4.0) -> WARN.
        clusters = {f"M{i}": 1 for i in range(7)}
        clusters["T1"] = 2
        clusters["T2"] = 3
        clusters["T3"] = 4
        evaluation = evaluate_cluster_gates(clusters, CorrelationRepresentativeConfig())
        eff = next(
            (r for r in evaluation.results if r.code == "EFFECTIVE_CLUSTER_COUNT")
        )
        assert eff.status is GateStatus.WARN
        expected_eff = math.exp(
            -(0.7 * math.log(0.7) + 3 * 0.1 * math.log(0.1))
        )
        assert eff.actual == pytest.approx(expected_eff)
        assert evaluation.overall is GateStatus.WARN

    def test_thresholds_come_from_config_not_hidden_constants(self):
        clusters = {f"C{i}": 1 for i in range(6)}
        clusters["OUT"] = 2
        custom = CorrelationRepresentativeConfig(
            max_cluster_share_warn=0.55, max_cluster_share_reject=0.95,
        )
        evaluation = evaluate_cluster_gates(clusters, custom)
        share = next(r for r in evaluation.results if r.code == "MAX_CLUSTER_SHARE")
        # 6/7 ≈ 0.857: > 0.55 warn, <= 0.95 -> WARN under custom thresholds
        # (would REJECT under defaults 0.5/0.8).
        assert share.status is GateStatus.WARN
        assert share.warn_threshold == 0.55
        assert share.reject_threshold == 0.95

    def test_effective_count_formula_matches_design(self):
        clusters = {"A": 1, "B": 1, "C": 2, "D": 2}
        evaluation = evaluate_cluster_gates(clusters, CorrelationRepresentativeConfig())
        eff = next(
            (r for r in evaluation.results if r.code == "EFFECTIVE_CLUSTER_COUNT")
        )
        expected = math.exp(-(0.5 * math.log(0.5) + 0.5 * math.log(0.5)))
        assert eff.actual == pytest.approx(expected)  # = 2.0


class TestRejectionDecisionSemantics:
    """§9: gate REJECT -> SET_TARGETS-to-cash (run succeeds, quality INVALID);
    §7.3: decision action INVALID -> sub-run terminates as FAILED."""

    def test_rejection_decision_shape(self):
        clusters = {f"C{i}": 1 for i in range(10)}
        evaluation = evaluate_cluster_gates(clusters, CorrelationRepresentativeConfig())
        decision = cluster_quality_rejection_decision(
            signal_date="20230602", decision_id="20230602-x", gates=evaluation,
        )
        assert decision.action is DecisionKind.SET_TARGETS
        assert decision.target_weights == {}
        assert decision.cash_weight == 1.0
        assert decision.reason_code == "CLUSTER_QUALITY_REJECTED"
        assert decision.quality_status is QualityStatus.INVALID
        assert decision.diagnostics["gates"]  # gate evidence preserved

    def test_reject_path_succeeds_with_cash_nav_but_invalid_action_fails(self):
        """Comparison through the real strategy-neutral Runner."""
        from tests.fund_rotation.test_runner import (
            CancellationToken,
            FakeConfig,
            FakeStrategy,
            _evaluation,
            _execution,
            _market_frames,
            _snapshot,
        )
        from backtest.fund_rotation.contracts import TargetWeightDecision
        from backtest.fund_rotation.runner import (
            FundRotationBacktestRunner,
            SubRunStatus,
        )

        class ScriptedSession:
            def __init__(self, scripts):
                self.scripts = scripts

            def scheduled_dates(self, calendar, sim_start, eval_end):
                return tuple(d for d in ("20240112", "20240119")
                             if sim_start <= d <= eval_end)

            def evaluate(self, context):
                return self.scripts[context.signal_date](context)

            def finalize(self):
                from backtest.fund_rotation.contracts import StrategyDiagnostics
                return StrategyDiagnostics()

        def _run_with(scripts):
            fund_daily, fund_adj, dim_fund = _market_frames()
            runner = FundRotationBacktestRunner(fund_daily, fund_adj, dim_fund)
            return runner.run(
                strategy=FakeStrategy(ScriptedSession(scripts)),
                config=FakeConfig(),
                snapshot=_snapshot(),
                evaluation=_evaluation(),
                execution=_execution(),
                cancellation=CancellationToken(),
            )

        rejection = cluster_quality_rejection_decision(
            signal_date="20240112", decision_id="20240112-rej",
            gates=evaluate_cluster_gates(
                {f"C{i}": 1 for i in range(10)},
                CorrelationRepresentativeConfig(),
            ),
        )

        # REJECT path: sub-run SUCCEEDS with full-interval cash NAV.
        reject_result = _run_with({
            "20240112": lambda ctx: rejection,
            "20240119": lambda ctx: TargetWeightDecision(
                decision_id="20240119-hold", signal_date="20240119",
                action=DecisionKind.HOLD_TARGETS,
            ),
        })
        assert reject_result.status is SubRunStatus.SUCCEEDED
        assert reject_result.weekly_targets == {"20240112": {}}
        assert (reject_result.executed_equity == 1.0).all()
        assert reject_result.decisions[0].quality_status is QualityStatus.INVALID

        # INVALID path: the same run shape but with decision action INVALID
        # terminates the sub-run as FAILED.
        invalid_result = _run_with({
            "20240112": lambda ctx: TargetWeightDecision(
                decision_id="20240112-inv", signal_date="20240112",
                action=DecisionKind.INVALID, reason_code="DATA_MISSING",
            ),
            "20240119": lambda ctx: TargetWeightDecision(
                decision_id="20240119-hold", signal_date="20240119",
                action=DecisionKind.HOLD_TARGETS,
            ),
        })
        assert invalid_result.status is SubRunStatus.FAILED
        assert invalid_result.error_code == "DATA_MISSING"
