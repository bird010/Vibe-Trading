"""Tests for the cohort engine orchestrator."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from backtest.stockpred.cohort.benchmark import (
    compute_liquidation_matched_benchmark,
    compute_target_horizon_benchmark,
)
from backtest.stockpred.cohort.engine import CohortBacktestConfig, CohortRunner, CohortRunResult, _compute_period_breakdown, _execution_event_row
from backtest.stockpred.cohort.contracts import CohortResult, CohortStatus, ExecutionEvent, TargetSnapshot
from backtest.stockpred.cohort.eligibility import SignalEligibilityGate
from backtest.stockpred.execution.costs import DEFAULT_COST_POLICY
from backtest.stockpred.execution.valuation import ValuationPolicy
from src.stockpred.contracts import ModelSnapshot
from src.stockpred.gateway import StockPredDataGateway
from src.stockpred.snapshot import build_snapshot


class MockGateway:
    """Minimal gateway for testing the cohort engine."""

    def __init__(self, days: int = 40) -> None:
        self._days = days
        self._dates = [f"202411{d:02d}" for d in range(1, 61)]
        self._dates += [f"202501{d:02d}" for d in range(1, min(days + 1, 29))]
        if days > 28:
            self._dates += [f"202502{d:02d}" for d in range(1, days - 27)]

    def trade_dates(self, start: str, end: str) -> list[str]:
        return [d for d in self._dates if start <= d <= end]

    def prices(self, start: str, end: str, codes: list[str]) -> pd.DataFrame:
        dates = self.trade_dates(start, end)
        rows = []
        for code in codes:
            for i, date in enumerate(dates):
                p = 10.0 * (1.005 ** i)
                rows.append({
                    "ts_code": code, "trade_date": date,
                    "open": p, "high": p * 1.02, "low": p * 0.98, "close": p,
                    "vol": 100000.0, "amount": 5000.0,
                    "adj_open": p, "adj_close": p,
                })
        return pd.DataFrame(rows)

    def adjustment_factors(self, start: str, end: str, codes: list[str]) -> pd.DataFrame:
        dates = self.trade_dates(start, end)
        rows = [{"ts_code": c, "trade_date": d, "adj_factor": 1.0} for c in codes for d in dates]
        return pd.DataFrame(rows)

    def stock_limits(self, start: str, end: str, codes: list[str]) -> pd.DataFrame:
        dates = self.trade_dates(start, end)
        rows = []
        for c in codes:
            for d in dates:
                rows.append({"ts_code": c, "trade_date": d, "up_limit": 100.0, "down_limit": 1.0})
        return pd.DataFrame(rows)

    def index_daily(self, code: str, start: str, end: str) -> pd.DataFrame:
        dates = self.trade_dates(start, end)
        rows = []
        for i, d in enumerate(dates):
            p = 100.0 * (1.003 ** i)
            rows.append({"ts_code": code, "trade_date": d, "open": p, "close": p, "adj_open": p})
        return pd.DataFrame(rows)

    def stock_dimension(self) -> pd.DataFrame:
        return pd.DataFrame([
            {"ts_code": "000001.SZ", "name": "Test A", "industry": "Bank", "list_date": "20241101",
             "delist_date": "", "list_status": "L", "exchange": "SZSE", "market": "main"},
            {"ts_code": "000002.SZ", "name": "Test B", "industry": "Tech", "list_date": "20241101",
             "delist_date": "", "list_status": "L", "exchange": "SZSE", "market": "main"},
            {"ts_code": "600001.SH", "name": "Test C", "industry": "Energy", "list_date": "20241101",
             "delist_date": "", "list_status": "L", "exchange": "SSE", "market": "main"},
        ])

    def name_history(self) -> pd.DataFrame:
        return pd.DataFrame([
            {"ts_code": code, "security_name": name, "effective_from": "20200101", "effective_to": "", "ann_date": "20200101", "change_reason": ""}
            for code, name in [("000001.SZ", "Alpha"), ("000002.SZ", "Bravo"), ("600001.SH", "Charlie")]
        ])


class MockStrategy:
    """Strategy that returns fixed scores."""

    def evaluate(self, eval_date: str, panel: dict | None = None) -> pd.DataFrame:
        return pd.DataFrame([
            {"ts_code": "000001.SZ", "score": 10.0},
            {"ts_code": "000002.SZ", "score": 9.0},
            {"ts_code": "600001.SH", "score": 8.0},
        ])


def _config(tmp_path: Path) -> CohortBacktestConfig:
    return CohortBacktestConfig(
        start="20250115",
        end="20250125",
        eval_step=5,
        holding_days=5,
        top_n=2,
        committed_capital_per_cohort=1_000_000.0,
        max_participation=0.05,
        adv_lookback_days=20,
        max_exit_extension_days=10,
        benchmark_code="000300.SH",
        strategy_id="test_strategy",
        strategy_version="a" * 64,
        data_snapshot_id="snap1",
        run_dir=tmp_path,
    )


class TestCohortRunner:
    def test_runner_accepts_lance_backed_total_return_index(
        self,
        tmp_path: Path,
        stockpred_root_factory,
    ):
        mock_gateway = MockGateway(40)
        index_dates = mock_gateway.trade_dates("20250101", "20250128")
        index_root = stockpred_root_factory(
            index_daily_rows={
                "ts_code": ["H00300.CSI"] * len(index_dates),
                "trade_date": index_dates,
                "open": [100.0 * (1.003 ** i) for i in range(len(index_dates))],
                "high": [101.0 * (1.003 ** i) for i in range(len(index_dates))],
                "low": [99.0 * (1.003 ** i) for i in range(len(index_dates))],
                "close": [100.0 * (1.003 ** i) for i in range(len(index_dates))],
                "pct_chg": [0.3] * len(index_dates),
            }
        )
        real_gateway = StockPredDataGateway(
            index_root,
            build_snapshot(
                index_root,
                as_of=datetime(2026, 6, 30, 15, tzinfo=ZoneInfo("Asia/Taipei")),
                model=ModelSnapshot(
                    id="stockpred-graph",
                    version="graph-v1",
                    config_sha256="cfg",
                ),
            ),
        )

        class ProductionIndexGateway(MockGateway):
            def index_daily(self, code: str, start: str, end: str) -> pd.DataFrame:
                return real_gateway.index_daily(code, start, end)

        result = CohortRunner(gateway=ProductionIndexGateway(40), strategy=MockStrategy()).run(
            _config(tmp_path).model_copy(update={"end": "20250115", "top_n": 1, "benchmark_code": "H00300.CSI"})
        )
        assert result.cohort_results[0].status == CohortStatus.LIQUIDATED

    def test_runner_passes_configured_benchmark_code_to_both_helpers(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        observed: dict[str, object] = {}

        def capture_target(**kwargs: object):
            observed["target"] = kwargs.get("benchmark_code")
            return compute_target_horizon_benchmark(**kwargs)

        def capture_liquidation(**kwargs: object):
            observed["liquidation"] = kwargs.get("benchmark_code")
            return compute_liquidation_matched_benchmark(**kwargs)

        monkeypatch.setattr(
            "backtest.stockpred.cohort.engine.compute_target_horizon_benchmark",
            capture_target,
        )
        monkeypatch.setattr(
            "backtest.stockpred.cohort.engine.compute_liquidation_matched_benchmark",
            capture_liquidation,
        )
        monkeypatch.setattr(
            "backtest.stockpred.cohort.engine.publish_cohort_artifacts",
            lambda **_kwargs: "test-version",
        )

        CohortRunner(gateway=MockGateway(40), strategy=MockStrategy()).run(
            _config(Path(".superpowers/sdd")).model_copy(
                update={"end": "20250115", "top_n": 1, "benchmark_code": "H00300.CSI"}
            )
        )

        assert observed == {
            "target": "H00300.CSI",
            "liquidation": "H00300.CSI",
        }
    def test_protocol_config_records_default_stale_valuation_gate(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        captured: dict[str, object] = {}

        def capture_publish(**kwargs: object) -> str:
            captured.update(kwargs["config"])
            return "test-version"

        monkeypatch.setattr("backtest.stockpred.cohort.engine.publish_cohort_artifacts", capture_publish)
        CohortRunner(gateway=MockGateway(40), strategy=MockStrategy()).run(
            _config(tmp_path).model_copy(update={"end": "20250115", "top_n": 1})
        )

        assert captured["quality_gate"] == {"max_stale_valuation_ratio": 0.02}

    def test_empty_target_is_recorded_as_failed_fact(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        def empty_target(_signals: object, *, cohort_id: str, **_kwargs: object) -> TargetSnapshot:
            return TargetSnapshot(
                cohort_id=cohort_id, evaluation_date="20250115", committed_capital=1_000_000.0,
            )

        monkeypatch.setattr("backtest.stockpred.cohort.engine.build_cohort_targets", empty_target)
        monkeypatch.setattr("backtest.stockpred.cohort.engine.publish_cohort_artifacts", lambda **_kwargs: "test-version")

        result = CohortRunner(gateway=MockGateway(40), strategy=MockStrategy()).run(
            _config(tmp_path).model_copy(update={"end": "20250115"})
        )

        assert len(result.cohort_results) == 1
        assert result.cohort_results[0].status == CohortStatus.FAILED_DATA
        assert result.cohort_results[0].evaluation_date == "20250115"
        assert result.cohort_results[0].data_quality["reason"] == "empty_target"

    def test_horizon_stale_valuation_is_preserved_on_result(self, tmp_path: Path):
        class StaleHorizonRunner(CohortRunner):
            def _load_market(self, codes: list[str], config: CohortBacktestConfig, *, start: str | None = None, end: str | None = None) -> pd.DataFrame:
                market = super()._load_market(codes, config, start=start, end=end)
                return market[market["trade_date"] != "20250121"]

        result = StaleHorizonRunner(gateway=MockGateway(40), strategy=MockStrategy()).run(
            _config(tmp_path).model_copy(update={
                "end": "20250115", "top_n": 1, "min_raw_label_coverage": 0.0,
            })
        )

        cohort = result.cohort_results[0]
        assert cohort.uses_stale_valuation
        assert cohort.max_stale_days >= 1

    def test_oversold_exit_is_not_added_to_benchmark_cash_flows(self, monkeypatch: pytest.MonkeyPatch):
        class OversoldExitPolicy:
            def __init__(self, **_kwargs: object) -> None:
                pass

            def execute_entry(self, *, code: str, cohort_id: str, **_kwargs: object) -> ExecutionEvent:
                return ExecutionEvent(
                    order_id=f"entry_{code}", cohort_id=cohort_id, trade_date="20250116", code=code,
                    side="BUY", requested_quantity=100, requested_value=1_000.0,
                    executed_quantity=100, executed_value=1_000.0, price=10.0,
                    status="FILLED", remaining_quantity=0,
                )

            def execute_exit(self, position: object, **_kwargs: object) -> list[ExecutionEvent]:
                return [ExecutionEvent(
                    order_id="oversold_exit", cohort_id=getattr(position, "cohort_id"), trade_date="20250121",
                    code=getattr(position, "code"), side="SELL", requested_quantity=200, requested_value=2_000.0,
                    executed_quantity=200, executed_value=2_000.0, price=10.0,
                    status="FILLED", remaining_quantity=0,
                )]

        observed_exit_events = []

        def record_exit_events(**kwargs: object):
            observed_exit_events.extend(kwargs["exit_events"])
            return compute_liquidation_matched_benchmark(**kwargs)

        def skip_artifact_publish(**_kwargs: object) -> str:
            return "test-version"

        monkeypatch.setattr("backtest.stockpred.cohort.engine.ExecutionPolicy", OversoldExitPolicy)
        monkeypatch.setattr("backtest.stockpred.cohort.engine.compute_liquidation_matched_benchmark", record_exit_events)
        monkeypatch.setattr("backtest.stockpred.cohort.engine.publish_cohort_artifacts", skip_artifact_publish)

        result = CohortRunner(gateway=MockGateway(40), strategy=MockStrategy()).run(
            _config(Path(".superpowers/sdd")).model_copy(update={"end": "20250115", "top_n": 1})
        )

        assert result.cohort_results[0].status == CohortStatus.FAILED_EXECUTION
        assert not any(not event.is_terminal for event in observed_exit_events)

    def test_execution_event_row_includes_requested_quantity_known(self):
        event = ExecutionEvent(
            order_id="unknown_entry", cohort_id="c1", trade_date="20250103", code="A", side="BUY",
            requested_quantity=0, requested_value=1_000.0, executed_quantity=0, executed_value=0.0,
            price=0.0, status="REJECTED", remaining_quantity=0, requested_quantity_known=False,
        )

        assert _execution_event_row(event)["requested_quantity_known"] is False

    def test_malformed_execution_event_produces_failed_execution_with_null_returns(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        class MalformedPolicy:
            def __init__(self, **_kwargs: object) -> None:
                pass

            def execute_entry(self, *, code: str, cohort_id: str, **_kwargs: object) -> ExecutionEvent:
                return ExecutionEvent(
                    order_id=f"bad_{code}", cohort_id=cohort_id, trade_date="20250116", code=code,
                    side="BUY", requested_quantity=100, requested_value=1_000.0,
                    executed_quantity=100, executed_value=999.0, price=10.0,
                    status="FILLED", remaining_quantity=0,
                )

            def execute_exit(self, *_args: object, **_kwargs: object) -> list[ExecutionEvent]:
                return []

        monkeypatch.setattr("backtest.stockpred.cohort.engine.ExecutionPolicy", MalformedPolicy)
        result = CohortRunner(gateway=MockGateway(40), strategy=MockStrategy()).run(
            _config(tmp_path).model_copy(update={"end": "20250115", "top_n": 1})
        )

        cohort = result.cohort_results[0]
        assert cohort.status == CohortStatus.FAILED_EXECUTION
        assert cohort.committed_capital_return is None
        assert cohort.executed_capital_return is None
        assert cohort.liquidation_return is None

    def test_terminal_stale_beyond_limit_is_failed_data(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        class NoExitPolicy:
            def __init__(self, **_kwargs: object) -> None: pass
            def execute_entry(self, *, code: str, cohort_id: str, **_kwargs: object) -> ExecutionEvent:
                return ExecutionEvent(f"buy_{code}", cohort_id, "20250116", code, "BUY", 100, 100, 1000.0, 10.0, requested_value=1000.0)
            def execute_exit(self, *_args: object, **_kwargs: object) -> list[ExecutionEvent]: return []
        monkeypatch.setattr("backtest.stockpred.cohort.engine.ExecutionPolicy", NoExitPolicy)
        monkeypatch.setattr("backtest.stockpred.cohort.engine._terminal_position_value", lambda **_kwargs: (1.0, 999))
        result = CohortRunner(gateway=MockGateway(40), strategy=MockStrategy()).run(_config(tmp_path).model_copy(update={"end": "20250115", "top_n": 1}))
        cohort = result.cohort_results[0]
        assert cohort.status == CohortStatus.FAILED_DATA
        assert cohort.data_quality["reason"] == "terminal_valuation_stale"
        assert cohort.committed_capital_return is None

    def test_failed_execution_preserves_stale_audit(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        class BadExitPolicy:
            def __init__(self, **_kwargs: object) -> None: pass
            def execute_entry(self, *, code: str, cohort_id: str, **_kwargs: object) -> ExecutionEvent:
                return ExecutionEvent(f"buy_{code}", cohort_id, "20250116", code, "BUY", 100, 100, 1000.0, 10.0, requested_value=1000.0)
            def execute_exit(self, position: object, **_kwargs: object) -> list[ExecutionEvent]:
                return [ExecutionEvent("bad", getattr(position, "cohort_id"), "20250121", getattr(position, "code"), "SELL", 200, 200, 2000.0, 10.0, requested_value=2000.0)]
        monkeypatch.setattr("backtest.stockpred.cohort.engine.ExecutionPolicy", BadExitPolicy)
        monkeypatch.setattr("backtest.stockpred.cohort.engine._terminal_position_value", lambda **_kwargs: (1.0, 3))
        result = CohortRunner(gateway=MockGateway(40), strategy=MockStrategy()).run(_config(tmp_path).model_copy(update={"end": "20250115", "top_n": 1}))
        cohort = result.cohort_results[0]
        assert cohort.status == CohortStatus.FAILED_EXECUTION
        assert cohort.uses_stale_valuation
        assert cohort.max_stale_days == 3
    def test_orders_artifact_preserves_every_execution_event(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        class AuditablePolicy:
            def __init__(self, **_kwargs: object) -> None:
                pass

            def execute_entry(self, *, code: str, cohort_id: str, **_kwargs: object) -> ExecutionEvent:
                if code == "000001.SZ":
                    return ExecutionEvent(
                        order_id="entry_rejected", cohort_id=cohort_id, trade_date="20250116",
                        code=code, side="BUY", requested_quantity=100, requested_value=1_000.0,
                        executed_quantity=0, executed_value=0.0, price=0.0,
                        fee_components={"commission": 0.0, "stamp_duty": 0.0, "transfer_fee": 0.0, "slippage": 0.0, "market_impact": 0.0},
                        status="REJECTED", reason_code="suspended", remaining_quantity=100,
                    )
                return ExecutionEvent(
                    order_id="entry_partial", cohort_id=cohort_id, trade_date="20250116",
                    code=code, side="BUY", requested_quantity=100, requested_value=1_000.0,
                    executed_quantity=50, executed_value=500.0, price=10.0,
                    fee_components={"commission": 1.0, "stamp_duty": 0.0, "transfer_fee": 0.0, "slippage": 2.0, "market_impact": 3.0},
                    status="PARTIAL", reason_code="capacity", remaining_quantity=50,
                )

            def execute_exit(self, position: object, **_kwargs: object) -> list[ExecutionEvent]:
                code = getattr(position, "code")
                cohort_id = getattr(position, "cohort_id")
                return [ExecutionEvent(
                    order_id="exit_partial", cohort_id=cohort_id, trade_date="20250121",
                    code=code, side="SELL", requested_quantity=50, requested_value=550.0,
                    executed_quantity=25, executed_value=275.0, price=11.0,
                    fee_components={"commission": 1.0, "stamp_duty": 0.3, "transfer_fee": 0.2, "slippage": 0.4, "market_impact": 0.5},
                    status="PARTIAL", reason_code="capacity", remaining_quantity=25,
                )]

        monkeypatch.setattr("backtest.stockpred.cohort.engine.ExecutionPolicy", AuditablePolicy)
        CohortRunner(gateway=MockGateway(40), strategy=MockStrategy()).run(
            _config(tmp_path).model_copy(update={"end": "20250115", "top_n": 2})
        )

        import json
        pointer = json.loads((tmp_path / "artifacts_current.json").read_text(encoding="utf-8"))
        orders = pd.read_csv(tmp_path / "artifacts_versions" / pointer["version_id"] / "cohort_orders.csv")
        assert set(orders["order_id"]) == {"entry_rejected", "entry_partial", "exit_partial"}
        assert set([
            "cohort_id", "trade_date", "code", "side", "requested_quantity", "requested_value",
            "executed_quantity", "executed_value", "price", "remaining_quantity", "status", "reason_code",
            "requested_quantity_known", "commission", "stamp_duty", "transfer_fee", "slippage", "market_impact", "total_fees",
        ]).issubset(orders.columns)
        assert orders.loc[orders["order_id"] == "exit_partial", "total_fees"].item() == pytest.approx(2.4)
    def test_empty_signal_date_is_auditable_failed_cohort(self, tmp_path: Path):
        class EmptyStrategy:
            def evaluate(self, eval_date: str) -> pd.DataFrame:
                return pd.DataFrame(columns=["ts_code", "score"])

        result = CohortRunner(gateway=MockGateway(40), strategy=EmptyStrategy()).run(_config(tmp_path).model_copy(update={"end": "20250115"}))
        assert result.aggregation.metrics.total_cohort_count == 1
        assert result.cohort_results[0].status == CohortStatus.FAILED_DATA, [row.data_quality for row in result.cohort_results]

    def test_signal_evaluation_exception_is_failed_data_and_does_not_abort(self, tmp_path: Path):
        class FlakyStrategy:
            def evaluate(self, eval_date: str) -> pd.DataFrame:
                if eval_date == "20250115":
                    raise RuntimeError("bad signal")
                return pd.DataFrame([{"ts_code": "000001.SZ", "score": 1.0}])

        result = CohortRunner(gateway=MockGateway(40), strategy=FlakyStrategy()).run(_config(tmp_path).model_copy(update={"top_n": 1}))
        assert any(row.data_quality["reason"] == "signal_evaluation_failure" for row in result.cohort_results)
        assert len(result.cohort_results) > 1

    def test_exit_extension_truncation_is_failed_data(self, tmp_path: Path):
        result = CohortRunner(gateway=MockGateway(22), strategy=MockStrategy()).run(
            _config(tmp_path).model_copy(update={"start": "20250115", "end": "20250115", "top_n": 1}),
        )
        assert result.cohort_results[0].status == CohortStatus.FAILED_DATA, [row.data_quality for row in result.cohort_results]
        assert result.cohort_results[0].data_quality["reason"] == "truncated_exit_extension"

    def test_strategy_declared_unknown_dependency_downgrades_pit(self, tmp_path: Path):
        class DeclaredDependencyStrategy(MockStrategy):
            dependencies = ("custom_unknown_table",)

        result = CohortRunner(gateway=MockGateway(40), strategy=DeclaredDependencyStrategy()).run(
            _config(tmp_path).model_copy(update={"end": "20250115", "top_n": 1}),
        )
        assert "pit_assurance_snapshot_only" in result.aggregation.quality.failures

    def test_period_breakdown_excludes_failed_none_returns_but_counts_them(self):
        failed = CohortResult("failed", None, None, None, None, None, None, None, None, 0, 1, 0, 0, 0, 0, CohortStatus.FAILED_DATA)
        valid = CohortResult("valid", 0.1, 0.1, None, 0.1, 0.1, 0.0, 0.1, 0.1, 1, 0, 0, 0, 0, 0, CohortStatus.LIQUIDATED)
        result = _compute_period_breakdown([("20250101", failed), ("20250102", valid)])
        all_row = result[result["period"] == "all"].iloc[0]
        assert all_row["count"] == 2
        assert all_row["mean_return"] == pytest.approx(0.1)

    def test_suspended_candidate_is_excluded_before_signal_snapshot(self):
        universe = MockGateway().stock_dimension().iloc[:1]
        prices = pd.DataFrame([{"ts_code": "000001.SZ", "trade_date": "20250115", "vol": 0.0}])

        result = SignalEligibilityGate(min_listed_trade_days=0).check(
            eval_date="20250115", universe=universe, prices=prices,
            adjustment_factors=pd.DataFrame([{"ts_code": "000001.SZ", "trade_date": "20250115", "adj_factor": 1.0}]),
            market_calendar=["20241101", "20250115"], name_history=MockGateway().name_history(),
        )

        assert result.eligible_codes == []
        assert result.rejected == {"000001.SZ": "SUSPENDED"}

    def test_missing_adjustment_coverage_fails_closed(self):
        universe = MockGateway().stock_dimension().iloc[:1]
        result = SignalEligibilityGate(min_listed_trade_days=0).check(
            eval_date="20250115", universe=universe,
            prices=pd.DataFrame([{"ts_code": "000001.SZ", "trade_date": "20250115", "vol": 1.0}]),
            adjustment_factors=pd.DataFrame(), market_calendar=["20241101", "20250115"], name_history=MockGateway().name_history(),
        )

        assert result.data_failure
        assert result.rejected == {"000001.SZ": "ADJ_INCOMPLETE"}

    def test_missing_name_history_and_market_calendar_fail_closed(self):
        universe = MockGateway().stock_dimension().iloc[:1]
        result = SignalEligibilityGate(min_listed_trade_days=0).check(
            eval_date="20250115", universe=universe,
            prices=pd.DataFrame([{"ts_code": "000001.SZ", "trade_date": "20250115", "vol": 1.0}]),
            adjustment_factors=pd.DataFrame([{"ts_code": "000001.SZ", "trade_date": "20250115", "adj_factor": 1.0}]),
        )
        assert result.data_failure

    def test_candidate_absent_from_universe_is_data_failure(self):
        result = SignalEligibilityGate(min_listed_trade_days=0).check(
            eval_date="20250115", universe=MockGateway().stock_dimension().iloc[:0], candidates=["MISSING.SZ"],
            prices=pd.DataFrame(), adjustment_factors=pd.DataFrame(), market_calendar=["20250115"],
            name_history=pd.DataFrame(),
        )
        assert result.rejected == {"MISSING.SZ": "NOT_IN_UNIVERSE"}
        assert result.data_failure

    def test_missing_or_nan_signal_day_raw_row_is_data_failure(self):
        universe = MockGateway().stock_dimension().iloc[:1]
        args = dict(
            eval_date="20250115", universe=universe, market_calendar=["20241101", "20250115"],
            adjustment_factors=pd.DataFrame([{"ts_code": "000001.SZ", "trade_date": "20250115", "adj_factor": 1.0}]),
            name_history=MockGateway().name_history(),
        )
        gate = SignalEligibilityGate(min_listed_trade_days=0)
        missing = gate.check(prices=pd.DataFrame(columns=["ts_code", "trade_date", "vol"]), **args)
        unknown_volume = gate.check(prices=pd.DataFrame([{"ts_code": "000001.SZ", "trade_date": "20250115", "vol": float("nan")}]), **args)
        suspended = gate.check(prices=pd.DataFrame([{"ts_code": "000001.SZ", "trade_date": "20250115", "vol": 0.0}]), **args)
        assert missing.data_failure
        assert unknown_volume.data_failure
        assert not suspended.data_failure

    def test_missing_signal_day_raw_row_produces_failed_data_with_null_returns(self, tmp_path: Path):
        class MissingSignalRowGateway(MockGateway):
            def prices(self, start: str, end: str, codes: list[str]) -> pd.DataFrame:
                if start == end == "20250115":
                    return pd.DataFrame(columns=["ts_code", "trade_date", "vol"])
                return super().prices(start, end, codes)

        result = CohortRunner(gateway=MissingSignalRowGateway(40), strategy=MockStrategy()).run(
            _config(tmp_path).model_copy(update={"start": "20250115", "end": "20250115", "top_n": 1}),
        )
        cohort = result.cohort_results[0]
        assert cohort.status == CohortStatus.FAILED_DATA
        assert cohort.committed_capital_return is None
        assert cohort.benchmark_return is None

    def test_all_rejected_date_is_counted_as_auditable_cohort(self, tmp_path: Path):
        class SuspendedGateway(MockGateway):
            def prices(self, start: str, end: str, codes: list[str]) -> pd.DataFrame:
                prices = super().prices(start, end, codes)
                prices.loc[prices["trade_date"] == "20250115", "vol"] = 0.0
                return prices

        config = _config(tmp_path).model_copy(update={"end": "20250115", "top_n": 1})
        result = CohortRunner(gateway=SuspendedGateway(40), strategy=MockStrategy()).run(config)

        assert result.aggregation.metrics.total_cohort_count == 1
        assert result.cohort_results[0].data_quality["reason"] == "no_eligible_candidates"

    def test_truncated_horizon_is_failed_data_with_null_returns(self, tmp_path: Path):
        config = _config(tmp_path).model_copy(update={"start": "20250125", "end": "20250125", "top_n": 1})
        result = CohortRunner(gateway=MockGateway(25), strategy=MockStrategy()).run(config)

        cohort = result.cohort_results[0]
        assert cohort.status.value == "FAILED_DATA"
        assert cohort.committed_capital_return is None
        assert cohort.benchmark_return is None
    def test_infinite_adjusted_entry_marks_one_cohort_failed_and_continues(self, tmp_path: Path):
        class SplitStrategy:
            def evaluate(self, eval_date: str) -> pd.DataFrame:
                code = "000001.SZ" if eval_date == "20250115" else "000002.SZ"
                return pd.DataFrame([{"ts_code": code, "score": 1.0}])

        class InfiniteEntryRunner(CohortRunner):
            def _load_market(self, codes: list[str], config: CohortBacktestConfig, *, start: str | None = None, end: str | None = None) -> pd.DataFrame:
                market = super()._load_market(codes, config, start=start, end=end)
                market.loc[
                    (market["ts_code"] == "000001.SZ")
                    & (market["trade_date"] == "20250116"),
                    "adj_open",
                ] = float("inf")
                return market

        config = _config(tmp_path).model_copy(update={"top_n": 1})
        result = InfiniteEntryRunner(gateway=MockGateway(40), strategy=SplitStrategy()).run(config)

        failed = [r for r in result.cohort_results if r.status.value == "FAILED_DATA"]
        completed = [r for r in result.cohort_results if r.status.value != "FAILED_DATA"]
        assert any(row.data_quality["reason"] == "horizon_valuation_failure" for row in failed)
        assert len(completed) >= 1

    def test_horizon_quality_failure_marks_one_cohort_failed_and_continues(self, tmp_path: Path):
        class MissingAdjustmentGateway(MockGateway):
            def adjustment_factors(self, start: str, end: str, codes: list[str]) -> pd.DataFrame:
                dates = self.trade_dates(start, end)
                return pd.DataFrame(
                    [{"ts_code": "000002.SZ", "trade_date": date, "adj_factor": 1.0} for date in dates]
                )

        class SplitStrategy:
            def evaluate(self, eval_date: str) -> pd.DataFrame:
                code = "000001.SZ" if eval_date == "20250115" else "000002.SZ"
                return pd.DataFrame([{"ts_code": code, "score": 1.0}])

        config = _config(tmp_path).model_copy(update={"top_n": 1})
        result = CohortRunner(gateway=MissingAdjustmentGateway(40), strategy=SplitStrategy()).run(config)

        failed = [r for r in result.cohort_results if r.status.value == "FAILED_DATA"]
        completed = [r for r in result.cohort_results if r.status.value != "FAILED_DATA"]
        assert any(row.data_quality["reason"] == "eligibility_data_failure" for row in failed)
        assert len(completed) >= 1

    def test_unliquidated_runner_uses_raw_terminal_value_and_terminal_benchmark(self, tmp_path: Path):
        class UnliquidatableGateway(MockGateway):
            def prices(self, start: str, end: str, codes: list[str]) -> pd.DataFrame:
                market = super().prices(start, end, codes)
                market["open"] = 20.0
                market["high"] = 20.0
                market["low"] = 20.0
                market["close"] = 20.0
                return market

            def stock_limits(self, start: str, end: str, codes: list[str]) -> pd.DataFrame:
                limits = super().stock_limits(start, end, codes)
                limits["up_limit"] = 22.0
                limits["down_limit"] = 20.0
                return limits

        class OneStockStrategy:
            def evaluate(self, eval_date: str) -> pd.DataFrame:
                return pd.DataFrame([{"ts_code": "000001.SZ", "score": 1.0}])

        class RawTerminalRunner(CohortRunner):
            def _load_market(self, codes: list[str], config: CohortBacktestConfig, *, start: str | None = None, end: str | None = None) -> pd.DataFrame:
                market = super()._load_market(codes, config, start=start, end=end)
                market["adj_open"] = 5.0
                return market

        config = _config(tmp_path).model_copy(update={
            "start": "20250115",
            "end": "20250115",
            "top_n": 1,
            "committed_capital_per_cohort": 10_000.0,
            "holding_days": 5,
            "max_exit_extension_days": 2,
        })
        result = RawTerminalRunner(gateway=UnliquidatableGateway(40), strategy=OneStockStrategy()).run(config)
        cohort = result.cohort_results[0]

        buy_quantity = 400
        buy_fees = DEFAULT_COST_POLICY.estimate_buy_fees(buy_quantity, 20.0, 5_000_000.0).total
        terminal = ValuationPolicy().terminal_value(
            quantity=buy_quantity,
            last_valid_price=20.0,
            stale_days=0,
            limit_band_rate=0.10,
            adv=5_000_000.0,
        ).terminal_value
        expected_return = (10_000.0 - buy_quantity * 20.0 - buy_fees + terminal - 10_000.0) / 10_000.0
        expected_benchmark = 0.8 * (1.003**7 - 1.0)

        assert cohort.status.value == "UNLIQUIDATED"
        assert cohort.committed_capital_return == pytest.approx(expected_return)
        assert cohort.liquidation_policy_excess_return == pytest.approx(
            expected_return - expected_benchmark
        )

    def test_adjustment_factor_missing_does_not_fill_from_another_stock(self):
        class MissingFactorGateway(MockGateway):
            def adjustment_factors(self, start: str, end: str, codes: list[str]) -> pd.DataFrame:
                return pd.DataFrame(
                    [{"ts_code": "000002.SZ", "trade_date": "20250111", "adj_factor": 2.0}]
                )

        runner = CohortRunner(gateway=MissingFactorGateway(), strategy=MockStrategy())

        market = runner._load_market(
            ["000001.SZ", "000002.SZ"],
            _config(Path(".")),
            start="20250111",
            end="20250111",
        )

        missing = market[market["ts_code"] == "000001.SZ"].iloc[0]
        assert pd.isna(missing["adj_open"])
        assert bool(missing["adj_factor_missing"])

    def test_run_produces_result(self, tmp_path: Path):
        gateway = MockGateway(40)
        strategy = MockStrategy()
        config = _config(tmp_path)
        runner = CohortRunner(gateway=gateway, strategy=strategy)

        result = runner.run(config)

        assert isinstance(result, CohortRunResult)
        assert result.metrics is not None
        assert "mean_return" in result.metrics

    def test_run_publishes_artifacts(self, tmp_path: Path):
        gateway = MockGateway(40)
        strategy = MockStrategy()
        config = _config(tmp_path)
        runner = CohortRunner(gateway=gateway, strategy=strategy)

        runner.run(config)

        assert (tmp_path / "artifacts_current.json").is_file()

    def test_progress_callback_called(self, tmp_path: Path):
        gateway = MockGateway(40)
        strategy = MockStrategy()
        config = _config(tmp_path)
        runner = CohortRunner(gateway=gateway, strategy=strategy)

        progress: list[tuple[int, int, str]] = []
        runner.run(config, on_progress=lambda d, t, e: progress.append((d, t, e)))

        assert len(progress) >= 1
        assert progress[-1][0] == progress[-1][1]  # last call: done == total

    def test_cohort_results_generated(self, tmp_path: Path):
        gateway = MockGateway(40)
        strategy = MockStrategy()
        config = _config(tmp_path)
        runner = CohortRunner(gateway=gateway, strategy=strategy)

        result = runner.run(config)

        assert len(result.cohort_results) >= 1

    def test_chart_bundle_published(self, tmp_path: Path):
        gateway = MockGateway(40)
        strategy = MockStrategy()
        config = _config(tmp_path)
        runner = CohortRunner(gateway=gateway, strategy=strategy)

        runner.run(config)

        # Chart manifest should exist in the version directory
        import json
        pointer = json.loads((tmp_path / "artifacts_current.json").read_text(encoding="utf-8"))
        version_dir = tmp_path / "artifacts_versions" / pointer["version_id"]
        assert (version_dir / "chart_bundle_manifest.json").is_file()
