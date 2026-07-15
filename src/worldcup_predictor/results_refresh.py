from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass

from worldcup_predictor import db, engine, ingest, ratings

PIPELINE_VERSION = "2"


@dataclass(frozen=True)
class RefreshResult:
    processed: bool
    revision: str
    synced_matches: int


def processed_results_revision(conn: sqlite3.Connection) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key='processed_results_revision'").fetchone()
    return str(row["value"]) if row is not None else None


def results_revision(conn: sqlite3.Connection) -> str:
    rows = conn.execute(
        "SELECT id, stage, group_id, slot, home_team, away_team, kickoff, neutral, "
        "home_score, away_score, status, winner_team FROM matches ORDER BY id"
    ).fetchall()
    payload = [list(row) for row in rows]
    encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _revision_from_parts(match_revision: str, history_revision: int) -> str:
    encoded = f"{PIPELINE_VERSION}:{match_revision}:{history_revision}".encode()
    return hashlib.sha256(encoded).hexdigest()


def refresh_revision(conn: sqlite3.Connection) -> str:
    return _revision_from_parts(results_revision(conn), db.history_revision(conn))


def refresh_results_model(
    conn: sqlite3.Connection, n: int = 20_000, seed: int | None = None
) -> RefreshResult:
    initial_match_revision = results_revision(conn)
    initial_revision = _revision_from_parts(initial_match_revision, db.history_revision(conn))
    if initial_revision == processed_results_revision(conn):
        return RefreshResult(processed=False, revision=initial_revision, synced_matches=0)

    synced = ingest.sync_finished_to_history(conn)
    if results_revision(conn) != initial_match_revision:
        raise RuntimeError("Match state changed during refresh; retry")
    stable_history_revision = db.history_revision(conn)
    elo = ratings.calculate_elo_ratings(conn)
    if (
        results_revision(conn) != initial_match_revision
        or db.history_revision(conn) != stable_history_revision
    ):
        raise RuntimeError("Match or history state changed during refresh; retry")
    simulation = engine.calculate_simulation(conn, n=n, seed=seed)
    revision = _revision_from_parts(initial_match_revision, stable_history_revision)
    conn.execute("BEGIN IMMEDIATE")
    try:
        if (
            results_revision(conn) != initial_match_revision
            or db.history_revision(conn) != stable_history_revision
        ):
            raise RuntimeError("Match or history state changed during refresh; retry")
        ratings.store_elo_ratings(conn, elo)
        engine.store_simulation(conn, simulation, n)
        db.set_update_timestamp(conn)
        conn.execute(
            "INSERT INTO meta(key, value) VALUES('processed_results_revision', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (revision,),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return RefreshResult(processed=True, revision=revision, synced_matches=synced)
