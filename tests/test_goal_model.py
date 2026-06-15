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


def test_neutral_flag_changes_prediction(history):
    m = GoalModel().fit(history)
    neutral_grid = m.predict_grid("Strong", "Weak", neutral=True)
    home_grid = m.predict_grid("Strong", "Weak", neutral=False)
    assert not np.allclose(neutral_grid.matrix, home_grid.matrix)


def test_fit_accepts_xi_and_decay_changes_predictions():
    # Two eras with different strength gaps; the decay xi controls how much the recent era
    # outweighs the old one, so different xi must yield different predictions.
    rng = np.random.default_rng(2)
    rows = []
    for i in range(40):
        d_old = f"2020-{1 + i % 9:02d}-01"
        rows.append((d_old, "Strong", "Weak", int(rng.integers(3, 6)), 0, False))
        rows.append((d_old, "Weak", "Strong", 0, int(rng.integers(3, 6)), False))
    for i in range(40):
        d_new = f"2024-{1 + i % 9:02d}-01"
        rows.append((d_new, "Strong", "Weak", 0, int(rng.integers(3, 6)), False))
        rows.append((d_new, "Weak", "Strong", int(rng.integers(3, 6)), 0, False))
    hist = pd.DataFrame(
        rows, columns=["date", "home_team", "away_team", "home_goals", "away_goals", "neutral"]
    )
    slow = GoalModel().fit(hist, xi=0.0003)  # long memory -> old era (Strong dominates) counts
    fast = GoalModel().fit(hist, xi=0.01)  # short memory -> recent era (Weak dominates) counts
    g_slow = slow.predict_grid("Strong", "Weak", neutral=True)
    g_fast = fast.predict_grid("Strong", "Weak", neutral=True)
    assert abs(g_slow.home_win - g_fast.home_win) > 1e-3  # decay actually changes the fit
