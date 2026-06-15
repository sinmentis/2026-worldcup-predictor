from __future__ import annotations

import csv
import io
import sqlite3

import httpx

from worldcup_predictor import config


def _to_bool_int(raw: str) -> int:
    return 1 if str(raw).strip().lower() in {"true", "1", "yes"} else 0


def load_history_from_text(conn: sqlite3.Connection, text: str) -> int:
    reader = csv.DictReader(io.StringIO(text))
    count = 0
    for row in reader:
        if not row.get("home_score") or not row.get("away_score"):
            continue
        conn.execute(
            "INSERT INTO historical_matches"
            "(date, home_team, away_team, home_score, away_score, tournament, neutral)"
            " VALUES (?,?,?,?,?,?,?)",
            (
                row["date"],
                row["home_team"],
                row["away_team"],
                int(row["home_score"]),
                int(row["away_score"]),
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
