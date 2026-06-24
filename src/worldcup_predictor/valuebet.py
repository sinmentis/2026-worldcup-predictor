"""Value-betting: compare our model probabilities against the bookmaker market.

The market consensus (de-margined median across books) is the sharpest available estimate of the
true probability, so "value" means our model thinks an outcome is meaningfully MORE likely than
the consensus does. We still report the expected value at the best available price (where you'd
actually bet) and a fractional-Kelly stake. The market is usually right, so these are edge
*candidates* to sanity-check, not guarantees.
"""

from __future__ import annotations

import datetime
import sqlite3
import statistics
from typing import Any

from worldcup_predictor import config
from worldcup_predictor.goal_model import GoalModel
from worldcup_predictor.odds import implied_probs
from worldcup_predictor.predict import adjusted_grid, predict_match

OUTCOME_LABELS = ["home", "draw", "away"]

# A "best" price more than this multiple of the cross-book median for the same outcome is
# almost always corrupt/stale book data (e.g. a 4.8 home price when every other book is ~1.5),
# not genuine value. Ignoring it stops one bad row from producing a phantom huge-EV bet.
BEST_PRICE_OUTLIER_FACTOR = 2.0


def _now_z() -> str:
    """Current UTC time as an ISO-Zulu string, comparable to stored kickoff timestamps."""
    return datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def best_prices(conn: sqlite3.Connection, match_id: int) -> list[tuple[float, str | None]]:
    """Best (highest) decimal price per outcome across books, with the offering bookmaker.

    Implausible outliers (more than ``BEST_PRICE_OUTLIER_FACTOR`` times the cross-book median
    for that outcome) are ignored: a price that good is corrupt/stale data, not real value, and
    would otherwise create a phantom huge-EV "value bet".
    """
    rows = conn.execute(
        "SELECT bookmaker, price_home, price_draw, price_away FROM odds WHERE match_id=?",
        (match_id,),
    ).fetchall()
    cols = ("price_home", "price_draw", "price_away")
    medians: list[float | None] = []
    for col in cols:
        vals = [float(r[col]) for r in rows if r[col]]
        medians.append(statistics.median(vals) if vals else None)
    best: list[tuple[float, str | None]] = [(0.0, None), (0.0, None), (0.0, None)]
    for r in rows:
        for i, col in enumerate(cols):
            p = r[col]
            if not p:
                continue
            price = float(p)
            med = medians[i]
            if med is not None and price > med * BEST_PRICE_OUTLIER_FACTOR:
                continue  # implausible outlier — corrupt/stale book data, not value
            if price > best[i][0]:
                best[i] = (price, r["bookmaker"])
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
        "WHERE m.status='SCHEDULED' AND (m.kickoff IS NULL OR m.kickoff > ?) "
        "ORDER BY (m.kickoff IS NULL), m.kickoff, m.id",
        (_now_z(),),
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
                    "market": "1x2",
                    "outcome": OUTCOME_LABELS[i],
                    "line": None,
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


def _best_total_prices(
    conn: sqlite3.Connection, match_id: int, line: float
) -> tuple[tuple[float, str | None], tuple[float, str | None]]:
    rows = conn.execute(
        "SELECT bookmaker, price_over, price_under FROM odds_totals WHERE match_id=? AND line=?",
        (match_id, line),
    ).fetchall()
    bo: tuple[float, str | None] = (0.0, None)
    bu: tuple[float, str | None] = (0.0, None)
    for r in rows:
        if r["price_over"] and float(r["price_over"]) > bo[0]:
            bo = (float(r["price_over"]), r["bookmaker"])
        if r["price_under"] and float(r["price_under"]) > bu[0]:
            bu = (float(r["price_under"]), r["bookmaker"])
    return bo, bu


def _totals_consensus(prices: list[tuple[float, float]]) -> tuple[float, float] | None:
    """De-margined median over/under probability across books for one line."""
    ov: list[float] = []
    un: list[float] = []
    for o, u in prices:
        if o and u:
            ro, ru = 1.0 / o, 1.0 / u
            s = ro + ru
            ov.append(ro / s)
            un.append(ru / s)
    if not ov:
        return None
    return statistics.median(ov), statistics.median(un)


def value_bets_totals(
    conn: sqlite3.Connection,
    model: GoalModel,
    min_edge: float | None = None,
    kelly_fraction: float | None = None,
) -> list[dict[str, Any]]:
    """Over/Under value: our Poisson total-goals probability vs the market consensus."""
    edge_floor = config.VALUE_MIN_EDGE if min_edge is None else min_edge
    kfrac = config.KELLY_FRACTION if kelly_fraction is None else kelly_fraction
    rows = conn.execute(
        "SELECT DISTINCT m.id, m.home_team, m.away_team, m.group_id, m.kickoff, m.neutral "
        "FROM matches m JOIN odds_totals o ON o.match_id = m.id "
        "WHERE m.status='SCHEDULED' AND (m.kickoff IS NULL OR m.kickoff > ?) "
        "ORDER BY (m.kickoff IS NULL), m.kickoff, m.id",
        (_now_z(),),
    ).fetchall()
    bets: list[dict[str, Any]] = []
    for r in rows:
        by_line: dict[float, list[tuple[float, float]]] = {}
        for tr in conn.execute(
            "SELECT line, price_over, price_under FROM odds_totals WHERE match_id=?", (r["id"],)
        ).fetchall():
            by_line.setdefault(float(tr["line"]), []).append((tr["price_over"], tr["price_under"]))
        if not by_line:
            continue
        line = max(by_line, key=lambda label: len(by_line[label]))  # the most-quoted line
        cons = _totals_consensus(by_line[line])
        if cons is None:
            continue
        grid, _ = adjusted_grid(
            conn, model, r["home_team"], r["away_team"], neutral=bool(r["neutral"])
        )
        our_over = grid.over(line)
        our = {"over": our_over, "under": 1.0 - our_over}
        cons_map = {"over": cons[0], "under": cons[1]}
        best_over, best_under = _best_total_prices(conn, r["id"], line)
        best_map = {"over": best_over, "under": best_under}
        for side in ("over", "under"):
            edge = our[side] - cons_map[side]
            if edge < edge_floor:
                continue
            price, book = best_map[side]
            ev = our[side] * price - 1.0 if price > 1.0 else None
            kelly = (
                max(0.0, (our[side] * price - 1.0) / (price - 1.0)) * kfrac if price > 1.0 else 0.0
            )
            bets.append(
                {
                    "match_id": r["id"],
                    "home_team": r["home_team"],
                    "away_team": r["away_team"],
                    "group": r["group_id"],
                    "kickoff": r["kickoff"],
                    "market": "totals",
                    "outcome": side,
                    "line": line,
                    "our_prob": our[side],
                    "market_prob": cons_map[side],
                    "edge": edge,
                    "best_price": price if price > 1.0 else None,
                    "bookmaker": book,
                    "ev": ev,
                    "kelly": kelly,
                }
            )
    bets.sort(key=lambda b: b["edge"], reverse=True)
    return bets


def _best_spread_prices(
    conn: sqlite3.Connection, match_id: int, line: float
) -> tuple[tuple[float, str | None], tuple[float, str | None]]:
    """Best home-cover / away-cover price for a handicap line, ignoring outliers (corrupt data)."""
    rows = conn.execute(
        "SELECT bookmaker, price_home, price_away FROM odds_spreads WHERE match_id=? AND line=?",
        (match_id, line),
    ).fetchall()
    home_vals = [float(r["price_home"]) for r in rows if r["price_home"]]
    away_vals = [float(r["price_away"]) for r in rows if r["price_away"]]
    home_med = statistics.median(home_vals) if home_vals else None
    away_med = statistics.median(away_vals) if away_vals else None
    bh: tuple[float, str | None] = (0.0, None)
    ba: tuple[float, str | None] = (0.0, None)
    for r in rows:
        if r["price_home"]:
            p = float(r["price_home"])
            if (home_med is None or p <= home_med * BEST_PRICE_OUTLIER_FACTOR) and p > bh[0]:
                bh = (p, r["bookmaker"])
        if r["price_away"]:
            p = float(r["price_away"])
            if (away_med is None or p <= away_med * BEST_PRICE_OUTLIER_FACTOR) and p > ba[0]:
                ba = (p, r["bookmaker"])
    return bh, ba


def _spreads_consensus(prices: list[tuple[float, float]]) -> tuple[float, float] | None:
    """De-margined median home-cover / away-cover probability across books for one line."""
    home: list[float] = []
    away: list[float] = []
    for ph, pa in prices:
        if ph and pa:
            rh, ra = 1.0 / ph, 1.0 / pa
            s = rh + ra
            home.append(rh / s)
            away.append(ra / s)
    if not home:
        return None
    return statistics.median(home), statistics.median(away)


def value_bets_spreads(
    conn: sqlite3.Connection,
    model: GoalModel,
    min_edge: float | None = None,
    kelly_fraction: float | None = None,
) -> list[dict[str, Any]]:
    """Handicap value: our model cover probability vs the market consensus."""
    edge_floor = config.VALUE_MIN_EDGE if min_edge is None else min_edge
    kfrac = config.KELLY_FRACTION if kelly_fraction is None else kelly_fraction
    rows = conn.execute(
        "SELECT DISTINCT m.id, m.home_team, m.away_team, m.group_id, m.kickoff, m.neutral "
        "FROM matches m JOIN odds_spreads o ON o.match_id = m.id "
        "WHERE m.status='SCHEDULED' AND (m.kickoff IS NULL OR m.kickoff > ?) "
        "ORDER BY (m.kickoff IS NULL), m.kickoff, m.id",
        (_now_z(),),
    ).fetchall()
    bets: list[dict[str, Any]] = []
    for r in rows:
        by_line: dict[float, list[tuple[float, float]]] = {}
        for sr in conn.execute(
            "SELECT line, price_home, price_away FROM odds_spreads WHERE match_id=?", (r["id"],)
        ).fetchall():
            by_line.setdefault(float(sr["line"]), []).append((sr["price_home"], sr["price_away"]))
        if not by_line:
            continue
        line = max(by_line, key=lambda label: len(by_line[label]))  # the most-quoted line
        cons = _spreads_consensus(by_line[line])
        if cons is None:
            continue
        grid, _ = adjusted_grid(
            conn, model, r["home_team"], r["away_team"], neutral=bool(r["neutral"])
        )
        our_home = grid.cover(line)
        our = {"home": our_home, "away": 1.0 - our_home}
        cons_map = {"home": cons[0], "away": cons[1]}
        best_home, best_away = _best_spread_prices(conn, r["id"], line)
        best_map = {"home": best_home, "away": best_away}
        for side in ("home", "away"):
            edge = our[side] - cons_map[side]
            if edge < edge_floor:
                continue
            price, book = best_map[side]
            ev = our[side] * price - 1.0 if price > 1.0 else None
            kelly = (
                max(0.0, (our[side] * price - 1.0) / (price - 1.0)) * kfrac if price > 1.0 else 0.0
            )
            bets.append(
                {
                    "match_id": r["id"],
                    "home_team": r["home_team"],
                    "away_team": r["away_team"],
                    "group": r["group_id"],
                    "kickoff": r["kickoff"],
                    "market": "spreads",
                    "outcome": side,
                    "line": line,
                    "our_prob": our[side],
                    "market_prob": cons_map[side],
                    "edge": edge,
                    "best_price": price if price > 1.0 else None,
                    "bookmaker": book,
                    "ev": ev,
                    "kelly": kelly,
                }
            )
    bets.sort(key=lambda b: b["edge"], reverse=True)
    return bets
