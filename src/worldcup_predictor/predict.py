from __future__ import annotations

import sqlite3
import time

from worldcup_predictor import intel
from worldcup_predictor.goal_model import GoalModel, poisson_grid
from worldcup_predictor.models import IntelFactor, MatchPrediction

MODEL_VERSION = "dc-elo-v1"


def predict_match(
    conn: sqlite3.Connection,
    model: GoalModel,
    home: str,
    away: str,
    match_id: int | None = None,
    neutral: bool = True,
    apply_intel: bool = True,
) -> MatchPrediction:
    grid = model.predict_grid(home, away, neutral=neutral)
    lam_h, lam_a = grid.exp_goals()
    factors: list[IntelFactor] = []
    if apply_intel:
        new_h, new_a, factors = intel.apply_intel(lam_h, lam_a, home, away, conn)
        if (new_h, new_a) != (lam_h, lam_a):
            grid = poisson_grid(new_h, new_a)
            lam_h, lam_a = new_h, new_a

    ml_h, ml_a = grid.most_likely()
    pred = MatchPrediction(
        home_team=home,
        away_team=away,
        p_home=grid.home_win,
        p_draw=grid.draw,
        p_away=grid.away_win,
        exp_home_goals=lam_h,
        exp_away_goals=lam_a,
        ml_home=ml_h,
        ml_away=ml_a,
        factors=factors,
    )
    if match_id is not None:
        reasoning = "; ".join(
            f"{f.team}: {f.description} (Δλ={f.lambda_delta:+.2f})" for f in factors
        )
        conn.execute(
            "INSERT INTO predictions(match_id, created_at, p_home, p_draw, p_away,"
            " exp_home_goals, exp_away_goals, ml_home, ml_away, model_version, reasoning)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                match_id,
                time.time(),
                pred.p_home,
                pred.p_draw,
                pred.p_away,
                lam_h,
                lam_a,
                ml_h,
                ml_a,
                MODEL_VERSION,
                reasoning,
            ),
        )
        conn.commit()
    return pred
