"""TDD contract test for R85: R81 representatives plus R74 scoring."""

from __future__ import annotations

from backtest.fund_rotation.contracts import StrategyDecisionContext
from tests.fund_rotation.test_ai_rotation_r83_r81_r57_r77_combo import (
    _DefensePoolView,
    _session,
)

from backtest.fund_rotation.strategies.ai_rotation_r85_r81_r74_combo.strategy import (
    AiRotationR85R81R74ComboStrategy,
)


def test_r85_keeps_r81_representatives_and_uses_r74_vol_adjusted_score():
    config = _session()._config
    session = AiRotationR85R81R74ComboStrategy().create_session(None, config)
    decision = session.evaluate(
        StrategyDecisionContext("20200131", _DefensePoolView())
    )

    assert decision.diagnostics["score_model"]["id"] == "r74_momentum_over_volatility_60"
    assert decision.diagnostics["score_model"]["scope"] == "ECONOMIC_ROLE_REPRESENTATIVE"

