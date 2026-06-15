from __future__ import annotations

import sqlite3
from dataclasses import asdict

from worldcup_predictor import config, db
from worldcup_predictor.simulate import standings_from_results


def get_group_standings(conn: sqlite3.Connection, group: str) -> list[dict]:
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


def get_upcoming_matches(conn: sqlite3.Connection, limit: int = 10) -> list[dict]:
    rows = conn.execute(
        "SELECT id, stage, group_id, home_team, away_team, kickoff, status "
        "FROM matches WHERE status='SCHEDULED' ORDER BY COALESCE(kickoff,''), id LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_knockout_bracket(conn: sqlite3.Connection) -> dict[str, list[dict]]:
    rounds: dict[str, list[dict]] = {}
    for stage in ("R32", "R16", "QF", "SF", "3RD", "FINAL"):
        rows = conn.execute(
            "SELECT id, home_team, away_team, home_score, away_score, status "
            "FROM matches WHERE stage=? ORDER BY id",
            (stage,),
        ).fetchall()
        rounds[stage] = [dict(r) for r in rows]
    return rounds


def get_match_detail(conn: sqlite3.Connection, match_id: int) -> dict:
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
