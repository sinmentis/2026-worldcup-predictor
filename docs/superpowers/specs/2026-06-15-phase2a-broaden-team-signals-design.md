# Phase 2a-broaden — Team-Level Off-Pitch Signals — Design Spec

**Date:** 2026-06-15
**Status:** Approved (defaults locked under autopilot) → building
**Builds on:** Phase 2a (`v0.2.1`) — reuses the trust gate, corroboration, expiry, and `apply_intel`.

## 1. Problem

Phase 2a only modeled **player availability** (injury/suspension/doubtful), and only ever *weakened*
a team. Real news carries far more between-the-lines signal — tactics, morale, motivation, fatigue,
team form/eye-test — in both directions. This extension lets the system capture any
qualitative signal that should nudge a team's expected goals up or down.

## 2. Decisions (locked)

- **Bidirectional:** signals can `weaken` or `strengthen`. Strengthen swings are capped **smaller**
  than weaken swings (positive narratives are more prone to over-optimism).
- **Team-level model:** a new `team_signal` store, separate from `player_status` (which stays as the
  player-availability model). One current signal **per `(team, category)`** (state-based per category).
- **Categories:** `tactical`, `morale`, `motivation`, `fatigue`, `form` (form = general team
  form / eye-test beyond the scoreline).
- **Soft + bounded:** team signals are qualitative, so magnitudes are small (far smaller than a key
  injury). The existing `ADJUST_CLAMP` (±0.6) still bounds each team's *net* delta across everything.
- **Reuse the guardrails:** same `derive_credibility`, the **non-demoting** trust gate, the pending
  review queue, source citation, and expiry as Phase 2a.
- **Avoid double-counting match results:** small caps + tool guidance to record only *forward-looking
  or beyond-the-result* signals (not match recaps, which are already ingested as results).

## 3. Data model

```sql
CREATE TABLE IF NOT EXISTS team_signal (
    id INTEGER PRIMARY KEY,
    team TEXT NOT NULL,
    category TEXT NOT NULL,        -- tactical | morale | motivation | fatigue | form
    direction TEXT NOT NULL,       -- weaken | strengthen
    magnitude_tier TEXT NOT NULL,  -- major | moderate | minor
    credibility REAL NOT NULL,
    sources TEXT NOT NULL,         -- JSON array of URLs
    official INTEGER DEFAULT 0,
    valid_until TEXT,
    as_of REAL NOT NULL,
    pending INTEGER DEFAULT 0,
    notes TEXT,
    UNIQUE(team, category)         -- one current signal per category per team
);
```

## 4. Magnitude mapping (`team_signal.py`)

```python
TEAM_SIGNAL_MAGNITUDE = {
    ("weaken", "major"): 0.88, ("weaken", "moderate"): 0.93, ("weaken", "minor"): 0.97,
    ("strengthen", "major"): 1.06, ("strengthen", "moderate"): 1.04, ("strengthen", "minor"): 1.02,
}
```

Per active signal: `delta = credibility * (multiplier - 1)`. `team_signal_factor(conn, team)` sums a
team's active, non-expired signals and clamps to `ADJUST_CLAMP`.

## 5. `apply_intel` (intel.py)

Reimplemented again to sum **three** sources, then clamp per team:
`legacy intel_events` + `player_status.team_status_factor` + `team_signal.team_signal_factor`.
Signature and import path unchanged; `predict`/`simulate` untouched.

## 6. Trust gate, expiry, review

Identical rules to Phase 2a: credibility = official→0.95 / ≥2 sources→0.80 / single→0.50; active iff
`credibility ≥ 0.70 AND confidence ≥ 0.60`; **non-demoting** (an active/approved signal is never
re-pended by a later lower-confidence corroboration); expiry via `valid_until` (team's next match +1d,
else +14d). `derive_credibility`, thresholds, and `_default_valid_until` are imported from
`player_status` (no import cycle: `team_signal` → `player_status`; `intel` → both).

## 7. Interfaces

- `team_signal.py`: `upsert_signal`, `team_signal_factor`, `list_pending`, `approve`, `reject`, `purge_expired`.
- `engine.py`: `upsert_team_signal`, plus `list_pending_intel` now returns **both** player statuses and
  team signals (tagged by `kind`), and `approve_intel`/`reject_intel` resolve either kind by a prefixed id.
- `mcp_server.py`: tool `upsert_team_signal(team, category, direction, magnitude_tier, confidence, source_url, official=False, notes="")` with `ToolError` validation; its docstring instructs the LLM to record only forward-looking/qualitative-beyond-result signals.
- `cli.py`: `intel-pending` lists both kinds.

## 8. Testing

- `team_signal.py`: magnitude lookup; `upsert_signal` (new/corroborate/non-demote/validate);
  per-category state (two categories coexist for one team); `team_signal_factor` (weaken vs
  strengthen sign, clamp, expiry).
- `intel.apply_intel`: now includes team-signal factor; legacy + player_status still work.
- engine/MCP/CLI: tool registration; a strengthen signal raises a team's win prob; pending review of
  a team signal.
- Integration: a team-level `tactical strengthen` signal raises a team's predicted win probability.

## 9. Out of scope

- Player-level form as a distinct schema (expressible as a team `form` signal for now).
- Auto-tuning the magnitude tables (Phase 2b) and the LLM advisor (Phase 2c).
- Surfacing signals in the web UI.

## 10. Risks

- **Subjectivity / over-optimism:** small caps (strengthen < weaken), trust gate, human review, clamp.
- **Double-counting results:** small caps + "forward-looking only" tool guidance + expiry.
