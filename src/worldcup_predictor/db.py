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
    status TEXT DEFAULT 'SCHEDULED',  -- 'SCHEDULED' | 'FINISHED'
    ext_id INTEGER,                   -- football-data match id (knockout upsert key)
    winner_team TEXT                  -- decisive winner (penalty-settled knockouts)
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
CREATE TABLE IF NOT EXISTS news_articles (
    id INTEGER PRIMARY KEY,
    source TEXT,
    url TEXT UNIQUE,
    title TEXT,
    summary TEXT,
    published_at TEXT,
    fetched_at REAL,
    processed INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS player_status (
    id INTEGER PRIMARY KEY,
    team TEXT NOT NULL,
    player TEXT NOT NULL,
    tier TEXT NOT NULL,
    status TEXT NOT NULL,
    credibility REAL NOT NULL,
    sources TEXT NOT NULL,
    official INTEGER DEFAULT 0,
    valid_until TEXT,
    as_of REAL NOT NULL,
    pending INTEGER DEFAULT 0,
    notes TEXT,
    affects TEXT NOT NULL DEFAULT 'attack'
        CHECK (affects IN ('attack','defense','both')),
    UNIQUE(team, player)
);
CREATE TABLE IF NOT EXISTS team_signal (
    id INTEGER PRIMARY KEY,
    team TEXT NOT NULL,
    category TEXT NOT NULL,
    direction TEXT NOT NULL,
    magnitude_tier TEXT NOT NULL,
    credibility REAL NOT NULL,
    sources TEXT NOT NULL,
    official INTEGER DEFAULT 0,
    valid_until TEXT,
    as_of REAL NOT NULL,
    pending INTEGER DEFAULT 0,
    notes TEXT,
    affects TEXT NOT NULL DEFAULT 'attack'
        CHECK (affects IN ('attack','defense','both')),
    UNIQUE(team, category)
);
CREATE TABLE IF NOT EXISTS odds (
    id INTEGER PRIMARY KEY,
    match_id INTEGER,
    bookmaker TEXT NOT NULL,
    price_home REAL,
    price_draw REAL,
    price_away REAL,
    commence_time TEXT,
    fetched_at REAL,
    UNIQUE(match_id, bookmaker)
);
CREATE TABLE IF NOT EXISTS odds_totals (
    id INTEGER PRIMARY KEY,
    match_id INTEGER,
    bookmaker TEXT NOT NULL,
    line REAL NOT NULL,
    price_over REAL,
    price_under REAL,
    fetched_at REAL,
    UNIQUE(match_id, bookmaker, line)
);
CREATE TABLE IF NOT EXISTS odds_spreads (
    id INTEGER PRIMARY KEY,
    match_id INTEGER,
    bookmaker TEXT NOT NULL,
    line REAL NOT NULL,             -- HOME handicap (favourite negative, e.g. -1.5)
    price_home REAL,                -- price for home covering (home + line)
    price_away REAL,                -- price for away covering (away - line)
    fetched_at REAL,
    UNIQUE(match_id, bookmaker, line)
);
CREATE TABLE IF NOT EXISTS paper_bets (
    id INTEGER PRIMARY KEY,
    match_id INTEGER NOT NULL,
    market TEXT NOT NULL,            -- '1x2' | 'totals' | 'spreads'
    outcome TEXT NOT NULL,          -- home | draw | away | over | under (home/away for spreads)
    line REAL,                      -- totals line / home handicap; NULL for 1x2
    our_prob REAL NOT NULL,
    market_prob REAL NOT NULL,      -- de-margined consensus at log time
    edge REAL NOT NULL,
    price_taken REAL NOT NULL,      -- best decimal price when first flagged (when you'd bet)
    bookmaker TEXT,
    kelly_frac REAL NOT NULL,       -- fraction-of-bankroll stake (already fractional Kelly)
    logged_at REAL NOT NULL,
    kickoff TEXT,
    closing_price REAL,             -- best decimal price near kickoff (the closing line)
    closing_market_prob REAL,       -- de-margined consensus at close
    clv REAL,                       -- no-vig CLV: price_taken * closing_market_prob - 1
    clv_price REAL,                 -- price CLV: price_taken / closing_price - 1
    closed_at REAL,
    result TEXT,                    -- 'win' | 'loss' | 'push'
    pnl_flat REAL,                  -- units, flat 1u stake
    pnl_kelly REAL,                 -- units, fractional-Kelly stake on the notional bankroll
    settled_at REAL
);
"""


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    p = Path(path) if path is not None else config.DB_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


_AFFECTS_TABLES = ("player_status", "team_signal")


def _has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    return any(r[1] == column for r in conn.execute(f"PRAGMA table_info({table})"))


def migrate(conn: sqlite3.Connection) -> None:
    """Idempotently bring an existing DB up to schema. Safe on fresh and existing DBs.

    `CREATE TABLE IF NOT EXISTS` is a no-op on an existing table, so a column added to
    SCHEMA never reaches the live prod DB; this adds `affects` via ALTER where missing.
    `ALTER TABLE ... ADD COLUMN ... NOT NULL DEFAULT 'attack'` backfills existing rows.
    """
    for table in _AFFECTS_TABLES:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        if not exists:
            continue
        if not _has_column(conn, table, "affects"):
            conn.execute(f"ALTER TABLE {table} ADD COLUMN affects TEXT NOT NULL DEFAULT 'attack'")
    has_matches = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='matches'"
    ).fetchone()
    if has_matches:
        if not _has_column(conn, "matches", "ext_id"):
            conn.execute("ALTER TABLE matches ADD COLUMN ext_id INTEGER")
        if not _has_column(conn, "matches", "winner_team"):
            conn.execute("ALTER TABLE matches ADD COLUMN winner_team TEXT")
    conn.commit()


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)  # fresh DBs get `affects` (with CHECK)
    migrate(conn)  # existing DBs get `affects` via ALTER
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
