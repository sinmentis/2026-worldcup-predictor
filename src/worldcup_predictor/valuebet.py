"""Value-betting: compare our model probabilities against the bookmaker market.

The market consensus (de-margined median across books) is the sharpest available estimate of the
true probability, so "value" means our model thinks an outcome is meaningfully MORE likely than
the consensus does. We still report the expected value at the best available price (where you'd
actually bet) and a fractional-Kelly stake. The market is usually right, so these are edge
*candidates* to sanity-check, not guarantees.
"""

from __future__ import annotations

import sqlite3
import statistics
from typing import Any

from worldcup_predictor import config
from worldcup_predictor.goal_model import GoalModel
from worldcup_predictor.odds import implied_probs
from worldcup_predictor.predict import predict_match

OUTCOME_LABELS = ["home", "draw", "away"]


def best_prices(conn: sqlite3.Connection, match_id: int) -> list[tuple[float, str | None]]:
    """Best (highest) decimal price per outcome across books, with the offering bookmaker."""
    rows = conn.execute(
        "SELECT bookmaker, price_home, price_draw, price_away FROM odds WHERE match_id=?",
        (match_id,),
    ).fetchall()
    best: list[tuple[float, str | None]] = [(0.0, None), (0.0, None), (0.0, None)]
    for r in rows:
        for i, col in enumerate(("price_home", "price_draw", "price_away")):
            p = r[col]
            if p and float(p) > best[i][0]:
                best[i] = (float(p), r["bookmaker"])
    return best


def consensus_probs(conn: sqlite3.Connection, match_id: int) -> list[float] | None:
    """De-margined market consensus per outcome: median across books, renormalised to sum 1."""
    rows = conn.execute(
        "SELECT price_home, price_draw, price_away FROM odds WHERE match_id=?",
        (match_id,),
    ).fetchall()
    probs = [
        implied_probs(float(r["price_home"]), float(r["price_draw"]), float(r["price_away"]))
        for r in rows
        if r["price_home"] and r["price_draw"] and r["price_away"]
    ]
    if not probs:
        return None
    med = [statistics.median(p[i] for p in probs) for i in range(3)]
    s = sum(med)
    return [m / s for m in med] if s > 0 else None


def value_bets(
    conn: sqlite3.Connection,
    model: GoalModel,
    min_edge: float | None = None,
    kelly_fraction: float | None = None,
) -> list[dict[str, Any]]:
    edge_floor = config.VALUE_MIN_EDGE if min_edge is None else min_edge
    kfrac = config.KELLY_FRACTION if kelly_fraction is None else kelly_fraction
    rows = conn.execute(
        "SELECT DISTINCT m.id, m.home_team, m.away_team, m.group_id, m.kickoff, m.neutral "
        "FROM matches m JOIN odds o ON o.match_id = m.id "
        "WHERE m.status='SCHEDULED' "
        "ORDER BY (m.kickoff IS NULL), m.kickoff, m.id"
    ).fetchall()
    bets: list[dict[str, Any]] = []
    for r in rows:
        consensus = consensus_probs(conn, r["id"])
        if consensus is None:
            continue
        pred = predict_match(
            conn, model, r["home_team"], r["away_team"], match_id=None, neutral=bool(r["neutral"])
        )
        our = [pred.p_home, pred.p_draw, pred.p_away]
        best = best_prices(conn, r["id"])
        for i in range(3):
            # value = we think this outcome more likely than the sharp market consensus does
            edge = our[i] - consensus[i]
            if edge < edge_floor:
                continue
            price, book = best[i]
            ev = our[i] * price - 1.0 if price > 1.0 else None
            kelly = max(0.0, (our[i] * price - 1.0) / (price - 1.0)) * kfrac if price > 1.0 else 0.0
            bets.append(
                {
                    "match_id": r["id"],
                    "home_team": r["home_team"],
                    "away_team": r["away_team"],
                    "group": r["group_id"],
                    "kickoff": r["kickoff"],
                    "outcome": OUTCOME_LABELS[i],
                    "our_prob": our[i],
                    "market_prob": consensus[i],
                    "edge": edge,
                    "best_price": price if price > 1.0 else None,
                    "bookmaker": book,
                    "ev": ev,
                    "kelly": kelly,
                }
            )
    bets.sort(key=lambda b: b["edge"], reverse=True)
    return bets
