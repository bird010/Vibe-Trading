from backtest.fund_rotation.strategies.ai_rotation_r63_r59_rank_buffer.strategy import select_rank_buffer_clusters

def test_r63_retains_previous_rank_four_and_fills_remaining_slot():
    selected, details = select_rank_buffer_clusters([1, 4, 2, 3], {3}, {1, 2, 3, 4})
    assert selected == [3, 1, 4]
    assert details["retained_clusters"] == [3]

def test_r63_forces_rank_five_out():
    selected, details = select_rank_buffer_clusters([1, 2, 4, 5, 3], {3}, {1, 2, 3, 4, 5})
    assert 3 not in selected
    assert details["forced_exit_clusters"] == [3]
