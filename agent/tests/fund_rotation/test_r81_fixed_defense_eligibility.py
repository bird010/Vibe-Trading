"""R81 fixed defense asset eligibility behavior tests."""

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
from backtest.fund_rotation.strategies.economic_role_rotation.roles import (
    BOND,
    CN_DEFENSIVE_EQUITY,
)
from backtest.fund_rotation.strategies.economic_role_rotation.strategy import (
    AiRotationR81EconomicRoleDynamicRepresentativeStrategy,
)


class _R81DefenseView:
    def __init__(self, signal_date: str, *, defense_close: float) -> None:
        self.signal_date = signal_date
        self.instruments = (
            FundInstrument("D", "红利ETF", "20100101"),
            FundInstrument("511010.SH", "10年国债ETF", "20100101"),
        )
        self.defense_close = defense_close

    def eligible_universe(self):
        return self.instruments

    def returns(self, frequency: str, lookback: int):
        assert frequency == "weekly"
        return pd.DataFrame(
            {instrument.ts_code: [0.01] * lookback for instrument in self.instruments}
        )

    def daily_bars(self, fields, lookback=None):
        rows = []
        for instrument in self.instruments:
            close = 1.0 if instrument.ts_code == "D" else self.defense_close
            rows.extend(
                {
                    "ts_code": instrument.ts_code,
                    "trade_date": self.signal_date,
                    "close": close,
                    "amount": 100.0,
                }
                for _ in range(20)
            )
        return pd.DataFrame(rows)

    def fund_adjustments(self, lookback=None):
        return pd.DataFrame(
            {
                "ts_code": [instrument.ts_code for instrument in self.instruments],
                "trade_date": [self.signal_date] * len(self.instruments),
                "adj_factor": [1.0] * len(self.instruments),
            }
        )


def _evaluate(defense_close: float):
    config = EconomicRoleConfig(
        top_n=2,
        fixed_role_manifest={
            CN_DEFENSIVE_EQUITY: ("D",),
            BOND: ("511010.SH",),
            "CN_GROWTH_EQUITY": ("G",),
            "OVERSEAS_GROWTH_EQUITY": ("O",),
            "GOLD": ("AU",),
        },
    )
    strategy = AiRotationR81EconomicRoleDynamicRepresentativeStrategy()
    session = strategy.create_session(
        StrategyInitializationContext("r81-defense", ("20200131",)), config
    )
    return session.evaluate(
        StrategyDecisionContext(
            "20200131",
            _R81DefenseView("20200131", defense_close=defense_close),
        )
    )


def test_fixed_defense_unavailable_falls_back_to_cash():
    decision = _evaluate(0.0)

    assert "511010.SH" not in decision.target_weights
    assert decision.cash_weight > 0.0
    assert decision.reason_code.endswith("FIXED_SHORT_BOND_UNAVAILABLE")
    assert decision.diagnostics["defense_asset"] is None


def test_fixed_defense_eligible_is_preserved():
    decision = _evaluate(1.0)

    assert decision.target_weights["511010.SH"] == 0.75
    assert decision.cash_weight == 0.0
    assert decision.reason_code.endswith("FIXED_SHORT_BOND_DEFENSE")
    assert decision.diagnostics["defense_asset"] == "511010.SH"
