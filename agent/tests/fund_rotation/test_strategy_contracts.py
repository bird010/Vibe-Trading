"""Phase 1 Task 1 — strategy contract and value-object tests (design §5/§7)."""

from __future__ import annotations

import pandas as pd
import pytest
from pydantic import BaseModel

from backtest.fund_rotation.contracts import (
    DecisionKind,
    FundRotationStrategy,
    FundRotationStrategyDescriptor,
    FundRotationStrategySession,
    QualityStatus,
    StrategyContractViolation,
    StrategyDataRequirements,
    StrategyDecisionContext,
    StrategyDiagnostics,
    StrategyInitializationContext,
    TargetWeightDecision,
    validate_target_decision,
)

ELIGIBLE = {"510300.SH", "510010.SH", "510020.SH"}


def _decision(**overrides) -> TargetWeightDecision:
    base = dict(
        decision_id="d1",
        signal_date="20240105",
        action=DecisionKind.SET_TARGETS,
        target_weights={"510300.SH": 0.6, "510010.SH": 0.4},
        cash_weight=0.0,
        reason_code="",
        quality_status=QualityStatus.VALID,
        diagnostics={},
    )
    base.update(overrides)
    return TargetWeightDecision(**base)


class TestTargetDecisionValidation:
    def test_valid_set_targets_passes(self):
        validate_target_decision(_decision(), ELIGIBLE, set())  # no raise

    def test_weights_plus_cash_must_sum_to_one(self):
        d = _decision(target_weights={"510300.SH": 0.6}, cash_weight=0.3)  # sums to 0.9
        with pytest.raises(StrategyContractViolation, match="sum to 1.0"):
            validate_target_decision(d, ELIGIBLE, set())

    def test_empty_targets_with_full_cash_is_valid(self):
        d = _decision(target_weights={}, cash_weight=1.0)
        validate_target_decision(d, ELIGIBLE, set())  # no raise

    def test_negative_weight_rejected(self):
        d = _decision(target_weights={"510300.SH": -0.5}, cash_weight=1.5)
        with pytest.raises(StrategyContractViolation, match="non-negative"):
            validate_target_decision(d, ELIGIBLE, set())

    def test_non_finite_weight_rejected(self):
        d = _decision(target_weights={"510300.SH": float("nan")}, cash_weight=1.0)
        with pytest.raises(StrategyContractViolation, match="finite"):
            validate_target_decision(d, ELIGIBLE, set())

    def test_ineligible_code_rejected(self):
        d = _decision(target_weights={"999999.SH": 1.0}, cash_weight=0.0)
        with pytest.raises(StrategyContractViolation, match="eligible pool"):
            validate_target_decision(d, ELIGIBLE, set())

    def test_duplicate_decision_id_rejected(self):
        d = _decision(decision_id="dup")
        with pytest.raises(StrategyContractViolation, match="duplicate decision_id"):
            validate_target_decision(d, ELIGIBLE, {"dup"})

    def test_validation_is_order_independent(self):
        d1 = _decision(target_weights={"510300.SH": 0.6, "510010.SH": 0.4}, cash_weight=0.0)
        d2 = _decision(target_weights={"510010.SH": 0.4, "510300.SH": 0.6}, cash_weight=0.0)
        # Both validate identically regardless of dict insertion order.
        validate_target_decision(d1, ELIGIBLE, set())
        validate_target_decision(d2, ELIGIBLE, set())


class TestHoldAndInvalidSemantics:
    def test_hold_targets_must_not_carry_weights(self):
        d = _decision(
            action=DecisionKind.HOLD_TARGETS,
            target_weights={"510300.SH": 1.0},
            cash_weight=0.0,
        )
        with pytest.raises(StrategyContractViolation, match="HOLD_TARGETS"):
            validate_target_decision(d, ELIGIBLE, set())

    def test_hold_targets_without_weights_is_valid(self):
        d = _decision(action=DecisionKind.HOLD_TARGETS, target_weights={}, cash_weight=1.0)
        validate_target_decision(d, ELIGIBLE, set())  # no raise

    def test_invalid_requires_reason_code(self):
        d = _decision(action=DecisionKind.INVALID, target_weights={}, reason_code="")
        with pytest.raises(StrategyContractViolation, match="reason_code"):
            validate_target_decision(d, ELIGIBLE, set())

    def test_invalid_with_reason_code_is_valid(self):
        d = _decision(
            action=DecisionKind.INVALID, target_weights={},
            reason_code="INSUFFICIENT_HISTORY",
        )
        validate_target_decision(d, ELIGIBLE, set())  # no raise


class TestProtocolConformance:
    def test_minimal_strategy_satisfies_protocol(self):
        class _Config(BaseModel):
            lookback: int = 20

        class _Session:
            def scheduled_dates(self, calendar, simulation_start_date, evaluation_end_date):
                return tuple(simulation_start_date)

            def evaluate(self, context):
                return TargetWeightDecision(
                    decision_id="d", signal_date=context.signal_date,
                    action=DecisionKind.HOLD_TARGETS,
                )

            def finalize(self):
                return StrategyDiagnostics()

        class _Strategy:
            descriptor = FundRotationStrategyDescriptor(
                id="test", name="Test", description="d",
                interface_version="1.0", supported_universe=("etf",), deterministic=True,
            )
            config_model = _Config

            def resolve_requirements(self, config):
                return StrategyDataRequirements(
                    required_datasets=("fund",), required_fields=("close",),
                    warmup_trade_days=100, frequency="weekly", needs_benchmark=True,
                )

            def create_session(self, initialization, config):
                return _Session()

        assert isinstance(_Strategy(), FundRotationStrategy)
        assert isinstance(_Session(), FundRotationStrategySession)

    def test_descriptor_is_immutable(self):
        descriptor = FundRotationStrategyDescriptor(
            id="x", name="n", description="d", interface_version="1.0",
            supported_universe=("etf",), deterministic=True,
        )
        with pytest.raises(Exception):
            descriptor.id = "y"  # frozen

    def test_decision_context_exposes_signal_date_and_previous_weights(self):
        class _View:
            @property
            def signal_date(self):
                return pd.Timestamp("20240105")

        ctx = StrategyDecisionContext(
            signal_date="20240105", data_view=_View(),
            previous_target_weights={"510300.SH": 1.0},
        )
        assert ctx.signal_date == "20240105"
        assert ctx.previous_target_weights == {"510300.SH": 1.0}
        assert ctx.data_view.signal_date == pd.Timestamp("20240105")
