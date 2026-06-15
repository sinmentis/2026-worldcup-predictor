"""Team-level off-pitch signals (Phase 2a-broaden).

Captures qualitative, between-the-lines team signals (tactics, morale, motivation,
fatigue, general form) that should nudge a team's expected goals up or down. This is a
sibling of ``player_status`` (player availability) and reuses its trust gate, expiry, and
credibility rules. One current signal per ``(team, category)``.

Import direction: ``team_signal`` -> ``player_status`` (for the shared trust-gate helpers).
``intel`` imports both. No cycle.
"""

from __future__ import annotations

import json
import sqlite3
import time
from datetime import date

from worldcup_predictor import config
from worldcup_predictor.config import ADJUST_CLAMP
from worldcup_predictor.models import IntelFactor
from worldcup_predictor.player_status import (
    ACTIVE_CONF_THRESHOLD,
    ACTIVE_CRED_THRESHOLD,
    _default_valid_until,
    derive_credibility,
)

CATEGORIES = {"tactical", "morale", "motivation", "fatigue", "form"}
DIRECTIONS = {"weaken", "strengthen"}
TIERS = {"major", "moderate", "minor"}

# Qualitative team signals are soft: strengthen swings are capped smaller than weaken
# swings (positive narratives are more prone to over-optimism). Each value is a lambda
# multiplier; delta = credibility * (multiplier - 1).
TEAM_SIGNAL_MAGNITUDE: dict[tuple[str, str], float] = {
    ("weaken", "major"): 0.88,
    ("weaken", "moderate"): 0.93,
    ("weaken", "minor"): 0.97,
    ("strengthen", "major"): 1.06,
    ("strengthen", "moderate"): 1.04,
    ("strengthen", "minor"): 1.02,
}


def signal_mult(direction: str, tier: str) -> float:
    return TEAM_SIGNAL_MAGNITUDE.get((direction, tier), 1.0)


def upsert_signal(
    conn: sqlite3.Connection,
    team: str,
    category: str,
    direction: str,
    magnitude_tier: str,
    confidence: float,
    source_url: str,
    official: bool = False,
    notes: str | None = None,
    valid_until: str | None = None,
) -> dict[str, object]:
    team = config.canonical_team(team)
    if category not in CATEGORIES:
        raise ValueError(f"category must be one of {sorted(CATEGORIES)}")
    if direction not in DIRECTIONS:
        raise ValueError(f"direction must be one of {sorted(DIRECTIONS)}")
    if magnitude_tier not in TIERS:
        raise ValueError(f"magnitude_tier must be one of {sorted(TIERS)}")
    if not 0.0 <= confidence <= 1.0:
        raise ValueError("confidence must be in [0, 1]")
    if not source_url:
        raise ValueError("source_url is required; intel must be traceable")

    row = conn.execute(
        "SELECT sources, official, pending FROM team_signal WHERE team=? AND category=?",
        (team, category),
    ).fetchone()
    sources: list[str] = json.loads(row["sources"]) if row else []
    if source_url not in sources:
        sources.append(source_url)
    official_ever = official or bool(row["official"]) if row else official
    cred = derive_credibility(len(sources), official_ever)
    was_active = row is not None and row["pending"] == 0
    gate_pass = cred >= ACTIVE_CRED_THRESHOLD and confidence >= ACTIVE_CONF_THRESHOLD
    # Non-demoting: an already-active (or human-approved) signal is never demoted by a
    # later lower-confidence corroboration; a pending one is promoted when the gate passes.
    pending = 0 if (was_active or gate_pass) else 1
    if valid_until is None:
        valid_until = _default_valid_until(conn, team)

    conn.execute(
        "INSERT INTO team_signal"
        "(team,category,direction,magnitude_tier,credibility,sources,official,"
        " valid_until,as_of,pending,notes)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?)"
        " ON CONFLICT(team,category) DO UPDATE SET"
        " direction=excluded.direction, magnitude_tier=excluded.magnitude_tier,"
        " credibility=excluded.credibility, sources=excluded.sources,"
        " official=excluded.official, valid_until=excluded.valid_until,"
        " as_of=excluded.as_of, pending=excluded.pending, notes=excluded.notes",
        (
            team,
            category,
            direction,
            magnitude_tier,
            cred,
            json.dumps(sources),
            int(official_ever),
            valid_until,
            time.time(),
            pending,
            notes,
        ),
    )
    conn.commit()
    return {
        "status": "active" if pending == 0 else "pending",
        "credibility": cred,
        "team": team,
        "category": category,
    }


def team_signal_factor(conn: sqlite3.Connection, team: str) -> tuple[float, list[IntelFactor]]:
    team = config.canonical_team(team)
    today = date.today().isoformat()
    rows = conn.execute(
        "SELECT category, direction, magnitude_tier, credibility FROM team_signal "
        "WHERE team=? AND pending=0 AND (valid_until IS NULL OR valid_until >= ?)",
        (team, today),
    ).fetchall()
    delta = 0.0
    factors: list[IntelFactor] = []
    for r in rows:
        contrib = float(r["credibility"]) * (signal_mult(r["direction"], r["magnitude_tier"]) - 1.0)
        delta += contrib
        factors.append(
            IntelFactor(
                team=team,
                description=f"{r['category']}: {r['direction']} ({r['magnitude_tier']})",
                lambda_delta=contrib,
            )
        )
    lo, hi = ADJUST_CLAMP
    return max(lo, min(hi, delta)), factors


def list_pending(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM team_signal WHERE pending=1 ORDER BY as_of DESC").fetchall()


def approve(conn: sqlite3.Connection, signal_id: int) -> None:
    cur = conn.execute("UPDATE team_signal SET pending=0 WHERE id=?", (signal_id,))
    if cur.rowcount == 0:
        raise ValueError(f"No pending team signal with id {signal_id}")
    conn.commit()


def reject(conn: sqlite3.Connection, signal_id: int) -> None:
    cur = conn.execute("DELETE FROM team_signal WHERE id=?", (signal_id,))
    if cur.rowcount == 0:
        raise ValueError(f"No team signal with id {signal_id}")
    conn.commit()


def purge_expired(conn: sqlite3.Connection) -> int:
    today = date.today().isoformat()
    cur = conn.execute(
        "DELETE FROM team_signal WHERE valid_until IS NOT NULL AND valid_until < ?", (today,)
    )
    conn.commit()
    return cur.rowcount
