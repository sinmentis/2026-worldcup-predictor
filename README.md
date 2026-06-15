# worldcup-predictor

FIFA World Cup 2026 prediction system with a deterministic Python engine, SQLite persistence, an MCP server for LLM clients, and a FastAPI web UI.

## What it does

- Seeds the 48-team 2026 World Cup group stage fixture set.
- Loads historical men's international results.
- Fits a Dixon-Coles goal model through `penaltyblog` and runs Elo-style team ratings.
- Predicts 1X2 probabilities, expected goals, and likely scorelines for fixtures.
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

Useful CLI commands:

```bash
uv run worldcup fetch-results
uv run worldcup predict <match_id>
uv run worldcup simulate --n 50000 --seed 123
uv run worldcup evaluate
```

`load-history` can also load a local CSV:

```bash
uv run worldcup load-history --file path/to/results.csv
```

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

The MCP tools include group standings, upcoming matches, result recording, match prediction, structured intel recording, and tournament simulation.

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
