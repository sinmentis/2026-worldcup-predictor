from worldcup_predictor.goal_model import poisson_grid


def test_poisson_grid_expected_goals_match():
    grid = poisson_grid(1.8, 1.1, max_goals=15)
    eh, ea = grid.exp_goals()
    assert abs(eh - 1.8) < 0.02
    assert abs(ea - 1.1) < 0.02
    assert abs(grid.matrix.sum() - 1.0) < 1e-9
