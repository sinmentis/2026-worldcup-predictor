# Knockout Bracket — Design

## Goal

Show the full knockout stage as a **single-direction left→right tree**: real fixtures (teams
fetched from the data feed as each round is decided) plus my prediction for every match,
projected all the way to the final. As real results come in, fetch them and re-predict the
downstream bracket. The user triggers fetch/update manually.

This complements the existing probabilistic forecast (title odds from the Monte Carlo sim); it
adds a concrete, readable predicted bracket the forecast can't show.

## Current state

- `matches` table **already supports** knockout stages: `stage` is documented as
  `'group' | 'R32' | 'R16' | 'QF' | 'SF' | '3RD' | 'FINAL'` and there is a `slot TEXT` column for
  knockout placeholders. Today only the 72 `group` rows exist.
- `ingest.apply_fixtures_payload` is **UPDATE-only** (matches existing rows by team pair), so it
  never creates knockout rows even though the feed already publishes them.
- `engine.get_knockout_bracket` already queries `R32/R16/QF/SF/3RD/FINAL` rows — returns empty.
- The web "knockout" view is therefore empty; only the simulated `get_bracket_projection`
  (per-team round probabilities) exists.
- The football-data v4 feed already returns **all 104 matches** with stages
  `GROUP_STAGE / LAST_32 / LAST_16 / QUARTER_FINALS / SEMI_FINALS / THIRD_PLACE / FINAL`, kickoff
  times, and teams populated **as each round is decided** (knockout teams are currently `null`
  because the group stage is still finishing).
- The sim (`simulate.py`) already encodes bracket topology: `_R32_TEMPLATE` (FIFA Annex C R32
  seeding) and progression by pairing consecutive winners; `_knockout_winner` resolves draws via
  an extra-time/shootout share. `static/app.js` already has `ZH` (Chinese names), `RANK` (FIFA
  Dec-2025), `FLAG`, and `STAGES` maps to reuse.

## Architecture

Data flow:

```
football-data feed (104 matches, teams-as-decided)
      │  fetch-fixtures / fetch-results  (manual trigger)
      ▼
ingest: upsert knockout matches  ──►  matches table (stage R32..FINAL, ext_id, slot, teams, score)
      │
      ▼
engine.get_predicted_bracket(conn)
   • feed teams where known (real) + forward-projected predicted winners for TBD slots
   • goal model predicts every match (advance %, likely score), intel applied
      │  /api/knockout/bracket
      ▼
web UI: single-direction L→R tree (reuses zh / RANK / flag)
```

The feed is the **source of truth** for bracket structure, real teams, dates and results. We own
only the **forward projection** (advancing my predicted winner into not-yet-decided slots) and the
per-match prediction.

### 1. Schema (tiny migration)

The `stage`/`slot` columns already exist. Add one nullable column for a stable upsert key:

- `ALTER TABLE matches ADD COLUMN ext_id INTEGER` — the feed's match id. Idempotent guard (check
  `PRAGMA table_info`) so re-running is safe. Group rows keep `ext_id = NULL`.

`slot` encodes the bracket position we assign (e.g. `R32-1..R32-16`, `R16-1..R16-8`, `QF-1..QF-4`,
`SF-1..SF-2`, `FINAL`, `3RD`). It drives the forward-projection topology.

### 2. Ingestion (`ingest.py`)

- `_STAGE_MAP = {GROUP_STAGE: 'group', LAST_32: 'R32', LAST_16: 'R16', QUARTER_FINALS: 'QF',
  SEMI_FINALS: 'SF', THIRD_PLACE: '3RD', FINAL: 'FINAL'}`.
- New `apply_knockout_fixtures(conn, payload) -> int`: for every non-group match in the feed,
  **upsert by `ext_id`** (the feed match id):
  - Set `stage` (mapped), `kickoff` (`utcDate`), `neutral = 1`, and `slot` (assigned by stage +
    order within stage — see topology below).
  - Set `home_team`/`away_team` (canonicalised) when the feed provides them, else leave `NULL`.
  - When `status == FINISHED`, set scores + `status='FINISHED'` (orientation-independent, matching
    the existing group-result logic).
  - Insert if `ext_id` unseen, else update in place. This keeps re-fetches idempotent and lets
    teams/scores fill in over time.
- `fetch_fixtures` calls both the existing group `apply_fixtures_payload` and the new
  `apply_knockout_fixtures` from the same feed response. `fetch_results` likewise settles knockout
  matches (they already flow through the FINISHED branch once rows exist).
- `_db.touch_update(conn)` on change, so the web SSE refresh fires.

### 3. Bracket topology + forward projection (`bracket.py`, new)

Fixed single-elimination topology over the assigned `slot`s:

- `R16-k` ← winners of `R32-(2k-1)` and `R32-(2k)`; `QF-k` ← `R16-(2k-1)/R16-(2k)`; `SF-k` ←
  `QF-(2k-1)/QF-(2k)`; `FINAL` ← `SF-1/SF-2`; `3RD` ← the two SF losers.
- Slots are assigned to feed matches by **stage + chronological order within stage**. (Assumption:
  the feed orders each knockout round in bracket order. **Validated empirically once the feed
  populates R32 teams (~June 28)**; if the order differs we adjust the slot-assignment lookup. This
  is the single biggest implementation-time risk and gets an explicit verification step.)

`build_predicted_bracket(conn, model)`:

1. Load all knockout rows, grouped by stage, ordered by slot.
2. Walk R32 → FINAL. For each match:
   - Resolve each side's team: feed team if known (`real`), else the **predicted winner of the
     feeder slot** (`projected`).
   - If both teams resolved, predict via the goal model (§4): advance %, most-likely score.
   - Record the predicted winner for downstream slots.
3. `3RD` = predicted losers of `SF-1`, `SF-2`.
4. When a match is `FINISHED`, its **actual** winner/loser overrides the predicted one for all
   downstream slots (results always beat predictions); everything downstream is recomputed.

Pure function over the DB snapshot; no persistence (recomputed per read).

### 4. Predicted advance probability (`predict.py` / `bracket.py`)

Knockouts can't draw. From the goal model's 90-minute `p_home/p_draw/p_away` (intel already
applied by `predict_match`), the probability the home side advances:

```
share = p_home / (p_home + p_away)            # regulation win share; 0.5 if even
advance_home = p_home + p_draw * share         # 90' win + (draw → ET/shootout)
advance_away = 1 - advance_home
```

This mirrors the sim's `_knockout_winner` resolution analytically (no sampling). Most-likely score
uses the existing `ml_home/ml_away`. Knockouts are treated as **neutral venue** (consistent with the
model's WC handling, which already strips normal home advantage).

### 5. Engine API (`engine.py`)

`get_predicted_bracket(conn) -> dict` (new; `/api/knockout/bracket` switches to it). Shape:

```jsonc
{
  "rounds": [
    { "stage": "R32", "label": "32强",
      "matches": [
        { "slot": "R32-1", "ext_id": 537123,
          "home": "Argentina", "away": "Cape Verde",
          "home_known": true, "away_known": true,     // teams from feed (real) vs projected
          "status": "FINISHED", "home_score": 2, "away_score": 0,
          "advance_home": 0.82, "advance_away": 0.18,
          "ml_home": 2, "ml_away": 0,
          "p_home": 0.71, "p_draw": 0.19, "p_away": 0.10,  // for the detail sheet
          "factors": [ /* intel factors, as in match-detail */ ] },
        ...
      ] },
    ... R16, QF, SF, FINAL ...
  ],
  "third_place": { ...one match... },
  "real_fixtures": 16, "total_fixtures": 31      // counter
}
```

Team names stay English in the payload; the UI maps to Chinese + FIFA rank + flag client-side.

### 6. Web UI (`static/app.js`, `index.html`, `styles.css`)

A **single-direction left→right tree** (validated mockup at `/static/bracket-mockup.html`):

- Columns R32 → R16 → QF → SF → FINAL, converging rightward; 季军赛 under the final. Connector
  elbows link each pair to its next-round slot (CSS `:nth-child(of .slot)` with `.top/.bot`
  fallback). Equal-height columns + per-round flex slots auto-align the tree.
- Fits a normal screen (≈5 columns wide, vertical scroll) — no ultra-wide requirement.
- Each node: flag + **中文名 + `FIFA #rank` + English** + **晋级概率%**, winner highlighted green,
  most-likely score. Reuses existing `zh()`, `rankBadge()`, `flag()` from `app.js`.
- Badges: `已赛` (finished, real score) / `真实` (teams known from feed) / `推测` (projected
  matchup). Counter "真实赛程 N / 31".
- Click a node → detail sheet: 90' 1X2 bar, advance %, likely score, xG, intel factors (reuses the
  existing match-detail style). Projected matchups show a "teams from predicted winners" note.
- Lives as a knockout tab; the existing simulated `get_bracket_projection` heatmap and the title
  forecast are unchanged.

### 7. Update flow (manual trigger)

No new automation. When the user asks to fetch/update:

- `fetch-fixtures` ingests the knockout skeleton + real teams as decided; `fetch-results` settles
  finished knockout matches. The bracket and all predictions recompute on the next read.
- Optional `worldcup bracket` CLI to print the predicted bracket for quick terminal checks.

## Testing

- `apply_knockout_fixtures`: stage mapping; insert-then-update idempotency by `ext_id`; null teams
  stored for TBD slots; FINISHED sets scores orientation-independently; group rows untouched.
- `build_predicted_bracket`: real team used when known; projected winner filled when TBD; actual
  result overrides predicted winner downstream; 3RD = SF losers; advance-prob formula
  (`advance_home + advance_away == 1`, draw splits by win share, even match → 0.5).
- Topology: R16-k feeders = R32-(2k-1)/(2k), etc.; slot assignment from feed order.
- Engine `get_predicted_bracket`: shape, counter (`real_fixtures`/`total_fixtures`), ordering.
- Migration: `ext_id` added idempotently; existing rows/predictions unaffected.

## Decisions & risks

- **Feed is authoritative** for structure/teams/dates/results; we never compute the real bracket
  from standings. (`build_r32`/`best_thirds` remain only a possible fallback — out of scope for v1.)
- **Topology alignment** (feed within-stage order ↔ our slot/feeder map) is the main risk; verify
  empirically when R32 teams populate (~June 28) before shipping the forward projection.
- **Neutral venue** for knockouts (matches the model's existing WC handling).
- **No write-on-read**: bracket predictions are computed in memory per request, never persisted
  (consistent with the recent upcoming-predictions fix).
- **Intel applies** to knockout predictions automatically (reuses `predict_match`).

## Out of scope (YAGNI)

- Automated/scheduled bracket updates (manual trigger only).
- Computing the bracket ourselves from group standings (feed provides it).
- Quarter-final-specific betting markets or value bets on knockouts (separate concern).
- Editable/what-if brackets, multiple projection paths, probabilistic per-slot matchups (the user
  chose the hybrid deterministic projection).
