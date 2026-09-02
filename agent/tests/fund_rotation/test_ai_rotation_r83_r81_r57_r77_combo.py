"""TDD contract tests for R83: R82 plus the R77 defense layer."""

from __future__ import annotations

import pandas as pd

from backtest.fund_rotation.causal_data import FundInstrument
from backtest.fund_rotation.contracts import (
    StrategyDecisionContext,
    StrategyInitializationContext,
)
from backtest.fund_rotation.strategies.economic_role_rotation.config import (
    EconomicRoleConfig,
)
from tests.fund_rotation.test_ai_rotation_r82_economic_role_dynamic_rep_r57_signal import (  # type: ignore[import-not-found]
    _RoleView,
)

from backtest.fund_rotation.strategies.ai_rotation_r83_r81_r57_r77_combo.strategy import (
    AiRotationR83R81R57R77ComboStrategy,
)


class _DefensePoolView(_RoleView):
    def __init__(self) -> None:
        super().__init__(include_defense_asset=True)
        self.instruments = self.instruments + (
            FundInstrument("511880.SH", "货币基金ETF", "20100101"),
            FundInstrument("518880.SH", "黄金ETF", "20100101"),
        )
        self.codes = [instrument.ts_code for instrument in self.instruments]

    def returns(self, frequency: str, lookback: int):
        assert frequency == "weekly"
        values = {code: [0.01] * lookback for code in self.codes}
        values["511010.SH"] = [-0.01] * lookback
        values["511880.SH"] = [0.02] * lookback
        values["518880.SH"] = [0.01] * lookback
        return pd.DataFrame(values)


class _DefenseSignalMissingView(_DefensePoolView):
    def daily_bars(self, fields, lookback=None):
        frame = super().daily_bars(fields, lookback)
        return frame.loc[
            ~(
                frame["ts_code"].isin({"511010.SH", "511880.SH", "518880.SH"})
                & frame["trade_date"].eq(self._signal_date)
            )
        ].reset_index(drop=True)


class _NoPositiveDefenseView(_DefensePoolView):
    def returns(self, frequency: str, lookback: int):
        frame = super().returns(frequency, lookback)
        for code in ("511010.SH", "511880.SH", "518880.SH"):
            frame[code] = -0.01
        return frame


def _session():
    config = EconomicRoleConfig(
        fixed_role_manifest={
            "CN_DEFENSIVE_EQUITY": ("D1", "D2"),
            "CN_GROWTH_EQUITY": ("G",),
            "OVERSEAS_GROWTH_EQUITY": ("O",),
            "GOLD": ("AU",),
            "BOND": ("B",),
        }
    )
    return AiRotationR83R81R57R77ComboStrategy().create_session(
        StrategyInitializationContext(
            "r83-test", ("20200103", "20200110", "20200131")
        ),
        config,
    )


def test_r83_uses_r77_relative_momentum_only_for_the_defense_arm():
    decision = _session().evaluate(
        StrategyDecisionContext("20200131", _DefensePoolView())
    )

    assert decision.diagnostics["score_model"]["id"] == "r57_three_factor"
    assert decision.diagnostics["risk_layer"] == "defense_relative_momentum"
    assert decision.diagnostics["defense_asset"] == "511880.SH"
    assert decision.diagnostics["defense_layer"]["arm"] == "relative_momentum"
    assert "DEFENSE_RELATIVE_MOMENTUM" in decision.reason_code
    assert decision.diagnostics["defense_layer"]["audit"]["observations"] == 4


def test_r83_fails_closed_to_cash_when_defense_scores_or_signal_day_are_unusable():
    for view in (_NoPositiveDefenseView(), _DefenseSignalMissingView()):
        decision = _session().evaluate(
            StrategyDecisionContext("20200131", view)
        )
        assert decision.diagnostics["defense_asset"] is None
        assert decision.diagnostics["risk_layer"] == "defense_relative_momentum"
        assert "DEFENSE_CASH_FALLBACK" in decision.reason_code
