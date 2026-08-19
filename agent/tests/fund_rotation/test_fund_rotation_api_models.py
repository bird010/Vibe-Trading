from src.stockpred.fund_rotation.api_models import (
    PortfolioSnapshot,
    RebalanceIndexItem,
    StrategyScorePoint,
)


def test_rebalance_index_and_snapshots_expose_execution_semantics():
    item = RebalanceIndexItem(
        signal_date="20240105",
        sequence=1,
        quality_status="VALID",
        target_changed_positions=2,
        required_changed_positions=2,
        actual_changed_positions=0,
        executed_changed_positions=0,
        target_turnover=1.0,
        required_turnover=1.0,
        execution_turnover=0.0,
    )
    before = PortfolioSnapshot(
        as_of_date="20240105",
        source="ACTUAL_POSITION",
        weights={"510300.SH": 1.0},
    )
    target = PortfolioSnapshot(
        as_of_date="20240105",
        source="TARGET",
        weights={"159915.SZ": 1.0},
    )

    assert item.required_changed_positions == 2
    assert item.actual_changed_positions == 0
    assert item.executed_changed_positions == 0
    assert before.source == "ACTUAL_POSITION"
    assert target.source == "TARGET"


def test_strategy_score_point_keeps_subject_identity_per_point():
    point = StrategyScorePoint(
        date="20240105",
        value=-0.03,
        eligible=False,
        subject_id="cluster:7",
    )

    assert point.subject_id == "cluster:7"
    assert point.eligible is False
