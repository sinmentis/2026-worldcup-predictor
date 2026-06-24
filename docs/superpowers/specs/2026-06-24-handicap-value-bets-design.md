# SPEC: Handicap (Asian/Spread) Value Bets

Status: Proposed (pending owner review)
Date: 2026-06-24
Scope files: `db.py`, `odds.py`, `goal_model.py`, `valuebet.py`, `engine.py`, `papertrade.py`,
`static/app.js`, tests.
Quality bar: `ruff`, `ruff format`, `mypy --strict`, `pytest` all green.

## 1. Overview & Motivation

Value betting today covers two markets: 1X2 (`value_bets`) and totals/over-under
(`value_bets_totals`). For lopsided matches the 1X2 favourite price is too short to ever be value;
the **handicap** market (a.k.a. spreads / Asian handicap — the favourite "gives" goals) is where an
edge on a mismatch actually shows up, and it's what sharp bettors use. This adds handicap as a
third value-bet market, fully mirroring the existing totals plumbing, and wires it into the
paper-trading ledger so the model's edge in this market is tracked independently.

The Dixon-Coles model already produces a full score grid, so the handicap cover probability is a
pure derivation (no model changes). The only new external data is the bookmaker `spreads` market,
which is available on the **same cheap bulk `/odds` endpoint** we already call (≈ +2 API credits
per fetch: 3 markets × 2 regions instead of 2 × 2).

## 2. Goals / Non-Goals

### Goals
- **G1** Fetch the bookmaker `spreads` market on the existing bulk endpoint; store it in a new
  `odds_spreads` table.
- **G2** Compute the model's handicap cover probability from the score grid (`ScoreGrid.cover`).
- **G3** Surface handicap value bets (`value_bets_spreads`) alongside 1X2 and totals, reusing the
  consensus / edge / EV / fractional-Kelly machinery and the **outlier-robust best-price** filter.
- **G4** Auto-log and settle handicap bets in the paper-trading ledger (win/loss/**push** on whole
  lines), so handicap ROI/CLV is tracked as its own market.
- **G5** Display handicap bets clearly in the value-bets and paper-trading web tabs
  (e.g. `Croatia -1.5`).
- **G6** Quality bar unchanged.

### Non-Goals
- **NG1** No quarter/split Asian lines in v1 (e.g. -0.75, -1.25). `parse_spreads_payload` keeps only
  whole and half lines (`line * 2` is an integer); quarter lines are skipped. Deferred follow-up.
- **NG2** No model changes — cover probability is derived from the existing grid.
- **NG3** No per-event Odds API endpoint (keeps cost on the cheap bulk endpoint).
- **NG4** No new paper_bets columns — the table is already generic (`market`, `outcome`, `line`);
  only its comment is extended to include `'spreads'`.
- **NG5** Do not change the existing 1X2 / totals value logic or their tests.

## 3. Architecture & Data Flow

```
fetch_odds (markets="h2h,totals,spreads")
  -> parse_spreads_payload  -> store_spreads (odds_spreads, oriented to OUR home/away)
value_bets_spreads(conn, model):
  per match: pick most-quoted line -> de-margin 2-way consensus (home-cover / away-cover)
             our_home = grid.cover(line); our_away = 1 - our_home
             edge/EV/Kelly per side, best price via outlier-robust _best_spread_prices
engine.get_value_bets -> 1x2 + totals + spreads (sorted by edge)
log_paper_bets -> logs spreads bets (market='spreads', line=home handicap, outcome 'home'|'away')
settle -> _result_spreads (win/loss/push) ; capture_closing -> _closing_spreads
web value-bets / paper tabs -> render handicap rows with the signed line
```

### Sign & orientation convention (the one subtlety vs totals)
- `odds_spreads.line` = the **home team's handicap** (favourite negative, e.g. Croatia `-1.5`).
- Home covers iff `home_goals + line > away_goals` (margin `h - a > -line`).
- Totals are orientation-independent, but **spreads are not**: in `store_spreads`, if our seeded
  home is the odds feed's away team (`reversed_orientation`, exactly as in `store_odds`), swap
  `price_home`/`price_away` **and negate the line**.

## 4. Detailed Design

### 4.1 Schema (`db.py`)
New table mirroring `odds_totals` (orientation handled at store time, so the stored row is always in
our fixture orientation):
```sql
CREATE TABLE IF NOT EXISTS odds_spreads (
    id INTEGER PRIMARY KEY,
    match_id INTEGER,
    bookmaker TEXT NOT NULL,
    line REAL NOT NULL,          -- HOME handicap (favourite negative, e.g. -1.5)
    price_home REAL,             -- price for home covering (home + line)
    price_away REAL,             -- price for away covering (away - line)
    fetched_at REAL,
    UNIQUE(match_id, bookmaker, line)
);
```
Extend the `paper_bets.market` comment to `'1x2' | 'totals' | 'spreads'` and the `outcome` comment to
include `home | away` for spreads. No column change. `init_schema` already creates new
`CREATE TABLE IF NOT EXISTS` tables on existing DBs (the prod DB gets `odds_spreads` automatically),
so **no migration is needed** for the new table.

### 4.2 Odds parse / store / fetch (`odds.py`)
- `fetch_odds`: change `"markets": "h2h,totals"` → `"h2h,totals,spreads"`.
- `parse_spreads_payload(payload)` — mirror `parse_totals_payload`. The `spreads` market has two
  outcomes named after the two teams, each with a `point` (the handicap) and a `price`. Take the
  outcome whose `name == raw_home` as the home side: `line = float(home_outcome["point"])`,
  `price_home = home_outcome["price"]`, `price_away = away_outcome["price"]`. **Guards:** both
  outcomes present with `point` and `price`; the two points are opposite (`home_point ≈ -away_point`);
  and **v1 keeps only whole/half lines** (`abs(line * 2 - round(line * 2)) < 1e-9`) — skip quarter
  lines (NG1). Output `{"home", "away", "lines": [{bookmaker, line, price_home, price_away}, ...]}`.
- `store_spreads(conn, parsed)` — mirror `store_totals`, but **orientation-dependent** like
  `store_odds`: resolve the match order-independently; compute
  `reversed_orientation = row["home_team"] == m["away"]`; for each book, if reversed, set
  `price_home, price_away = price_away, price_home` and `line = -line` before the
  `INSERT ... ON CONFLICT(match_id,bookmaker,line) DO UPDATE`.
- `fetch_odds` stores all three: `n = store_odds(...) + store_totals(...) + store_spreads(...)`.

### 4.3 Model cover probability (`goal_model.py`)
Add to `ScoreGrid`:
```python
def cover(self, line: float) -> float:
    """P(home covers the home handicap `line`): P(home_goals + line > away_goals)."""
    total = 0.0
    for h in range(self.matrix.shape[0]):
        for a in range(self.matrix.shape[1]):
            if h + line > a:
                total += self.matrix[h, a]
    return float(total)
```
For value we use `our_home = grid.cover(line)`, `our_away = 1 - our_home` — identical treatment to
totals' `over` / `1 - over` (on whole lines the push mass folds into the away side for the
*probability*, exactly as totals folds it into `under`; push is handled exactly at **settlement**,
where the PnL actually depends on it). v1 lines are whole/half, and half lines have no push.

### 4.4 Value computation (`valuebet.py`)
Mirror the totals trio:
- `_best_spread_prices(conn, match_id, line)` → best home-cover / away-cover price for that line,
  **with the same outlier guard** as `best_prices`: ignore a price `> BEST_PRICE_OUTLIER_FACTOR ×`
  the cross-book median for that side (so a corrupt handicap quote can't create a phantom bet).
- `_spreads_consensus(prices)` → de-margined median P(home-cover) / P(away-cover) across books
  (same shape as `_totals_consensus`).
- `value_bets_spreads(conn, model, min_edge=None, kelly_fraction=None)` → mirror
  `value_bets_totals`: select `SCHEDULED` matches with `odds_spreads` and `kickoff > now`
  (excludes already-started matches, same as totals), group by line, take the **most-quoted line**,
  build the grid via `adjusted_grid`, `our_home = grid.cover(line)`, `our_away = 1 - our_home`,
  compute `edge = our - consensus` per side, EV and fractional Kelly, and emit rows with
  `market="spreads"`, `outcome in {"home","away"}`, `line=<home handicap>`,
  plus `home_team/away_team/group/kickoff` (so the UI can render `<team> <±line>`).

### 4.5 Engine (`engine.py`)
`get_value_bets` returns `value_bets(...) + value_bets_totals(...) + value_bets_spreads(...)`,
sorted by edge. `log_paper_bets` already consumes `get_value_bets`, so handicap bets are logged
automatically (the dedup key is `(match_id, market, outcome, line)`, already line-aware).

### 4.6 Paper-trading (`papertrade.py`)
- `_result_spreads(home_score, away_score, line, outcome)` → with margin `m = home_score - away_score`
  and `edge_val = m + line`: home side → `win` if `edge_val > 0`, `push` if `== 0`, else `loss`;
  away side is the mirror (`win` if `edge_val < 0`, `push` if `== 0`, else `loss`).
- `capture_closing`: add an `elif r["market"] == "spreads":` branch calling a new
  `_closing_spreads(conn, match_id, line, outcome)` that mirrors `_closing_totals` (best price +
  de-margined consensus for that side, via `_best_spread_prices` / `_spreads_consensus`).
- `settle`: add `elif r["market"] == "spreads": res = _result_spreads(hs, as_, r["line"], r["outcome"])`
  to the dispatch. PnL (flat + Kelly, push = 0) is unchanged and already generic.

### 4.7 Web UI (`static/app.js`)
The value-bets and paper tabs already render bets generically. Add a small formatter so a
`market === "spreads"` row shows the handicap clearly: the team that "gives" goals with the signed
line, e.g. `Croatia -1.5` (home, line −1.5) or `Panama +1.5` (away). Reuse the existing FIFA-rank
badges and result chips. No structural UI change.

## 5. Testing (TDD)
1. `ScoreGrid.cover(line)` on a hand-built grid: e.g. cover(-1.5) sums only cells with `h - a ≥ 2`.
2. `parse_spreads_payload`: parses a spreads payload; **skips a quarter line** (-0.75).
3. `store_spreads` orientation: a feed with reversed home/away stores swapped prices and a negated
   line in our orientation.
4. `value_bets_spreads`: a strong-vs-weak fixture with handicap odds yields a home `-line` value bet
   with the expected sign; an outlier handicap price is ignored.
5. `_result_spreads`: win / loss / **push** for whole lines, plus the away mirror, plus a half-line
   (no push).
6. Regression: existing 1X2 / totals value + paper tests unchanged and green.

## 6. Rollout
1. Schema: `odds_spreads` is created by `init_schema` on next start (no migration).
2. Ship code + tests green.
3. `fetch-odds` once (now includes `spreads`) → `value-bets` shows handicap rows; `paper-log` logs
   them; `paper-settle` settles finished ones.
4. Web restart to pick up the JS + the new endpoints' data (note: a restart incurs the ~2-min model
   warm window already documented).

## 7. Cost & Risks
- **Cost:** +2 API credits per odds fetch (bulk endpoint, 3 markets × 2 regions). Negligible vs the
  per-event alternative.
- **Orientation bug** (R1, the most likely defect): the line sign + price swap on reversed feeds —
  covered by test 3.
- **Push correctness** (R2): only whole lines push; settlement uses exact integer margin — covered
  by test 5. The probability-side fold (push into away) matches the existing totals approximation
  and is immaterial for half lines.
- **Quarter lines** (R3): skipped at parse in v1 (NG1) so nothing mis-settles; revisit if the feed
  turns out to quote them often.

## 8. Open Questions
None blocking — v1 scope (whole/half lines, paper-trading integrated) is settled with the owner.
Quarter-line Asian handling is an explicit deferred follow-up.
