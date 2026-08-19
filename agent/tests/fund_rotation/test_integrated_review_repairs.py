"""Focused regressions for the integrated fund-rotation review repairs."""

from __future__ import annotations

from types import SimpleNamespace

import pandas as pd
import pytest
from pydantic import ValidationError

from backtest.fund_rotation.contracts import (
    StrategyDataRequirements,
    merge_requirements,
)
from backtest.fund_rotation.runner import ExecutionConfig
from backtest.fund_rotation.strategies.correlation_all_members.config import (
    CorrelationAllMembersConfig,
)
from backtest.fund_rotation.strategies.correlation_all_members.signals import (
    ensure_instrument_pool,
)
from backtest.fund_rotation.strategies.correlation_representative.representative import (
    maintain_representative_lock,
)
from src.stockpred.fund_rotation.batch_models import StrategyBatchRequest
from src.stockpred.fund_rotation.comparison import (
    comparison_contract_fingerprint,
    evaluation_calendar_hash,
)


class TestConfigurationBoundaries:
    def test_strategy_schema_contains_only_algorithm_parameters(self):
        fields = set(CorrelationAllMembersConfig.model_fields)
        assert {
            "k",
            "top_n",
            "momentum_window_weeks",
            "correlation_lookback_weeks",
        } <= fields
        assert {
            "initial_capital",
            "commission_rate",
            "commission_min",
            "max_participation_rate",
            "base_slippage_bps",
            "start_date",
            "end_date",
        }.isdisjoint(fields)

    def test_execution_contract_rejects_invalid_ranges(self):
        with pytest.raises(ValidationError):
            ExecutionConfig(initial_capital=0)
        with pytest.raises(ValidationError):
            ExecutionConfig(max_participation_rate=1.1)
        with pytest.raises(ValueError, match="adv_min_observations"):
            ExecutionConfig(adv_lookback=5, adv_min_observations=6)
        with pytest.raises(ValueError, match="max_slippage_bps"):
            ExecutionConfig(base_slippage_bps=10, max_slippage_bps=5)

    def test_mixed_frequencies_share_data_without_rejecting_batch(self):
        merged = merge_requirements(
            [
                StrategyDataRequirements(
                    required_datasets=("fund",),
                    required_fields=("close",),
                    warmup_trade_days=20,
                    frequency="D",
                    needs_benchmark=False,
                ),
                StrategyDataRequirements(
                    required_datasets=("fund_adj",),
                    required_fields=("adj_factor",),
                    warmup_trade_days=260,
                    frequency="W",
                    needs_benchmark=True,
                ),
            ]
        )
        assert merged.frequency == "MIXED"
        assert merged.warmup_trade_days == 260
        assert merged.required_datasets == ("fund", "fund_adj")
        assert merged.needs_benchmark is True

    def test_from_legacy_accepts_object_and_ignores_execution_fields(self):
        legacy = SimpleNamespace(
            k=6,
            top_n=2,
            rebalance_freq="M",
            initial_capital=88_000_000,
            commission_rate=0.25,
            start_date="20200101",
        )
        converted = CorrelationAllMembersConfig.from_legacy(legacy)
        assert converted.k == 6
        assert converted.top_n == 2
        assert converted.rebalance_freq == "M"
        assert "initial_capital" not in converted.model_fields

    def test_from_legacy_accepts_mapping_and_old_frequency_alias(self):
        converted = CorrelationAllMembersConfig.from_legacy(
            {
                "k": 5,
                "top_n": 2,
                "rebalance_frequency": "M",
                "initial_capital": 1,
            }
        )
        assert converted.k == 5
        assert converted.top_n == 2
        assert converted.rebalance_freq == "M"


class TestBatchRequestValidation:
    def _request(self, **overrides):
        payload = {
            "schema_version": "1",
            "idempotency_key": "repair-test",
            "mode": "RESEARCH_ONLY",
            "evaluation_start_date": "20240102",
            "evaluation_end_date": "20241231",
            "execution": {},
            "variants": [
                {
                    "strategy_id": "correlation_all_members",
                    "params": {},
                }
            ],
        }
        payload.update(overrides)
        return StrategyBatchRequest.model_validate(payload)

    def test_rejects_impossible_calendar_date(self):
        with pytest.raises(ValidationError, match="invalid YYYYMMDD"):
            self._request(evaluation_start_date="20240231")

    def test_rejects_unsupported_schema_version(self):
        with pytest.raises(ValidationError, match="schema_version"):
            self._request(schema_version="2")

    def test_resolves_execution_defaults(self):
        request = self._request()
        assert request.execution.initial_capital == 1_000_000.0
        assert request.execution.lot_size == 100
        assert request.execution.adv_min_observations <= request.execution.adv_lookback


class TestComparisonIdentity:
    def test_actual_execution_parameters_change_comparison_fingerprint(self):
        common = {
            "framework_implementation_hash": "framework",
            "data_snapshot_fingerprint": "data",
            "evaluation_calendar": ["20240102", "20240103"],
        }
        components_a, fingerprint_a = comparison_contract_fingerprint(
            **common,
            execution_contract={
                "commission_rate": 0.00025,
                "base_slippage_bps": 5.0,
            },
        )
        components_b, fingerprint_b = comparison_contract_fingerprint(
            **common,
            execution_contract={
                "commission_rate": 0.001,
                "base_slippage_bps": 5.0,
            },
        )
        assert components_a["execution_contract"] != components_b["execution_contract"]
        assert fingerprint_a != fingerprint_b
        assert len(components_a) == 8

    def test_calendar_order_is_not_identity_bearing(self):
        common = {
            "framework_implementation_hash": "framework",
            "data_snapshot_fingerprint": "data",
            "execution_contract": {"commission_rate": 0.00025},
        }
        _, first = comparison_contract_fingerprint(
            **common,
            evaluation_calendar=["20240102", "20240103"],
        )
        _, second = comparison_contract_fingerprint(
            **common,
            evaluation_calendar=["20240103", "20240102"],
        )
        assert first == second
        assert evaluation_calendar_hash(
            ["20240103", "20240102"]
        ) == evaluation_calendar_hash(["20240102", "20240103"])

    def test_calendar_duplicates_are_rejected(self):
        with pytest.raises(ValueError, match="duplicate"):
            evaluation_calendar_hash(["20240102", "20240102"])


class _PointInTimeView:
    def __init__(self, bars: pd.DataFrame, adjustments: pd.DataFrame):
        self._bars = bars
        self._adjustments = adjustments

    def eligible_universe(self):
        return (
            SimpleNamespace(ts_code="A", name="A ETF", list_date="20200101"),
            SimpleNamespace(ts_code="B", name="B ETF", list_date="20200101"),
        )

    def daily_bars(self, fields, lookback=None):
        return self._bars.copy()

    def fund_adjustments(self, lookback=None):
        return self._adjustments.copy()


class TestPointInTimeEligibility:
    def test_adjustment_coverage_is_checked_for_current_window(self):
        bars = pd.DataFrame(
            [
                {"ts_code": code, "trade_date": date, "close": 1.0}
                for code in ("A", "B")
                for date in ("20240102", "20240103")
            ]
        )
        adjustments = pd.DataFrame(
            [
                {"ts_code": "A", "trade_date": "20240102", "adj_factor": 1.0},
                {"ts_code": "A", "trade_date": "20240103", "adj_factor": 1.0},
                {"ts_code": "B", "trade_date": "20240103", "adj_factor": 1.0},
            ]
        )
        pool = ensure_instrument_pool(
            _PointInTimeView(bars, adjustments),
            lookback_trade_days=2,
        )
        assert pool["ts_code"].tolist() == ["A"]

    def test_positive_complete_adjustments_allow_reentry(self):
        bars = pd.DataFrame(
            [
                {"ts_code": code, "trade_date": date, "close": 1.0}
                for code in ("A", "B")
                for date in ("20240102", "20240103")
            ]
        )
        adjustments = pd.DataFrame(
            [
                {"ts_code": code, "trade_date": date, "adj_factor": 1.0}
                for code in ("A", "B")
                for date in ("20240102", "20240103")
            ]
        )
        pool = ensure_instrument_pool(
            _PointInTimeView(bars, adjustments),
            lookback_trade_days=2,
        )
        assert pool["ts_code"].tolist() == ["A", "B"]


class TestRepresentativeLock:
    @staticmethod
    def _inputs():
        distance = pd.DataFrame(
            [[0.0, 0.2], [0.2, 0.0]],
            index=["A", "B"],
            columns=["A", "B"],
        )
        weekly = pd.DataFrame(
            {
                "A": [0.10, -0.10, 0.10, -0.10],
                "B": [-0.10, 0.10, -0.10, 0.10],
            }
        )
        return distance, weekly

    def test_correlation_drift_does_not_break_existing_lock(self):
        distance, weekly = self._inputs()
        selection = maintain_representative_lock(
            distance=distance,
            weekly_window=weekly,
            members=["A", "B"],
            adv20={"A": 100.0, "B": 200.0},
            candidate_count=2,
            min_cluster_corr=0.9,
            eligible={"A", "B"},
            current="A",
        )
        assert selection.selected == "A"
        assert selection.lock_maintained is True

    def test_hard_failure_allows_frozen_candidate_fallback(self):
        distance, weekly = self._inputs()
        selection = maintain_representative_lock(
            distance=distance,
            weekly_window=weekly,
            members=["A", "B"],
            adv20={"A": 100.0, "B": 200.0},
            candidate_count=2,
            min_cluster_corr=0.9,
            eligible={"B"},
            current="A",
        )
        assert selection.selected == "B"
        assert selection.lock_maintained is False
