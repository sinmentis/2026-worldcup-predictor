# worldcup-predictor

FIFA World Cup 2026 prediction system with a deterministic Python engine, SQLite persistence, an MCP server for LLM clients, and a FastAPI web UI.

## What it does

- Seeds the 48-team 2026 World Cup group stage fixture set.
- Loads historical men's international results.
- Predicts 1X2 probabilities, expected goals, and likely scorelines by fitting a
  Dixon-Coles goal model through `penaltyblog`. (Elo ratings are computed and stored via
  `worldcup rate` as a standalone strength view and a future lambda prior; the Phase-1
  prediction pipeline uses Dixon-Coles directly, not Elo.)
- Applies source-linked off-pitch intelligence adjustments that are stored in SQLite.
- Runs Monte Carlo tournament simulations.
- Scores finished predictions against simple baselines.
- Exposes the engine through a Typer CLI, FastMCP stdio server, and FastAPI web UI.

The core engine is LLM-free. Cron or systemd should run deterministic jobs such as result fetching and simulations. GitHub Copilot CLI drives LLM-backed jobs such as reading news, turning it into structured `intel_event` records, and explaining predictions through the MCP tools.

## Architecture

- **Core engine:** deterministic Python library under `src/worldcup_predictor`. It handles ingest, SQLite schema, ratings, Dixon-Coles prediction, intel adjustments, simulations, and evaluation.
- **SQLite source of truth:** default database is `data/worldcup.db`. Override it with `WC_DB_PATH`; override the data directory with `WC_DATA_DIR`.
- **MCP adapter:** `worldcup-mcp` is a thin FastMCP stdio server over engine functions. It does not own prediction logic.
- **Web UI:** `worldcup-web` and `worldcup serve` start a FastAPI app with JSON endpoints, static UI assets, and server-sent events for update notifications. The UI reads from SQLite and does not compute model output itself.
- **Automation split:** cron/systemd runs no-LLM jobs. Copilot CLI runs LLM jobs through MCP when unstructured intelligence or natural-language analysis is needed.

Think of SQLite as the device registers, the core engine as the driver, MCP as the ioctl-style control interface, and Copilot CLI as the userspace process calling into it.

## Install

Requirements: Python 3.12 and `uv`.

```bash
cd /home/shunlyu/work/worldcup-predictor
uv sync
cp .env.example .env
```

Set `FOOTBALL_DATA_TOKEN` in `.env` if you want `fetch-results` to call football-data.org.

## Quickstart

Initialize the database, seed the 2026 fixtures, load historical results, and run a deterministic simulation:

```bash
uv run worldcup init-db
uv run worldcup seed
uv run worldcup load-history
uv run worldcup simulate --n 50000
```

Equivalent chained form:

```bash
uv run worldcup init-db && uv run worldcup seed && uv run worldcup load-history && uv run worldcup simulate --n 50000
```

Start the web UI:

```bash
uv run worldcup-web
```

or:

```bash
uv run worldcup serve --host 0.0.0.0 --port 8080
```

Then open `http://localhost:8080`.

The web UI has five tabs (Chinese): 即将开赛 (upcoming matches with our per-match prediction and
the active off-pitch factors), 夺冠预测 (title odds), 战绩对比 (our original prediction vs the
actual result, with a model-vs-baseline scoreboard), 小组积分 (group tables), and 淘汰赛 (bracket).
JSON endpoints: `/api/upcoming-predictions`, `/api/accuracy`, `/api/forecast`,
`/api/groups/{g}/standings`, `/api/knockout/bracket`, `/api/matches/{id}`.

Useful CLI commands:

```bash
uv run worldcup fetch-fixtures   # populate kickoff times (and results) from football-data.org
uv run worldcup fetch-results
uv run worldcup predict <match_id>
uv run worldcup simulate --n 50000 --seed 123
uv run worldcup evaluate
uv run worldcup backtest --fit-calibration   # walk-forward skill/calibration + fit the calibrator
```

### Model calibration and backtest

`worldcup backtest` runs a walk-forward, no-look-ahead evaluation: for each refit window the
goal model is trained only on matches before that window and used to predict it, yielding
out-of-sample predictions. It reports RPS/Brier/log-loss versus a flat baseline plus a
reliability curve and ECE (calibration error). With `--fit-calibration` it fits a small
parametric calibrator (draw multiplier + temperature) that minimises out-of-sample RPS and
stores it in `tuning_params`; `predict` then applies it to the 1X2 (identity until fitted).
Host nations (USA/Mexico/Canada) get a modest expected-goals bump (`config.HOST_ADVANTAGE`)
since World Cup matches strip out home advantage.

`load-history` can also load a local CSV:

```bash
uv run worldcup load-history --file path/to/results.csv
```

## Phase 2a — Off-pitch intelligence

Phase 2a adds source-linked player-status intelligence to the deterministic prediction engine. Cron can run `worldcup fetch-news` to store raw RSS articles in SQLite. In a Copilot CLI session, ask it to process the latest news through MCP. The MCP flow is `get_unprocessed_news`, `upsert_player_status`, then `mark_news_processed`.

The trust gate keeps weak intel out of predictions. A status becomes active only when confidence is high and it has either at least two sources or an official source. Single-source or lower-confidence items stay pending and have no model effect until reviewed.

Status multipliers are defined by `MAGNITUDE_TABLE`:

| Tier | `out` | `suspended` | `doubtful` |
| --- | ---: | ---: | ---: |
| `key` | 0.72 | 0.72 | 0.88 |
| `regular` | 0.85 | 0.85 | 0.93 |
| `fringe` | 0.96 | 0.96 | 0.98 |

Review pending items with:

```bash
uv run worldcup intel-pending
uv run worldcup intel-approve <ref>
uv run worldcup intel-reject <ref>
```

`intel-pending` prints a `ref` for each item: `ps:<id>` for a player status, `ts:<id>` for a
team signal. Pass that `ref` to approve/reject (a bare number is still treated as a player id).

Statuses expire at the team's next scheduled match when that date is known. If no kickoff is set, the default expiry is 14 days.

### Team-level signals (broadened intel)

Player availability is only one kind of off-pitch signal. The `team_signal` store captures
qualitative, between-the-lines team signals in both directions across five categories:
`tactical`, `morale`, `motivation`, `fatigue`, and `form`. The MCP tool is `upsert_team_signal`;
it records only forward-looking or beyond-the-result signals (not match recaps, which are
ingested as results). One signal is kept per `(team, category)`.

These signals are deliberately soft, and strengthen swings are capped smaller than weaken swings:

| Tier | `weaken` | `strengthen` |
| --- | ---: | ---: |
| `major` | 0.88 | 1.06 |
| `moderate` | 0.93 | 1.04 |
| `minor` | 0.97 | 1.02 |

They share the same trust gate, credibility rules, expiry, and pending review queue as player
statuses, and feed the same per-team delta in `apply_intel` (legacy events + player statuses +
team signals), bounded by the existing `ADJUST_CLAMP`.

## MCP server

The stdio MCP server exposes the engine through FastMCP tools:

```bash
uv --directory /home/shunlyu/work/worldcup-predictor run worldcup-mcp
```

VS Code can use the checked-in `.vscode/mcp.json`:

```json
{
  "servers": {
    "worldcup-predictor": {
      "command": "uv",
      "args": ["--directory", "/home/shunlyu/work/worldcup-predictor", "run", "worldcup-mcp"]
    }
  }
}
```

In GitHub Copilot CLI, add the same server command with `/mcp add` and point it at:

```bash
uv --directory /home/shunlyu/work/worldcup-predictor run worldcup-mcp
```

The MCP tools include group standings, upcoming matches, result recording, match prediction, structured intel recording (player statuses and team-level signals), pending-intel review, and tournament simulation.

## Deployment templates

Templates live under `deploy/`. They use the real checkout path `/home/shunlyu/work/worldcup-predictor` and the `.venv/bin/worldcup` entry points. They are examples only. Do not enable them until you have reviewed paths, environment, and log locations.

Cron example:

```bash
crontab deploy/crontab.example
```

Systemd example:

```bash
sudo cp deploy/worldcup-web.service /etc/systemd/system/
sudo cp deploy/worldcup-fetch.service /etc/systemd/system/
sudo cp deploy/worldcup-fetch.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now worldcup-web.service
sudo systemctl enable --now worldcup-fetch.timer
```

`worldcup-web.service` runs the web UI on port 8080. `worldcup-fetch.timer` triggers `worldcup-fetch.service` every 10 minutes with `OnUnitActiveSec=10min`. The repository does not enable any services automatically.

## Data sources and licenses

- **Historical international results:** `martj42/international_results` raw `results.csv`, licensed CC0 1.0 Universal. Used by `worldcup load-history` for bootstrap history.
- **Finished World Cup results:** football-data.org API v4, competition code `WC`. The free API needs `FOOTBALL_DATA_TOKEN` and is governed by football-data.org terms, including rate limits and attribution requirements. Check their current terms before public or commercial use.
- **Off-pitch news:** RSS feeds are intended for Phase 2 intelligence gathering. RSS licensing and reuse rights are publisher-specific. Store source URLs and respect each publisher's feed terms.

## Model backend note

MODEL_BACKEND=primary (`penaltyblog`).

`penaltyblog` installs, imports, and fits on this arm64 host with `penaltyblog==1.11.0`. Model code must pass writable goal arrays using `.to_numpy().copy()` for `home_goals` and `away_goals`. Calls to `dixon_coles_weights` must pass datetimes, for example with `pd.to_datetime(...)`.

## Roadmap

Phase 2:

- Automated LLM off-pitch intelligence from RSS/news into structured, source-cited `intel_event` records.
- Level-2 auto-tuning with walk-forward backtests and guardrails.
- Level-3 advisor that proposes model changes for human approval.

Phase 3:

- Value-bet helper for manually supplied odds.
- Richer UI and expected-goals, or xG, data where licensing allows.
