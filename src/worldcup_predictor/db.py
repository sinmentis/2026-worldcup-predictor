from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from worldcup_predictor import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS teams (
    name TEXT PRIMARY KEY,
    group_id TEXT,
    elo REAL,
    fifa_rank INTEGER,
    is_host INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS matches (
    id INTEGER PRIMARY KEY,
    stage TEXT NOT NULL,          -- 'group' | 'R32' | 'R16' | 'QF' | 'SF' | '3RD' | 'FINAL'
    group_id TEXT,
    slot TEXT,                    -- e.g. 'W73', 'RU-A', '3rd-1' for knockout placeholders
    home_team TEXT,
    away_team TEXT,
    kickoff TEXT,
    neutral INTEGER DEFAULT 1,
    home_score INTEGER,
    away_score INTEGER,
    status TEXT DEFAULT 'SCHEDULED'  -- 'SCHEDULED' | 'FINISHED'
);
CREATE TABLE IF NOT EXISTS historical_matches (
    id INTEGER PRIMARY KEY,
    date TEXT,
    home_team TEXT,
    away_team TEXT,
    home_score INTEGER,
    away_score INTEGER,
    tournament TEXT,
    neutral INTEGER DEFAULT 0
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_hist_match
    ON historical_matches(date, home_team, away_team, tournament, home_score, away_score);
CREATE TABLE IF NOT EXISTS predictions (
    id INTEGER PRIMARY KEY,
    match_id INTEGER,
    created_at REAL,
    p_home REAL, p_draw REAL, p_away REAL,
    exp_home_goals REAL, exp_away_goals REAL,
    ml_home INTEGER, ml_away INTEGER,
    model_version TEXT,
    reasoning TEXT
);
CREATE TABLE IF NOT EXISTS intel_events (
    id INTEGER PRIMARY KEY,
    created_at REAL,
    team TEXT,
    player TEXT,
    event_type TEXT,              -- injury | illness | suspension | rotation | morale | other
    direction TEXT,               -- 'weaken' | 'strengthen'
    magnitude REAL,               -- lambda multiplier delta, e.g. -0.15
    source_url TEXT,
    credibility REAL,             -- 0..1
    valid_from TEXT,
    notes TEXT
);
CREATE TABLE IF NOT EXISTS ratings_history (
    id INTEGER PRIMARY KEY, team TEXT, date TEXT, elo REAL
);
CREATE TABLE IF NOT EXISTS sim_results (
    id INTEGER PRIMARY KEY, created_at REAL, team TEXT,
    advance_prob REAL, r16_prob REAL, qf_prob REAL, sf_prob REAL,
    final_prob REAL, title_prob REAL, n_iter INTEGER
);
CREATE TABLE IF NOT EXISTS metrics (
    id INTEGER PRIMARY KEY, created_at REAL, metric TEXT, value REAL, scope TEXT
);
CREATE TABLE IF NOT EXISTS tuning_params (
    key TEXT PRIMARY KEY, value TEXT, updated_at REAL
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    p = Path(path) if path is not None else config.DB_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def touch_update(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT INTO meta(key, value) VALUES('last_update', ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (str(time.time()),),
    )
    conn.commit()


def get_last_update_ts(conn: sqlite3.Connection) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key='last_update'").fetchone()
    return row[0] if row else None
