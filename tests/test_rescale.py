from worldcup_predictor.goal_model import poisson_grid, retilt_grid


def test_poisson_grid_expected_goals_match():
    grid = poisson_grid(1.8, 1.1, max_goals=15)
    eh, ea = grid.exp_goals()
    assert abs(eh - 1.8) < 0.02
    assert abs(ea - 1.1) < 0.02
    assert abs(grid.matrix.sum() - 1.0) < 1e-9


def test_retilt_unchanged_lambdas_is_noop():
    grid = poisson_grid(1.5, 1.2)
    same = retilt_grid(grid, 1.5, 1.2, 1.5, 1.2)
    assert abs((same.matrix - grid.matrix).max()) < 1e-12


def test_retilt_shifts_means_toward_targets_and_normalizes():
    grid = poisson_grid(1.8, 1.1)
    tilted = retilt_grid(grid, 1.8, 1.1, 1.2, 1.1)  # weaken home only
    eh, ea = tilted.exp_goals()
    assert eh < 1.8  # home expected goals dropped
    assert abs(ea - 1.1) < 0.05  # away roughly unchanged
    assert abs(tilted.matrix.sum() - 1.0) < 1e-9

