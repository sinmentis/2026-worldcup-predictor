"""Bookmaker odds ingest (The Odds API) + de-margining.

Parsing is isolated from fetching so a different provider can be swapped in. Odds are mapped to
our scheduled fixtures by team pair (order-independent) and stored per bookmaker.
"""

from __future__ import annotations

import os
import sqlite3
import time
from typing import Any

import httpx

from worldcup_predictor import config
from worldcup_predictor import db as _db


def parse_odds_payload(payload: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Turn a The Odds API h2h response into per-match lists of each book's H/D/A decimal prices."""
    out: list[dict[str, Any]] = []
    for item in payload:
        raw_home, raw_away = item.get("home_team"), item.get("away_team")
        if not raw_home or not raw_away:
            continue
        books: list[dict[str, Any]] = []
        for bk in item.get("bookmakers", []):
            market = next((m for m in bk.get("markets", []) if m.get("key") == "h2h"), None)
            if not market:
                continue
            prices = {o.get("name"): o.get("price") for o in market.get("outcomes", [])}
            ph, pa, pd_ = prices.get(raw_home), prices.get(raw_away), prices.get("Draw")
            if ph and pa and pd_:
                books.append(
                    {
                        "bookmaker": bk.get("key") or bk.get("title") or "unknown",
                        "price_home": float(ph),
                        "price_draw": float(pd_),
                        "price_away": float(pa),
                    }
                )
        if books:
            out.append(
                {
                    "home": config.canonical_team(raw_home),
                    "away": config.canonical_team(raw_away),
                    "commence_time": item.get("commence_time"),
                    "books": books,
                }
            )
    return out


def store_odds(conn: sqlite3.Connection, parsed: list[dict[str, Any]]) -> int:
    """Upsert odds by (match_id, bookmaker), mapped to our fixture orientation."""
    n = 0
    now = time.time()
    for m in parsed:
        row = conn.execute(
            "SELECT id, home_team, away_team FROM matches "
            "WHERE (home_team=? AND away_team=?) OR (home_team=? AND away_team=?) LIMIT 1",
            (m["home"], m["away"], m["away"], m["home"]),
        ).fetchone()
        if not row:
            continue  # not one of our fixtures
        match_id = row["id"]
        reversed_orientation = row["home_team"] == m["away"]  # our home is the odds' away team
        for b in m["books"]:
            ph, pd_, pa = b["price_home"], b["price_draw"], b["price_away"]
            if reversed_orientation:
                ph, pa = pa, ph  # store prices in our fixture's home/away orientation
            conn.execute(
                "INSERT INTO odds"
                "(match_id,bookmaker,price_home,price_draw,price_away,commence_time,fetched_at)"
                " VALUES (?,?,?,?,?,?,?)"
                " ON CONFLICT(match_id,bookmaker) DO UPDATE SET"
                " price_home=excluded.price_home, price_draw=excluded.price_draw,"
                " price_away=excluded.price_away, commence_time=excluded.commence_time,"
                " fetched_at=excluded.fetched_at",
                (match_id, b["bookmaker"], ph, pd_, pa, m["commence_time"], now),
            )
            n += 1
    conn.commit()
    _db.touch_update(conn)
    return n


def parse_totals_payload(payload: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Turn a The Odds API totals response into per-match lists of each book's over/under prices."""
    out: list[dict[str, Any]] = []
    for item in payload:
        raw_home, raw_away = item.get("home_team"), item.get("away_team")
        if not raw_home or not raw_away:
            continue
        lines: list[dict[str, Any]] = []
        for bk in item.get("bookmakers", []):
            market = next((m for m in bk.get("markets", []) if m.get("key") == "totals"), None)
            if not market:
                continue
            over = next((o for o in market.get("outcomes", []) if o.get("name") == "Over"), None)
            under = next((o for o in market.get("outcomes", []) if o.get("name") == "Under"), None)
            if (
                over
                and under
                and over.get("point") is not None
                and over.get("price")
                and under.get("price")
            ):
                lines.append(
                    {
                        "bookmaker": bk.get("key") or bk.get("title") or "unknown",
                        "line": float(over["point"]),
                        "price_over": float(over["price"]),
                        "price_under": float(under["price"]),
                    }
                )
        if lines:
            out.append(
                {
                    "home": config.canonical_team(raw_home),
                    "away": config.canonical_team(raw_away),
                    "lines": lines,
                }
            )
    return out


def store_totals(conn: sqlite3.Connection, parsed: list[dict[str, Any]]) -> int:
    """Upsert totals odds by (match_id, bookmaker, line). Totals are orientation-independent."""
    n = 0
    now = time.time()
    for m in parsed:
        row = conn.execute(
            "SELECT id FROM matches "
            "WHERE (home_team=? AND away_team=?) OR (home_team=? AND away_team=?) LIMIT 1",
            (m["home"], m["away"], m["away"], m["home"]),
        ).fetchone()
        if not row:
            continue
        match_id = row["id"]
        for b in m["lines"]:
            conn.execute(
                "INSERT INTO odds_totals"
                "(match_id,bookmaker,line,price_over,price_under,fetched_at) VALUES (?,?,?,?,?,?)"
                " ON CONFLICT(match_id,bookmaker,line) DO UPDATE SET"
                " price_over=excluded.price_over, price_under=excluded.price_under,"
                " fetched_at=excluded.fetched_at",
                (match_id, b["bookmaker"], b["line"], b["price_over"], b["price_under"], now),
            )
            n += 1
    conn.commit()
    _db.touch_update(conn)
    return n


def fetch_odds(conn: sqlite3.Connection, key: str | None = None, regions: str | None = None) -> int:
    key = key or os.environ.get("ODDS_API_KEY", "")
    if not key:
        raise ValueError("ODDS_API_KEY is not set; add it to .env to fetch odds.")
    url = f"{config.ODDS_API_BASE}/sports/{config.ODDS_API_SPORT}/odds"
    params = {
        "apiKey": key,
        "markets": "h2h,totals",
        "oddsFormat": "decimal",
        "regions": regions or config.ODDS_API_REGIONS,
    }
    resp = httpx.get(url, params=params, timeout=60.0)
    resp.raise_for_status()
    payload = resp.json()
    n = store_odds(conn, parse_odds_payload(payload))
    n += store_totals(conn, parse_totals_payload(payload))
    return n


def implied_probs(
    price_home: float, price_draw: float, price_away: float
) -> tuple[float, float, float]:
    """De-margined fair probabilities (raw 1/price normalised by the overround)."""
    raw = [1.0 / price_home, 1.0 / price_draw, 1.0 / price_away]
    s = sum(raw)
    if s <= 0:
        return (0.0, 0.0, 0.0)
    return (raw[0] / s, raw[1] / s, raw[2] / s)
