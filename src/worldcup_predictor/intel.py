from __future__ import annotations

import sqlite3
import time

from worldcup_predictor.models import IntelEvent, IntelFactor

LAMBDA_MIN = 0.05
ADJUST_CLAMP = (-0.6, 0.6)  # bound the net multiplier delta per team


def record_intel(conn: sqlite3.Connection, event: IntelEvent) -> None:
    conn.execute(
        "INSERT INTO intel_events"
        "(created_at, team, player, event_type, direction, magnitude, source_url,"
        " credibility, valid_from, notes) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            time.time(),
            event.team,
            event.player,
            event.event_type,
            event.direction,
            event.magnitude,
            event.source_url,
            event.credibility,
            event.valid_from,
            event.notes,
        ),
    )
    conn.commit()


def active_intel_for(conn: sqlite3.Connection, team: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM intel_events WHERE team=? ORDER BY created_at DESC", (team,)
    ).fetchall()


def _team_factor(conn: sqlite3.Connection, team: str) -> tuple[float, list[IntelFactor]]:
    delta = 0.0
    factors: list[IntelFactor] = []
    for row in active_intel_for(conn, team):
        contrib = float(row["credibility"]) * float(row["magnitude"])
        delta += contrib
        label = row["player"] or row["event_type"]
        factors.append(
            IntelFactor(
                team=team,
                description=f"{label}: {row['event_type']} ({row['notes'] or ''})".strip(),
                lambda_delta=contrib,
            )
        )
    lo, hi = ADJUST_CLAMP
    return max(lo, min(hi, delta)), factors


def apply_intel(
    lam_h: float, lam_a: float, home: str, away: str, conn: sqlite3.Connection
) -> tuple[float, float, list[IntelFactor]]:
    dh, fh = _team_factor(conn, home)
    da, fa = _team_factor(conn, away)
    lam_h = max(LAMBDA_MIN, lam_h * (1 + dh))
    lam_a = max(LAMBDA_MIN, lam_a * (1 + da))
    return lam_h, lam_a, fh + fa
