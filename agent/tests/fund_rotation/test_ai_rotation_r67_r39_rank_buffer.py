from backtest.fund_rotation.strategies.ai_rotation_r67_r39_rank_buffer.strategy import (
    AiRotationR67R39RankBufferStrategy,
    _quality_reason_code,
    select_rank_buffer_clusters,
)


def test_rank_four_incumbent_is_retained_and_displaces_rank_three_challenger():
    selected, retained = select_rank_buffer_clusters(
        [10, 20, 30, 40, 50],
        previous_selected=[40, 90],
        top_n=3,
        exit_rank=4,
    )

    assert selected == [40, 10, 20]
    assert retained == [40]


def test_rank_buffer_uses_cluster_rejection_reason_only_for_rejected_gate():
    assert _quality_reason_code("PASS") == ""
    assert _quality_reason_code("WARN") == ""
    assert _quality_reason_code("REJECT") == "CLUSTER_QUALITY_REJECTED"


def test_rank_five_prior_cluster_is_forced_out_and_current_candidate_fills_slot():
    selected, retained = select_rank_buffer_clusters(
        [10, 20, 30, 40, 50],
        previous_selected=[50],
        top_n=3,
        exit_rank=4,
    )

    assert selected == [10, 20, 30]
    assert retained == []


def test_epoch_reset_ignores_previous_selection():
    selected, retained = select_rank_buffer_clusters(
        [10, 20, 30, 40],
        previous_selected=[40],
        top_n=3,
        exit_rank=4,
        epoch_reset=True,
    )

    assert selected == [10, 20, 30]
    assert retained == []


def test_rank_buffer_is_deterministic_and_bounded_by_top_n():
    first = select_rank_buffer_clusters(
        [3, 1, 2, 4],
        previous_selected=[4, 2],
        top_n=2,
        exit_rank=4,
    )
    second = select_rank_buffer_clusters(
        [3, 1, 2, 4],
        previous_selected=[4, 2],
        top_n=2,
        exit_rank=4,
    )

    assert first == second == ([2, 4], [2, 4])


def test_strategy_has_new_identity_without_changing_r39():
    strategy = AiRotationR67R39RankBufferStrategy()

    assert strategy.descriptor.id == "ai_rotation_r67_r39_rank_buffer"
