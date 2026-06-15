import numpy as np
import pandas as pd
import pytest

from worldcup_predictor.goal_model import GoalModel


@pytest.fixture
def history() -> pd.DataFrame:
    # synthetic: "Strong" beats "Weak" repeatedly; "Mid" in between
    rng = np.random.default_rng(0)
    rows = []
    for _ in range(60):
        rows.append(("2024-01-01", "Strong", "Weak", rng.integers(2, 5), rng.integers(0, 2), False))
        rows.append(("2024-01-01", "Mid", "Weak", rng.integers(1, 4), rng.integers(0, 2), False))
        rows.append(("2024-01-01", "Strong", "Mid", rng.integers(1, 4), rng.integers(0, 3), False))
    return pd.DataFrame(
        rows, columns=["date", "home_team", "away_team", "home_goals", "away_goals", "neutral"]
    )


def test_grid_probs_sum_to_one(history):
    m = GoalModel().fit(history)
    grid = m.predict_grid("Strong", "Weak", neutral=True)
    assert abs(grid.home_win + grid.draw + grid.away_win - 1.0) < 1e-6


def test_strong_beats_weak(history):
    m = GoalModel().fit(history)
    grid = m.predict_grid("Strong", "Weak", neutral=True)
    assert grid.home_win > grid.away_win


def test_most_likely_and_exact(history):
    m = GoalModel().fit(history)
    grid = m.predict_grid("Strong", "Weak", neutral=True)
    h, a = grid.most_likely()
    assert isinstance(h, int) and isinstance(a, int)
    assert 0.0 <= grid.exact(1, 0) <= 1.0
