from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from math import exp, factorial

import numpy as np
import pandas as pd
from penaltyblog.models import (  # type: ignore[import-untyped]
    DixonColesGoalModel,
    dixon_coles_weights,
)

from worldcup_predictor import config


@dataclass
class ScoreGrid:
    matrix: np.ndarray  # matrix[h, a] = P(home=h, away=a)

    @property
    def home_win(self) -> float:
        return float(np.tril(self.matrix, -1).sum())

    @property
    def away_win(self) -> float:
        return float(np.triu(self.matrix, 1).sum())

    @property
    def draw(self) -> float:
        return float(np.trace(self.matrix))

    def exp_goals(self) -> tuple[float, float]:
        idx = np.arange(self.matrix.shape[0])
        eh = float((self.matrix.sum(axis=1) * idx).sum())
        ea = float((self.matrix.sum(axis=0) * idx).sum())
        return eh, ea

    def most_likely(self) -> tuple[int, int]:
        h, a = np.unravel_index(int(np.argmax(self.matrix)), self.matrix.shape)
        return int(h), int(a)

    def exact(self, h: int, a: int) -> float:
        return float(self.matrix[h, a])

    def over(self, line: float) -> float:
        total = 0.0
        for h in range(self.matrix.shape[0]):
            for a in range(self.matrix.shape[1]):
                if h + a > line:
                    total += self.matrix[h, a]
        return float(total)

    def btts(self) -> float:
        return float(self.matrix[1:, 1:].sum())


class GoalModel:
    """Dixon-Coles wrapper. Backend = penaltyblog (fallback: see plan Task 4.x)."""

    def __init__(self) -> None:
        self._model: DixonColesGoalModel | None = None

    def fit(self, history: pd.DataFrame) -> GoalModel:
        # penaltyblog's Cython core needs writable arrays and real datetimes (arm64-verified):
        # pandas Series are read-only buffers, and dixon_coles_weights wants datetimes not strings.
        weights = dixon_coles_weights(pd.to_datetime(history["date"]), xi=config.TIME_DECAY_XI)
        self._model = DixonColesGoalModel(
            history["home_goals"].to_numpy().copy(),
            history["away_goals"].to_numpy().copy(),
            history["home_team"].to_numpy(),
            history["away_team"].to_numpy(),
            weights=np.asarray(weights, dtype="float64").copy(),
            neutral_venue=history["neutral"].to_numpy(),
        )
        self._model.fit()
        return self

    def predict_grid(
        self, home: str, away: str, neutral: bool = True, max_goals: int = 15
    ) -> ScoreGrid:
        if self._model is None:
            raise RuntimeError("GoalModel.fit() must be called before predict_grid()")
        fpg = self._model.predict(home, away, max_goals=max_goals, neutral_venue=neutral)
        matrix = np.asarray(fpg.grid, dtype=float)
        matrix = matrix / matrix.sum()
        return ScoreGrid(matrix=matrix)


def history_frame(conn: sqlite3.Connection, since: str = "2018-01-01") -> pd.DataFrame:
    rows = conn.execute(
        "SELECT date, home_team, away_team, home_score, away_score, neutral "
        "FROM historical_matches WHERE date >= ? "
        "AND home_score IS NOT NULL AND away_score IS NOT NULL ORDER BY date",
        (since,),
    ).fetchall()
    return pd.DataFrame(
        [
            {
                "date": r["date"],
                "home_team": r["home_team"],
                "away_team": r["away_team"],
                "home_goals": r["home_score"],
                "away_goals": r["away_score"],
                "neutral": bool(r["neutral"]),
            }
            for r in rows
        ],
        columns=["date", "home_team", "away_team", "home_goals", "away_goals", "neutral"],
    )


def poisson_grid(lam_h: float, lam_a: float, max_goals: int = 15) -> ScoreGrid:
    def pmf(k: int, lam: float) -> float:
        return exp(-lam) * lam**k / factorial(k)

    h = np.array([pmf(i, lam_h) for i in range(max_goals)])
    a = np.array([pmf(j, lam_a) for j in range(max_goals)])
    matrix = np.outer(h, a)
    matrix = matrix / matrix.sum()
    return ScoreGrid(matrix=matrix)


def retilt_grid(
    grid: ScoreGrid,
    lam_h_old: float,
    lam_a_old: float,
    lam_h_new: float,
    lam_a_new: float,
) -> ScoreGrid:
    """Shift a fitted grid's marginals from old to new expected goals while preserving
    its dependency structure (the Dixon-Coles low-score correction).

    Multiplies cell (h, a) by the Poisson pmf ratio (lam_new/lam_old)^h for the home
    axis and ^a for the away axis, then renormalizes. This is the exact transform for an
    independent-Poisson grid and a shape-preserving approximation for Dixon-Coles; when
    the lambdas are unchanged the grid is returned untouched (continuity).
    """
    n = grid.matrix.shape[0]
    tilt_h = (lam_h_new / lam_h_old) ** np.arange(n)
    tilt_a = (lam_a_new / lam_a_old) ** np.arange(n)
    matrix = grid.matrix * np.outer(tilt_h, tilt_a)
    return ScoreGrid(matrix=matrix / matrix.sum())
