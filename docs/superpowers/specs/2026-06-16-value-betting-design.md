# Phase 3 — Value Betting (automated odds) — Design Spec

**Date:** 2026-06-16
**Status:** Approved direction (autopilot) -> building. Live fetch needs a key the user will add.

## 1. Goal

Replace manual odds entry with an automated odds feed, and flag positive-EV bets by comparing
our model's probabilities against bookmaker prices. The original "C" goal.

## 2. Decisions

- **Source:** The Odds API (free 500 req/month). Sport `soccer_fifa_world_cup`, market `h2h`,
  decimal odds, configurable regions; includes sharp books (Pinnacle). Key in `.env` as
  `ODDS_API_KEY` (gitignored), mirroring the football-data.org token. The parse layer is isolated
  so another provider can be swapped in later.
- **Fair probability:** de-margin a book's three prices (`1/price` normalised by the overround).
  Value is assessed at the BEST available decimal price across books.
- **Honesty:** sharp closing lines are efficient; surfaced bets are edge *candidates*, not a
  money printer. Fractional Kelly + an edge threshold keep staking conservative.

## 3. Data model

```sql
CREATE TABLE IF NOT EXISTS odds (
    id INTEGER PRIMARY KEY,
    match_id INTEGER,            -- mapped to our fixtures by team pair (order-independent)
    bookmaker TEXT NOT NULL,
    price_home REAL, price_draw REAL, price_away REAL,
    commence_time TEXT,
    fetched_at REAL,
    UNIQUE(match_id, bookmaker)
);
```

## 4. Modules

- `odds.py`:
  - `parse_odds_payload(payload)` -> per-match list with each book's H/D/A decimal prices
    (team names canonicalised; the "Draw" outcome mapped to draw).
  - `store_odds(conn, parsed)` -> upsert by `(match_id, bookmaker)`, mapping team pair to our
    scheduled match; skip unmapped fixtures.
  - `fetch_odds(conn, key=None)` -> GET The Odds API and store (uses `ODDS_API_KEY`).
  - `implied_probs(ph, pd, pa)` -> de-margined fair probabilities (sum to 1).
- `valuebet.py`:
  - `best_prices(conn, match_id)` -> best decimal price per outcome across books + the book.
  - `value_bets(conn, model, min_edge, kelly_fraction)` -> for each scheduled match with odds,
    EV per outcome = `our_p * best_price - 1`; flag `EV >= min_edge`; Kelly = `EV/(price-1)`
    scaled by `kelly_fraction`; include our prob, de-margined implied prob, best price + book.

## 5. Config

`ODDS_API_BASE`, `ODDS_API_SPORT="soccer_fifa_world_cup"`, `ODDS_API_REGIONS="eu,uk"`,
`VALUE_MIN_EDGE=0.05`, `KELLY_FRACTION=0.25`; `ODDS_API_KEY` from env.

## 6. Interfaces

- engine: `fetch_odds`, `get_value_bets`.
- CLI: `worldcup fetch-odds`, `worldcup value-bets`.
- web: `GET /api/value-bets` + a 价值投注 tab (match, our pick, our prob vs implied, best price +
  book, EV%, suggested Kelly%).

## 7. Testing

`parse_odds_payload` (fixture JSON, name mapping + draw); `implied_probs` (de-margins, sums to 1);
`store_odds` (maps by team pair, upserts); `value_bets` (+EV flagged with correct Kelly; no edge ->
no bet); `fetch_odds` with monkeypatched httpx. Web endpoint returns 200 + shape.

## 8. Out of scope

Betfair exchange / multiple providers; line-movement history; arbitrage; auto-staking/bankroll;
non-1X2 markets (totals/spreads).
