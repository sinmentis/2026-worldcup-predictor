import pandas as pd
import pytest


@pytest.mark.skip(
    reason=(
        "MODEL_BACKEND=fallback: penaltyblog installs/imports on arm64, but "
        "DixonColesGoalModel.fit() fails with ValueError: buffer source array is read-only"
    )
)
def test_penaltyblog_imports_and_fits():
    from penaltyblog.models import DixonColesGoalModel

    df = pd.DataFrame(
        {
            "home_team": ["A", "B", "A", "C", "B", "C"] * 4,
            "away_team": ["B", "A", "C", "A", "C", "B"] * 4,
            "home_goals": [1, 2, 0, 3, 1, 2] * 4,
            "away_goals": [0, 1, 0, 1, 1, 2] * 4,
        }
    )
    model = DixonColesGoalModel(
        df["home_goals"], df["away_goals"], df["home_team"], df["away_team"]
    )
    model.fit()
    grid = model.predict("A", "B")
    probs = [grid.home_win, grid.draw, grid.away_win]
    assert abs(sum(probs) - 1.0) < 1e-6
    assert all(0.0 <= p <= 1.0 for p in probs)
