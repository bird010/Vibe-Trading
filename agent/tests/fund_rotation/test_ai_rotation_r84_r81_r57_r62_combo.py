"""TDD contract tests for R84: R82 plus true inverse-volatility weights."""

from __future__ import annotations

import numpy as np

from backtest.fund_rotation.contracts import StrategyDecisionContext
from tests.fund_rotation.test_ai_rotation_r83_r81_r57_r77_combo import (
    _DefensePoolView,
    _session,
)

from backtest.fund_rotation.strategies.ai_rotation_r84_r81_r57_r62_combo.strategy import (
    AiRotationR84R81R57R62ComboStrategy,
)


class _InverseVolView(_DefensePoolView):
    def adjusted_closes(self, lookback: int):
        index = np.arange(lookback, dtype=float)
        columns = {}
        for position, code in enumerate(self.codes):
            amplitude = 0.001 * (position + 1)
            columns[code] = 1.0 + 0.01 * index + amplitude * np.sin(index)
        return __import__("pandas").DataFrame(columns)


def test_r84_keeps_r57_selection_and_adds_true_inverse_volatility_weighting():
    config_session = _session()
    config = config_session._config
    session = AiRotationR84R81R57R62ComboStrategy().create_session(
        None, config
    )
    decision = session.evaluate(
        StrategyDecisionContext("20200131", _InverseVolView())
    )

    assert decision.diagnostics["score_model"]["id"] == "r57_three_factor"
    assert decision.diagnostics["portfolio_weighting"]["mode"] == "true_inverse_volatility"
    assert decision.diagnostics["portfolio_weighting"]["selected_codes"]
    assert "R62" in decision.diagnostics["portfolio_weighting"]["layer"]

