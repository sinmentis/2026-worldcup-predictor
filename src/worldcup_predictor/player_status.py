import json
import sqlite3
import time
from datetime import date, datetime, timedelta

from worldcup_predictor import config
from worldcup_predictor.config import ADJUST_CLAMP, LAMBDA_MIN  # noqa: F401
from worldcup_predictor.models import IntelFactor

TIERS = {"key", "regular", "fringe"}
STATUSES = {"out", "doubtful", "suspended", "available"}
AFFECTS = {"attack", "defense", "both"}

MAGNITUDE_TABLE: dict[tuple[str, str], float] = {
    ("key", "out"): 0.72,
    ("key", "suspended"): 0.72,
    ("key", "doubtful"): 0.88,
    ("regular", "out"): 0.85,
    ("regular", "suspended"): 0.85,
    ("regular", "doubtful"): 0.93,
    ("fringe", "out"): 0.96,
    ("fringe", "suspended"): 0.96,
    ("fringe", "doubtful"): 0.98,
}

ACTIVE_CRED_THRESHOLD = 0.70
ACTIVE_CONF_THRESHOLD = 0.60
DEFAULT_EXPIRY_DAYS = 14


def status_mult(tier: str, status: str) -> float:
    return MAGNITUDE_TABLE.get((tier, status), 1.0)


def derive_credibility(n_sources: int, official: bool) -> float:
    if official:
        return 0.95
    if n_sources >= 2:
        return 0.80
    return 0.50


def _default_valid_until(conn: sqlite3.Connection, team: str) -> str:
    row = conn.execute(
        "SELECT MIN(kickoff) FROM matches WHERE status='SCHEDULED' AND kickoff IS NOT NULL "
        "AND (home_team=? OR away_team=?)",
        (team, team),
    ).fetchone()
    if row and row[0]:
        try:
            d = datetime.fromisoformat(str(row[0]).replace("Z", "+00:00")).date()
            return (d + timedelta(days=1)).isoformat()
        except ValueError:
            pass
    return (date.today() + timedelta(days=DEFAULT_EXPIRY_DAYS)).isoformat()


def upsert_status(
    conn: sqlite3.Connection,
    team: str,
    player: str,
    tier: str,
    status: str,
    confidence: float,
    source_url: str,
    official: bool = False,
    notes: str | None = None,
    valid_until: str | None = None,
    affects: str | None = None,
) -> dict[str, object]:
    team = config.canonical_team(team)
    if tier not in TIERS:
        raise ValueError(f"tier must be one of {sorted(TIERS)}")
    if status not in STATUSES:
        raise ValueError(f"status must be one of {sorted(STATUSES)}")
    if not 0.0 <= confidence <= 1.0:
        raise ValueError("confidence must be in [0, 1]")
    if not source_url:
        raise ValueError("source_url is required; intel must be traceable")
    if affects is not None and affects not in AFFECTS:
        raise ValueError(f"affects must be one of {sorted(AFFECTS)}")

    if status == "available":
        conn.execute("DELETE FROM player_status WHERE team=? AND player=?", (team, player))
        conn.commit()
        return {"status": "cleared", "team": team, "player": player}

    row = conn.execute(
        "SELECT sources, official, pending, affects FROM player_status WHERE team=? AND player=?",
        (team, player),
    ).fetchone()
    sources: list[str] = json.loads(row["sources"]) if row else []
    if source_url not in sources:
        sources.append(source_url)
    official_ever = official or bool(row["official"]) if row else official
    cred = derive_credibility(len(sources), official_ever)
    was_active = row is not None and row["pending"] == 0
    gate_pass = cred >= ACTIVE_CRED_THRESHOLD and confidence >= ACTIVE_CONF_THRESHOLD
    # Corroboration only raises trust: an already-active (or human-approved) status is never
    # demoted by a later lower-confidence report; a pending one is promoted when the gate passes.
    pending = 0 if (was_active or gate_pass) else 1
    affects_to_store = affects if affects is not None else (row["affects"] if row else "attack")
    if valid_until is None:
        valid_until = _default_valid_until(conn, team)

    conn.execute(
        "INSERT INTO player_status"
        "(team,player,tier,status,credibility,sources,official,"
        " valid_until,as_of,pending,notes,affects)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)"
        " ON CONFLICT(team,player) DO UPDATE SET"
        " tier=excluded.tier, status=excluded.status, credibility=excluded.credibility,"
        " sources=excluded.sources, official=excluded.official, valid_until=excluded.valid_until,"
        " as_of=excluded.as_of, pending=excluded.pending, notes=excluded.notes,"
        " affects=excluded.affects",
        (
            team,
            player,
            tier,
            status,
            cred,
            json.dumps(sources),
            int(official_ever),
            valid_until,
            time.time(),
            pending,
            notes,
            affects_to_store,
        ),
    )
    conn.commit()
    return {
        "status": "active" if pending == 0 else "pending",
        "credibility": cred,
        "team": team,
        "player": player,
    }


def team_status_factor(conn: sqlite3.Connection, team: str) -> tuple[float, list[IntelFactor]]:
    team = config.canonical_team(team)
    today = date.today().isoformat()
    rows = conn.execute(
        "SELECT player, tier, status, credibility FROM player_status "
        "WHERE team=? AND pending=0 AND (valid_until IS NULL OR valid_until >= ?)",
        (team, today),
    ).fetchall()
    delta = 0.0
    factors: list[IntelFactor] = []
    for r in rows:
        contrib = float(r["credibility"]) * (status_mult(r["tier"], r["status"]) - 1.0)
        delta += contrib
        factors.append(
            IntelFactor(
                team=team,
                description=f"{r['player']}: {r['status']} ({r['tier']})",
                lambda_delta=contrib,
            )
        )
    lo, hi = ADJUST_CLAMP
    return max(lo, min(hi, delta)), factors


def list_pending(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM player_status WHERE pending=1 ORDER BY as_of DESC"
    ).fetchall()


def approve(conn: sqlite3.Connection, status_id: int) -> None:
    cur = conn.execute("UPDATE player_status SET pending=0 WHERE id=?", (status_id,))
    if cur.rowcount == 0:
        raise ValueError(f"No pending status with id {status_id}")
    conn.commit()


def reject(conn: sqlite3.Connection, status_id: int) -> None:
    cur = conn.execute("DELETE FROM player_status WHERE id=?", (status_id,))
    if cur.rowcount == 0:
        raise ValueError(f"No status with id {status_id}")
    conn.commit()


def purge_expired(conn: sqlite3.Connection) -> int:
    today = date.today().isoformat()
    cur = conn.execute(
        "DELETE FROM player_status WHERE valid_until IS NOT NULL AND valid_until < ?", (today,)
    )
    conn.commit()
    return cur.rowcount
