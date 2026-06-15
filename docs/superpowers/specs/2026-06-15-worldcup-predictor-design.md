# worldcup-predictor — Design Spec

**Date:** 2026-06-15
**Status:** Approved (brainstorming) → planning
**Target host:** headless arm64 Ubuntu 24.04, passwordless sudo, no GUI/DISPLAY.

## 1. Goal

Predict FIFA World Cup 2026 match results as accurately and as well-calibrated as
possible, using both on-pitch stats and off-pitch intelligence (injuries, lineups,
rotation, travel). The system fetches/syncs real-world data, records its own
predictions, scores them after the fact, and improves over time. It exposes:

- an **MCP server** so an LLM client (GitHub Copilot CLI) can drive it as tools, and
- a **web UI** (bracket + group tables + match detail) that updates with live results.

Success is measured by **forecast accuracy/calibration** (RPS, Brier, log-loss,
hit-rate) versus naive baselines, **not** by betting profit. Odds are an optional,
manually-supplied side input for ad-hoc value checks.

## 2. Priorities (from brainstorming)

- A (technical project) + C (find market mispricing) primary; D (readable explanations) secondary.
- Off-pitch intelligence is central: the LLM's main job is turning unstructured news
  into structured, source-cited adjustments to the model — not inventing numbers.
- "Self-evolution": engine = level-2 auto-tuning of weights via walk-forward backtest;
  LLM = level-3 "advisor" that proposes structural changes but **only applies on human approval**.
- Predict **1X2 (win/draw/loss) + scoreline**; over/under and BTTS fall out of the same
  goal model for free. Tournament Monte Carlo (advancement/winner) is in scope.

## 3. Architecture (4 layers + automation)

1. **Core engine** (`worldcup_predictor` Python lib + CLI): deterministic, testable,
   LLM-free. Data ingest, Elo ratings, Dixon-Coles goal model, prediction, Monte Carlo
   simulation, evaluation/backtest, auto-tuning, SQLite persistence. **Numbers only come
   from code here** — this is the anti-hallucination guarantee.
2. **Intelligence layer** (LLM via MCP/Copilot): fetch news → extract structured
   `intel_event`s (entity, type, direction, magnitude, source URL, credibility, ts) →
   feed the adjustment engine. Plus the "advisor" reflection loop. Phase 2 automates this;
   Phase 1 supports manual/MCP-driven intel entry + the adjustment mechanism.
3. **MCP server** (`mcp_server.py`): thin FastMCP adapter exposing engine functions as tools.
4. **Web UI** (`web_server.py`): FastAPI REST/JSON over the same SQLite DB + vanilla-JS
   bracket; SSE for live updates. DB is the single source of truth; UI never computes.

**Automation:** cron/systemd runs deterministic jobs (sync data, log results, refit, backtest)
with no LLM. Copilot CLI sessions drive the LLM jobs (intel, prediction narration, advisor).

Analogy: core lib = peripheral + registers; MCP = the ioctl interface; Copilot CLI = the
kernel process driving it through a stable contract instead of poking registers directly.

## 4. Model (verified)

- **Elo** (eloratings.net method): `W_e = 1/(10^(-dr/400)+1)`, `dr = R_a - R_b` (+100 for a
  non-neutral home team; WC group/knockout are neutral, hosts are the exception).
  Update `R += K·G·(W - W_e)`, K=60 (WC), G goal-difference multiplier, W=0.5/0.5 on penalties.
  Seed from eloratings.net table or FIFA-rank formula; shrink data-sparse teams to the mean.
- **Dixon-Coles** goal model via **`penaltyblog`** (MIT, Cython): `λ_h = exp(α_h+β_a+γ)`,
  `λ_a = exp(α_a+β_h)`, γ=0 at neutral venues; low-score correction τ(ρ); time-decay weights
  (ξ≈0.001 for sparse intl data). Produces a score-probability grid → 1X2, exact score, O/U, BTTS.
  For sparse teams: Elo→λ mapping and/or L2 regularisation toward the mean.
- **Evaluation:** RPS (the 1X2 standard, respects ordering), Brier, log-loss; calibration via
  Platt/isotonic; **walk-forward** backtest (no look-ahead) vs baselines (bookmaker-implied,
  higher-Elo, base rates, random).
- **Monte Carlo:** 50,000 sims; pre-compute the 1,128 pairwise grids; vectorised ≈5–10s.
  Group standings with WC tiebreakers (pts → GD → GF → head-to-head → fair-play → FIFA rank),
  top-2 + 8 best 3rd-placed → R32 bracket (fixed pairings, Annex C) → knockout (ET → penalties).

**Risk:** `penaltyblog` is Cython-compiled — must verify it builds/imports on arm64 early.
Fallback: `statsmodels` Poisson GLM + hand-rolled Dixon-Coles τ correction via `scipy.optimize`.

## 5. Verified reference data

**Format:** 48 teams, 12 groups (A–L) of 4, 104 matches. Top 2 per group + 8 best 3rd-placed
= 32 → R32 → R16 → QF → SF → 3rd-place + Final. ET (2×15) then penalty shootout in knockouts.
Group tiebreakers (Annex C): H2H pts/GD/GF (re-applied among still-tied) → overall GD → overall
GF → fair-play → latest FIFA ranking → older FIFA rankings. **No drawing of lots.**

**Groups (as of 2026-06-15):**
A: Mexico(H), South Africa, South Korea, Czech Republic ·
B: Canada(H), Switzerland, Bosnia & Herzegovina, Qatar ·
C: Brazil, Morocco, Scotland, Haiti ·
D: USA(H), Australia, Paraguay, Turkey ·
E: Germany, Ecuador, Ivory Coast, Curaçao ·
F: Netherlands, Japan, Sweden, Tunisia ·
G: Belgium, Egypt, Iran, New Zealand ·
H: Spain, Uruguay, Saudi Arabia, Cape Verde ·
I: France, Senegal, Norway, Iraq ·
J: Argentina, Algeria, Austria, Jordan ·
K: Portugal, Colombia, DR Congo, Uzbekistan ·
L: England, Croatia, Ghana, Panama.
Dates: group stage Jun 11–27; R32 Jun 28–Jul 3; R16 Jul 4–7; QF Jul 9–11; SF Jul 14–15;
3rd place Jul 18; Final Jul 19 (MetLife). Hosts USA/Canada/Mexico. Defending champ: Argentina.

**Data sources (free):**
- History (bootstrap Elo/DC): `martj42/international_results` raw CSV (1872–present, no auth).
- Live fixtures/results: football-data.org API v4, competition code `WC` (free key, 10 req/min).
  Backups: API-Football (RapidAPI free 100/day), ESPN undocumented JSON.
- Off-pitch news: RSS (BBC/Sky/Guardian) via `feedparser`; NewsAPI dev key (dev only).
- xG (later): StatsBomb open-data (historical WC) / FBref via `soccerdata` (rate-limited).

## 6. Tech stack (verified versions)

Python 3.12, **uv** (src layout), `hatchling` build backend, `[project.scripts]` entry points.
`mcp[cli]>=1.27,<2` (FastMCP, stdio transport — never print to stdout). `fastapi[standard]>=0.115`
+ `sse-starlette>=2.1` + vanilla JS/CSS-grid bracket. `penaltyblog` (model), `pandas`/`numpy`,
`httpx`, `feedparser`, `typer` (CLI). Dev: `pytest`+`pytest-asyncio`, `ruff`, `mypy --strict`.

## 7. Phasing

- **Phase 1 (this plan):** end-to-end working system — scaffold, SQLite, historical+fixture
  ingestion, Elo, Dixon-Coles prediction (1X2+score), the intel→λ adjustment mechanism with
  manual/MCP entry, Monte Carlo simulation, evaluation vs baselines, CLI, MCP server, web UI
  (bracket + tables + match detail + SSE), cron automation.
- **Phase 2:** automated off-pitch intelligence (scrape→LLM-extract→structured intel),
  level-2 auto-tuning, level-3 advisor. Separate spec/plan.
- **Phase 3 (optional):** unattended-LLM intel (embedded API), value-bet helper, richer UI/xG.

## 8. Key risks & mitigations

- **LLM fabrication** → numbers always from code; intel must be source-linked + corroborated; credibility weighting; human gate on structural changes.
- **Market already prices known news** (for value use) → timing matters; not core to success metric.
- **Overfitting in self-tuning** → walk-forward only, regularisation, human approval for structural change.
- **Small WC sample** → bootstrap from broad international history; WC specifics as adjustments.
- **arm64 build of penaltyblog** → verify early; documented fallback.
- **Free-source fragility** → modular source adapters, DB caching, graceful degradation.
