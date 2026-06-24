# Backlog — post-review (2026-06-24)

Generated from a 4-agent review panel (architecture, test-coverage, PM/undone-tasks, docs)
reconciled against the live code, the prod DB, and the specs/plans. Each open item is written
to be lifted straight into a GitHub issue: context, evidence (`file:line`), impact, effort
(S/M/L), and a concrete recommendation.

## Resolved in this review pass

- **README rewrite + MIT `LICENSE`** — friendly rewrite, 12 inaccuracies fixed.
- **Totals best-price outlier filter** — `_best_total_prices` now drops corrupt prices like 1x2/spreads.
- **MCP `affects` reset bug** — corroborating upserts no longer flip a `defense`/`both` row back to `attack`; Partey re-tagged `both`.
- **Public write-on-read** — `/api/upcoming-predictions` no longer persists a prediction per hit; prod `predictions` deduped 9,632 → 72 (one original snapshot per match).
- **Stuck-LIVE settlement** — `stale_unsettled_matches` detector + `fetch-results` warning + `stale-matches`/`record-result` CLI.
- **Tidy** — removed empty `.worktrees/` and the 7.9MB local `.bak`.

---

## Open backlog

### A. Modeling / analytics

**A1 — Calibration is inert, and there is no calibration↔paper-trading loop.** *(High, L)*
The 1X2 calibrator grid-searched to identity (`tuning_params.calibration` = `draw_mult=1.0, temp=1.0`),
and by design it only touches 1X2 in `predict` (`predict.py:~69`) — not the simulation and not the
totals/handicap markets, which is exactly where the value bets cluster (underdog longshots). The
paper ledger measures CLV/ROI but nothing feeds back into the model, so "are our edges real or
model bias?" is currently unanswered for the markets that matter. Treat as a research spike: use
the accumulating settled paper bets as the OOS signal for a per-market edge shrinkage or a
totals/cover calibration, rather than implying the weekly backtest already covers it.

**A2 — Auto-tuning has never been adopted in prod.** *(Low, S)*
`worldcup tune --apply` exists and is cron-scheduled, but `tuning_params` holds no tuned
`model_params`/`xi` row — the hand-set `xi` was already optimal so nothing moved. Run `tune` once,
record the outcome, and stop advertising "self-evolution" beyond what has actually been adopted.

**A3 — Elo prior into the goal model.** *(Low, M)* Deferred per the backtest (marginal). Revisit only
if sparse-team fits prove unstable.

### B. Architecture / optimization

**B1 — `engine.py` is a 554-LOC god-module.** *(Important, M)*
Mixes read-projection DTO assembly, stateful model-cache lifecycle (globals + lock), write/command
ops, and orchestration (`engine.py` cache `~413-457`, reads `~26-233`, commands `~340-518`). Split
into `model_cache.py` / `views.py` / `commands.py`, keeping `engine.py` as a thin re-export facade so
callers/tests don't churn. Isolating the threading/global state is the highest-value cut.

**B2 — Value-bet best-price/consensus logic is triplicated across the three markets.** *(Important, M)*
`best_prices`/`_best_total_prices`/`_best_spread_prices` and `consensus_probs`/`_totals_consensus`/
`_spreads_consensus` are ~3×100 LOC of near-identical scaffolding (`valuebet.py`). Introduce a small
`Market` protocol (`best_prices`, `consensus`, `our_probs`) + one generic `_value_bets(market, …)`
driver. (The totals outlier gap that triplication hid was already patched.)

**B3 — No persistent model cache.** *(Important, M)*
`_MODEL` is a process-local global, so web/MCP/CLI each pay the full cold fit (~62s on the 2018+
window) on first use. `CACHE_DIR` is defined (`config.py:14`) but never used. Pickle the fitted model
keyed on `(db file hash/mtime, time_decay_xi)`; load before refitting; invalidate on `load-history`/`tune`.

**B4 — `attack/defense/both` distribution block duplicated across two intel modules.** *(Nice-to-have, S)*
`player_status.py:154-180` and `team_signal.py:145-171` share the same ~25-line shape. Extract a
`distribute_affects(base, desc, team) -> (atk_delta, dfn_delta, factors)` helper.

**B5 — `ScoreGrid.over`/`cover` are O(n²) Python loops.** *(Nice-to-have, S)*
`goal_model.py:46-52,57-68` — inconsistent with the vectorized `home_win`/`away_win`/`draw`. Vectorize
with a precomputed `H+A` index matrix + boolean mask. (Immaterial perf — readability/consistency.)

**B6 — Monte Carlo `_sample_score` per-call `rng.choice`.** *(Nice-to-have, S)*
`simulate.py:~162` renormalizes + samples a 225-element array ~3.6M times at n=50k. Precompute each
pair's flattened CDF once and sample via `np.searchsorted`. (Cached cron job — acceptable today.)

**B7 — `_conn()` duplicated across entrypoints; declared-unused config.** *(Nice-to-have, S)*
Same body in `web_server.py`/`mcp_server.py`/`cli.py`; `CACHE_DIR` and `NEWSAPI_KEY` are declared but
unused. Centralize `db.connect_default()`; wire up or remove the unused config.

**B8 — SSE holds one DB connection per browser tab.** *(Nice-to-have, S)*
`web_server.py:~132-149` keeps a connection open for the connection's lifetime polling
`meta.last_update`. Open/close a short-lived connection inside the loop so idle tabs don't pin connections.

**B9 — Inconsistent network error handling in fetch/ingest.** *(Nice-to-have, S)*
`news.fetch_news` swallows per-feed failures, but `odds.fetch_odds`/`ingest.fetch_*` let raw `httpx`
errors propagate, so a single 429/timeout aborts the whole run. Pick one convention; at minimum catch
`httpx.HTTPError` for actionable messages.

### C. Operations

**C1 — No monitoring/alerting.** *(Medium, S/M)*
No alert on cron failure, stale data, or odds-quota exhaustion; failures are silent on a live site.
Add a freshness/heartbeat check (age of last successful fetch) + one notification channel. The new
`stale_unsettled_matches` warning is the first signal to wire in.

**C2 — Auto-intel is semi-manual; no scheduled news→intel extraction.** *(Medium, L)*
Cron only pulls RSS into `news_articles` (`news.py`); turning articles into intel relies on a human
driving the MCP/LLM loop, so intel goes stale if nobody runs it. Decide: document it as human-gated,
or build a scheduled LLM extractor that calls the MCP tools (the `affects`-reset fix is a prerequisite).

**C3 — Legacy intel path lingers.** *(Low, S)*
The old attack-only `record_intel`/`intel_events` path (`mcp_server.py:~84`, `intel.py`) coexists with
the newer upsert tools. Document or deprecate to avoid confusion.

### D. Features (deferred)

**D1 — Defensive channel invisible in the web UI + no structured `IntelFactor.affects`.** *(Medium, M)*
The channel is conveyed only via description text; `models.py` `IntelFactor` has no `affects` field, and
`app.js` shows no defense badge. Add the field + a web badge so users can see why an opponent's xG moved.

**D2 — Quarter/split Asian lines (-0.75, -1.25) skipped.** *(Medium, M)*
`odds.py:~191` skips non-whole/half lines. Add split-line handling (two half-line legs + half-push
settlement) once the feed's quarter-line frequency justifies it.

**D3 — Per-source reliability weighting for news.** *(Low, M)* Weight credibility by source track record.

### E. Test-coverage gaps

Highest-leverage first (severity from the test-coverage review):

- **E1 (High)** — `value_bets_spreads` **away-cover branch** is entirely untested (`valuebet.py:319-346`).
  Seed a fixture where a weak away +1.5 is priced cheap; assert an `outcome=="away"` bet.
- **E2 (Med)** — `team_signal.team_signal_factor` `affects='both'`/`'defense'` paths untested
  (`team_signal.py:152-169`).
- **E3 (Med)** — MCP tool **execution/validation** untested: ToolError guards on `record_match_result`
  (negative scores) and `record_intel` (bad direction/credibility), plus passthrough tools never *called*
  (`mcp_server.py:71-73,101-106`). (The `affects`-preserve regression test was added this pass.)
- **E4 (Med)** — `web_server` **lifespan** model-warm uncovered: tests build `TestClient(app)` without the
  `with` context, so the daemon warm thread + its blanket `except` never run (`web_server.py:27-44`).
- **E5 (Med)** — `best_prices` outlier filter only exercised on the **home** column; draw/away and
  `_best_spread_prices` away-side outlier untested (`valuebet.py:46-63,261-263`).
- **E6 (Med)** — `get_match_detail` grid-populated branch (`scorelines`/`over25`/`btts`) untested
  (`engine.py:122-129`).
- **E7 (Low/Med)** — failure injection: `news.fetch_news` per-feed isolation, `fetch_live_results`/
  `fetch_fixtures` token handling, malformed-payload skips (`ingest.py:84-85`, `odds.py:191`).

Two weak tests to tighten: the `/api/upcoming-predictions` + `/api/value-bets` web tests fully
monkeypatch the engine (passthrough-only — add one real end-to-end path); `test_mcp_server.py:72` uses
a broad `pytest.raises(Exception)` (narrow to `ToolError`).
