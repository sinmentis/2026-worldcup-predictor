from __future__ import annotations

import sqlite3
from dataclasses import asdict
from typing import Any

from worldcup_predictor import config, db
from worldcup_predictor import intel as _intel
from worldcup_predictor.goal_model import GoalModel, history_frame
from worldcup_predictor.models import IntelEvent
from worldcup_predictor.predict import predict_match
from worldcup_predictor.simulate import simulate_tournament, standings_from_results


def get_group_standings(conn: sqlite3.Connection, group: str) -> list[dict[str, Any]]:
    group = group.upper()
    teams = config.GROUPS[group]
    results = [
        (r["home_team"], r["away_team"], r["home_score"], r["away_score"])
        for r in conn.execute(
            "SELECT home_team, away_team, home_score, away_score FROM matches "
            "WHERE group_id=? AND status='FINISHED'",
            (group,),
        ).fetchall()
    ]
    return [asdict(row) for row in standings_from_results(teams, results)]


def get_upcoming_matches(conn: sqlite3.Connection, limit: int = 10) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT id, stage, group_id, home_team, away_team, kickoff, status "
        "FROM matches WHERE status='SCHEDULED' ORDER BY COALESCE(kickoff,''), id LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_knockout_bracket(conn: sqlite3.Connection) -> dict[str, list[dict[str, Any]]]:
    rounds: dict[str, list[dict[str, Any]]] = {}
    for stage in ("R32", "R16", "QF", "SF", "3RD", "FINAL"):
        rows = conn.execute(
            "SELECT id, home_team, away_team, home_score, away_score, status "
            "FROM matches WHERE stage=? ORDER BY id",
            (stage,),
        ).fetchall()
        rounds[stage] = [dict(r) for r in rows]
    return rounds


def get_match_detail(conn: sqlite3.Connection, match_id: int) -> dict[str, Any]:
    match = conn.execute("SELECT * FROM matches WHERE id=?", (match_id,)).fetchone()
    pred = conn.execute(
        "SELECT * FROM predictions WHERE match_id=? ORDER BY created_at DESC LIMIT 1",
        (match_id,),
    ).fetchone()
    return {
        "match": dict(match) if match else None,
        "prediction": dict(pred) if pred else None,
    }


def get_last_update_ts(conn: sqlite3.Connection) -> str | None:
    return db.get_last_update_ts(conn)


_MODEL: GoalModel | None = None
_MODEL_DB: str | None = None


def _db_path(conn: sqlite3.Connection) -> str:
    row = conn.execute("PRAGMA database_list").fetchone()
    return str(row["file"]) if row else ""


def get_model(conn: sqlite3.Connection, refit: bool = False) -> GoalModel:
    """Return a fitted goal model, cached per database file.

    The cache is keyed on the DB path so reusing the process against a different database
    (e.g. across tests) refits instead of silently reusing a stale model.
    """
    global _MODEL, _MODEL_DB
    path = _db_path(conn)
    if _MODEL is None or refit or path != _MODEL_DB:
        frame = history_frame(conn)
        if frame.empty:
            raise ValueError(
                "No historical data loaded. Run 'worldcup load-history' before predicting."
            )
        _MODEL = GoalModel().fit(frame)
        _MODEL_DB = path
    return _MODEL


def record_result(
    conn: sqlite3.Connection, match_id: int, home_score: int, away_score: int
) -> None:
    cur = conn.execute(
        "UPDATE matches SET home_score=?, away_score=?, status='FINISHED' WHERE id=?",
        (home_score, away_score, match_id),
    )
    if cur.rowcount == 0:
        raise ValueError(f"No match with id {match_id}")
    conn.commit()
    db.touch_update(conn)


_VALID_DIRECTIONS = {"weaken", "strengthen"}


def record_intel_event(conn: sqlite3.Connection, **kwargs: Any) -> None:
    direction = str(kwargs.get("direction", "")).strip().lower()
    if direction not in _VALID_DIRECTIONS:
        raise ValueError("direction must be 'weaken' or 'strengthen'")
    credibility = float(kwargs.get("credibility", 0.0))
    if not 0.0 <= credibility <= 1.0:
        raise ValueError("credibility must be in [0, 1]")
    _intel.record_intel(conn, IntelEvent(**kwargs))
    db.touch_update(conn)


def predict_fixture(conn: sqlite3.Connection, match_id: int) -> dict[str, Any]:
    m = conn.execute(
        "SELECT home_team, away_team, neutral FROM matches WHERE id=?", (match_id,)
    ).fetchone()
    if m is None:
        raise ValueError(f"No match with id {match_id}")
    model = get_model(conn)
    pred = predict_match(
        conn, model, m["home_team"], m["away_team"], match_id=match_id, neutral=bool(m["neutral"])
    )
    db.touch_update(conn)
    return {
        "home_team": pred.home_team,
        "away_team": pred.away_team,
        "p_home": pred.p_home,
        "p_draw": pred.p_draw,
        "p_away": pred.p_away,
        "exp_home_goals": pred.exp_home_goals,
        "exp_away_goals": pred.exp_away_goals,
        "most_likely": pred.most_likely_scoreline,
        "factors": [
            {"team": f.team, "description": f.description, "delta": f.lambda_delta}
            for f in pred.factors
        ],
    }


def run_simulation(
    conn: sqlite3.Connection, n: int = 50_000, seed: int | None = None
) -> dict[str, dict[str, float]]:
    model = get_model(conn)
    result = simulate_tournament(conn, model, n=n, seed=seed)
    db.touch_update(conn)
    return result
