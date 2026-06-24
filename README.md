# ⚽ World Cup Predictor

> A deterministic, source-honest prediction engine for the FIFA World Cup 2026 — goal model, Monte Carlo, off-pitch intel, and value betting you can audit.

**🔴 Live demo: [worldcup.shunlyu.com](https://worldcup.shunlyu.com)** — public, read-only, no sign-up.

## What is this?

World Cup Predictor forecasts the 2026 tournament from first principles. It ingests
~49,000 historical international matches and fits a Dixon-Coles goal model on the most
recent years (recency-weighted), turns that into 1X2 probabilities, expected goals and
likely scorelines, then runs a Monte Carlo simulation of the whole bracket to get title
and round-by-round odds.

On top of the numbers, it folds in **off-pitch intelligence** (injuries, suspensions,
tactical and morale signals) — every adjustment source-linked and human-gated — and
hunts for **value bets** against the bookmaker market, tracked through a **paper-trading
ledger that risks zero real money**.

The core engine is deterministic and LLM-free. An LLM (GitHub Copilot CLI) only reads
the news and writes structured intel through an MCP server. The math stays reproducible.

## ✨ Features

- 🎯 **Dixon-Coles goal model** → full score grid → 1X2 probabilities, expected goals, likely scorelines.
- 🏆 **Monte Carlo tournament sim** → title, advancement and round-by-round odds. The fitted model is cached and warmed in the background on startup (a cold fit is ~2 min).
- 🕵️ **Off-pitch intelligence** — source-linked player availability and team signals (tactical / morale / motivation / fatigue / form) nudge each team's scoring rate. A defensive channel means a key defender's absence raises the *opponent's* expected goals.
- 🔒 **Credibility trust gate** — single-source intel stays *pending*; multi-source or official intel auto-activates. Humans stay in the loop.
- 💸 **Value betting across three markets** — 1X2, totals (over/under) and handicap/Asian spreads — comparing the model to a de-margined bookmaker consensus (median across ~40 books) with edge, EV and fractional-Kelly stakes.
- 📒 **Paper-trading ledger (no real money)** — auto-logs flagged bets, captures the closing line at kickoff, settles win/loss/push, and tracks CLV and ROI.
- 📰 **News → intel pipeline** — free RSS feeds in, LLM-extracted structured intel out, via MCP.
- 🌐 **Web UI** — forecast board, live per-match predictions, value bets, paper-trading ledger, accuracy vs. baseline, FIFA-rank badges, group standings and the knockout bracket.
- ✅ **Quality bar** — `ruff`, `mypy --strict`, and 205 passing tests.

## 🚀 Quickstart (≈60 seconds)

You need **Python 3.12** and **[uv](https://docs.astral.sh/uv/)**.

```bash
# 1. Install dependencies and create your env file
uv sync
cp .env.example .env

# 2. Build the database and seed the 48-team, 72-fixture group stage
uv run worldcup init-db
uv run worldcup seed
uv run worldcup load-history      # ~49k historical international results

# 3. Simulate the tournament
uv run worldcup simulate --n 50000

# 4. Launch the web UI → http://127.0.0.1:8080
uv run worldcup serve
```

> The first prediction or simulation triggers a one-time model fit (~2 min). After that
> it's cached and fast. `serve` warms the model in the background on startup.

To expose it on your LAN:

```bash
uv run worldcup serve --host 0.0.0.0 --port 8080
```

### Handy commands

```bash
uv run worldcup fetch-fixtures        # kickoff times + results from football-data.org
uv run worldcup fetch-results         # finished World Cup results
uv run worldcup predict <match_id>    # predict and persist one fixture
uv run worldcup evaluate              # score finished predictions vs baseline
uv run worldcup backtest --fit-calibration   # walk-forward skill + fit the calibrator
uv run worldcup tune --apply          # auto-tune recency decay (dry-run without --apply)

uv run worldcup fetch-odds            # bookmaker odds (needs ODDS_API_KEY)
uv run worldcup value-bets --min-edge 0.05   # edge candidates across 1X2 / totals / spreads

uv run worldcup paper-log             # log current value bets to the paper ledger
uv run worldcup paper-settle          # capture closing lines + settle finished bets
uv run worldcup paper-status          # ROI, hit-rate and CLV scoreboard

uv run worldcup fetch-news            # pull RSS articles into SQLite (cron-friendly)
uv run worldcup intel-pending         # review intel awaiting approval
uv run worldcup intel-approve <ref>   # ref is ps:<id> (player) or ts:<id> (signal)
uv run worldcup intel-reject <ref>
```

Run `uv run worldcup --help` for the full list.

## 🧠 How it works

**1. The goal model.** A Dixon-Coles model (via [`penaltyblog`](https://pypi.org/project/penaltyblog/))
learns each team's attack and defence strength from history, with recency weighting and a
modest host-nation bump (World Cup matches strip out normal home advantage). For a fixture
it produces a full grid of scorelines, which collapses into win/draw/win probabilities,
expected goals and the most likely results.

**2. Off-pitch adjustments.** Before predicting, the engine applies *active* intel for each
team — player availability and qualitative team signals. Each item carries a soft multiplier
on the relevant scoring rate. Attacking absences lower a team's own expected goals; defensive
absences raise the opponent's. Strengthen swings are deliberately capped smaller than weaken
swings, everything is bounded by a clamp, and only intel that clears the trust gate has any
effect at all.

**3. The tournament simulation.** Monte Carlo plays out every group match and the entire
knockout bracket tens of thousands of times, yielding title odds, advancement probabilities
and a round-by-round heatmap.

**4. Keeping it honest.** A walk-forward `backtest` measures out-of-sample skill (RPS, Brier,
log-loss) against a flat baseline, reports calibration (reliability curve + ECE), and can fit
a small calibrator. `tune` grid-searches the recency-decay hyperparameter with guardrails.

## 💸 Value betting & paper trading (the honest part)

The model also looks for **edges against the market**. For each fixture it de-margins every
book's odds, takes the median across ~40 books as the consensus, and flags outcomes — in 1X2,
over/under and handicap markets — where its own probability beats that consensus, reporting
edge, EV at the best available price, and a fractional-Kelly stake.

**The market is usually right.** A ~40-book consensus is extremely sharp, so these are *edge
candidates to sanity-check*, not guaranteed profit — a big gap most often means a stale or
erroneous line.

So nothing here bets real money. The **paper-trading ledger** logs flagged bets, captures the
**closing line** at kickoff, settles them, and tracks **ROI and CLV (closing-line value)**.
CLV — did you beat the price the market settled on? — is the honest edge signal; win/loss over
a handful of bets is mostly luck. This is evidence-gathering *before* anyone risks capital.

## 🤖 LLM + automation split

The engine is deterministic; the LLM only handles language. The two never blur:

- **No-LLM jobs (cron / systemd):** `fetch-results`, `fetch-fixtures`, `fetch-odds`,
  `fetch-news`, `simulate`, `paper-settle`. Reproducible and schedulable.
- **LLM jobs (GitHub Copilot CLI via MCP):** read raw news, extract structured source-linked
  intel, explain predictions. The model math is never touched by an LLM.

### MCP server

`worldcup-mcp` is a thin [FastMCP](https://github.com/jlowin/fastmcp) stdio server over the
engine. It exposes tools for group standings, upcoming matches, recording results, predicting
matches, recording intel (player statuses and team signals, with the `affects` channel),
reviewing pending intel, processing news, and running simulations — it owns no prediction logic.

```bash
uv --directory /path/to/worldcup-predictor run worldcup-mcp
```

VS Code picks up the checked-in `.vscode/mcp.json` automatically. In GitHub Copilot CLI, add
the same command with `/mcp add`.

A typical news → intel flow: `get_unprocessed_news` → `upsert_player_status` /
`upsert_team_signal` → `mark_news_processed`. New intel lands in the pending queue until it
clears the trust gate or a human approves it.

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  SQLite (data/worldcup.db) — single source of truth          │
└─────────────────────────────────────────────────────────────┘
        ▲                    ▲                    ▲
        │                    │                    │
┌───────────────┐   ┌────────────────┐   ┌─────────────────────┐
│ Core engine   │   │ MCP adapter    │   │ Web UI (FastAPI)    │
│ (deterministic│   │ worldcup-mcp   │   │ worldcup serve /    │
│  Python lib)  │   │ thin FastMCP   │   │ worldcup-web        │
│ ingest, model,│   │ wrapper for    │   │ JSON API + static   │
│ intel, sim,   │   │ LLM clients    │   │ assets + SSE        │
│ value, eval   │   │                │   │ (reads SQLite only) │
└───────────────┘   └────────────────┘   └─────────────────────┘
```

- **SQLite is the source of truth** (`data/worldcup.db`). Everything else reads and writes it.
- **The core engine** (`src/worldcup_predictor/`) is a deterministic library: ingest, schema,
  ratings, Dixon-Coles prediction, intel adjustments, simulation, value betting, evaluation.
- **The MCP adapter** is a stateless wrapper — it calls engine functions, owns no logic.
- **The web UI** computes nothing itself: it serves cached engine output from SQLite as JSON,
  static assets and server-sent events for live refresh.

Key web endpoints: `/api/upcoming-predictions`, `/api/forecast`, `/api/value-bets`,
`/api/paper-trades`, `/api/accuracy`, `/api/groups/{g}/standings`, `/api/knockout/bracket`,
`/api/bracket-projection`, `/api/matches/{id}`, `/api/events`.

The live site runs as systemd user services behind a Cloudflare Tunnel on an Azure VM;
templates live under `deploy/` (review paths before enabling — they're examples).

## ⚙️ Configuration

Copy `.env.example` to `.env` and fill in what you need. All keys are optional — the system
runs offline on bundled history and free RSS.

| Variable | Purpose |
| --- | --- |
| `WC_DB_PATH` | Override the SQLite path (default `data/worldcup.db`). |
| `WC_DATA_DIR` | Override the data directory (default `./data`). |
| `FOOTBALL_DATA_TOKEN` | [football-data.org](https://www.football-data.org) key for `fetch-results` / `fetch-fixtures`. |
| `ODDS_API_KEY` | [The Odds API](https://the-odds-api.com) key (free 500 req/month) for `fetch-odds` and value betting. |
| `NEWSAPI_KEY` | Optional [NewsAPI](https://newsapi.org) key to supplement free RSS feeds. Not required. |

## 🛠️ Development

```bash
uv sync                 # install everything, including dev tools
uv run pytest           # 205 tests
uv run ruff check .     # lint
uv run ruff format .    # format
uv run mypy --strict src
```

Conventions: Python 3.12, 100-char lines, double quotes, strict typing. New features ship
with tests.

## ⚠️ Limitations & disclaimer

- **The model compresses margins.** It tends to over-rate underdog draws and handicaps relative
  to the sharp market — which is exactly why value bets are paper-traded first and judged on CLV,
  not on early wins.
- **The market is usually right.** Flagged edges are candidates to investigate; a large gap
  usually means a stale or bad line, not free money.
- **Results depend on an upstream data feed** (football-data.org / The Odds API) that can lag or
  rate-limit. Stale inputs mean stale predictions.
- **This is not financial advice.** No real money is wagered anywhere in this project, and you
  shouldn't treat its output as a betting tip. It's a forecasting and research toy — enjoy it as one.

## 📜 Data sources & licenses

- **Historical results:** [`martj42/international_results`](https://github.com/martj42/international_results) (`results.csv`, CC0 1.0).
- **World Cup results & fixtures:** football-data.org API v4 (competition `WC`) — check their terms before public/commercial use.
- **Bookmaker odds:** The Odds API (`soccer_fifa_world_cup`) — free tier 500 req/month; check their terms.
- **Off-pitch news:** publisher RSS feeds — licensing is publisher-specific; source URLs are stored and feed terms respected.

## License

[MIT](LICENSE).
