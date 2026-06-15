from __future__ import annotations

import csv
import io
import os
import sqlite3
from itertools import combinations
from typing import Any

import httpx

from worldcup_predictor import config
from worldcup_predictor import db as _db


def _to_bool_int(raw: str) -> int:
    return 1 if str(raw).strip().lower() in {"true", "1", "yes"} else 0


def _parse_int(raw: str | None) -> int | None:
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return None


def load_history_from_text(conn: sqlite3.Connection, text: str) -> int:
    reader = csv.DictReader(io.StringIO(text))
    count = 0
    for row in reader:
        home_score = _parse_int(row.get("home_score"))
        away_score = _parse_int(row.get("away_score"))
        if home_score is None or away_score is None:
            continue  # skip unplayed/NA/non-numeric rows (e.g. in-progress fixtures)
        conn.execute(
            "INSERT INTO historical_matches"
            "(date, home_team, away_team, home_score, away_score, tournament, neutral)"
            " VALUES (?,?,?,?,?,?,?)",
            (
                row["date"],
                row["home_team"],
                row["away_team"],
                home_score,
                away_score,
                row.get("tournament", ""),
                _to_bool_int(row.get("neutral", "False")),
            ),
        )
        count += 1
    conn.commit()
    return count


def load_history(conn: sqlite3.Connection, url: str | None = None) -> int:
    resp = httpx.get(url or config.HISTORY_CSV_URL, timeout=60.0, follow_redirects=True)
    resp.raise_for_status()
    return load_history_from_text(conn, resp.text)


def seed_teams_and_fixtures(conn: sqlite3.Connection) -> None:
    for gid, teams in config.GROUPS.items():
        for team in teams:
            conn.execute(
                "INSERT OR REPLACE INTO teams(name, group_id, elo, is_host) VALUES (?,?,?,?)",
                (team, gid, config.DEFAULT_ELO, 1 if team in config.HOSTS else 0),
            )
    mid = 1
    for gid, teams in config.GROUPS.items():
        for home, away in combinations(teams, 2):
            conn.execute(
                "INSERT INTO matches(id, stage, group_id, home_team, away_team, neutral, status)"
                " VALUES (?, 'group', ?, ?, ?, 1, 'SCHEDULED')",
                (mid, gid, home, away),
            )
            mid += 1
    conn.commit()


def apply_results_payload(conn: sqlite3.Connection, payload: dict[str, Any]) -> int:
    updated = 0
    for m in payload.get("matches", []):
        if m.get("status") != "FINISHED":
            continue
        home = m["homeTeam"]["name"]
        away = m["awayTeam"]["name"]
        ft = m["score"]["fullTime"]
        # Seeded fixtures use an arbitrary home/away order (itertools.combinations), so
        # match the pair order-independently and store scores in the seeded orientation.
        cur = conn.execute(
            "UPDATE matches SET home_score=?, away_score=?, status='FINISHED' "
            "WHERE home_team=? AND away_team=? AND status!='FINISHED'",
            (ft["home"], ft["away"], home, away),
        )
        if cur.rowcount == 0:
            cur = conn.execute(
                "UPDATE matches SET home_score=?, away_score=?, status='FINISHED' "
                "WHERE home_team=? AND away_team=? AND status!='FINISHED'",
                (ft["away"], ft["home"], away, home),
            )
        updated += cur.rowcount
    conn.commit()
    if updated:
        _db.touch_update(conn)
    return updated


def fetch_live_results(conn: sqlite3.Connection, token: str | None = None) -> int:
    token = token or os.environ.get("FOOTBALL_DATA_TOKEN", "")
    url = f"{config.FOOTBALL_DATA_BASE}/competitions/{config.FOOTBALL_DATA_COMP}/matches"
    headers = {"X-Auth-Token": token} if token else {}
    resp = httpx.get(url, headers=headers, params={"status": "FINISHED"}, timeout=60.0)
    resp.raise_for_status()
    return apply_results_payload(conn, resp.json())
