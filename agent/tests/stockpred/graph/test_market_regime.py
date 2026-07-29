"""Unit tests for src.stockpred.graph.market_regime."""

from __future__ import annotations

import pandas as pd
import pytest

from src.stockpred.graph.market_regime import (
    apply_regime_overlay,
    detect_market_regime,
    generate_regime_summary,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_features_with_predictions(
    n: int = 20,
    *,
    direction: str = "强",
    stage: str = "启动",
    industry_momentum: float = 0.05,
) -> pd.DataFrame:
    """Build a features DataFrame with stage/direction columns pre-filled."""
    industries = [f"IND_{i:03d}" for i in range(4)]
    rows: list[dict[str, object]] = []
    for i in range(n):
        rows.append({
            "ts_code": f"{600000 + i:06d}.SH",
            "trade_date": "20260105",
            "industry": industries[i % len(industries)],
            "industry_momentum_20d": industry_momentum,
            "stage": stage,
            "direction": direction,
        })
    return pd.DataFrame(rows)


def _make_advisory_df(n: int = 4) -> pd.DataFrame:
    """Build a minimal advisory DataFrame."""
    return pd.DataFrame({
        "ts_code": [f"{600000 + i:06d}.SH" for i in range(n)],
        "action": ["买入", "增持", "持有", "减持"],
        "confidence": [0.8, 0.7, 0.5, 0.3],
        "position_weight": [0.4, 0.3, 0.2, 0.1],
        "stop_loss_pct": [-5.0, -5.0, -4.0, -3.0],
    })


# ---------------------------------------------------------------------------
# detect_market_regime
# ---------------------------------------------------------------------------


class TestDetectMarketRegime:
    def test_empty_dataframe_returns_default(self) -> None:
        result = detect_market_regime(pd.DataFrame())
        assert result["regime"] == "震荡市"
        assert result["confidence"] == 0.0
        assert result["positioning_suggestion"] == "精选"
        assert result["risk_appetite"] == "中性"

    def test_bull_market_detection(self) -> None:
        # High breadth (all positive momentum) + strong direction + bull stages
        df = _make_features_with_predictions(
            direction="强", stage="启动", industry_momentum=0.1
        )
        result = detect_market_regime(df)
        assert result["regime"] == "牛市"
        assert result["confidence"] > 0.5
        assert result["positioning_suggestion"] == "进攻"
        assert result["risk_appetite"] == "积极"

    def test_bear_market_detection(self) -> None:
        # Low breadth (negative momentum) + weak direction + bear stages
        df = _make_features_with_predictions(
            direction="弱", stage="退潮", industry_momentum=-0.1
        )
        result = detect_market_regime(df)
        assert result["regime"] == "熊市"
        assert result["confidence"] > 0.5
        assert result["positioning_suggestion"] == "防守"
        assert result["risk_appetite"] == "谨慎"

    def test_oscillation_market(self) -> None:
        # Mixed signals → oscillation
        df = _make_features_with_predictions(
            direction="中性", stage="无行情", industry_momentum=0.0
        )
        result = detect_market_regime(df)
        assert result["regime"] == "震荡市"
        assert result["positioning_suggestion"] == "精选"

    def test_stage_distribution_computed(self) -> None:
        df = _make_features_with_predictions(stage="确认")
        result = detect_market_regime(df)
        assert "确认" in result["stage_distribution"]
        assert result["stage_distribution"]["确认"] == 1.0

    def test_direction_distribution_computed(self) -> None:
        df = _make_features_with_predictions(direction="偏强")
        result = detect_market_regime(df)
        assert "偏强" in result["direction_distribution"]

    def test_breadth_calculation(self) -> None:
        # 2 industries positive, 2 negative → breadth = 0.5
        rows = []
        for i in range(4):
            rows.append({
                "ts_code": f"{600000 + i:06d}.SH",
                "industry": f"IND_{i:03d}",
                "industry_momentum_20d": 0.05 if i < 2 else -0.05,
                "stage": "无行情",
                "direction": "中性",
            })
        df = pd.DataFrame(rows)
        result = detect_market_regime(df)
        assert result["breadth"] == 0.5


# ---------------------------------------------------------------------------
# apply_regime_overlay
# ---------------------------------------------------------------------------


class TestApplyRegimeOverlay:
    def test_bull_market_boosts_confidence(self) -> None:
        advisory = _make_advisory_df()
        regime_info = {"regime": "牛市"}
        result = apply_regime_overlay(advisory, regime_info)

        # 买入/增持 rows should have boosted confidence
        buy_row = result[result["action"] == "买入"].iloc[0]
        assert buy_row["confidence"] == pytest.approx(0.88, abs=0.01)
        # position_weight boosted
        assert buy_row["position_weight"] == pytest.approx(0.48, abs=0.01)
        # action unchanged
        assert result["regime_overlay_action"].iloc[0] == "买入"

    def test_bull_market_confidence_capped_at_1(self) -> None:
        advisory = pd.DataFrame({
            "action": ["买入"],
            "confidence": [0.95],
            "position_weight": [0.5],
            "stop_loss_pct": [-5.0],
        })
        result = apply_regime_overlay(advisory, {"regime": "牛市"})
        assert result["confidence"].iloc[0] <= 1.0

    def test_bear_market_downgrades_action(self) -> None:
        advisory = _make_advisory_df()
        regime_info = {"regime": "熊市"}
        result = apply_regime_overlay(advisory, regime_info)

        # 买入 → 增持, 增持 → 持有
        assert result["regime_overlay_action"].iloc[0] == "增持"
        assert result["regime_overlay_action"].iloc[1] == "持有"
        # 持有/减持 unchanged
        assert result["regime_overlay_action"].iloc[2] == "持有"
        assert result["regime_overlay_action"].iloc[3] == "减持"

    def test_bear_market_reduces_position_weight(self) -> None:
        advisory = _make_advisory_df()
        result = apply_regime_overlay(advisory, {"regime": "熊市"})
        # position_weight * 0.7
        assert result["position_weight"].iloc[0] == pytest.approx(0.28, abs=0.01)

    def test_bear_market_tightens_stop_loss(self) -> None:
        advisory = _make_advisory_df()
        result = apply_regime_overlay(advisory, {"regime": "熊市"})
        # stop_loss_pct - 2.0
        assert result["stop_loss_pct"].iloc[0] == pytest.approx(-7.0, abs=0.01)

    def test_oscillation_market_no_change(self) -> None:
        advisory = _make_advisory_df()
        original_confidence = advisory["confidence"].tolist()
        result = apply_regime_overlay(advisory, {"regime": "震荡市"})
        assert result["confidence"].tolist() == original_confidence
        assert (result["regime_overlay_action"] == result["action"]).all()

    def test_does_not_mutate_input(self) -> None:
        advisory = _make_advisory_df()
        original = advisory.copy()
        apply_regime_overlay(advisory, {"regime": "熊市"})
        pd.testing.assert_frame_equal(advisory, original)


# ---------------------------------------------------------------------------
# generate_regime_summary
# ---------------------------------------------------------------------------


class TestGenerateRegimeSummary:
    def test_bull_summary_contains_key_info(self) -> None:
        regime_info = {
            "regime": "牛市",
            "confidence": 0.85,
            "breadth": 0.9,
            "momentum_dispersion": 0.3,
            "positioning_suggestion": "进攻",
            "risk_appetite": "积极",
            "stage_distribution": {"启动": 0.6, "确认": 0.3},
            "direction_distribution": {"强": 0.7, "偏强": 0.2},
        }
        summary = generate_regime_summary(regime_info)
        assert "牛市" in summary
        assert "85.0%" in summary
        assert "进攻" in summary
        assert "启动" in summary

    def test_bear_summary_contains_defense_advice(self) -> None:
        regime_info = {
            "regime": "熊市",
            "confidence": 0.7,
            "breadth": 0.2,
            "momentum_dispersion": 0.5,
            "positioning_suggestion": "防守",
            "risk_appetite": "谨慎",
            "stage_distribution": {},
            "direction_distribution": {},
        }
        summary = generate_regime_summary(regime_info)
        assert "防守" in summary
        assert "止损" in summary

    def test_oscillation_summary(self) -> None:
        regime_info = {
            "regime": "震荡市",
            "confidence": 0.6,
            "breadth": 0.5,
            "momentum_dispersion": 0.2,
            "positioning_suggestion": "精选",
            "risk_appetite": "中性",
            "stage_distribution": {},
            "direction_distribution": {},
        }
        summary = generate_regime_summary(regime_info)
        assert "精选" in summary

    def test_empty_distributions_omitted(self) -> None:
        regime_info = {
            "regime": "震荡市",
            "confidence": 0.5,
            "breadth": 0.5,
            "momentum_dispersion": 0.1,
            "positioning_suggestion": "精选",
            "risk_appetite": "中性",
            "stage_distribution": {},
            "direction_distribution": {},
        }
        summary = generate_regime_summary(regime_info)
        assert "阶段分布" not in summary
        assert "方向分布" not in summary
