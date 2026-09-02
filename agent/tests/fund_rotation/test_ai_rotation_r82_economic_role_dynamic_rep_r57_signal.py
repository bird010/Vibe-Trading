"""TDD contract tests for R82: R81 dynamic representatives plus R57 scoring."""

from __future__ import annotations

import json

import pandas as pd
import pytest

from backtest.fund_rotation.causal_data import FundInstrument
from backtest.fund_rotation.contracts import (
    StrategyDecisionContext,
    StrategyInitializationContext,
    validate_target_decision,
)
from backtest.fund_rotation.risk_layers import apply_defense_asset
from backtest.fund_rotation.strategies.ai_rotation_r34_staged_reentry.strategy import (
    apply_staged_reentry,
)
from backtest.fund_rotation.strategies.ai_rotation_r39_incumbent_carry.strategy import (
    apply_incumbent_carry,
)
from backtest.fund_rotation.strategies.economic_role_rotation.config import (
    EconomicRoleConfig,
)
from backtest.fund_rotation.strategies.economic_role_rotation.roles import (
    CN_DEFENSIVE_EQUITY,
)
from backtest.fund_rotation.strategies.economic_role_rotation.strategy import (
    AiRotationR81EconomicRoleDynamicRepresentativeStrategy,
)

try:
    from backtest.fund_rotation.strategies.ai_rotation_r82_economic_role_dynamic_rep_r57_signal.strategy import (
        DESCRIPTOR,
        AiRotationR82EconomicRoleDynamicRepR57SignalStrategy,
    )
    _R82_IMPORT_ERROR = None
except ImportError as exc:  # Expected RED state before the package is added.
    DESCRIPTOR = None
    AiRotationR82EconomicRoleDynamicRepR57SignalStrategy = None
    _R82_IMPORT_ERROR = exc


def _require_r82() -> None:
    assert _R82_IMPORT_ERROR is None, f"R82 package missing: {_R82_IMPORT_ERROR}"


class _RoleView:
    """Small causal view with deterministic role and factor data."""

    def __init__(
        self,
        signal_date: str = "20200131",
        *,
        failed_code: str | None = None,
        invalid_factor_codes: set[str] | None = None,
        future_rows: bool = False,
        defensive_member_close: float = 1.0,
        include_defense_asset: bool = False,
    ) -> None:
        self._signal_date = signal_date
        self.failed_code = failed_code
        self.invalid_factor_codes = invalid_factor_codes or set()
        self.future_rows = future_rows
        self.defensive_member_close = defensive_member_close
        instruments = [
            FundInstrument("D1", "红利低波ETF", "20100101"),
            FundInstrument("D2", "红利ETF", "20100101"),
            FundInstrument("G", "创业板50ETF", "20100101"),
            FundInstrument("O", "NASDAQ100 ETF", "20100101"),
            FundInstrument("AU", "黄金ETF", "20100101"),
            FundInstrument("B", "10年国债ETF", "20100101"),
        ]
        if include_defense_asset:
            instruments.append(
                FundInstrument("511010.SH", "10年国债ETF", "20100101")
            )
        self.instruments = tuple(instruments)
        self.codes = [instrument.ts_code for instrument in self.instruments]

    @property
    def signal_date(self) -> pd.Timestamp:
        return pd.Timestamp(self._signal_date)

    def eligible_universe(self):
        return self.instruments

    def returns(self, frequency: str, lookback: int):
        assert frequency == "weekly"
        return pd.DataFrame({code: [0.01] * lookback for code in self.codes})

    def _dates(self) -> list[str]:
        dates = pd.date_range(end=self._signal_date, periods=49, freq="D")
        result = [date.strftime("%Y%m%d") for date in dates]
        if self.future_rows:
            result.append(
                (pd.Timestamp(self._signal_date) + pd.Timedelta(days=1)).strftime(
                    "%Y%m%d"
                )
            )
        return result

    def daily_bars(self, fields, lookback=None):
        rows = []
        dates = self._dates()
        for code in self.codes:
            for index, date in enumerate(dates):
                close = 1.0 + index * 0.001
                if code == "D2":
                    close = self.defensive_member_close + index * 0.001
                if code in self.invalid_factor_codes and date != self._signal_date:
                    close = float("nan")
                if code in self.invalid_factor_codes and date == self._signal_date:
                    close = 1.0
                rows.append(
                    {
                        "ts_code": code,
                        "trade_date": date,
                        "open": close * 0.99,
                        "high": close * 1.01,
                        "low": close * 0.98,
                        "close": close,
                        "vol": 100.0,
                        "amount": 0.0 if code == self.failed_code and index >= len(dates) - 5 else 200.0,
                    }
                )
        frame = pd.DataFrame(rows)
        if fields is not None:
            wanted = ["ts_code", "trade_date", *fields]
            frame = frame[[column for column in wanted if column in frame.columns]]
        if lookback is not None:
            frame = frame.groupby("ts_code", sort=False, group_keys=False).tail(lookback)
        return frame.reset_index(drop=True)

    def fund_adjustments(self, lookback=None):
        rows = [
            {"ts_code": code, "trade_date": date, "adj_factor": 1.0}
            for code in self.codes
            for date in self._dates()
        ]
        frame = pd.DataFrame(rows)
        if lookback is not None:
            frame = frame.groupby("ts_code", sort=False, group_keys=False).tail(lookback)
        return frame.reset_index(drop=True)


class _IncompleteFactorView(_RoleView):
    def daily_bars(self, fields, lookback=None):
        frame = super().daily_bars(fields, lookback)
        if {"open", "high", "low"} <= set(fields):
            return frame[["ts_code", "trade_date", "amount"]]
        return frame


def _session(strategy):
    config = EconomicRoleConfig(
        fixed_role_manifest={
            "CN_DEFENSIVE_EQUITY": ("D1", "D2"),
            "CN_GROWTH_EQUITY": ("G",),
            "OVERSEAS_GROWTH_EQUITY": ("O",),
            "GOLD": ("AU",),
            "BOND": ("B",),
        }
    )
    return strategy.create_session(
        StrategyInitializationContext(
            "r82-test", ("20200103", "20200110", "20200131")
        ),
        config,
    )


def _artifacts(session):
    return {artifact.role: artifact.payload for artifact in session.finalize().artifacts}


def test_r82_has_independent_descriptor_package_and_implementation_identity():
    _require_r82()

    assert DESCRIPTOR.id == "ai_rotation_r82_economic_role_dynamic_rep_r57_signal"
    assert AiRotationR82EconomicRoleDynamicRepR57SignalStrategy.__module__ != (
        AiRotationR81EconomicRoleDynamicRepresentativeStrategy.__module__
    )
    from backtest.fund_rotation.catalog import FundRotationStrategyCatalog
    from backtest.fund_rotation.strategies.registry import default_fund_rotation_strategies

    catalog = FundRotationStrategyCatalog(default_fund_rotation_strategies())
    r82 = catalog.require(DESCRIPTOR.id)
    r81 = catalog.require("ai_rotation_r81_economic_role_dynamic_rep")
    assert r82.factory is not r81.factory
    assert r82.implementation_snapshot.implementation_hash
    assert r82.implementation_snapshot.implementation_hash != (
        r81.implementation_snapshot.implementation_hash
    )


def test_r82_preserves_r81_representative_lifecycle_and_pit_role_snapshot():
    _require_r82()

    r81 = _session(AiRotationR81EconomicRoleDynamicRepresentativeStrategy())
    r82 = _session(AiRotationR82EconomicRoleDynamicRepR57SignalStrategy())
    contexts = [
        StrategyDecisionContext("20200103", _RoleView("20200103")),
        StrategyDecisionContext("20200110", _RoleView("20200110")),
        StrategyDecisionContext(
            "20200131", _RoleView("20200131", failed_code="D1")
        ),
    ]
    for context in contexts:
        r81.evaluate(context)
        r82.evaluate(context)

    r81_artifacts = _artifacts(r81)
    r82_artifacts = _artifacts(r82)
    assert r82_artifacts["role_representatives"] == r81_artifacts[
        "role_representatives"
    ]
    assert r82_artifacts["role_history"] == r81_artifacts["role_history"]
    assert r82_artifacts["role_history"][-1]["members_as_of"] == "20200131"
    defensive = [
        row
        for row in r82_artifacts["role_representatives"]
        if row["role_id"] == CN_DEFENSIVE_EQUITY
    ]
    assert defensive[1]["selection_mode"] == "LOCK_MAINTENANCE"
    assert defensive[2]["selection_mode"] == "HARD_FAILURE_FALLBACK"
    assert defensive[2]["representative"] == "D2"


def test_r82_r57_uses_causal_49_day_window_and_frozen_weights():
    _require_r82()

    session = _session(AiRotationR82EconomicRoleDynamicRepR57SignalStrategy())
    decision = session.evaluate(
        StrategyDecisionContext(
            "20200131", _RoleView("20200131", include_defense_asset=True)
        )
    )
    row = decision.diagnostics["factor_scores"]["D1"]

    assert row["observations"] == 49
    assert row["bias_required_observations"] == 49
    assert row["slope_required_observations"] == 25
    assert row["efficiency_required_observations"] == 25
    assert decision.diagnostics["score_model"]["weights"] == {
        "bias": 0.3,
        "slope": 0.3,
        "efficiency": 0.4,
    }
    assert decision.diagnostics["score_model"]["lookback_days"] == 49


def test_r82_fails_closed_when_fewer_than_two_complete_representatives_exist():
    _require_r82()

    invalid = {"D2", "G", "O", "AU", "B"}
    decision = _session(AiRotationR82EconomicRoleDynamicRepR57SignalStrategy()).evaluate(
        StrategyDecisionContext(
            "20200131", _RoleView("20200131", invalid_factor_codes=invalid)
        )
    )

    assert "INSUFFICIENT_COMPLETE_CANDIDATES" in decision.reason_code
    assert decision.diagnostics["complete_candidate_count"] == 1
    assert decision.diagnostics["selected_roles"] == []
    assert decision.diagnostics["factor_scores"]["D1"]["complete_candidate"] is True


def test_r82_future_rows_do_not_change_historical_factor_scores():
    _require_r82()

    base = _session(AiRotationR82EconomicRoleDynamicRepR57SignalStrategy()).evaluate(
        StrategyDecisionContext("20200131", _RoleView("20200131"))
    )
    future = _session(AiRotationR82EconomicRoleDynamicRepR57SignalStrategy()).evaluate(
        StrategyDecisionContext(
            "20200131", _RoleView("20200131", future_rows=True)
        )
    )

    assert future.diagnostics["factor_scores"] == base.diagnostics["factor_scores"]


def test_r82_fails_closed_on_incomplete_factor_columns():
    _require_r82()

    decision = _session(AiRotationR82EconomicRoleDynamicRepR57SignalStrategy()).evaluate(
        StrategyDecisionContext("20200131", _IncompleteFactorView("20200131"))
    )

    assert decision.diagnostics["complete_candidate_count"] == 0
    assert "INSUFFICIENT_COMPLETE_CANDIDATES" in decision.reason_code
    assert all(
        row["status_code"] == "INVALID_FACTOR_INPUT"
        for row in decision.diagnostics["factor_scores"].values()
    )


def test_r82_hard_failed_representative_switches_and_re_scores_new_representative():
    _require_r82()

    session = _session(AiRotationR82EconomicRoleDynamicRepR57SignalStrategy())
    session.evaluate(StrategyDecisionContext("20200103", _RoleView("20200103")))
    decision = session.evaluate(
        StrategyDecisionContext("20200131", _RoleView("20200131", failed_code="D1"))
    )

    assert decision.diagnostics["selection_modes"][CN_DEFENSIVE_EQUITY] == (
        "HARD_FAILURE_FALLBACK"
    )
    assert decision.diagnostics["factor_scores"]["D2"]["is_representative"] is True
    assert decision.diagnostics["factor_scores"]["D2"]["observations"] == 49
    assert "D1" not in decision.diagnostics["factor_scores"]


def test_r82_selects_top_three_roles_with_one_third_base_slots():
    _require_r82()

    decision = _session(AiRotationR82EconomicRoleDynamicRepR57SignalStrategy()).evaluate(
        StrategyDecisionContext("20200131", _RoleView("20200131"))
    )
    selected = decision.diagnostics["selected_roles"]
    base_weights = decision.diagnostics["base_target_weights"]

    assert len(selected) == 3
    assert decision.diagnostics["top_n"] == 3
    assert decision.diagnostics["slot_weight"] == pytest.approx(1 / 3)
    assert all(base_weights[code] == pytest.approx(1 / 3) for code in base_weights)


def test_r82_downstream_weights_are_exact_r34_r39_r76_composition():
    _require_r82()

    session = _session(AiRotationR82EconomicRoleDynamicRepR57SignalStrategy())
    decision = session.evaluate(
        StrategyDecisionContext(
            "20200131", _RoleView("20200131", include_defense_asset=True)
        )
    )
    base = decision.diagnostics["base_target_weights"]
    staged, staged_cash, staged_codes = apply_staged_reentry({}, base)
    carried, carried_cash, carried_codes, incumbents = apply_incumbent_carry(
        {}, staged
    )
    expected, expected_cash, _ = apply_defense_asset(
        carried, carried_cash, defense_code="511010.SH"
    )

    assert dict(decision.target_weights) == expected
    assert decision.cash_weight == pytest.approx(expected_cash)
    assert set(decision.diagnostics["staged_reentry_codes"]) == staged_codes
    assert set(decision.diagnostics["incumbent_carry_codes"]) == incumbents
    assert decision.diagnostics["risk_layer"] == "fixed_short_bond"
    assert decision.diagnostics["defense_asset"] == "511010.SH"
    assert "STAGED_REENTRY" in decision.reason_code
    assert "FIXED_SHORT_BOND_DEFENSE" in decision.reason_code
    assert carried_codes == staged_codes
    assert staged_cash == pytest.approx(1.0 - sum(staged.values()))


def test_nonrepresentative_role_member_changes_do_not_change_r82_score():
    _require_r82()

    first = _session(AiRotationR82EconomicRoleDynamicRepR57SignalStrategy()).evaluate(
        StrategyDecisionContext("20200131", _RoleView("20200131", defensive_member_close=1.0))
    )
    second = _session(AiRotationR82EconomicRoleDynamicRepR57SignalStrategy()).evaluate(
        StrategyDecisionContext("20200131", _RoleView("20200131", defensive_member_close=100.0))
    )

    assert first.diagnostics["factor_scores"]["D1"] == second.diagnostics[
        "factor_scores"
    ]["D1"]
    assert "D2" not in first.diagnostics["factor_scores"]
    assert "D2" not in second.diagnostics["factor_scores"]


def test_r82_outputs_strict_json_without_cluster_identity_and_meets_runner_pit_contract():
    _require_r82()

    strategy = AiRotationR82EconomicRoleDynamicRepR57SignalStrategy()
    config = EconomicRoleConfig()
    session = _session(strategy)
    decision = session.evaluate(
        StrategyDecisionContext(
            "20200131", _RoleView("20200131", include_defense_asset=True)
        )
    )
    requirements = strategy.resolve_requirements(config)

    validate_target_decision(
        decision,
        set(_RoleView(include_defense_asset=True).codes),
        set(),
    )
    json.dumps(decision.diagnostics, ensure_ascii=False, allow_nan=False)
    serialized = json.dumps(decision.diagnostics, ensure_ascii=False)
    assert "cluster_id" not in serialized
    assert "persistent_geometric_role_momentum" not in serialized
    finalized = _artifacts(session)
    json.dumps(finalized, ensure_ascii=False, allow_nan=False)
    json.dumps(session.finalize().decision_trace, ensure_ascii=False, allow_nan=False)
    assert "persistent_geometric_role_momentum" not in json.dumps(
        finalized, ensure_ascii=False
    )
    assert requirements.frequency == "weekly"
    assert {"fund", "fact_fund_adj", "dim_fund"} <= set(
        requirements.required_datasets
    )
    assert {"close", "adj_factor", "amount"} <= set(requirements.required_fields)
    assert decision.diagnostics["effective_universe_hash"]
    assert decision.diagnostics["effective_role_assignment_hash"]


def test_r82_rejects_unfrozen_top_n_configuration():
    _require_r82()

    strategy = AiRotationR82EconomicRoleDynamicRepR57SignalStrategy()
    with pytest.raises(ValueError, match="top_n=3"):
        strategy.create_session(
            StrategyInitializationContext("r82-test", ("20200131",)),
            EconomicRoleConfig(top_n=2),
        )
