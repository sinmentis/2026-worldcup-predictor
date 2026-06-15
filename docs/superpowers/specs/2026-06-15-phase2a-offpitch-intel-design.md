# Phase 2a — Automated Off-Pitch Intelligence — Design Spec

**Date:** 2026-06-15
**Status:** Approved (brainstorming) → planning
**Builds on:** Phase 1 (`v0.1.1`), specifically the `apply_intel` → λ adjustment mechanism.

## 1. Goal

Automatically turn football news into structured, source-cited, credibility-weighted
adjustments to the model's expected goals (λ). Pipeline:

1. **Deterministic fetch** (no LLM, cron-able): pull news from free sources into a DB.
2. **LLM extraction in-session** (Copilot CLI via MCP): read raw articles, extract structured
   player statuses (who, importance tier, status, confidence, source).
3. **State-based truth**: a current-status-per-player store that news *updates* (not stacks).
4. **λ adjustment**: the existing `apply_intel` is reimplemented to read current statuses.
5. **Confidence + corroboration gate**: trustworthy items apply automatically; weak ones queue
   for human approval.

Success: news about injuries/suspensions/rotations measurably and traceably shifts the relevant
match predictions and tournament odds, with no hand-entry, and without letting a misread headline
silently distort the forecast.

## 2. Decisions locked in brainstorming

- **Quantification = hybrid (option C):** the LLM only classifies `{event/status, importance tier,
  confidence}`; a fixed, tunable `MAGNITUDE_TABLE[tier][status]` maps that to a number. Phase 2b
  will auto-tune the table. The LLM never invents the magnitude.
- **Lifecycle = state-based (option B):** one current row per `(team, player)`. News updates the
  state; "returns/available" clears it; multiple sources corroborate (raise credibility) instead
  of stacking deltas. This solves dedup / expiry / retraction.
- **Trust = confidence + corroboration gated (option C):** high confidence AND (≥2 independent
  sources OR 1 official source) → applies automatically; otherwise queued for human review.
- **Execution = autonomy C:** extraction runs in a Copilot CLI session via MCP tools (no embedded
  LLM API key required); architecture leaves room for unattended extraction later.
- **Sources:** free — RSS (BBC Sport, Sky Sports, The Guardian, ESPN) via `feedparser` (already a
  dependency). NewsAPI is an optional, key-gated supplement (not required).

## 3. Architecture & components

### New modules
- `news.py` — deterministic fetcher. Pulls configured RSS feeds (+ optional NewsAPI), normalizes
  to articles, dedups by URL, stores in `news_articles`. Per-feed `try/except`, polite rate limits,
  graceful degradation. No LLM.
- `player_status.py` — the state-based truth store. Owns the `player_status` table operations
  (`upsert_status`, the trust gate, expiry, `purge_expired`), the `MAGNITUDE_TABLE`, and a
  `team_status_factor(conn, team) -> (delta, factors)` that turns active statuses into a λ delta.

### Reused / changed in place
- `intel.py` — **`apply_intel(lam_h, lam_a, home, away, conn)` keeps its name, signature, AND import
  path** (`predict.py` and `simulate.adjusted_grid` call `intel.apply_intel`). It is reimplemented to
  sum **both** the new `player_status.team_status_factor` (primary) and the legacy `intel_events`
  factor (back-compat), then clamp. Phase-1 manual `record_intel` / `intel_events` and their tests
  stay green.
- `predict.py`, `simulate.py` — unchanged.

## 4. Data model (SQLite)

```sql
CREATE TABLE IF NOT EXISTS news_articles (
    id INTEGER PRIMARY KEY,
    source TEXT,
    url TEXT UNIQUE,            -- dedup key
    title TEXT,
    summary TEXT,
    published_at TEXT,
    fetched_at REAL,
    processed INTEGER DEFAULT 0 -- 1 once the LLM has extracted from it
);

CREATE TABLE IF NOT EXISTS player_status (
    id INTEGER PRIMARY KEY,
    team TEXT NOT NULL,
    player TEXT NOT NULL,
    tier TEXT NOT NULL,         -- 'key' | 'regular' | 'fringe'
    status TEXT NOT NULL,       -- 'out' | 'doubtful' | 'suspended' | 'available'
    credibility REAL NOT NULL,  -- 0..1, derived from sources
    sources TEXT NOT NULL,      -- JSON array of source URLs
    valid_until TEXT,           -- ISO date; NULL = no expiry
    as_of REAL NOT NULL,        -- last-updated unix ts
    pending INTEGER DEFAULT 0,  -- 1 = awaiting human approval
    notes TEXT,
    UNIQUE(team, player)        -- one current row per player
);
```

`team` is canonicalized via `config.canonical_team`. A status of `available` removes the player's
row (back to normal). Only **active** rows (`pending=0` and not expired) affect λ.

## 5. Magnitude mapping (decision C; tunable in 2b)

```python
MAGNITUDE_TABLE = {
    ("key", "out"): 0.72,     ("key", "suspended"): 0.72,     ("key", "doubtful"): 0.88,
    ("regular", "out"): 0.85, ("regular", "suspended"): 0.85, ("regular", "doubtful"): 0.93,
    ("fringe", "out"): 0.96,  ("fringe", "suspended"): 0.96,  ("fringe", "doubtful"): 0.98,
}
# 'available' -> multiplier 1.0 (no effect / row removed)
```

Per active status, `delta = credibility * (multiplier - 1)`. Per team, sum deltas and clamp to the
existing `ADJUST_CLAMP` (±0.6); then `lam *= (1 + delta)`. This matches Phase-1 `apply_intel`
semantics, so `retilt_grid` and everything downstream is unchanged.

Example: key player `out`, credibility 0.9 → `delta = 0.9 * (0.72 - 1) = -0.252` → team λ × 0.748.

## 6. Trust gate & credibility (decision C)

`upsert_status(conn, team, player, tier, status, confidence, source_url, official=False, ...)`:

1. Canonicalize team; validate `tier`/`status` enums, `confidence ∈ [0,1]`, non-empty `source_url`.
2. Look up the existing `(team, player)` row.
   - If `status == 'available'`: delete the row (player recovered) and return.
   - Else upsert: append `source_url` to `sources` (dedup), set `tier`/`status`, bump `as_of`,
     set `valid_until` (see §7), carry an `official_ever` flag.
3. **Derive credibility** from the row's evidence:
   - official source ever → `0.95`
   - else ≥2 distinct sources → `0.80`
   - else single non-official source → `0.50`
4. **Gate:** `pending = 0` (active) iff `credibility ≥ 0.70` AND `confidence ≥ 0.60`; else `pending = 1`.
   A later corroborating source can flip a pending single-source item to active (sources→2 ⇒ 0.80).
5. Return `{status: 'active'|'pending', credibility, ...}` so the caller (the LLM) knows the outcome.

Human review: `list_pending`, `approve(id)` (force `pending=0`), `reject(id)` (delete).

## 7. Expiry

`valid_until` defaults to the team's **next scheduled match date** (from `matches`, the earliest
`SCHEDULED` match for that team) plus a 1-day buffer; if no schedule is known, default to +14 days.
`apply_intel` filters `valid_until IS NULL OR valid_until >= date('now')`. A `purge_expired(conn)`
helper deletes expired rows (called by the fetch/cron job).

## 8. Interfaces

### MCP tools (the in-session extraction surface — Copilot drives)
- `get_unprocessed_news(limit=20) -> list[Article]` — raw articles with `processed=0` for the LLM to read.
- `upsert_player_status(team, player, tier, status, confidence, source_url, official=False, notes="")`
  — applies the trust gate; returns active/pending + credibility.
- `mark_news_processed(article_ids: list[int])` — flag articles done.
- `list_pending_intel() -> list[...]`, `approve_intel(id)`, `reject_intel(id)` — human review queue.

### CLI
- `worldcup fetch-news` — deterministic fetch (cron-able).
- `worldcup intel-pending` — list the pending queue.
- `worldcup intel-approve <id>` / `worldcup intel-reject <id>`.

### Engine facade (used by adapters)
- `engine.fetch_news(conn)`, `engine.get_unprocessed_news(conn, limit)`,
  `engine.upsert_player_status(conn, **kwargs)`, `engine.mark_news_processed(conn, ids)`,
  `engine.list_pending_intel(conn)`, `engine.approve_intel(conn, id)`, `engine.reject_intel(conn, id)`.

## 9. Data flow (one cycle)

```
cron: worldcup fetch-news  ──>  news_articles (raw, processed=0)   [no LLM]
you (in a Copilot session): "process the latest news"
  └─ Copilot: get_unprocessed_news() -> read articles
              -> for each relevant item: extract {team, player, tier, status, confidence, source}
              -> upsert_player_status(...)        [trust gate: active or pending]
              -> mark_news_processed([...])
predict/simulate  ──>  apply_intel reads ACTIVE player_status (+ legacy events)  ──>  λ shifts
pending items  ──>  you: intel-approve / intel-reject (or via Copilot)
```

## 10. Error handling

- Fetch: each feed independent (`try/except`), URL dedup, polite intervals, degrade to "no new
  articles" on failure. Never crash the cron job.
- Extraction (LLM, in-session): record only what the article states; every status carries
  `source_url`; the gate + pending queue catch low-confidence / single-source items.
- Inputs: validate enums, ranges, required `source_url` (as Phase-1 `record_intel` already does).

## 11. Testing

- `news.py`: parse a captured RSS XML **fixture** → articles; URL dedup; no network in tests.
- `player_status.py`: upsert (new / corroborate / supersede / available-clears); credibility
  derivation; the trust gate (active vs pending, single→corroborated flip); expiry filtering;
  the λ-delta math; legacy-events back-compat in `apply_intel`.
- MCP tools: registration + a call updates status / queues pending / approves.
- Integration: fetch (fixture) → upsert active status → `predict_fixture` reflects it; approve a
  pending item → it becomes active and changes the prediction.

## 12. Phasing within 2a (each its own milestones in the plan)

- **2a.1** — data model (`news_articles`, `player_status`) + `news.py` RSS fetcher + `fetch-news` CLI.
- **2a.2** — `player_status` store: `upsert_status`, credibility, trust gate, expiry,
  `MAGNITUDE_TABLE`, and the reimplemented `apply_intel` (state + legacy). Tests.
- **2a.3** — MCP tools (`get_unprocessed_news`, `upsert_player_status`, pending review) + CLI
  `intel-pending` / `intel-approve` / `intel-reject` + engine facade.
- **2a.4** — end-to-end (real RSS fetch + an in-session extraction walkthrough) + README/docs +
  cron template for `fetch-news`.

## 13. Out of scope (later)

- **2b** level-2 auto-tuning of `MAGNITUDE_TABLE` / thresholds via walk-forward backtest.
- **2c** level-3 LLM advisor (reviews systematic errors, proposes changes, human-gated).
- Unattended (no-session) LLM extraction via an embedded API key (autonomy B).
- A player-impact dataset (data-driven magnitudes) as an alternative to the tier table.
- Surfacing intel/pending state in the web UI.

## 14. Risks & mitigations

- **LLM misread → wrong adjustment:** trust gate + pending queue + source links + the LLM never
  setting the magnitude (only the tier). Human can `reject`.
- **Stale intel:** state-based model + `valid_until` expiry tied to the team's next match.
- **Source fragility (free RSS):** per-feed isolation, URL dedup, graceful degradation.
- **Double-count with legacy intel:** documented; `apply_intel` sums both but the clamp bounds the
  net per-team effect; the new pipeline is the primary path.
