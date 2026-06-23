from __future__ import annotations

import sqlite3
import time

from worldcup_predictor import player_status, team_signal
from worldcup_predictor.config import ADJUST_CLAMP, LAMBDA_MIN  # noqa: F401, RUF100
from worldcup_predictor.models import IntelEvent, IntelFactor


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
        # `direction` is authoritative for the sign: "weaken" lowers lambda, "strengthen"
        # raises it, regardless of the sign the caller put on `magnitude`.
        magnitude = abs(float(row["magnitude"]))
        direction = (row["direction"] or "").strip().lower()
        if direction == "weaken":
            magnitude = -magnitude
        elif direction != "strengthen":
            # Unknown/empty direction: fall back to the raw signed magnitude.
            magnitude = float(row["magnitude"])
        contrib = float(row["credibility"]) * magnitude
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
    lo, hi = ADJUST_CLAMP
    ev_atk_h, fe_h = _team_factor(conn, home)  # legacy intel_events: attack-only
    ev_atk_a, fe_a = _team_factor(conn, away)
    ps_atk_h, ps_def_h, fps_h = player_status.team_status_factor(conn, home)
    ps_atk_a, ps_def_a, fps_a = player_status.team_status_factor(conn, away)
    ts_atk_h, ts_def_h, fts_h = team_signal.team_signal_factor(conn, home)
    ts_atk_a, ts_def_a, fts_a = team_signal.team_signal_factor(conn, away)

    atk_home = max(lo, min(hi, ev_atk_h + ps_atk_h + ts_atk_h))
    atk_away = max(lo, min(hi, ev_atk_a + ps_atk_a + ts_atk_a))
    def_home = max(lo, min(hi, ps_def_h + ts_def_h))  # legacy events have no defence
    def_away = max(lo, min(hi, ps_def_a + ts_def_a))

    lam_h = max(LAMBDA_MIN, lam_h * (1 + atk_home) * (1 + def_away))
    lam_a = max(LAMBDA_MIN, lam_a * (1 + atk_away) * (1 + def_home))
    return lam_h, lam_a, fe_h + fps_h + fts_h + fe_a + fps_a + fts_a
