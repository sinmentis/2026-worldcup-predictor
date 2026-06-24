"""Paper-trading ledger: record value-bet recommendations, capture the closing line, and settle
against real results, so we can measure CLV and hypothetical ROI without risking money.

Nothing here bets real money. The point is evidence. If our flagged bets beat the closing line
(positive CLV) over a meaningful sample, that's the leading indicator that we have an edge; if not,
we learned it cheaply. The market is usually right, so expect this to be humbling.

Two metrics matter:
  * CLV (closing-line value): did we get a better price than the market's closing line?
    - ``clv`` (no-vig): ``price_taken * closing_market_prob - 1`` -- EV of our price at the fair
      closing probability. Positive = we beat the sharp close. This is the trusted signal.
    - ``clv_price``: ``price_taken / closing_price - 1`` -- simply, did we get better odds.
  * ROI: realised profit per unit staked, both flat (1u/bet) and fractional-Kelly.
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable
from typing import Any

from worldcup_predictor import valuebet

# Notional bankroll for Kelly staking, expressed in flat-stake units (1 unit == 1 flat stake).
BANKROLL_UNITS = 100.0

_OUT_IDX = {"home": 0, "draw": 1, "away": 2}


def log_bets(conn: sqlite3.Connection, bets: list[dict[str, Any]]) -> int:
    """Record any not-yet-logged value-bet recommendations (idempotent per selection).

    We keep the FIRST price we saw the edge at, because that's when you would place the bet; a
    re-run never overwrites an existing entry. Unpriceable rows (no decimal price) are skipped.
    """
    now = time.time()
    n = 0
    for b in bets:
        price = b.get("best_price")
        if not price or float(price) <= 1.0:
            continue
        line = b.get("line")
        exists = conn.execute(
            "SELECT 1 FROM paper_bets WHERE match_id=? AND market=? AND outcome=? "
            "AND ((line IS NULL AND ? IS NULL) OR line=?)",
            (b["match_id"], b["market"], b["outcome"], line, line),
        ).fetchone()
        if exists:
            continue
        conn.execute(
            "INSERT INTO paper_bets(match_id,market,outcome,line,our_prob,market_prob,edge,"
            "price_taken,bookmaker,kelly_frac,logged_at,kickoff) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                b["match_id"],
                b["market"],
                b["outcome"],
                line,
                float(b["our_prob"]),
                float(b["market_prob"]),
                float(b["edge"]),
                float(price),
                b.get("bookmaker"),
                float(b.get("kelly") or 0.0),
                now,
                b.get("kickoff"),
            ),
        )
        n += 1
    conn.commit()
    return n


def _closing_1x2(
    conn: sqlite3.Connection, match_id: int, outcome: str
) -> tuple[float | None, float | None]:
    cons = valuebet.consensus_probs(conn, match_id)
    best = valuebet.best_prices(conn, match_id)
    idx = _OUT_IDX[outcome]
    price = best[idx][0]
    prob = cons[idx] if cons else None
    return (price if price > 1.0 else None), prob


def _closing_totals(
    conn: sqlite3.Connection, match_id: int, line: float, outcome: str
) -> tuple[float | None, float | None]:
    best_over, best_under = valuebet._best_total_prices(conn, match_id, line)
    rows = conn.execute(
        "SELECT price_over, price_under FROM odds_totals WHERE match_id=? AND line=?",
        (match_id, line),
    ).fetchall()
    cons = valuebet._totals_consensus([(r["price_over"], r["price_under"]) for r in rows])
    if outcome == "over":
        price, prob = best_over[0], (cons[0] if cons else None)
    else:
        price, prob = best_under[0], (cons[1] if cons else None)
    return (price if price > 1.0 else None), prob


def _closing_spreads(
    conn: sqlite3.Connection, match_id: int, line: float, outcome: str
) -> tuple[float | None, float | None]:
    best_home, best_away = valuebet._best_spread_prices(conn, match_id, line)
    rows = conn.execute(
        "SELECT price_home, price_away FROM odds_spreads WHERE match_id=? AND line=?",
        (match_id, line),
    ).fetchall()
    cons = valuebet._spreads_consensus([(r["price_home"], r["price_away"]) for r in rows])
    if outcome == "home":
        price, prob = best_home[0], (cons[0] if cons else None)
    else:
        price, prob = best_away[0], (cons[1] if cons else None)
    return (price if price > 1.0 else None), prob


def capture_closing(conn: sqlite3.Connection, *, now_z: str | None = None) -> int:
    """Snapshot the closing line for any logged bet whose match has kicked off.

    Once a game starts the bookmaker feed drops it, so the odds we still hold are the last
    pre-kickoff prices -- a good proxy for the close. Best-effort: if no odds remain we still mark
    it closed (CLV left NULL) so we don't retry forever.
    """
    now = now_z or valuebet._now_z()
    rows = conn.execute(
        "SELECT id, match_id, market, outcome, line, price_taken FROM paper_bets "
        "WHERE closed_at IS NULL AND kickoff IS NOT NULL AND kickoff <= ?",
        (now,),
    ).fetchall()
    n = 0
    for r in rows:
        if r["market"] == "totals":
            cprice, cprob = _closing_totals(conn, r["match_id"], r["line"], r["outcome"])
        elif r["market"] == "spreads":
            cprice, cprob = _closing_spreads(conn, r["match_id"], r["line"], r["outcome"])
        else:
            cprice, cprob = _closing_1x2(conn, r["match_id"], r["outcome"])
        clv = (r["price_taken"] * cprob - 1.0) if cprob is not None else None
        clv_price = (r["price_taken"] / cprice - 1.0) if cprice else None
        conn.execute(
            "UPDATE paper_bets SET closing_price=?, closing_market_prob=?, clv=?, clv_price=?, "
            "closed_at=? WHERE id=?",
            (cprice, cprob, clv, clv_price, time.time(), r["id"]),
        )
        n += 1
    conn.commit()
    return n


def _result_1x2(hs: int, as_: int, outcome: str) -> str:
    winner = "draw" if hs == as_ else ("home" if hs > as_ else "away")
    return "win" if outcome == winner else "loss"


def _result_totals(hs: int, as_: int, line: float, outcome: str) -> str:
    total = hs + as_
    if total == line:
        return "push"
    over_hits = total > line
    if outcome == "over":
        return "win" if over_hits else "loss"
    return "win" if not over_hits else "loss"


def _result_spreads(hs: int, as_: int, line: float, outcome: str) -> str:
    edge_val = (hs - as_) + line  # home margin adjusted by the home handicap
    if edge_val == 0:
        return "push"  # only possible on whole lines
    home_covers = edge_val > 0
    if outcome == "home":
        return "win" if home_covers else "loss"
    return "win" if not home_covers else "loss"


def settle(
    conn: sqlite3.Connection, *, now_z: str | None = None, bankroll: float = BANKROLL_UNITS
) -> int:
    """Capture closing lines, then settle every open bet whose match has finished."""
    capture_closing(conn, now_z=now_z)
    rows = conn.execute(
        "SELECT p.id, p.market, p.outcome, p.line, p.price_taken, p.kelly_frac, "
        "m.home_score, m.away_score FROM paper_bets p JOIN matches m ON m.id=p.match_id "
        "WHERE p.settled_at IS NULL AND m.status='FINISHED' "
        "AND m.home_score IS NOT NULL AND m.away_score IS NOT NULL"
    ).fetchall()
    n = 0
    for r in rows:
        hs, as_ = int(r["home_score"]), int(r["away_score"])
        if r["market"] == "totals":
            res = _result_totals(hs, as_, r["line"], r["outcome"])
        elif r["market"] == "spreads":
            res = _result_spreads(hs, as_, r["line"], r["outcome"])
        else:
            res = _result_1x2(hs, as_, r["outcome"])
        price, kfrac = r["price_taken"], r["kelly_frac"]
        if res == "push":
            pnl_flat = pnl_kelly = 0.0
        elif res == "win":
            pnl_flat = price - 1.0
            pnl_kelly = kfrac * bankroll * (price - 1.0)
        else:
            pnl_flat = -1.0
            pnl_kelly = -(kfrac * bankroll)
        conn.execute(
            "UPDATE paper_bets SET result=?, pnl_flat=?, pnl_kelly=?, settled_at=? WHERE id=?",
            (res, pnl_flat, pnl_kelly, time.time(), r["id"]),
        )
        n += 1
    conn.commit()
    return n


def ledger(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """All paper bets joined with their match (teams, stage, status, score)."""
    rows = conn.execute(
        "SELECT p.*, m.home_team, m.away_team, m.stage, m.status AS match_status, "
        "m.home_score, m.away_score FROM paper_bets p JOIN matches m ON m.id=p.match_id "
        "ORDER BY COALESCE(p.kickoff,''), p.id"
    ).fetchall()
    return [dict(r) for r in rows]


def _breakdown(
    settled: list[dict[str, Any]], keyfn: Callable[[dict[str, Any]], str]
) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for r in settled:
        d = out.setdefault(keyfn(r), {"n": 0, "wins": 0, "pnl_flat": 0.0, "staked_flat": 0.0})
        d["n"] += 1
        if r["result"] == "win":
            d["wins"] += 1
        if r["result"] != "push":
            d["staked_flat"] += 1.0
            d["pnl_flat"] += r["pnl_flat"] or 0.0
    for d in out.values():
        d["roi_flat"] = (d["pnl_flat"] / d["staked_flat"]) if d["staked_flat"] else None
    return out


def summary(conn: sqlite3.Connection, *, bankroll: float = BANKROLL_UNITS) -> dict[str, Any]:
    """The paper-trading scoreboard: counts, ROI (flat + Kelly), hit-rate, CLV, and the ledger."""
    rows = ledger(conn)
    settled = [r for r in rows if r["settled_at"] is not None]
    open_bets = [r for r in rows if r["settled_at"] is None]

    agg: dict[str, Any] = {
        "n_total": len(rows),
        "n_open": len(open_bets),
        "n_settled": len(settled),
        "bankroll": bankroll,
    }
    if settled:
        decided = [r for r in settled if r["result"] != "push"]
        wins = sum(1 for r in settled if r["result"] == "win")
        losses = sum(1 for r in settled if r["result"] == "loss")
        staked_flat = float(len(decided))
        staked_kelly = sum(r["kelly_frac"] * bankroll for r in decided)
        pnl_flat = sum(r["pnl_flat"] or 0.0 for r in settled)
        pnl_kelly = sum(r["pnl_kelly"] or 0.0 for r in settled)
        agg.update(
            {
                "wins": wins,
                "losses": losses,
                "pushes": sum(1 for r in settled if r["result"] == "push"),
                "pnl_flat": pnl_flat,
                "pnl_kelly": pnl_kelly,
                "staked_flat": staked_flat,
                "staked_kelly": staked_kelly,
                "roi_flat": (pnl_flat / staked_flat) if staked_flat else None,
                "roi_kelly": (pnl_kelly / staked_kelly) if staked_kelly else None,
                "hit_rate": (wins / (wins + losses)) if (wins + losses) else None,
            }
        )
    clv_vals = [r["clv"] for r in rows if r["clv"] is not None]
    if clv_vals:
        agg["avg_clv"] = sum(clv_vals) / len(clv_vals)
        agg["beat_close_rate"] = sum(1 for v in clv_vals if v > 0) / len(clv_vals)
        agg["n_clv"] = len(clv_vals)

    return {
        "aggregate": agg,
        "open": sorted(open_bets, key=lambda r: (r["kickoff"] or "9999", r["id"])),
        "settled": sorted(settled, key=lambda r: (r["kickoff"] or "", r["id"]), reverse=True),
        "by_market": _breakdown(settled, lambda r: r["market"]),
        "by_stage": _breakdown(settled, lambda r: "group" if r["stage"] == "group" else "knockout"),
    }
