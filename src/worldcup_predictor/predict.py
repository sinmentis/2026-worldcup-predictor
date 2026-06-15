from __future__ import annotations

import sqlite3
import time

from worldcup_predictor import calibrate, config, intel
from worldcup_predictor.goal_model import GoalModel, ScoreGrid, retilt_grid
from worldcup_predictor.models import IntelFactor, MatchPrediction

MODEL_VERSION = "dc-v1"


def host_adjust(lam_h: float, lam_a: float, home: str, away: str) -> tuple[float, float]:
    """Give a host nation a modest expected-goals bump when it plays a non-host.

    All WC2026 matches are at neutral venues, but the host nations (USA/Mexico/Canada)
    still enjoy a home-crowd edge that ``neutral=True`` strips out.
    """
    h_host = home in config.HOSTS
    a_host = away in config.HOSTS
    if h_host and not a_host:
        return lam_h * config.HOST_ADVANTAGE, lam_a
    if a_host and not h_host:
        return lam_h, lam_a * config.HOST_ADVANTAGE
    return lam_h, lam_a


def adjusted_grid(
    conn: sqlite3.Connection,
    model: GoalModel,
    home: str,
    away: str,
    neutral: bool = True,
) -> tuple[ScoreGrid, list[IntelFactor]]:
    """Return the score grid for a match with host advantage and off-pitch intel applied.

    Shared by both single-match prediction and the tournament simulation so the two are
    always consistent. Host advantage and intel shift each team's expected goals; the fitted
    Dixon-Coles grid is re-tilted toward the new lambdas (no-op when nothing applies).
    """
    grid = model.predict_grid(home, away, neutral=neutral)
    lam_h, lam_a = grid.exp_goals()
    host_h, host_a = host_adjust(lam_h, lam_a, home, away)
    new_h, new_a, factors = intel.apply_intel(host_h, host_a, home, away, conn)
    if (new_h, new_a) != (lam_h, lam_a):
        grid = retilt_grid(grid, lam_h, lam_a, new_h, new_a)
    return grid, factors


def predict_match(
    conn: sqlite3.Connection,
    model: GoalModel,
    home: str,
    away: str,
    match_id: int | None = None,
    neutral: bool = True,
    apply_intel: bool = True,
) -> MatchPrediction:
    if apply_intel:
        grid, factors = adjusted_grid(conn, model, home, away, neutral=neutral)
    else:
        grid = model.predict_grid(home, away, neutral=neutral)
        factors = []
    lam_h, lam_a = grid.exp_goals()

    ml_h, ml_a = grid.most_likely()
    # Post-hoc calibration of the 1X2 (raises under-weighted draws, tames over-confidence).
    # No-op until parameters are fitted and stored, so behaviour is unchanged by default.
    p_home, p_draw, p_away = calibrate.apply(
        grid.home_win, grid.draw, grid.away_win, calibrate.load(conn)
    )
    pred = MatchPrediction(
        home_team=home,
        away_team=away,
        p_home=p_home,
        p_draw=p_draw,
        p_away=p_away,
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
