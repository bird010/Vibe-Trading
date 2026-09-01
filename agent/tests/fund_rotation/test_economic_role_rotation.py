"""TDD contract tests for the v4.3 Economic Role research strategies."""

from __future__ import annotations

import math
from types import SimpleNamespace

import pandas as pd
import pytest

try:
    from backtest.fund_rotation.strategies.economic_role_rotation.config import (
        EconomicRoleConfig,
    )
    from backtest.fund_rotation.strategies.economic_role_rotation.roles import (
        AMBIGUOUS,
        BOND,
        CN_DEFENSIVE_EQUITY,
        CN_GROWTH_EQUITY,
        EMPTY_NAME,
        GOLD,
        MATCHED,
        OVERSEAS_GROWTH_EQUITY,
        UNCLASSIFIED,
        classify_fund_name,
        role_rule_hash,
        select_dynamic_representative,
        select_fixed_representative,
    )
    from backtest.fund_rotation.strategies.economic_role_rotation.strategy import (
        AiRotationR80EconomicRoleFixedRepresentativeStrategy,
        compute_role_score,
        persistent_geometric_role_score,
    )
    from backtest.fund_rotation.causal_data import FundInstrument
    from backtest.fund_rotation.contracts import (
        StrategyDecisionContext,
        StrategyInitializationContext,
    )
    from src.stockpred.fund_rotation.batch_child_runtime import _role_input_contract
    _IMPORT_ERROR = None
except ImportError as exc:  # Red phase: package is not implemented yet.
    EconomicRoleConfig = None
    AMBIGUOUS = BOND = CN_DEFENSIVE_EQUITY = CN_GROWTH_EQUITY = None
    EMPTY_NAME = GOLD = MATCHED = OVERSEAS_GROWTH_EQUITY = None
    UNCLASSIFIED = None
    classify_fund_name = role_rule_hash = None
    select_dynamic_representative = select_fixed_representative = None
    persistent_geometric_role_score = None
    compute_role_score = None
    AiRotationR80EconomicRoleFixedRepresentativeStrategy = None
    FundInstrument = StrategyDecisionContext = StrategyInitializationContext = None
    _role_input_contract = None
    _IMPORT_ERROR = exc


def _require_implementation() -> None:
    assert _IMPORT_ERROR is None, f"Economic Role package missing: {_IMPORT_ERROR}"


def test_classifier_matches_roles_with_specific_tiers_and_exclusions() -> None:
    _require_implementation()

    defensive = classify_fund_name("红利低波ETF")
    growth = classify_fund_name("创业板50ETF")
    overseas = classify_fund_name("NASDAQ100 ETF")
    gold = classify_fund_name("黄金ETF")
    bond = classify_fund_name("10年国债ETF")

    assert (defensive.status, defensive.role_id, defensive.tier) == (
        MATCHED,
        CN_DEFENSIVE_EQUITY,
        1,
    )
    assert (growth.status, growth.role_id, growth.tier) == (
        MATCHED,
        CN_GROWTH_EQUITY,
        1,
    )
    assert (overseas.status, overseas.role_id, overseas.tier) == (
        MATCHED,
        OVERSEAS_GROWTH_EQUITY,
        1,
    )
    assert (gold.status, gold.role_id, gold.tier) == (MATCHED, GOLD, 1)
    assert (bond.status, bond.role_id, bond.tier) == (MATCHED, BOND, 1)

    assert classify_fund_name("黄金股票ETF").status == UNCLASSIFIED
    assert classify_fund_name("纳指医药ETF").status == UNCLASSIFIED
    assert classify_fund_name("信用债ETF").status == UNCLASSIFIED
    assert classify_fund_name("").status == EMPTY_NAME


def test_classifier_marks_multi_role_matches_ambiguous_and_does_not_choose_silently() -> None:
    _require_implementation()

    result = classify_fund_name("黄金ETF10年国债ETF")

    assert result.status == AMBIGUOUS
    assert result.role_id is None
    assert result.tier is None
    assert result.exclusion_reason == "AMBIGUOUS_ROLE_MATCH"


def test_specific_index_phrase_is_evaluated_before_broader_phrase() -> None:
    _require_implementation()

    result = classify_fund_name("创业板50ETF")

    assert result.role_id == CN_GROWTH_EQUITY
    assert result.tier == 1
    assert result.matched_rule == "创业板50"


def test_bond_classifier_accepts_real_name_period_variant() -> None:
    _require_implementation()

    result = classify_fund_name("上证10年期国债ETF")

    assert (result.status, result.role_id, result.tier) == (MATCHED, BOND, 1)


def test_role_rule_hash_is_canonical_and_stable() -> None:
    _require_implementation()

    assert role_rule_hash() == role_rule_hash()
    assert len(role_rule_hash()) == 64
    assert role_rule_hash() != ""


def test_fixed_representative_follows_manifest_and_filters_ineligible_candidates() -> None:
    _require_implementation()

    selected = select_fixed_representative(
        ["PRIMARY", "FALLBACK"],
        candidates={"PRIMARY", "FALLBACK"},
        eligible={"FALLBACK"},
        adv={"FALLBACK": 1.0},
    )

    assert selected == "FALLBACK"


def test_dynamic_representative_prefers_tier_before_adv_and_code_tie_break() -> None:
    _require_implementation()

    selected = select_dynamic_representative(
        {
            "TIER2_HIGH_ADV": (2, 100.0),
            "TIER1_LOW_ADV": (1, 1.0),
            "TIER1_TIE": (1, 1.0),
        },
        eligible={"TIER2_HIGH_ADV", "TIER1_LOW_ADV", "TIER1_TIE"},
    )

    assert selected == "TIER1_LOW_ADV"


def test_persistent_role_score_uses_role_return_m0_m1_and_role_scope() -> None:
    _require_implementation()

    score = persistent_geometric_role_score(
        current_momentum=0.20,
        lagged_momentum=0.05,
    )

    assert score.value == pytest.approx(math.sqrt(1.20 * 1.05) - 1.0)
    assert score.eligible is True
    assert score.scope == "ECONOMIC_ROLE"
    assert score.subject_id == "role:CN_DEFENSIVE_EQUITY"
    assert score.display_label == "持续几何 Role 动量"
    assert "cluster" not in str(score.components).lower()


@pytest.mark.parametrize(
    ("current", "lagged"),
    [(0.0, 0.1), (-0.01, 0.1), (0.1, 0.0), (0.1, -0.01), (math.nan, 0.1)],
)
def test_role_score_requires_finite_strictly_positive_m0_and_m1(
    current: float,
    lagged: float,
) -> None:
    _require_implementation()

    score = persistent_geometric_role_score(current, lagged)

    assert score.value is None
    assert score.eligible is False


def test_config_freezes_v43_windows_and_manifest_content() -> None:
    _require_implementation()

    config = EconomicRoleConfig()

    assert config.history_quality_lookback_weeks == 52
    assert config.correlation_lookback_weeks == 52
    assert config.min_valid_weeks == 20
    assert config.momentum_window_weeks == 4
    assert config.refresh_interval_weeks == 26
    assert config.warmup_trade_days == 264
    assert config.representative_liquidity_window_days == 20
    assert config.representative_min_liquidity_observations == 15
    assert config.fixed_role_manifest
    with pytest.raises(Exception):
        config.top_n = 2


def test_config_rejects_mismatched_history_quality_window() -> None:
    _require_implementation()

    with pytest.raises(ValueError, match="history_quality_lookback_weeks"):
        EconomicRoleConfig(history_quality_lookback_weeks=26)


def test_role_score_averages_members_before_applying_m0_m1_formula() -> None:
    _require_implementation()

    weekly = pd.DataFrame(
        {
            "A": [0.01, 0.02, 0.03, 0.04, 0.05],
            "B": [0.03, 0.04, 0.05, 0.06, 0.07],
        }
    )
    score, diagnostics = compute_role_score(
        weekly, ["A", "B"], CN_DEFENSIVE_EQUITY, momentum_window=4,
    )

    role_returns = [0.02, 0.03, 0.04, 0.05, 0.06]
    m1 = math.prod(1.0 + value for value in role_returns[:4]) - 1.0
    m0 = math.prod(1.0 + value for value in role_returns[1:]) - 1.0
    assert diagnostics["M1"] == pytest.approx(m1)
    assert diagnostics["M0"] == pytest.approx(m0)
    assert score.value == pytest.approx(math.sqrt((1 + m0) * (1 + m1)) - 1)


class _LifecycleView:
    def __init__(self, signal_date: str, *, failed_code: str | None = None) -> None:
        self.signal_date = signal_date
        self.failed_code = failed_code
        self.instruments = (
            FundInstrument("D1", "红利低波ETF", "20100101"),
            FundInstrument("D2", "红利ETF", "20100101"),
            FundInstrument("G", "创业板50ETF", "20100101"),
            FundInstrument("O", "NASDAQ100 ETF", "20100101"),
            FundInstrument("AU", "黄金ETF", "20100101"),
            FundInstrument("B", "10年国债ETF", "20100101"),
        )
        self.codes = [instrument.ts_code for instrument in self.instruments]

    def eligible_universe(self):
        return self.instruments

    def returns(self, frequency: str, lookback: int):
        assert frequency == "weekly"
        return pd.DataFrame({code: [0.01] * lookback for code in self.codes})

    def daily_bars(self, fields, lookback=None):
        if "amount" in fields:
            rows = []
            for code in self.codes:
                values = [0.0] * 5 if code == self.failed_code else [100.0] * 20
                rows.extend(
                    {"ts_code": code, "trade_date": f"2020{index:04d}", "amount": value}
                    for index, value in enumerate(values, start=1)
                )
            return pd.DataFrame(rows)
        return pd.DataFrame(
            {"ts_code": self.codes, "trade_date": [self.signal_date] * len(self.codes), "close": [1.0] * len(self.codes)}
        )

    def fund_adjustments(self, lookback=None):
        return pd.DataFrame(
            {"ts_code": self.codes, "trade_date": [self.signal_date] * len(self.codes), "adj_factor": [1.0] * len(self.codes)}
        )


def test_representative_lifecycle_locks_then_switches_only_on_hard_failure() -> None:
    _require_implementation()

    config = EconomicRoleConfig(
        fixed_role_manifest={
            CN_DEFENSIVE_EQUITY: ("D1", "D2"),
            CN_GROWTH_EQUITY: ("G",),
            OVERSEAS_GROWTH_EQUITY: ("O",),
            GOLD: ("AU",),
            BOND: ("B",),
        }
    )
    strategy = AiRotationR80EconomicRoleFixedRepresentativeStrategy()
    session = strategy.create_session(
        StrategyInitializationContext("lifecycle", ("20200103", "20200110", "20200117")),
        config,
    )

    first = session.evaluate(StrategyDecisionContext("20200103", _LifecycleView("20200103")))
    second = session.evaluate(StrategyDecisionContext("20200110", _LifecycleView("20200110")))
    third = session.evaluate(
        StrategyDecisionContext("20200117", _LifecycleView("20200117", failed_code="D1"))
    )
    reps = [artifact for artifact in session.finalize().artifacts if artifact.role == "role_representatives"][0].payload
    defensive_reps = [row for row in reps if row["role_id"] == CN_DEFENSIVE_EQUITY]

    assert first.diagnostics["selection_modes"][CN_DEFENSIVE_EQUITY] == "REGULAR_REFRESH"
    assert second.diagnostics["selection_modes"][CN_DEFENSIVE_EQUITY] == "LOCK_MAINTENANCE"
    assert third.diagnostics["selection_modes"][CN_DEFENSIVE_EQUITY] == "HARD_FAILURE_FALLBACK"
    assert defensive_reps[1]["representative"] == "D1"
    assert defensive_reps[1]["previous_representative"] == "D1"
    assert defensive_reps[2]["representative"] == "D2"
    assert defensive_reps[2]["previous_representative"] == "D1"


def test_role_input_contract_is_identity_bearing_and_order_stable() -> None:
    _require_implementation()

    def decision(date: str, universe: str, assignment: str):
        return SimpleNamespace(
            signal_date=date,
            diagnostics={
                "role_rule_hash": "r" * 64,
                "effective_universe_hash": universe,
                "effective_role_assignment_hash": assignment,
            },
        )

    first = _role_input_contract(
        SimpleNamespace(decisions=[decision("20200110", "u2", "a2"), decision("20200103", "u1", "a1")])
    )
    second = _role_input_contract(
        SimpleNamespace(decisions=[decision("20200103", "u1", "a1"), decision("20200110", "u2", "a2")])
    )

    assert first["role_input_contract_hash"] != ""
    assert first["role_rule_hashes"] == ["r" * 64]
    assert first["role_input_contract_hash"] == second["role_input_contract_hash"]
