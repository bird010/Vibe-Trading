from __future__ import annotations

from backtest.fund_rotation.champion_validation.stability_surface import (
    build_stability_grid,
    evaluate_stability_surface,
)


def _result(point, *, excess=0.02, sharpe=1.0, technical_status="PASS"):
    return {
        "momentum_window": point.momentum_window,
        "top_n": point.top_n,
        "recluster_weeks": point.recluster_weeks,
        "annualized_excess_return": excess,
        "sharpe": sharpe,
        "technical_status": technical_status,
    }


def test_stability_grid_is_exactly_45_preregistered_points():
    grid = build_stability_grid()

    assert len(grid) == 45
    assert len({(p.momentum_window, p.top_n, p.recluster_weeks) for p in grid}) == 45
    assert {p.momentum_window for p in grid} == {3, 4, 6, 8, 12}
    assert {p.top_n for p in grid} == {2, 3, 4}
    assert {p.recluster_weeks for p in grid} == {13, 26, 52}


def test_stability_surface_applies_positive_neighborhood_and_sharpe_gates():
    grid = build_stability_grid()
    results = [_result(point) for point in grid]

    evaluated = evaluate_stability_surface(results)

    assert evaluated.status == "PASS"
    assert evaluated.complete_points == 45
    assert evaluated.positive_excess_ratio == 1.0
    assert evaluated.neighborhood_positive_ratio == 1.0
    assert evaluated.neighborhood_sharpe_median == 1.0
    assert not hasattr(evaluated, "winner")
    assert not hasattr(evaluated, "recommended_parameter")


def test_stability_surface_rejects_a_positive_center_parameter_island():
    grid = build_stability_grid()
    results = []
    for point in grid:
        is_center = (point.momentum_window, point.top_n, point.recluster_weeks) == (4, 3, 26)
        results.append(_result(point, excess=0.02 if is_center else -0.01))

    evaluated = evaluate_stability_surface(results)

    assert evaluated.status == "FAIL"
    assert evaluated.parameter_island is True
    assert "PARAMETER_ISLAND" in evaluated.reason_codes


def test_stability_surface_technical_failure_blocks_pass():
    grid = build_stability_grid()
    results = [_result(point) for point in grid]
    results[-1]["technical_status"] = "FAIL"

    evaluated = evaluate_stability_surface(results)

    assert evaluated.status == "FAIL"
    assert "TECHNICAL_FAILURE" in evaluated.reason_codes


def test_stability_surface_rejects_duplicate_or_out_of_grid_results():
    grid = build_stability_grid()
    results = [_result(point) for point in grid]
    results.append(_result(grid[0]))
    results.append({**_result(grid[0]), "momentum_window": 99})

    evaluated = evaluate_stability_surface(results)

    assert evaluated.status == "FAIL"
    assert any(code.startswith("DUPLICATE:") for code in evaluated.technical_failures)
    assert any(code.startswith("OUT_OF_GRID:") for code in evaluated.technical_failures)


def test_stability_surface_requires_explicit_technical_terminal_status_for_every_point():
    grid = build_stability_grid()
    results = [_result(point) for point in grid]
    del results[0]["technical_status"]

    evaluated = evaluate_stability_surface(results)

    assert evaluated.status == "FAIL"
    assert evaluated.complete_points == 44
    assert any(code.startswith("MISSING_TECHNICAL_STATUS:") for code in evaluated.technical_failures)
