from __future__ import annotations

import math

import pandas as pd
import pytest

from backtest.fund_rotation.strategies.ai_rotation_r72_r39_absolute_momentum.strategy import (
    AiRotationR72R39AbsoluteMomentumStrategy,
    AiRotationR72R39AbsoluteMomentumSession,
    apply_absolute_momentum_gate,
    compute_absolute_momentum_returns,
)
from backtest.fund_rotation.strategies.ai_rotation_r11_persist_geom.strategy import (
    AiRotationR11PersistGeomSession,
)
from backtest.fund_rotation.strategies.ai_rotation_r39_incumbent_carry.strategy import (
    AiRotationR39IncumbentCarrySession,
)
from backtest.fund_rotation.strategies.correlation_representative.config import (
    CorrelationRepresentativeConfig,
)
from backtest.fund_rotation.contracts import (
    DecisionKind,
    QualityStatus,
    StrategyDecisionContext,
    TargetWeightDecision,
)
from backtest.fund_rotation.strategies.registry import default_fund_rotation_strategies


def _prices(values: list[float]) -> pd.DataFrame:
    dates = pd.date_range("2020-01-01", periods=len(values), freq="D")
    return pd.DataFrame({"A": values}, index=dates.strftime("%Y%m%d"))


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        ([1.0] * 126 + [2.0], 1.0),
        ([1.0] * 126 + [0.5], -0.5),
        ([1.0] * 126 + [1.0], 0.0),
    ],
)
def test_compute_126d_return_has_strict_positive_boundary(values, expected):
    returns = compute_absolute_momentum_returns(
        _prices(values), signal_date="20200506", codes=("A",)
    )
    assert returns["A"] == pytest.approx(expected)


def test_compute_126d_return_is_missing_when_window_is_short():
    returns = compute_absolute_momentum_returns(
        _prices([1.0] * 126), signal_date="20200506", codes=("A",)
    )
    assert returns == {"A": None}


def test_compute_126d_return_excludes_t1_t2_future_rows():
    prices = _prices([1.0] * 126 + [2.0])
    prices.loc["20200507", "A"] = 100.0
    prices.loc["20200508", "A"] = 1000.0
    returns = compute_absolute_momentum_returns(
        prices, signal_date="20200506", codes=("A",)
    )
    assert returns["A"] == pytest.approx(1.0)


def test_compute_126d_return_sorts_causal_rows_before_taking_window():
    prices = _prices([1.0] * 126 + [2.0]).iloc[::-1]
    returns = compute_absolute_momentum_returns(
        prices, signal_date="20200506", codes=("A",)
    )
    assert returns["A"] == pytest.approx(1.0)


@pytest.mark.parametrize("bad_value", [math.inf, -math.inf, 0.0, -1.0])
def test_compute_126d_return_rejects_internal_nonfinite_or_nonpositive_prices(bad_value):
    values = [1.0] * 64 + [bad_value] + [2.0] * 62
    returns = compute_absolute_momentum_returns(
        _prices(values), signal_date="20200506", codes=("A",)
    )
    assert returns == {"A": None}


def test_positive_gate_preserves_r39_targets_exactly():
    targets = {"A": 0.4, "B": 0.3}
    actual_targets, cash, missing, negative = apply_absolute_momentum_gate(
        targets, 0.3, {"A": 0.1, "B": 0.01}
    )
    assert actual_targets == targets
    assert cash == pytest.approx(0.3)
    assert missing == set()
    assert negative == set()


def test_failed_candidates_are_released_to_cash_without_reallocation():
    actual_targets, cash, missing, negative = apply_absolute_momentum_gate(
        {"A": 0.4, "B": 0.3, "C": 0.2},
        0.1,
        {"A": None, "B": 0.0, "C": -0.1},
    )
    assert actual_targets == {}
    assert cash == pytest.approx(1.0)
    assert missing == {"A"}
    assert negative == {"B", "C"}


@pytest.mark.parametrize("invalid_cash", [math.nan, math.inf, -0.1, True])
def test_invalid_cash_fails_closed_to_all_cash(invalid_cash):
    actual_targets, cash, missing, negative = apply_absolute_momentum_gate(
        {"A": 0.4}, invalid_cash, {"A": 0.1}
    )
    assert actual_targets == {}
    assert cash == pytest.approx(1.0)
    assert missing == {"A"}
    assert negative == set()


def test_r72_is_registered_and_only_adds_the_absolute_gate():
    strategy = AiRotationR72R39AbsoluteMomentumStrategy()
    assert strategy.descriptor.id == "ai_rotation_r72_r39_absolute_momentum"
    assert AiRotationR72R39AbsoluteMomentumStrategy in default_fund_rotation_strategies()
    pipeline = strategy.describe_decision_pipeline(strategy.config_model())
    assert pipeline["absolute_momentum_lookback_days"] == 126
    assert "R126d > 0" in pipeline["selection_rule"]


def test_r72_directly_inherits_r39_without_r40_ceiling():
    assert issubclass(AiRotationR72R39AbsoluteMomentumSession, AiRotationR39IncumbentCarrySession)
    assert AiRotationR72R39AbsoluteMomentumSession.__bases__ == (
        AiRotationR39IncumbentCarrySession,
    )
    assert not any(
        base.__name__ == "AiRotationR40SingleNameCeilingSession"
        for base in AiRotationR72R39AbsoluteMomentumSession.__mro__
    )


def test_r72_requirements_declare_full_absolute_momentum_warmup():
    requirements = AiRotationR72R39AbsoluteMomentumStrategy().resolve_requirements(
        CorrelationRepresentativeConfig(correlation_lookback_weeks=20)
    )
    assert requirements.warmup_trade_days >= 127


def test_session_commits_final_gated_target_to_artifacts_and_previous_state(monkeypatch):
    def fake_r11_evaluate(self, context):
        decision = TargetWeightDecision(
            decision_id=f"{context.signal_date}-ai_rotation_r11_persist_geom",
            signal_date=context.signal_date,
            action=DecisionKind.SET_TARGETS,
            target_weights={"A": 1 / 3, "B": 1 / 3},
            cash_weight=0.0,
            quality_status=QualityStatus.VALID,
        )
        self._log_decision(decision)
        return decision

    class _View:
        def adjusted_closes(self, lookback=None):
            del lookback
            frame = _prices([1.0] * 126 + [2.0])
            frame["B"] = [1.0] * 126 + [0.5]
            return frame

    monkeypatch.setattr(AiRotationR11PersistGeomSession, "evaluate", fake_r11_evaluate)
    session = AiRotationR72R39AbsoluteMomentumSession(CorrelationRepresentativeConfig())
    session._previous_weights = {"A": 1 / 3}

    decision = session.evaluate(
        StrategyDecisionContext(signal_date="20200506", data_view=_View())
    )

    assert decision.target_weights == {"A": pytest.approx(1 / 2)}
    assert decision.cash_weight == pytest.approx(1 / 2)
    assert session._previous_weights == decision.target_weights
    decisions = next(
        artifact.payload
        for artifact in session.finalize().artifacts
        if artifact.role == "decisions"
    )
    assert len(decisions) == 1
    assert decisions[-1]["target_weights"] == decision.target_weights
    assert decisions[-1]["cash_weight"] == decision.cash_weight


def test_session_clears_pending_artifacts_after_gate_exception(monkeypatch):
    def fake_r11_evaluate(self, context):
        self._week_index += 1
        self._clusters = {1: "MUTATED"}
        self._cluster_history.append({"mutated": True})
        self._previous_weights = {"MUTATED": 1.0}
        decision = TargetWeightDecision(
            decision_id=f"{context.signal_date}-ai_rotation_r11_persist_geom",
            signal_date=context.signal_date,
            action=DecisionKind.SET_TARGETS,
            target_weights={"A": 1 / 3},
            cash_weight=2 / 3,
            quality_status=QualityStatus.VALID,
        )
        self._log_decision(decision)
        return decision

    monkeypatch.setattr(AiRotationR11PersistGeomSession, "evaluate", fake_r11_evaluate)
    monkeypatch.setattr(
        "backtest.fund_rotation.strategies.ai_rotation_r72_r39_absolute_momentum.strategy._momentum_observations",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("probe")),
    )
    session = AiRotationR72R39AbsoluteMomentumSession(CorrelationRepresentativeConfig())
    session._week_index = 7
    session._clusters = {2: "ORIGINAL"}
    session._cluster_history = [{"original": True}]
    session._previous_weights = {"ORIGINAL": 1.0}
    with pytest.raises(RuntimeError, match="probe"):
        session.evaluate(StrategyDecisionContext(signal_date="20200506", data_view=object()))
    assert session._pending_log_args is None
    assert session._decision_log == []
    assert session._week_index == 7
    assert session._clusters == {2: "ORIGINAL"}
    assert session._cluster_history == [{"original": True}]
    assert session._previous_weights == {"ORIGINAL": 1.0}
