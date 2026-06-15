"""MCP server for worldcup-predictor. Thin adapter over engine. stdio transport."""

from __future__ import annotations

import logging
import os
import sqlite3
import sys
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from pydantic import BaseModel, Field

from worldcup_predictor import config, db, engine
from worldcup_predictor import player_status as _ps

logging.basicConfig(level=logging.INFO, stream=sys.stderr)
logger = logging.getLogger("worldcup-mcp")

mcp = FastMCP("worldcup-predictor")
_CONN: sqlite3.Connection | None = None


def _conn() -> sqlite3.Connection:
    global _CONN
    if _CONN is None:
        _CONN = db.connect(os.environ.get("WC_DB_PATH"))
        db.init_schema(_CONN)
    return _CONN


def _reset_conn() -> None:
    global _CONN
    if _CONN is not None:
        _CONN.close()
    _CONN = None


class GroupStanding(BaseModel):
    team: str
    played: int
    won: int
    drawn: int
    lost: int
    gf: int
    ga: int
    gd: int = Field(description="Goal difference")
    pts: int


@mcp.tool()
def get_group_standings(group: str) -> list[GroupStanding]:
    """Return current standings for a 2026 World Cup group (A-L)."""
    group = group.upper().strip()
    if group not in config.GROUPS:
        raise ToolError(f"'{group}' is not a valid group. Must be A-L.")
    return [GroupStanding(**row) for row in engine.get_group_standings(_conn(), group)]


@mcp.tool()
def get_upcoming_matches(limit: int = 5) -> list[dict[str, Any]]:
    """Return the next N scheduled matches without a result yet."""
    return engine.get_upcoming_matches(_conn(), min(max(1, limit), 20))


@mcp.tool()
def record_match_result(match_id: int, home_score: int, away_score: int) -> dict[str, str]:
    """Record an official match result and refresh standings/bracket."""
    if home_score < 0 or away_score < 0:
        raise ToolError("Scores must be non-negative.")
    engine.record_result(_conn(), match_id, home_score, away_score)
    return {"status": "ok", "match_id": str(match_id)}


@mcp.tool()
def predict_match(match_id: int) -> dict[str, Any]:
    """Predict 1X2 + scoreline for a fixture (applies current intel) and persist it."""
    return engine.predict_fixture(_conn(), match_id)


@mcp.tool()
def record_intel(
    team: str,
    event_type: str,
    direction: str,
    magnitude: float,
    source_url: str,
    credibility: float,
    player: str = "",
    notes: str = "",
) -> dict[str, str]:
    """Record an off-pitch intelligence event that adjusts a team's expected goals.

    `direction` ("weaken" or "strengthen") determines the sign of the effect; `magnitude`
    is the size of the lambda multiplier delta (e.g. 0.20 for a key injury with
    direction="weaken"). credibility in [0,1] scales the effect. ALWAYS pass a real
    source_url so the adjustment is traceable.
    """
    if not source_url:
        raise ToolError("source_url is required; intel must be traceable.")
    if direction.strip().lower() not in {"weaken", "strengthen"}:
        raise ToolError("direction must be 'weaken' or 'strengthen'.")
    if not 0.0 <= credibility <= 1.0:
        raise ToolError("credibility must be in [0,1].")
    engine.record_intel_event(
        _conn(),
        team=team,
        event_type=event_type,
        direction=direction,
        magnitude=magnitude,
        source_url=source_url,
        credibility=credibility,
        player=player or None,
        notes=notes or None,
    )
    return {"status": "ok", "team": team}


@mcp.tool()
def run_simulation(
    iterations: int = 50_000, seed: int | None = None
) -> dict[str, dict[str, float]]:
    """Run the Monte Carlo tournament simulation; return top title contenders."""
    result = engine.run_simulation(_conn(), n=iterations, seed=seed)
    top = sorted(result.items(), key=lambda kv: kv[1]["title"], reverse=True)[:10]
    return dict(top)


@mcp.tool()
def get_unprocessed_news(limit: int = 20) -> list[dict[str, Any]]:
    """Return raw news articles not yet processed, for off-pitch intel extraction."""
    return engine.get_unprocessed_news(_conn(), limit)


@mcp.tool()
def upsert_player_status(
    team: str,
    player: str,
    tier: str,
    status: str,
    confidence: float,
    source_url: str,
    official: bool = False,
    notes: str = "",
) -> dict[str, object]:
    """Record/update a player's current status from news, adjusting that team's expected goals.

    tier: 'key' | 'regular' | 'fringe' (you judge importance from the article).
    status: 'out' | 'doubtful' | 'suspended' | 'available' ('available' clears a prior status).
    confidence in [0,1]; ALWAYS pass a real source_url. official=True only for
    club/federation sources. High confidence AND (>=2 sources OR official) applies
    immediately; otherwise it is queued for review.
    """
    if tier not in _ps.TIERS:
        raise ToolError(f"tier must be one of {sorted(_ps.TIERS)}")
    if status not in _ps.STATUSES:
        raise ToolError(f"status must be one of {sorted(_ps.STATUSES)}")
    if not source_url:
        raise ToolError("source_url is required; intel must be traceable.")
    if not 0.0 <= confidence <= 1.0:
        raise ToolError("confidence must be in [0,1].")
    return engine.upsert_player_status(
        _conn(),
        team=team,
        player=player,
        tier=tier,
        status=status,
        confidence=confidence,
        source_url=source_url,
        official=official,
        notes=notes or None,
    )


@mcp.tool()
def mark_news_processed(article_ids: list[int]) -> dict[str, int]:
    """Mark news articles as processed so they are not re-extracted."""
    return {"processed": engine.mark_news_processed(_conn(), article_ids)}


@mcp.tool()
def list_pending_intel() -> list[dict[str, Any]]:
    """List player-status items awaiting human approval (low-confidence / single-source)."""
    return engine.list_pending_intel(_conn())


@mcp.tool()
def approve_intel(status_id: int) -> dict[str, str]:
    """Approve a pending player-status item so it starts affecting predictions."""
    engine.approve_intel(_conn(), status_id)
    return {"status": "approved", "id": str(status_id)}


@mcp.tool()
def reject_intel(status_id: int) -> dict[str, str]:
    """Reject (delete) a pending or active player-status item."""
    engine.reject_intel(_conn(), status_id)
    return {"status": "rejected", "id": str(status_id)}


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
