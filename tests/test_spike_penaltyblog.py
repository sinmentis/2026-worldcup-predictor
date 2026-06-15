import pandas as pd


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
        df["home_goals"].to_numpy().copy(),
        df["away_goals"].to_numpy().copy(),
        df["home_team"].to_numpy(),
        df["away_team"].to_numpy(),
    )
    model.fit()
    grid = model.predict("A", "B")
    probs = [grid.home_win, grid.draw, grid.away_win]
    assert abs(sum(probs) - 1.0) < 1e-6
    assert all(0.0 <= p <= 1.0 for p in probs)
