from __future__ import annotations

import csv
import datetime
import io
import logging
import os
import sqlite3
from itertools import combinations
from typing import Any

import httpx

from worldcup_predictor import config
from worldcup_predictor import db as _db

logger = logging.getLogger("worldcup.ingest")

_STAGE_MAP: dict[str, str] = {
    "GROUP_STAGE": "group",
    "LAST_32": "R32",
    "LAST_16": "R16",
    "QUARTER_FINALS": "QF",
    "SEMI_FINALS": "SF",
    "THIRD_PLACE": "3RD",
    "FINAL": "FINAL",
}
_KNOCKOUT_STAGES: frozenset[str] = frozenset(s for s, v in _STAGE_MAP.items() if v != "group")


def _winner_team(score: dict[str, Any], home: str | None, away: str | None) -> str | None:
    w = score.get("winner")
    if w == "HOME_TEAM":
        return home
    if w == "AWAY_TEAM":
        return away
    return None


def _knockout_outcome(
    score: dict[str, Any], home: str | None, away: str | None
) -> tuple[int | None, int | None, str | None]:
    """On-pitch score and decisive winner for a finished knockout match.

    A penalty shootout's ``fullTime`` folds in the shootout goals, so for 90'/ET bet
    settlement and display we store the regulation(+ET) score and record the shootout winner
    (from penalties, falling back to ``fullTime``) separately. Non-shootout matches keep
    ``fullTime`` and the feed's declared winner.
    """
    ft = score.get("fullTime") or {}
    if score.get("duration") == "PENALTY_SHOOTOUT":
        rt = score.get("regularTime") or {}
        et = score.get("extraTime") or {}
        if rt.get("home") is not None:
            hs = (rt.get("home") or 0) + (et.get("home") or 0)
            as_ = (rt.get("away") or 0) + (et.get("away") or 0)
        else:
            hs, as_ = ft.get("home"), ft.get("away")
        pens = score.get("penalties") or {}
        ph, pa = pens.get("home") or 0, pens.get("away") or 0
        if ph != pa:
            winner = home if ph > pa else away
        else:
            fth, fta = ft.get("home") or 0, ft.get("away") or 0
            winner = _winner_team(score, home, away) or (
                home if fth > fta else away if fta > fth else None
            )
        return hs, as_, winner
    return ft.get("home"), ft.get("away"), _winner_team(score, home, away)


def apply_knockout_fixtures(conn: sqlite3.Connection, payload: dict[str, Any]) -> int:
    """Upsert knockout matches (R32..FINAL) from the feed, keyed by the feed match id.

    Teams are stored when the feed knows them (else NULL for not-yet-decided slots). Scores,
    status and the decisive winner are filled in once a match is FINISHED. Idempotent: a later
    fetch updates the same row in place.
    """
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_matches_ext_id ON matches(ext_id)")
    touched = 0
    for m in payload.get("matches", []):
        stage = m.get("stage")
        if stage not in _KNOCKOUT_STAGES:
            continue
        ext_id = m.get("id")
        if ext_id is None:
            continue
        mapped = _STAGE_MAP[stage]
        ht: Any = m.get("homeTeam") or {}
        at: Any = m.get("awayTeam") or {}
        home = config.canonical_team(ht.get("name")) if ht.get("name") else None
        away = config.canonical_team(at.get("name")) if at.get("name") else None
        kickoff = m.get("utcDate")
        score = m.get("score") or {}
        finished = m.get("status") == "FINISHED"
        status = "FINISHED" if finished else "SCHEDULED"
        hs, as_, winner = _knockout_outcome(score, home, away) if finished else (None, None, None)
        conn.execute(
            "INSERT INTO matches(stage, slot, group_id, home_team, away_team, kickoff, "
            " neutral, home_score, away_score, status, ext_id, winner_team) "
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?) "
            " ON CONFLICT(ext_id) DO UPDATE SET stage=excluded.stage, home_team=excluded.home_team,"
            " away_team=excluded.away_team, kickoff=excluded.kickoff, "
            "home_score=excluded.home_score,"
            " away_score=excluded.away_score, status=excluded.status, "
            "winner_team=excluded.winner_team",
            (mapped, None, None, home, away, kickoff, 1, hs, as_, status, ext_id, winner),
        )
        touched += 1
    conn.commit()
    if touched:
        _db.touch_update(conn)
    return touched


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
        cur = conn.execute(
            "INSERT OR IGNORE INTO historical_matches"
            "(date, home_team, away_team, home_score, away_score, tournament, neutral)"
            " VALUES (?,?,?,?,?,?,?)",
            (
                row["date"],
                config.canonical_team(row["home_team"]),
                config.canonical_team(row["away_team"]),
                home_score,
                away_score,
                row.get("tournament", ""),
                _to_bool_int(row.get("neutral", "False")),
            ),
        )
        count += cur.rowcount  # OR IGNORE => 0 for duplicate rows, so reloads are idempotent
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
        if m.get("stage") in _KNOCKOUT_STAGES:
            continue
        if m.get("status") != "FINISHED":
            continue
        home = config.canonical_team(m["homeTeam"]["name"])
        away = config.canonical_team(m["awayTeam"]["name"])
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


def apply_fixtures_payload(conn: sqlite3.Connection, payload: dict[str, Any]) -> int:
    """Set each match's kickoff (utcDate) by team pair, order-independently, and apply any
    finished scores. Returns the number of fixtures whose kickoff was set."""
    set_kickoffs = 0
    for m in payload.get("matches", []):
        if m.get("stage") in _KNOCKOUT_STAGES:
            continue
        home = config.canonical_team(m["homeTeam"]["name"])
        away = config.canonical_team(m["awayTeam"]["name"])
        kickoff = m.get("utcDate")
        if kickoff:
            cur = conn.execute(
                "UPDATE matches SET kickoff=? WHERE "
                "(home_team=? AND away_team=?) OR (home_team=? AND away_team=?)",
                (kickoff, home, away, away, home),
            )
            set_kickoffs += 1 if cur.rowcount else 0
        if m.get("status") == "FINISHED":
            ft = m.get("score", {}).get("fullTime", {})
            if ft.get("home") is not None:
                c2 = conn.execute(
                    "UPDATE matches SET home_score=?, away_score=?, status='FINISHED' "
                    "WHERE home_team=? AND away_team=? AND status!='FINISHED'",
                    (ft["home"], ft["away"], home, away),
                )
                if c2.rowcount == 0:
                    conn.execute(
                        "UPDATE matches SET home_score=?, away_score=?, status='FINISHED' "
                        "WHERE home_team=? AND away_team=? AND status!='FINISHED'",
                        (ft["away"], ft["home"], away, home),
                    )
    conn.commit()
    _db.touch_update(conn)
    return set_kickoffs


def fetch_fixtures(conn: sqlite3.Connection, token: str | None = None) -> tuple[int, int]:
    """Fetch ALL WC fixtures; populate group kickoffs/results and knockout fixtures.

    Returns (group_kickoffs_set, knockout_rows_upserted).
    """
    token = token or os.environ.get("FOOTBALL_DATA_TOKEN", "")
    url = f"{config.FOOTBALL_DATA_BASE}/competitions/{config.FOOTBALL_DATA_COMP}/matches"
    headers = {"X-Auth-Token": token} if token else {}
    resp = httpx.get(url, headers=headers, timeout=60.0)
    resp.raise_for_status()
    payload = resp.json()
    groups = apply_fixtures_payload(conn, payload)
    knockout = apply_knockout_fixtures(conn, payload)
    return groups, knockout


def _parse_kickoff(raw: str | None) -> datetime.datetime | None:
    if not raw:
        return None
    try:
        return datetime.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def stale_unsettled_matches(
    conn: sqlite3.Connection,
    min_hours: float = 6.0,
    now: datetime.datetime | None = None,
) -> list[dict[str, Any]]:
    """Matches whose kickoff is more than ``min_hours`` in the past but still not FINISHED.

    These are matches an upstream feed left stuck (e.g. perpetually IN_PLAY/PAUSED), so they
    never settle automatically and their paper bets never close. We never fabricate a result
    from a live score; we surface them so a human can record the official result. Returns
    id/teams/kickoff/hours_overdue, most overdue first.
    """
    now = now or datetime.datetime.now(datetime.UTC)
    rows = conn.execute(
        "SELECT id, home_team, away_team, kickoff, status FROM matches "
        "WHERE status!='FINISHED' AND kickoff IS NOT NULL"
    ).fetchall()
    stale: list[dict[str, Any]] = []
    for r in rows:
        ko = _parse_kickoff(r["kickoff"])
        if ko is None:
            continue
        if ko.tzinfo is None:
            ko = ko.replace(tzinfo=datetime.UTC)
        hours_overdue = (now - ko).total_seconds() / 3600.0
        if hours_overdue >= min_hours:
            stale.append(
                {
                    "id": r["id"],
                    "home_team": r["home_team"],
                    "away_team": r["away_team"],
                    "kickoff": r["kickoff"],
                    "status": r["status"],
                    "hours_overdue": round(hours_overdue, 1),
                }
            )
    stale.sort(key=lambda m: m["hours_overdue"], reverse=True)
    return stale
