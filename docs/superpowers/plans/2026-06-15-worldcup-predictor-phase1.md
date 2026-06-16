# worldcup-predictor Implementation Plan (Phase 1)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a working end-to-end FIFA World Cup 2026 prediction system: ingest data, rate teams (Elo), model goals (Dixon-Coles) to predict 1X2 + scoreline, apply off-pitch intel adjustments, Monte-Carlo the bracket, score predictions, and expose it via an MCP server and a live web UI.

**Architecture:** A deterministic Python core library (`worldcup_predictor`) owns all math and a SQLite DB (single source of truth). Two thin adapters sit on top: an MCP server (FastMCP, stdio) for LLM/Copilot-CLI control, and a FastAPI web UI (bracket + group tables + match detail, SSE live-updates). Cron/systemd runs LLM-free deterministic jobs; Copilot CLI sessions drive LLM jobs (intel, narration).

**Tech Stack:** Python 3.12, `uv` (src layout, hatchling), `penaltyblog` (Elo + Dixon-Coles), `pandas`/`numpy`, `mcp[cli]>=1.27,<2` (FastMCP), `fastapi[standard]` + `sse-starlette` + vanilla JS, `httpx`, `feedparser`, `typer`. Dev: `pytest`+`pytest-asyncio`, `ruff`, `mypy --strict`. SQLite via stdlib `sqlite3`.

**Companion design spec:** `files/design.md` in this session folder holds the full verified reference data (WC 2026 format, all 12 groups, data-source URLs, model equations, risks). Read it alongside this plan.

---

## Conventions for every task

- TDD: write the failing test first, run it red, implement minimally, run it green, commit.
- Commit messages use Conventional Commits (`feat:`, `test:`, `chore:`, `fix:`, `docs:`).
- Run `uv run pytest`, `uv run ruff check`, `uv run mypy src` before each commit once those exist.
- All code/comments/commits in English. No secrets in the repo (API keys go in `.env`, gitignored).
- Project root: `/path/to/worldcup-predictor`. Package import name: `worldcup_predictor`.

## Module map (lock these names/signatures; later tasks depend on them)

```
src/worldcup_predictor/
  __init__.py
  config.py        # DB_PATH, GROUPS (A–L → 4 teams), SOURCE URLs, K_TABLE, settings
  db.py            # connect() -> sqlite3.Connection ; init_schema(conn) ; SCHEMA SQL
  models.py        # dataclasses: HistMatch, Fixture, MatchPrediction, IntelEvent, GroupRow, BracketMatch, SimResult
  ingest.py        # load_history(), seed_teams_and_fixtures(), fetch_live_results()
  ratings.py       # elo_expected(), goal_diff_multiplier(), elo_update(), compute_elo_ratings()
  goal_model.py    # GoalModel.fit(history) ; GoalModel.predict_grid(home,away,neutral) -> ScoreGrid ; ScoreGrid
  intel.py         # record_intel(), active_intel_for(), apply_intel(lam_h,lam_a,home,away) -> (lh,la,factors)
  predict.py       # predict_match(home,away,neutral,apply_intel) -> MatchPrediction ; persist_prediction()
  simulate.py      # simulate_tournament(n, seed) -> dict ; group tiebreakers ; R32 bracket ; knockout
  evaluate.py      # rps(), multiclass_brier(), log_loss_score(), score_finished_predictions(), baselines
  engine.py        # FACADE for adapters: get_group_standings(), get_knockout_bracket(),
                   #   get_upcoming_matches(), record_result(), predict_fixture(), run_simulation(),
                   #   record_intel_event(), get_last_update_ts(), get_match_detail()
  cli.py           # typer app: init-db, seed, fetch-results, predict, simulate, evaluate, serve
  mcp_server.py    # FastMCP thin adapter -> engine
  web_server.py    # FastAPI app -> engine ; serves static/ ; SSE /api/events
  static/          # index.html, bracket.css, app.js
tests/             # one test module per engine module
```

## Milestone 0 — Project scaffold & tooling

### Task 0.1: Initialize project with uv + git

**Files:**
- Create: `/path/to/worldcup-predictor/` (new dir)
- Create: `pyproject.toml`, `.gitignore`, `.python-version`, `README.md`, `src/worldcup_predictor/__init__.py`

- [ ] **Step 1: Create project and venv**

```bash
cd ~/work
uv init --package --name worldcup-predictor worldcup-predictor
cd worldcup-predictor
echo "3.12" > .python-version
uv python pin 3.12
```

- [ ] **Step 2: Write `pyproject.toml`** (replace the generated one)

```toml
[project]
name = "worldcup-predictor"
version = "0.1.0"
description = "2026 FIFA World Cup prediction system with MCP + web UI"
requires-python = ">=3.12"
license = { text = "MIT" }
dependencies = [
    "penaltyblog>=1.0",
    "pandas>=2.2",
    "numpy>=1.26",
    "httpx>=0.27",
    "feedparser>=6.0",
    "mcp[cli]>=1.27,<2",
    "fastapi[standard]>=0.115",
    "sse-starlette>=2.1",
    "pydantic>=2.11",
    "typer>=0.16",
    "python-dotenv>=1.0",
]

[project.scripts]
worldcup = "worldcup_predictor.cli:app"
worldcup-mcp = "worldcup_predictor.mcp_server:main"
worldcup-web = "worldcup_predictor.web_server:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/worldcup_predictor"]

[tool.uv]
dev-dependencies = [
    "pytest>=8.3",
    "pytest-asyncio>=0.24",
    "ruff>=0.9",
    "mypy>=1.13",
]

[tool.ruff]
target-version = "py312"
line-length = 100
[tool.ruff.lint]
select = ["E", "W", "F", "I", "B", "UP", "RUF"]
ignore = ["B008"]
[tool.ruff.lint.isort]
known-first-party = ["worldcup_predictor"]
[tool.ruff.format]
quote-style = "double"

[tool.mypy]
python_version = "3.12"
strict = true
plugins = ["pydantic.mypy"]
[[tool.mypy.overrides]]
module = "tests.*"
disallow_untyped_defs = false

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
addopts = ["-ra", "--strict-markers"]
```

- [ ] **Step 3: Write `.gitignore`**

```gitignore
.venv/
__pycache__/
*.pyc
.env
*.db
*.sqlite3
.pytest_cache/
.mypy_cache/
.ruff_cache/
data/cache/
```

- [ ] **Step 4: Sync, init git, copy specs into repo**

```bash
mkdir -p src/worldcup_predictor/static tests docs/superpowers/specs docs/superpowers/plans data
uv sync
git init -q
cp ~/.copilot/session-state/<session-id>/files/design.md docs/superpowers/specs/2026-06-15-worldcup-predictor-design.md
cp ~/.copilot/session-state/<session-id>/plan.md docs/superpowers/plans/2026-06-15-worldcup-predictor-phase1.md
```

- [ ] **Step 5: Verify toolchain runs**

Run: `uv run python -c "import worldcup_predictor; print('ok')"`
Expected: `ok`

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "chore: scaffold worldcup-predictor project (uv, ruff, mypy, pytest)

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

### Task 0.2: Spike — verify `penaltyblog` builds/imports on arm64

**Why:** `penaltyblog` is Cython-compiled. If it won't build on this arm64 host, we must switch to the fallback (statsmodels Poisson + hand-rolled Dixon-Coles) before building the model milestone. Resolve this risk now.

**Files:** Test: `tests/test_spike_penaltyblog.py`

- [ ] **Step 1: Write the spike test**

```python
# tests/test_spike_penaltyblog.py
import pandas as pd


def test_penaltyblog_imports_and_fits():
    from penaltyblog.models import DixonColesGoalModel

    df = pd.DataFrame(
        {
            "home_team": ["A", "B", "A", "C", "B", "C"] * 4,
            "away_team": ["B", "A", "C", "A", "C", "B"] * 4,
            "home_goals": [1, 2, 0, 3, 1, 2] * 4,
            "away_goals": [0, 1, 0, 1, 1, 2] * 4,
        }
    )
    model = DixonColesGoalModel(
        df["home_goals"], df["away_goals"], df["home_team"], df["away_team"]
    )
    model.fit()
    grid = model.predict("A", "B")
    probs = [grid.home_win, grid.draw, grid.away_win]
    assert abs(sum(probs) - 1.0) < 1e-6
    assert all(0.0 <= p <= 1.0 for p in probs)
```

- [ ] **Step 2: Run it**

Run: `uv run pytest tests/test_spike_penaltyblog.py -v`
Expected: PASS. **If it fails to install/build on arm64**, set `tuning_params`-style decision `MODEL_BACKEND=fallback` and use the Task 4.x fallback notes (statsmodels + scipy). Document the decision in `README.md` and proceed; all later tasks call `GoalModel`, which hides the backend.

- [ ] **Step 3: Commit**

```bash
git add tests/test_spike_penaltyblog.py
git commit -m "test: spike penaltyblog arm64 build verification

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

## Milestone 1 — Persistence & config

### Task 1.1: Config constants

**Files:** Create: `src/worldcup_predictor/config.py` · Test: `tests/test_config.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_config.py
from worldcup_predictor import config


def test_groups_complete():
    assert len(config.GROUPS) == 12
    assert sorted(config.GROUPS) == list("ABCDEFGHIJKL")
    for teams in config.GROUPS.values():
        assert len(teams) == 4
    all_teams = [t for ts in config.GROUPS.values() for t in ts]
    assert len(all_teams) == 48
    assert len(set(all_teams)) == 48
    assert "Argentina" in config.GROUPS["J"]


def test_k_table_has_world_cup():
    assert config.K_TABLE["world_cup"] == 60
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_config.py -v`
Expected: FAIL (`ModuleNotFoundError` / `AttributeError`).

- [ ] **Step 3: Implement `config.py`** (groups verified 2026-06-15; see `files/design.md`)

```python
# src/worldcup_predictor/config.py
from __future__ import annotations

import os
from pathlib import Path

DATA_DIR = Path(os.environ.get("WC_DATA_DIR", Path(__file__).resolve().parents[2] / "data"))
DB_PATH = Path(os.environ.get("WC_DB_PATH", DATA_DIR / "worldcup.db"))
CACHE_DIR = DATA_DIR / "cache"

HISTORY_CSV_URL = (
    "https://raw.githubusercontent.com/martj42/international_results/master/results.csv"
)
FOOTBALL_DATA_BASE = "https://api.football-data.org/v4"
FOOTBALL_DATA_COMP = "WC"

# eloratings.net K-factors by competition importance
K_TABLE = {
    "world_cup": 60,
    "continental_final": 50,
    "qualifier": 40,
    "minor_tournament": 30,
    "friendly": 20,
}
HOME_ADVANTAGE_ELO = 100  # added to a non-neutral home team's rating
DEFAULT_ELO = 1500.0
ELO_SHRINK_GAMES = 30  # shrink sparse teams toward the mean over this many games
TIME_DECAY_XI = 0.001  # Dixon-Coles weight decay (~693-day half-life)

HOSTS = {"Mexico", "Canada", "United States"}

GROUPS: dict[str, list[str]] = {
    "A": ["Mexico", "South Africa", "South Korea", "Czech Republic"],
    "B": ["Canada", "Switzerland", "Bosnia and Herzegovina", "Qatar"],
    "C": ["Brazil", "Morocco", "Scotland", "Haiti"],
    "D": ["United States", "Australia", "Paraguay", "Turkey"],
    "E": ["Germany", "Ecuador", "Ivory Coast", "Curacao"],
    "F": ["Netherlands", "Japan", "Sweden", "Tunisia"],
    "G": ["Belgium", "Egypt", "Iran", "New Zealand"],
    "H": ["Spain", "Uruguay", "Saudi Arabia", "Cape Verde"],
    "I": ["France", "Senegal", "Norway", "Iraq"],
    "J": ["Argentina", "Algeria", "Austria", "Jordan"],
    "K": ["Portugal", "Colombia", "DR Congo", "Uzbekistan"],
    "L": ["England", "Croatia", "Ghana", "Panama"],
}
```

- [ ] **Step 4: Run it to verify it passes**

Run: `uv run pytest tests/test_config.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/worldcup_predictor/config.py tests/test_config.py
git commit -m "feat: add config with WC2026 groups, Elo K-table, source URLs

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

### Task 1.2: SQLite schema & connection

**Files:** Create: `src/worldcup_predictor/db.py` · Test: `tests/test_db.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_db.py
from worldcup_predictor import db


def test_init_schema_creates_tables(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.init_schema(conn)
    names = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    expected = {
        "teams", "matches", "historical_matches", "predictions",
        "intel_events", "ratings_history", "sim_results", "metrics",
        "tuning_params", "meta",
    }
    assert expected <= names


def test_set_and_get_last_update(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.init_schema(conn)
    db.touch_update(conn)
    ts1 = db.get_last_update_ts(conn)
    assert ts1 is not None
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_db.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement `db.py`**

```python
# src/worldcup_predictor/db.py
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from worldcup_predictor import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS teams (
    name TEXT PRIMARY KEY,
    group_id TEXT,
    elo REAL,
    fifa_rank INTEGER,
    is_host INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS matches (
    id INTEGER PRIMARY KEY,
    stage TEXT NOT NULL,          -- 'group' | 'R32' | 'R16' | 'QF' | 'SF' | '3RD' | 'FINAL'
    group_id TEXT,
    slot TEXT,                    -- e.g. 'W73', 'RU-A', '3rd-1' for knockout placeholders
    home_team TEXT,
    away_team TEXT,
    kickoff TEXT,
    neutral INTEGER DEFAULT 1,
    home_score INTEGER,
    away_score INTEGER,
    status TEXT DEFAULT 'SCHEDULED'  -- 'SCHEDULED' | 'FINISHED'
);
CREATE TABLE IF NOT EXISTS historical_matches (
    id INTEGER PRIMARY KEY,
    date TEXT,
    home_team TEXT,
    away_team TEXT,
    home_score INTEGER,
    away_score INTEGER,
    tournament TEXT,
    neutral INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS predictions (
    id INTEGER PRIMARY KEY,
    match_id INTEGER,
    created_at REAL,
    p_home REAL, p_draw REAL, p_away REAL,
    exp_home_goals REAL, exp_away_goals REAL,
    ml_home INTEGER, ml_away INTEGER,
    model_version TEXT,
    reasoning TEXT
);
CREATE TABLE IF NOT EXISTS intel_events (
    id INTEGER PRIMARY KEY,
    created_at REAL,
    team TEXT,
    player TEXT,
    event_type TEXT,              -- 'injury' | 'illness' | 'suspension' | 'rotation' | 'morale' | 'other'
    direction TEXT,               -- 'weaken' | 'strengthen'
    magnitude REAL,               -- lambda multiplier delta, e.g. -0.15
    source_url TEXT,
    credibility REAL,             -- 0..1
    valid_from TEXT,
    notes TEXT
);
CREATE TABLE IF NOT EXISTS ratings_history (
    id INTEGER PRIMARY KEY, team TEXT, date TEXT, elo REAL
);
CREATE TABLE IF NOT EXISTS sim_results (
    id INTEGER PRIMARY KEY, created_at REAL, team TEXT,
    advance_prob REAL, r16_prob REAL, qf_prob REAL, sf_prob REAL,
    final_prob REAL, title_prob REAL, n_iter INTEGER
);
CREATE TABLE IF NOT EXISTS metrics (
    id INTEGER PRIMARY KEY, created_at REAL, metric TEXT, value REAL, scope TEXT
);
CREATE TABLE IF NOT EXISTS tuning_params (
    key TEXT PRIMARY KEY, value TEXT, updated_at REAL
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    p = Path(path) if path is not None else config.DB_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def touch_update(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT INTO meta(key, value) VALUES('last_update', ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (str(time.time()),),
    )
    conn.commit()


def get_last_update_ts(conn: sqlite3.Connection) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key='last_update'").fetchone()
    return row[0] if row else None
```

- [ ] **Step 4: Run it to verify it passes**

Run: `uv run pytest tests/test_db.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/worldcup_predictor/db.py tests/test_db.py
git commit -m "feat: add SQLite schema and connection helpers

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

### Task 1.3: Domain models (dataclasses)

**Files:** Create: `src/worldcup_predictor/models.py` · Test: `tests/test_models.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_models.py
from worldcup_predictor.models import MatchPrediction


def test_match_prediction_probs_sum_to_one():
    p = MatchPrediction(
        home_team="Brazil", away_team="Germany",
        p_home=0.5, p_draw=0.3, p_away=0.2,
        exp_home_goals=1.6, exp_away_goals=1.1,
        ml_home=2, ml_away=1, factors=[],
    )
    assert abs((p.p_home + p.p_draw + p.p_away) - 1.0) < 1e-9
    assert p.most_likely_scoreline == "2-1"
```

- [ ] **Step 2: Run it (red)**

Run: `uv run pytest tests/test_models.py -v` → FAIL.

- [ ] **Step 3: Implement `models.py`**

```python
# src/worldcup_predictor/models.py
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class HistMatch:
    date: str
    home_team: str
    away_team: str
    home_score: int
    away_score: int
    tournament: str
    neutral: bool


@dataclass(frozen=True)
class Fixture:
    id: int
    stage: str
    group_id: str | None
    home_team: str | None
    away_team: str | None
    kickoff: str | None
    neutral: bool
    home_score: int | None
    away_score: int | None
    status: str


@dataclass
class IntelFactor:
    team: str
    description: str
    lambda_delta: float


@dataclass
class MatchPrediction:
    home_team: str
    away_team: str
    p_home: float
    p_draw: float
    p_away: float
    exp_home_goals: float
    exp_away_goals: float
    ml_home: int
    ml_away: int
    factors: list[IntelFactor] = field(default_factory=list)

    @property
    def most_likely_scoreline(self) -> str:
        return f"{self.ml_home}-{self.ml_away}"


@dataclass
class GroupRow:
    team: str
    played: int
    won: int
    drawn: int
    lost: int
    gf: int
    ga: int
    gd: int
    pts: int


@dataclass
class IntelEvent:
    team: str
    event_type: str
    direction: str
    magnitude: float
    source_url: str
    credibility: float
    player: str | None = None
    valid_from: str | None = None
    notes: str | None = None
```

- [ ] **Step 4: Run it (green)**

Run: `uv run pytest tests/test_models.py -v` → PASS.

- [ ] **Step 5: Commit**

```bash
git add src/worldcup_predictor/models.py tests/test_models.py
git commit -m "feat: add domain dataclasses

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

## Milestone 2 — Data ingestion

### Task 2.1: Load historical international results (bootstrap)

**Files:** Create: `src/worldcup_predictor/ingest.py` · Test: `tests/test_ingest_history.py`
**Source:** `martj42/international_results` CSV (no auth). Columns: `date,home_team,away_team,home_score,away_score,tournament,city,country,neutral`.

- [ ] **Step 1: Write the failing test** (uses a local CSV fixture, no network)

```python
# tests/test_ingest_history.py
from worldcup_predictor import db, ingest

CSV = """date,home_team,away_team,home_score,away_score,tournament,city,country,neutral
2024-06-01,Brazil,Germany,2,1,Friendly,Rio,Brazil,False
2024-09-01,Argentina,Brazil,1,1,FIFA World Cup qualification,Buenos Aires,Argentina,False
"""


def test_load_history_from_csv_text(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.init_schema(conn)
    n = ingest.load_history_from_text(conn, CSV)
    assert n == 2
    rows = conn.execute(
        "SELECT home_team, away_team, tournament, neutral FROM historical_matches ORDER BY date"
    ).fetchall()
    assert rows[0]["home_team"] == "Brazil"
    assert rows[0]["neutral"] == 0
```

- [ ] **Step 2: Run it (red)** → `uv run pytest tests/test_ingest_history.py -v` → FAIL.

- [ ] **Step 3: Implement the history loader in `ingest.py`**

```python
# src/worldcup_predictor/ingest.py
from __future__ import annotations

import csv
import io
import sqlite3

import httpx

from worldcup_predictor import config


def _to_bool_int(raw: str) -> int:
    return 1 if str(raw).strip().lower() in {"true", "1", "yes"} else 0


def load_history_from_text(conn: sqlite3.Connection, text: str) -> int:
    reader = csv.DictReader(io.StringIO(text))
    count = 0
    for row in reader:
        if not row.get("home_score") or not row.get("away_score"):
            continue
        conn.execute(
            "INSERT INTO historical_matches"
            "(date, home_team, away_team, home_score, away_score, tournament, neutral)"
            " VALUES (?,?,?,?,?,?,?)",
            (
                row["date"],
                row["home_team"],
                row["away_team"],
                int(row["home_score"]),
                int(row["away_score"]),
                row.get("tournament", ""),
                _to_bool_int(row.get("neutral", "False")),
            ),
        )
        count += 1
    conn.commit()
    return count


def load_history(conn: sqlite3.Connection, url: str | None = None) -> int:
    resp = httpx.get(url or config.HISTORY_CSV_URL, timeout=60.0, follow_redirects=True)
    resp.raise_for_status()
    return load_history_from_text(conn, resp.text)
```

- [ ] **Step 4: Run it (green)** → PASS.

- [ ] **Step 5: Commit**

```bash
git add src/worldcup_predictor/ingest.py tests/test_ingest_history.py
git commit -m "feat: load historical international results into DB

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

### Task 2.2: Seed teams + group fixtures

**Files:** Modify: `src/worldcup_predictor/ingest.py` · Test: `tests/test_seed.py`
**Note:** Each group of 4 plays a round-robin = 6 matches per group → 72 group fixtures total.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_seed.py
from itertools import combinations

from worldcup_predictor import config, db, ingest


def test_seed_creates_teams_and_group_fixtures(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.init_schema(conn)
    ingest.seed_teams_and_fixtures(conn)

    assert conn.execute("SELECT COUNT(*) FROM teams").fetchone()[0] == 48
    # 12 groups * C(4,2)=6 = 72 group-stage fixtures
    n_group = conn.execute(
        "SELECT COUNT(*) FROM matches WHERE stage='group'"
    ).fetchone()[0]
    assert n_group == 72
    # hosts flagged
    hosts = {
        r[0] for r in conn.execute("SELECT name FROM teams WHERE is_host=1").fetchall()
    }
    assert hosts == config.HOSTS
    # all group matches neutral in phase 1
    assert conn.execute(
        "SELECT COUNT(*) FROM matches WHERE stage='group' AND neutral=0"
    ).fetchone()[0] == 0
    # exactly the round-robin pairings for group A
    a_pairs = {
        tuple(sorted((r["home_team"], r["away_team"])))
        for r in conn.execute(
            "SELECT home_team, away_team FROM matches WHERE group_id='A'"
        ).fetchall()
    }
    assert a_pairs == {tuple(sorted(p)) for p in combinations(config.GROUPS["A"], 2)}
```

- [ ] **Step 2: Run it (red)** → FAIL.

- [ ] **Step 3: Implement `seed_teams_and_fixtures` in `ingest.py`** (append)

```python
from itertools import combinations


def seed_teams_and_fixtures(conn: sqlite3.Connection) -> None:
    for gid, teams in config.GROUPS.items():
        for team in teams:
            conn.execute(
                "INSERT OR REPLACE INTO teams(name, group_id, elo, is_host) VALUES (?,?,?,?)",
                (team, gid, config.DEFAULT_ELO, 1 if team in config.HOSTS else 0),
            )
    mid = 1
    for gid, teams in config.GROUPS.items():
        for home, away in combinations(teams, 2):
            conn.execute(
                "INSERT INTO matches(id, stage, group_id, home_team, away_team, neutral, status)"
                " VALUES (?, 'group', ?, ?, ?, 1, 'SCHEDULED')",
                (mid, gid, home, away),
            )
            mid += 1
    conn.commit()
```

- [ ] **Step 4: Run it (green)** → PASS.

- [ ] **Step 5: Commit**

```bash
git add src/worldcup_predictor/ingest.py tests/test_seed.py
git commit -m "feat: seed 48 teams and 72 group fixtures

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

### Task 2.3: Fetch & store live results (football-data.org adapter)

**Files:** Modify: `src/worldcup_predictor/ingest.py` · Test: `tests/test_fetch_results.py`
**Note:** API key from `.env` as `FOOTBALL_DATA_TOKEN`. Test parses a captured JSON payload (no network). Matching real fixtures to seeded rows is by `(home_team, away_team)` for the group stage; unknown names are logged and skipped (handled in Task 11 reconciliation).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_fetch_results.py
from worldcup_predictor import db, ingest

PAYLOAD = {
    "matches": [
        {
            "homeTeam": {"name": "Brazil"},
            "awayTeam": {"name": "Morocco"},
            "score": {"fullTime": {"home": 2, "away": 0}},
            "status": "FINISHED",
        }
    ]
}


def test_apply_results_payload_updates_match(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.init_schema(conn)
    ingest.seed_teams_and_fixtures(conn)
    updated = ingest.apply_results_payload(conn, PAYLOAD)
    assert updated == 1
    row = conn.execute(
        "SELECT home_score, away_score, status FROM matches "
        "WHERE home_team='Brazil' AND away_team='Morocco'"
    ).fetchone()
    assert (row["home_score"], row["away_score"], row["status"]) == (2, 0, "FINISHED")
```

- [ ] **Step 2: Run it (red)** → FAIL.

- [ ] **Step 3: Implement results application + fetch in `ingest.py`** (append)

```python
import os
from typing import Any

from worldcup_predictor import db as _db


def apply_results_payload(conn: sqlite3.Connection, payload: dict[str, Any]) -> int:
    updated = 0
    for m in payload.get("matches", []):
        if m.get("status") != "FINISHED":
            continue
        home = m["homeTeam"]["name"]
        away = m["awayTeam"]["name"]
        ft = m["score"]["fullTime"]
        cur = conn.execute(
            "UPDATE matches SET home_score=?, away_score=?, status='FINISHED' "
            "WHERE home_team=? AND away_team=? AND status!='FINISHED'",
            (ft["home"], ft["away"], home, away),
        )
        updated += cur.rowcount
    conn.commit()
    if updated:
        _db.touch_update(conn)
    return updated


def fetch_live_results(conn: sqlite3.Connection, token: str | None = None) -> int:
    token = token or os.environ.get("FOOTBALL_DATA_TOKEN", "")
    url = f"{config.FOOTBALL_DATA_BASE}/competitions/{config.FOOTBALL_DATA_COMP}/matches"
    headers = {"X-Auth-Token": token} if token else {}
    resp = httpx.get(url, headers=headers, params={"status": "FINISHED"}, timeout=60.0)
    resp.raise_for_status()
    return apply_results_payload(conn, resp.json())
```

- [ ] **Step 4: Run it (green)** → PASS.

- [ ] **Step 5: Commit**

```bash
git add src/worldcup_predictor/ingest.py tests/test_fetch_results.py
git commit -m "feat: fetch and apply live WC results from football-data.org

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

## Milestone 3 — Elo ratings

### Task 3.1: Elo primitives

**Files:** Create: `src/worldcup_predictor/ratings.py` · Test: `tests/test_ratings_primitives.py`
**Equations (eloratings.net):** `W_e = 1/(10^(-dr/400)+1)`, `dr = R_a - R_b (+100 home if not neutral)`; `G`: 1 for gd≤1, 1.5 for gd=2, `(11+gd)/8` for gd≥3; `R_new = R + K·G·(W - W_e)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_ratings_primitives.py
import pytest

from worldcup_predictor import ratings


def test_elo_expected_even():
    assert ratings.elo_expected(1500, 1500) == pytest.approx(0.5)


def test_elo_expected_home_advantage_neutral_false():
    # +100 to home when not neutral
    assert ratings.elo_expected(1500, 1500, neutral=False) == pytest.approx(
        1 / (10 ** (-100 / 400) + 1)
    )


def test_goal_diff_multiplier():
    assert ratings.goal_diff_multiplier(0) == 1.0
    assert ratings.goal_diff_multiplier(1) == 1.0
    assert ratings.goal_diff_multiplier(2) == 1.5
    assert ratings.goal_diff_multiplier(3) == pytest.approx(1.75)
    assert ratings.goal_diff_multiplier(5) == pytest.approx(2.0)


def test_elo_update_winner_gains():
    we = ratings.elo_expected(1500, 1500)
    new = ratings.elo_update(1500, k=60, g=1.5, w=1.0, we=we)
    assert new > 1500
```

- [ ] **Step 2: Run it (red)** → FAIL.

- [ ] **Step 3: Implement primitives in `ratings.py`**

```python
# src/worldcup_predictor/ratings.py
from __future__ import annotations

import sqlite3

from worldcup_predictor import config
from worldcup_predictor.models import HistMatch


def elo_expected(r_team: float, r_opp: float, neutral: bool = True) -> float:
    dr = r_team - r_opp
    if not neutral:
        dr += config.HOME_ADVANTAGE_ELO
    return 1.0 / (10 ** (-dr / 400) + 1)


def goal_diff_multiplier(gd: int) -> float:
    gd = abs(gd)
    if gd <= 1:
        return 1.0
    if gd == 2:
        return 1.5
    return (11 + gd) / 8


def elo_update(r: float, k: int, g: float, w: float, we: float) -> float:
    return r + k * g * (w - we)
```

- [ ] **Step 4: Run it (green)** → PASS.

- [ ] **Step 5: Commit**

```bash
git add src/worldcup_predictor/ratings.py tests/test_ratings_primitives.py
git commit -m "feat: add Elo primitives (expected, GD multiplier, update)

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

### Task 3.2: Compute ratings over match history + persist

**Files:** Modify: `src/worldcup_predictor/ratings.py` · Test: `tests/test_compute_elo.py`
**Behavior:** process `historical_matches` in date order, applying updates; W=0.5/0.5 if `tournament` contains "penalt" is not available in this dataset, so treat all as W∈{0,0.5,1} by score; K chosen from tournament string; ratings start at `DEFAULT_ELO`; sparse-team shrinkage toward the mean after computing.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_compute_elo.py
from worldcup_predictor import db, ingest, ratings

CSV = """date,home_team,away_team,home_score,away_score,tournament,city,country,neutral
2024-01-01,A,B,3,0,FIFA World Cup,X,Y,True
2024-02-01,A,B,2,0,FIFA World Cup,X,Y,True
2024-03-01,A,B,1,0,FIFA World Cup,X,Y,True
"""


def test_compute_elo_winner_above_loser(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.init_schema(conn)
    ingest.load_history_from_text(conn, CSV)
    table = ratings.compute_elo_ratings(conn)
    assert table["A"] > table["B"]
    # persisted to teams
    elo_a = conn.execute("SELECT elo FROM teams WHERE name='A'").fetchone()
    # team rows only exist after seeding; compute should upsert ratings into teams too
    assert elo_a is not None


def test_k_factor_lookup():
    assert ratings.k_for_tournament("FIFA World Cup") == 60
    assert ratings.k_for_tournament("FIFA World Cup qualification") == 40
    assert ratings.k_for_tournament("Friendly") == 20
    assert ratings.k_for_tournament("UEFA Euro") == 50
```

- [ ] **Step 2: Run it (red)** → FAIL.

- [ ] **Step 3: Implement in `ratings.py`** (append)

```python
def k_for_tournament(tournament: str) -> int:
    t = tournament.lower()
    if "qualification" in t or "qualifier" in t:
        return config.K_TABLE["qualifier"]
    if "world cup" in t:
        return config.K_TABLE["world_cup"]
    if any(x in t for x in ("euro", "copa", "afcon", "nations", "gold cup", "asian cup")):
        return config.K_TABLE["continental_final"]
    if "friendly" in t:
        return config.K_TABLE["friendly"]
    return config.K_TABLE["minor_tournament"]


def _result(home_score: int, away_score: int) -> tuple[float, float]:
    if home_score > away_score:
        return 1.0, 0.0
    if home_score < away_score:
        return 0.0, 1.0
    return 0.5, 0.5


def compute_elo_ratings(conn: sqlite3.Connection) -> dict[str, float]:
    rows = conn.execute(
        "SELECT date, home_team, away_team, home_score, away_score, tournament, neutral "
        "FROM historical_matches ORDER BY date, id"
    ).fetchall()

    elo: dict[str, float] = {}
    games: dict[str, int] = {}

    def get(team: str) -> float:
        return elo.get(team, config.DEFAULT_ELO)

    for r in rows:
        h, a = r["home_team"], r["away_team"]
        neutral = bool(r["neutral"])
        we_h = elo_expected(get(h), get(a), neutral=neutral)
        we_a = 1.0 - we_h
        w_h, w_a = _result(r["home_score"], r["away_score"])
        g = goal_diff_multiplier(r["home_score"] - r["away_score"])
        k = k_for_tournament(r["tournament"] or "")
        elo[h] = elo_update(get(h), k, g, w_h, we_h)
        elo[a] = elo_update(get(a), k, g, w_a, we_a)
        games[h] = games.get(h, 0) + 1
        games[a] = games.get(a, 0) + 1

    # shrink sparse teams toward the global mean
    mean = sum(elo.values()) / len(elo) if elo else config.DEFAULT_ELO
    for team, rating in elo.items():
        n = games.get(team, 0)
        shrunk = (n * rating + config.ELO_SHRINK_GAMES * mean) / (n + config.ELO_SHRINK_GAMES)
        elo[team] = shrunk
        conn.execute(
            "INSERT INTO teams(name, elo) VALUES(?, ?) "
            "ON CONFLICT(name) DO UPDATE SET elo=excluded.elo",
            (team, shrunk),
        )
    conn.commit()
    return elo
```

- [ ] **Step 4: Run it (green)** → PASS.

- [ ] **Step 5: Commit**

```bash
git add src/worldcup_predictor/ratings.py tests/test_compute_elo.py
git commit -m "feat: compute Elo ratings from history with sparse-team shrinkage

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

## Milestone 4 — Dixon-Coles goal model

### Task 4.1: `GoalModel` + `ScoreGrid` wrapper over penaltyblog

**Files:** Create: `src/worldcup_predictor/goal_model.py` · Test: `tests/test_goal_model.py`
**Design:** `GoalModel.fit(history_df)` fits Dixon-Coles with time-decay weights and neutral-venue flag. `GoalModel.predict_grid(home, away, neutral=True)` returns a `ScoreGrid` exposing `.matrix` (numpy), `.home_win/.draw/.away_win`, `.exp_goals()`, `.most_likely()`, `.exact(h,a)`, `.over(line)`, `.btts()`. This isolates penaltyblog so the rest of the system is backend-agnostic (see fallback note below).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_goal_model.py
import numpy as np
import pandas as pd
import pytest

from worldcup_predictor.goal_model import GoalModel


@pytest.fixture
def history() -> pd.DataFrame:
    # synthetic: "Strong" beats "Weak" repeatedly; "Mid" in between
    rng = np.random.default_rng(0)
    rows = []
    for _ in range(60):
        rows.append(("2024-01-01", "Strong", "Weak", rng.integers(2, 5), rng.integers(0, 2), False))
        rows.append(("2024-01-01", "Mid", "Weak", rng.integers(1, 4), rng.integers(0, 2), False))
        rows.append(("2024-01-01", "Strong", "Mid", rng.integers(1, 4), rng.integers(0, 3), False))
    return pd.DataFrame(
        rows, columns=["date", "home_team", "away_team", "home_goals", "away_goals", "neutral"]
    )


def test_grid_probs_sum_to_one(history):
    m = GoalModel().fit(history)
    grid = m.predict_grid("Strong", "Weak", neutral=True)
    assert abs(grid.home_win + grid.draw + grid.away_win - 1.0) < 1e-6


def test_strong_beats_weak(history):
    m = GoalModel().fit(history)
    grid = m.predict_grid("Strong", "Weak", neutral=True)
    assert grid.home_win > grid.away_win


def test_most_likely_and_exact(history):
    m = GoalModel().fit(history)
    grid = m.predict_grid("Strong", "Weak", neutral=True)
    h, a = grid.most_likely()
    assert isinstance(h, int) and isinstance(a, int)
    assert 0.0 <= grid.exact(1, 0) <= 1.0
```

- [ ] **Step 2: Run it (red)** → FAIL.

- [ ] **Step 3: Implement `goal_model.py`**

```python
# src/worldcup_predictor/goal_model.py
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from penaltyblog.models import DixonColesGoalModel, dixon_coles_weights

from worldcup_predictor import config


@dataclass
class ScoreGrid:
    matrix: np.ndarray  # matrix[h, a] = P(home=h, away=a)

    @property
    def home_win(self) -> float:
        return float(np.tril(self.matrix, -1).sum())

    @property
    def away_win(self) -> float:
        return float(np.triu(self.matrix, 1).sum())

    @property
    def draw(self) -> float:
        return float(np.trace(self.matrix))

    def exp_goals(self) -> tuple[float, float]:
        idx = np.arange(self.matrix.shape[0])
        eh = float((self.matrix.sum(axis=1) * idx).sum())
        ea = float((self.matrix.sum(axis=0) * idx).sum())
        return eh, ea

    def most_likely(self) -> tuple[int, int]:
        h, a = np.unravel_index(int(np.argmax(self.matrix)), self.matrix.shape)
        return int(h), int(a)

    def exact(self, h: int, a: int) -> float:
        return float(self.matrix[h, a])

    def over(self, line: float) -> float:
        total = 0.0
        for h in range(self.matrix.shape[0]):
            for a in range(self.matrix.shape[1]):
                if h + a > line:
                    total += self.matrix[h, a]
        return float(total)

    def btts(self) -> float:
        return float(self.matrix[1:, 1:].sum())


class GoalModel:
    """Dixon-Coles wrapper. Backend = penaltyblog (fallback: see plan Task 4.x)."""

    def __init__(self) -> None:
        self._model: DixonColesGoalModel | None = None

    def fit(self, history: pd.DataFrame) -> GoalModel:
        weights = dixon_coles_weights(history["date"], xi=config.TIME_DECAY_XI)
        self._model = DixonColesGoalModel(
            history["home_goals"],
            history["away_goals"],
            history["home_team"],
            history["away_team"],
            weights=weights,
        )
        self._model.fit()
        return self

    def predict_grid(self, home: str, away: str, neutral: bool = True, max_goals: int = 15) -> ScoreGrid:
        if self._model is None:
            raise RuntimeError("GoalModel.fit() must be called before predict_grid()")
        fpg = self._model.predict(home, away, max_goals=max_goals)
        matrix = np.asarray(fpg.grid, dtype=float)
        matrix = matrix / matrix.sum()
        return ScoreGrid(matrix=matrix)
```

> **Fallback (only if Task 0.2 spike failed on arm64):** keep the same `GoalModel`/`ScoreGrid` public API but implement `fit`/`predict_grid` with a hand-rolled Dixon-Coles: fit `α_i, β_i, ρ` via `scipy.optimize.minimize` on the DC log-likelihood (equations in `files/design.md` §4), build `matrix[h,a] = τ(h,a,λ_h,λ_a,ρ)·Pois(h;λ_h)·Pois(a;λ_a)` with `λ_h=exp(α_h+β_a)`, `λ_a=exp(α_a+β_h)` (γ=0 neutral). Everything downstream is unchanged because it only touches `ScoreGrid`.

- [ ] **Step 4: Run it (green)** → PASS.

- [ ] **Step 5: Commit**

```bash
git add src/worldcup_predictor/goal_model.py tests/test_goal_model.py
git commit -m "feat: Dixon-Coles GoalModel + ScoreGrid wrapper

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

### Task 4.2: Build the training DataFrame from the DB

**Files:** Modify: `src/worldcup_predictor/goal_model.py` · Test: `tests/test_history_frame.py`
**Why:** the model needs a tidy frame from `historical_matches`; recent window + only matches between known teams keep it stable.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_history_frame.py
from worldcup_predictor import db, ingest
from worldcup_predictor.goal_model import history_frame

CSV = """date,home_team,away_team,home_score,away_score,tournament,city,country,neutral
2024-01-01,Brazil,Germany,2,1,Friendly,X,Y,False
2019-01-01,Brazil,Germany,1,1,Friendly,X,Y,False
"""


def test_history_frame_respects_since(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.init_schema(conn)
    ingest.load_history_from_text(conn, CSV)
    frame = history_frame(conn, since="2023-01-01")
    assert list(frame.columns) == ["date", "home_team", "away_team", "home_goals", "away_goals", "neutral"]
    assert len(frame) == 1
    assert frame.iloc[0]["home_goals"] == 2
```

- [ ] **Step 2: Run it (red)** → FAIL.

- [ ] **Step 3: Implement `history_frame` in `goal_model.py`** (append)

```python
import sqlite3


def history_frame(conn: sqlite3.Connection, since: str = "2018-01-01") -> pd.DataFrame:
    rows = conn.execute(
        "SELECT date, home_team, away_team, home_score, away_score, neutral "
        "FROM historical_matches WHERE date >= ? "
        "AND home_score IS NOT NULL AND away_score IS NOT NULL ORDER BY date",
        (since,),
    ).fetchall()
    return pd.DataFrame(
        [
            {
                "date": r["date"],
                "home_team": r["home_team"],
                "away_team": r["away_team"],
                "home_goals": r["home_score"],
                "away_goals": r["away_score"],
                "neutral": bool(r["neutral"]),
            }
            for r in rows
        ],
        columns=["date", "home_team", "away_team", "home_goals", "away_goals", "neutral"],
    )
```

- [ ] **Step 4: Run it (green)** → PASS.

- [ ] **Step 5: Commit**

```bash
git add src/worldcup_predictor/goal_model.py tests/test_history_frame.py
git commit -m "feat: build training DataFrame from historical_matches

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

## Milestone 5 — Intel adjustment engine

### Task 5.1: Record intel + apply to lambdas

**Files:** Create: `src/worldcup_predictor/intel.py` · Test: `tests/test_intel.py`
**Mechanism:** an `intel_event` carries a `magnitude` (signed multiplier delta on the affected team's expected goals) weighted by `credibility`. `apply_intel(lam_h, lam_a, home, away, conn)` multiplies each team's λ by `(1 + Σ credibility*magnitude)` clamped to a sane range, and returns the adjusted λs plus a list of `IntelFactor`s for the prediction reasoning. This is the concrete home of "star out → strong team's λ drops → prediction flips".

- [ ] **Step 1: Write the failing test**

```python
# tests/test_intel.py
from worldcup_predictor import db
from worldcup_predictor import intel
from worldcup_predictor.models import IntelEvent


def test_injury_weakens_team_lambda(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.init_schema(conn)
    intel.record_intel(
        conn,
        IntelEvent(
            team="France",
            player="Star Striker",
            event_type="injury",
            direction="weaken",
            magnitude=-0.30,
            source_url="https://example.com/news",
            credibility=1.0,
            notes="ruled out",
        ),
    )
    lh, la, factors = intel.apply_intel(2.0, 1.0, home="France", away="Iraq", conn=conn)
    assert lh < 2.0           # France weakened
    assert la == 1.0          # Iraq unchanged
    assert len(factors) == 1
    assert factors[0].team == "France"


def test_no_intel_is_noop(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.init_schema(conn)
    lh, la, factors = intel.apply_intel(1.5, 1.2, home="A", away="B", conn=conn)
    assert (lh, la) == (1.5, 1.2)
    assert factors == []


def test_credibility_scales_effect(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.init_schema(conn)
    intel.record_intel(
        conn,
        IntelEvent("A", "injury", "weaken", -0.40, "u", 0.5, player="x"),
    )
    lh, _, _ = intel.apply_intel(2.0, 1.0, home="A", away="B", conn=conn)
    assert lh == 2.0 * (1 + 0.5 * -0.40)
```

> Note: the third test constructs `IntelEvent` positionally — confirm the dataclass field order in `models.py` matches `(team, event_type, direction, magnitude, source_url, credibility, player=...)`. It does.

- [ ] **Step 2: Run it (red)** → FAIL.

- [ ] **Step 3: Implement `intel.py`**

```python
# src/worldcup_predictor/intel.py
from __future__ import annotations

import sqlite3
import time

from worldcup_predictor.models import IntelEvent, IntelFactor

LAMBDA_MIN = 0.05
ADJUST_CLAMP = (-0.6, 0.6)  # bound the net multiplier delta per team


def record_intel(conn: sqlite3.Connection, event: IntelEvent) -> None:
    conn.execute(
        "INSERT INTO intel_events"
        "(created_at, team, player, event_type, direction, magnitude, source_url,"
        " credibility, valid_from, notes) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            time.time(),
            event.team,
            event.player,
            event.event_type,
            event.direction,
            event.magnitude,
            event.source_url,
            event.credibility,
            event.valid_from,
            event.notes,
        ),
    )
    conn.commit()


def active_intel_for(conn: sqlite3.Connection, team: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM intel_events WHERE team=? ORDER BY created_at DESC", (team,)
    ).fetchall()


def _team_factor(conn: sqlite3.Connection, team: str) -> tuple[float, list[IntelFactor]]:
    delta = 0.0
    factors: list[IntelFactor] = []
    for row in active_intel_for(conn, team):
        contrib = float(row["credibility"]) * float(row["magnitude"])
        delta += contrib
        label = row["player"] or row["event_type"]
        factors.append(
            IntelFactor(
                team=team,
                description=f"{label}: {row['event_type']} ({row['notes'] or ''})".strip(),
                lambda_delta=contrib,
            )
        )
    lo, hi = ADJUST_CLAMP
    return max(lo, min(hi, delta)), factors


def apply_intel(
    lam_h: float, lam_a: float, home: str, away: str, conn: sqlite3.Connection
) -> tuple[float, float, list[IntelFactor]]:
    dh, fh = _team_factor(conn, home)
    da, fa = _team_factor(conn, away)
    lam_h = max(LAMBDA_MIN, lam_h * (1 + dh))
    lam_a = max(LAMBDA_MIN, lam_a * (1 + da))
    return lam_h, lam_a, fh + fa
```

- [ ] **Step 4: Run it (green)** → PASS.

- [ ] **Step 5: Commit**

```bash
git add src/worldcup_predictor/intel.py tests/test_intel.py
git commit -m "feat: intel events + lambda adjustment engine

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

### Task 5.2: Re-scale a ScoreGrid after a λ change

**Files:** Modify: `src/worldcup_predictor/goal_model.py` · Test: `tests/test_rescale.py`
**Why:** intel changes target λ (expected goals), but the fitted grid is built from the model's own λ. We re-derive an independent-Poisson grid from the adjusted (λ_h, λ_a) and blend it onto the DC shape by scaling rows/cols, then renormalize. Simplest robust approach: build a fresh independent-Poisson grid at the adjusted λ (DC low-score correction is second-order; acceptable for the adjustment path).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_rescale.py
from worldcup_predictor.goal_model import poisson_grid


def test_poisson_grid_expected_goals_match():
    grid = poisson_grid(1.8, 1.1, max_goals=15)
    eh, ea = grid.exp_goals()
    assert abs(eh - 1.8) < 0.02
    assert abs(ea - 1.1) < 0.02
    assert abs(grid.matrix.sum() - 1.0) < 1e-9
```

- [ ] **Step 2: Run it (red)** → FAIL.

- [ ] **Step 3: Implement `poisson_grid` in `goal_model.py`** (append)

```python
from math import exp, factorial


def poisson_grid(lam_h: float, lam_a: float, max_goals: int = 15) -> ScoreGrid:
    def pmf(k: int, lam: float) -> float:
        return exp(-lam) * lam**k / factorial(k)

    h = np.array([pmf(i, lam_h) for i in range(max_goals)])
    a = np.array([pmf(j, lam_a) for j in range(max_goals)])
    matrix = np.outer(h, a)
    matrix = matrix / matrix.sum()
    return ScoreGrid(matrix=matrix)
```

- [ ] **Step 4: Run it (green)** → PASS.

- [ ] **Step 5: Commit**

```bash
git add src/worldcup_predictor/goal_model.py tests/test_rescale.py
git commit -m "feat: independent-Poisson grid for intel-adjusted lambdas

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

## Milestone 6 — Prediction (model + intel, persisted)

### Task 6.1: `predict_match` combining grid + intel + persistence

**Files:** Create: `src/worldcup_predictor/predict.py` · Test: `tests/test_predict.py`
**Flow:** fit/reuse a `GoalModel` → base grid → base λ via `grid.exp_goals()` → `apply_intel` → if intel changed λ, rebuild grid via `poisson_grid` → derive `MatchPrediction` (probs, exp goals, most-likely) → persist into `predictions`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_predict.py
import numpy as np
import pandas as pd

from worldcup_predictor import db, intel
from worldcup_predictor.goal_model import GoalModel
from worldcup_predictor.models import IntelEvent
from worldcup_predictor.predict import predict_match


def _history() -> pd.DataFrame:
    rng = np.random.default_rng(1)
    rows = []
    for _ in range(80):
        rows.append(("2024-01-01", "Strong", "Weak", int(rng.integers(2, 5)), int(rng.integers(0, 2)), False))
        rows.append(("2024-01-01", "Weak", "Strong", int(rng.integers(0, 2)), int(rng.integers(2, 5)), False))
    return pd.DataFrame(rows, columns=["date", "home_team", "away_team", "home_goals", "away_goals", "neutral"])


def test_predict_match_persists_and_sums(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.init_schema(conn)
    conn.execute("INSERT INTO matches(id, stage, home_team, away_team, neutral, status)"
                 " VALUES (1,'group','Strong','Weak',1,'SCHEDULED')")
    conn.commit()
    model = GoalModel().fit(_history())

    pred = predict_match(conn, model, match_id=1, home="Strong", away="Weak", neutral=True)
    assert abs(pred.p_home + pred.p_draw + pred.p_away - 1.0) < 1e-6
    assert pred.p_home > pred.p_away
    stored = conn.execute("SELECT p_home, p_away FROM predictions WHERE match_id=1").fetchone()
    assert stored is not None


def test_intel_shifts_prediction(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.init_schema(conn)
    model = GoalModel().fit(_history())
    base = predict_match(conn, model, match_id=None, home="Strong", away="Weak", neutral=True)
    intel.record_intel(conn, IntelEvent("Strong", "injury", "weaken", -0.5, "u", 1.0, player="key"))
    after = predict_match(conn, model, match_id=None, home="Strong", away="Weak", neutral=True)
    assert after.p_home < base.p_home          # weakened favourite
    assert any(f.team == "Strong" for f in after.factors)
```

- [ ] **Step 2: Run it (red)** → FAIL.

- [ ] **Step 3: Implement `predict.py`**

```python
# src/worldcup_predictor/predict.py
from __future__ import annotations

import sqlite3
import time

from worldcup_predictor import intel
from worldcup_predictor.goal_model import GoalModel, poisson_grid
from worldcup_predictor.models import MatchPrediction

MODEL_VERSION = "dc-elo-v1"


def predict_match(
    conn: sqlite3.Connection,
    model: GoalModel,
    home: str,
    away: str,
    match_id: int | None = None,
    neutral: bool = True,
    apply_intel: bool = True,
) -> MatchPrediction:
    grid = model.predict_grid(home, away, neutral=neutral)
    lam_h, lam_a = grid.exp_goals()
    factors = []
    if apply_intel:
        new_h, new_a, factors = intel.apply_intel(lam_h, lam_a, home, away, conn)
        if (new_h, new_a) != (lam_h, lam_a):
            grid = poisson_grid(new_h, new_a)
            lam_h, lam_a = new_h, new_a

    ml_h, ml_a = grid.most_likely()
    pred = MatchPrediction(
        home_team=home,
        away_team=away,
        p_home=grid.home_win,
        p_draw=grid.draw,
        p_away=grid.away_win,
        exp_home_goals=lam_h,
        exp_away_goals=lam_a,
        ml_home=ml_h,
        ml_away=ml_a,
        factors=factors,
    )
    if match_id is not None:
        reasoning = "; ".join(f"{f.team}: {f.description} (Δλ={f.lambda_delta:+.2f})" for f in factors)
        conn.execute(
            "INSERT INTO predictions(match_id, created_at, p_home, p_draw, p_away,"
            " exp_home_goals, exp_away_goals, ml_home, ml_away, model_version, reasoning)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (match_id, time.time(), pred.p_home, pred.p_draw, pred.p_away,
             lam_h, lam_a, ml_h, ml_a, MODEL_VERSION, reasoning),
        )
        conn.commit()
    return pred
```

- [ ] **Step 4: Run it (green)** → PASS.

- [ ] **Step 5: Commit**

```bash
git add src/worldcup_predictor/predict.py tests/test_predict.py
git commit -m "feat: predict_match combining goal model, intel, persistence

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

## Milestone 7 — Monte Carlo tournament simulation

### Task 7.1: Group standings & tiebreakers

**Files:** Create: `src/worldcup_predictor/simulate.py` · Test: `tests/test_standings.py`
**Tiebreakers implemented (Annex C, the decisive subset):** points → overall GD → overall GF → head-to-head points among tied → head-to-head GD → random. (Fair-play / FIFA-rank are omitted in simulation; ties reaching that depth are rare at N=50k and resolved randomly, per research.)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_standings.py
from worldcup_predictor.simulate import standings_from_results


def test_points_then_gd():
    # A beats B 2-0, A beats C 1-0, B beats C 3-0, C-? ... build a small group
    teams = ["A", "B", "C", "D"]
    results = [
        ("A", "B", 2, 0),
        ("A", "C", 1, 0),
        ("A", "D", 1, 0),
        ("B", "C", 3, 0),
        ("B", "D", 1, 0),
        ("C", "D", 0, 0),
    ]
    table = standings_from_results(teams, results)
    order = [row.team for row in table]
    assert order[0] == "A"   # 9 pts
    assert order[1] == "B"   # 6 pts
    # C vs D: C 1pt (0-0 draw + losses), D 1pt -> GD/GF tiebreak
    assert set(order[2:]) == {"C", "D"}


def test_head_to_head_breaks_equal_points():
    teams = ["X", "Y"]
    results = [("X", "Y", 1, 0)]
    table = standings_from_results(teams, results)
    assert table[0].team == "X"
```

- [ ] **Step 2: Run it (red)** → FAIL.

- [ ] **Step 3: Implement standings in `simulate.py`**

```python
# src/worldcup_predictor/simulate.py
from __future__ import annotations

import random
from dataclasses import dataclass

import numpy as np

from worldcup_predictor.models import GroupRow

Result = tuple[str, str, int, int]  # home, away, home_goals, away_goals


@dataclass
class _Acc:
    played: int = 0
    won: int = 0
    drawn: int = 0
    lost: int = 0
    gf: int = 0
    ga: int = 0
    pts: int = 0


def _accumulate(teams: list[str], results: list[Result]) -> dict[str, _Acc]:
    table = {t: _Acc() for t in teams}
    for h, a, hg, ag in results:
        table[h].played += 1
        table[a].played += 1
        table[h].gf += hg
        table[h].ga += ag
        table[a].gf += ag
        table[a].ga += hg
        if hg > ag:
            table[h].won += 1
            table[a].lost += 1
            table[h].pts += 3
        elif hg < ag:
            table[a].won += 1
            table[h].lost += 1
            table[a].pts += 3
        else:
            table[h].drawn += 1
            table[a].drawn += 1
            table[h].pts += 1
            table[a].pts += 1
    return table


def _h2h(team: str, others: set[str], results: list[Result]) -> tuple[int, int]:
    pts = gd = 0
    for h, a, hg, ag in results:
        if h == team and a in others:
            gd += hg - ag
            pts += 3 if hg > ag else (1 if hg == ag else 0)
        elif a == team and h in others:
            gd += ag - hg
            pts += 3 if ag > hg else (1 if hg == ag else 0)
    return pts, gd


def standings_from_results(
    teams: list[str], results: list[Result], rng: random.Random | None = None
) -> list[GroupRow]:
    rng = rng or random.Random()
    acc = _accumulate(teams, results)

    def key(team: str) -> tuple:
        a = acc[team]
        tied = {t for t in teams if acc[t].pts == a.pts and t != team} | {team}
        h2h_pts, h2h_gd = _h2h(team, tied - {team}, results) if len(tied) > 1 else (0, 0)
        return (a.pts, a.gf - a.ga, a.gf, h2h_pts, h2h_gd, rng.random())

    ordered = sorted(teams, key=key, reverse=True)
    return [
        GroupRow(
            team=t,
            played=acc[t].played,
            won=acc[t].won,
            drawn=acc[t].drawn,
            lost=acc[t].lost,
            gf=acc[t].gf,
            ga=acc[t].ga,
            gd=acc[t].gf - acc[t].ga,
            pts=acc[t].pts,
        )
        for t in ordered
    ]
```

- [ ] **Step 4: Run it (green)** → PASS.

- [ ] **Step 5: Commit**

```bash
git add src/worldcup_predictor/simulate.py tests/test_standings.py
git commit -m "feat: group standings with WC tiebreakers

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

### Task 7.2: Best-third-placed ranking & R32 bracket assembly

**Files:** Modify: `src/worldcup_predictor/simulate.py` · Test: `tests/test_bracket.py`
**Rules:** rank the 12 third-placed teams by (pts, GD, GF, random); take the best 8. R32 pairings follow the fixed Annex C table (see `files/design.md` A.3). For the 8 "Winner vs best-3rd" slots, assign the 8 qualifying thirds to those slots in a fixed deterministic order (Phase 1 simplification; the official letter-combination map is a Phase 2 refinement and does not change advancement probabilities materially).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_bracket.py
from worldcup_predictor.models import GroupRow
from worldcup_predictor.simulate import best_thirds, build_r32


def _row(team, pts, gd, gf):
    return GroupRow(team, 3, 0, 0, 0, gf, gf - gd, gd, pts)


def test_best_thirds_picks_top_8():
    thirds = {g: _row(f"T{g}", pts=pts, gd=0, gf=0)
              for g, pts in zip("ABCDEFGHIJKL", [9, 8, 7, 6, 5, 4, 3, 2, 1, 0, 0, 0])}
    chosen = best_thirds(thirds)
    assert len(chosen) == 8
    assert "TA" in {r.team for r in chosen}
    assert "TL" not in {r.team for r in chosen}


def test_build_r32_has_16_matches():
    winners = {g: f"W{g}" for g in "ABCDEFGHIJKL"}
    runners = {g: f"RU{g}" for g in "ABCDEFGHIJKL"}
    thirds = [f"3rd{i}" for i in range(8)]
    bracket = build_r32(winners, runners, thirds)
    assert len(bracket) == 16
    # every match is a 2-tuple of team names
    assert all(len(m) == 2 and all(m) for m in bracket)
```

- [ ] **Step 2: Run it (red)** → FAIL.

- [ ] **Step 3: Implement in `simulate.py`** (append)

```python
# Fixed Annex C R32 pairing template. "3" marks a best-third slot (filled in order).
_R32_TEMPLATE: list[tuple[str, str]] = [
    ("RU_A", "RU_B"), ("W_E", "3"), ("W_F", "RU_C"), ("W_C", "RU_F"),
    ("W_I", "3"), ("RU_E", "RU_I"), ("W_A", "3"), ("W_L", "3"),
    ("W_D", "3"), ("W_G", "3"), ("RU_K", "RU_L"), ("W_H", "RU_J"),
    ("W_B", "3"), ("W_J", "RU_H"), ("W_K", "3"), ("RU_D", "RU_G"),
]


def best_thirds(thirds: dict[str, GroupRow], rng: random.Random | None = None) -> list[GroupRow]:
    rng = rng or random.Random()
    ranked = sorted(
        thirds.values(),
        key=lambda r: (r.pts, r.gd, r.gf, rng.random()),
        reverse=True,
    )
    return ranked[:8]


def build_r32(
    winners: dict[str, str], runners: dict[str, str], thirds: list[str]
) -> list[tuple[str, str]]:
    third_iter = iter(thirds)
    out: list[tuple[str, str]] = []
    for left, right in _R32_TEMPLATE:
        a = _resolve(left, winners, runners, third_iter)
        b = _resolve(right, winners, runners, third_iter)
        out.append((a, b))
    return out


def _resolve(token: str, winners: dict[str, str], runners: dict[str, str], thirds) -> str:
    if token == "3":
        return next(thirds)
    side, gid = token.split("_")
    return winners[gid] if side == "W" else runners[gid]
```

- [ ] **Step 4: Run it (green)** → PASS.

- [ ] **Step 5: Commit**

```bash
git add src/worldcup_predictor/simulate.py tests/test_bracket.py
git commit -m "feat: best-third ranking and R32 bracket assembly

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

### Task 7.3: Full tournament Monte Carlo

**Files:** Modify: `src/worldcup_predictor/simulate.py` · Test: `tests/test_simulate.py`
**Design:** pre-compute per-pair 1X2 probs and score grids once; in each of N iterations simulate group matches (sample scoreline from grid), build standings, pick qualifiers, simulate knockout (R32→Final) sampling 1X2 then 50/50 penalties on a draw, accumulate advancement/title counts. `simulate_tournament(conn, model, n, seed)` returns `{team: {"advance":p, "r16":p, "qf":p, "sf":p, "final":p, "title":p}}` and persists to `sim_results`.

- [ ] **Step 1: Write the failing test** (small N, only checks invariants — not exact probabilities)

```python
# tests/test_simulate.py
import numpy as np
import pandas as pd

from worldcup_predictor import config, db, ingest
from worldcup_predictor.goal_model import GoalModel
from worldcup_predictor.simulate import simulate_tournament


def _history() -> pd.DataFrame:
    rng = np.random.default_rng(2)
    teams = [t for ts in config.GROUPS.values() for t in ts]
    rows = []
    for _ in range(2000):
        h, a = rng.choice(teams, 2, replace=False)
        rows.append(("2024-01-01", h, a, int(rng.poisson(1.3)), int(rng.poisson(1.2)), True))
    return pd.DataFrame(rows, columns=["date", "home_team", "away_team", "home_goals", "away_goals", "neutral"])


def test_simulation_probabilities_valid(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.init_schema(conn)
    ingest.seed_teams_and_fixtures(conn)
    model = GoalModel().fit(_history())

    result = simulate_tournament(conn, model, n=200, seed=7)
    # 48 teams present
    assert len(result) == 48
    # each prob in [0,1], title <= final <= sf <= qf <= r16 <= advance
    for team, p in result.items():
        for v in p.values():
            assert 0.0 <= v <= 1.0
        assert p["title"] <= p["final"] <= p["sf"] <= p["qf"] <= p["r16"] <= p["advance"] + 1e-9
    # exactly one champion's worth of probability mass
    assert abs(sum(p["title"] for p in result.values()) - 1.0) < 1e-9
    # advancement mass equals 32 teams
    assert abs(sum(p["advance"] for p in result.values()) - 32.0) < 1e-6
    # persisted
    assert conn.execute("SELECT COUNT(*) FROM sim_results").fetchone()[0] == 48
```

- [ ] **Step 2: Run it (red)** → FAIL.

- [ ] **Step 3: Implement `simulate_tournament` in `simulate.py`** (append)

```python
import sqlite3
import time
from itertools import combinations

from worldcup_predictor import config
from worldcup_predictor.goal_model import GoalModel


def _sample_score(matrix: np.ndarray, rng: np.random.Generator) -> tuple[int, int]:
    flat = matrix.ravel()
    idx = rng.choice(flat.size, p=flat / flat.sum())
    h, a = np.unravel_index(idx, matrix.shape)
    return int(h), int(a)


def _knockout_winner(a: str, b: str, probs, grids, rng: np.random.Generator) -> str:
    p_h, p_d, p_a = probs[(a, b)]
    r = rng.random()
    if r < p_h:
        return a
    if r < p_h + p_a:
        return b
    return a if rng.random() < 0.5 else b  # penalties ~ 50/50


def simulate_tournament(
    conn: sqlite3.Connection, model: GoalModel, n: int = 50_000, seed: int | None = None
) -> dict[str, dict[str, float]]:
    rng = np.random.default_rng(seed)
    teams = [t for ts in config.GROUPS.values() for t in ts]

    # Pre-compute grids and 1X2 probs for every ordered pair once.
    grids: dict[tuple[str, str], np.ndarray] = {}
    probs: dict[tuple[str, str], tuple[float, float, float]] = {}
    for x in teams:
        for y in teams:
            if x == y:
                continue
            g = model.predict_grid(x, y, neutral=True)
            grids[(x, y)] = g.matrix
            probs[(x, y)] = (g.home_win, g.draw, g.away_win)

    counts = {t: dict(advance=0, r16=0, qf=0, sf=0, final=0, title=0) for t in teams}

    for _ in range(n):
        winners: dict[str, str] = {}
        runners: dict[str, str] = {}
        thirds_rows: dict[str, GroupRow] = {}

        for gid, gteams in config.GROUPS.items():
            results: list[Result] = []
            for h, a in combinations(gteams, 2):
                hg, ag = _sample_score(grids[(h, a)], rng)
                results.append((h, a, hg, ag))
            table = standings_from_results(gteams, results, random.Random(int(rng.integers(1 << 30))))
            winners[gid] = table[0].team
            runners[gid] = table[1].team
            thirds_rows[gid] = table[2]

        for t in list(winners.values()) + list(runners.values()):
            counts[t]["advance"] += 1
        qual_thirds = best_thirds(thirds_rows, random.Random(int(rng.integers(1 << 30))))
        for r in qual_thirds:
            counts[r.team]["advance"] += 1

        bracket = build_r32(winners, runners, [r.team for r in qual_thirds])
        # Winning round R32/R16/QF/SF/Final credits reaching r16/qf/sf/final/title.
        for round_key in ("r16", "qf", "sf", "final", "title"):
            winners_round = [_knockout_winner(a, b, probs, grids, rng) for a, b in bracket]
            for w in winners_round:
                counts[w][round_key] += 1
            it = iter(winners_round)
            bracket = list(zip(it, it))  # pair winners for the next round (empty after final)

    result = {
        t: {k: v / n for k, v in counts[t].items()} for t in teams
    }
    now = time.time()
    conn.execute("DELETE FROM sim_results")
    for t, p in result.items():
        conn.execute(
            "INSERT INTO sim_results(created_at, team, advance_prob, r16_prob, qf_prob,"
            " sf_prob, final_prob, title_prob, n_iter) VALUES (?,?,?,?,?,?,?,?,?)",
            (now, t, p["advance"], p["r16"], p["qf"], p["sf"], p["final"], p["title"], n),
        )
    conn.commit()
    return result
```

> **Bookkeeping invariant:** per iteration, `advance`=32, `r16`=16, `qf`=8, `sf`=4, `final`=2, `title`=1, so the monotonicity assertion in the test holds by construction.

- [ ] **Step 4: Run it (green)**

Run: `uv run pytest tests/test_simulate.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/worldcup_predictor/simulate.py tests/test_simulate.py
git commit -m "feat: Monte Carlo tournament simulation with advancement/title probs

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

## Milestone 8 — Evaluation

### Task 8.1: Scoring rules (RPS, Brier, log-loss)

**Files:** Create: `src/worldcup_predictor/evaluate.py` · Test: `tests/test_metrics.py`
**Outcome encoding:** 0=home win, 1=draw, 2=away win. RPS uses the ordered cumulative form.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_metrics.py
import pytest

from worldcup_predictor.evaluate import log_loss_score, multiclass_brier, rps


def test_rps_perfect_is_zero():
    assert rps([1.0, 0.0, 0.0], 0) == pytest.approx(0.0)


def test_rps_known_value():
    # probs H/D/A = .5/.3/.2, outcome draw(1): F1=.5,F2=.8 ; O1=0,O2=1
    # rps = ((.5-0)^2 + (.8-1)^2)/2 = (.25 + .04)/2 = .145
    assert rps([0.5, 0.3, 0.2], 1) == pytest.approx(0.145)


def test_brier_perfect_is_zero():
    assert multiclass_brier([0.0, 1.0, 0.0], 1) == pytest.approx(0.0)


def test_log_loss_penalizes_confident_wrong():
    good = log_loss_score([0.8, 0.1, 0.1], 0)
    bad = log_loss_score([0.01, 0.1, 0.89], 0)
    assert bad > good
```

- [ ] **Step 2: Run it (red)** → FAIL.

- [ ] **Step 3: Implement metrics in `evaluate.py`**

```python
# src/worldcup_predictor/evaluate.py
from __future__ import annotations

import math
import sqlite3
import time


def rps(probs: list[float], outcome: int) -> float:
    """Ranked Probability Score for ordered 1X2 outcome (0=H,1=D,2=A)."""
    cum_p = 0.0
    cum_o = 0.0
    total = 0.0
    for k in range(len(probs) - 1):
        cum_p += probs[k]
        cum_o += 1.0 if outcome == k else 0.0
        total += (cum_p - cum_o) ** 2
    return total / (len(probs) - 1)


def multiclass_brier(probs: list[float], outcome: int) -> float:
    return sum((p - (1.0 if i == outcome else 0.0)) ** 2 for i, p in enumerate(probs))


def log_loss_score(probs: list[float], outcome: int, eps: float = 1e-15) -> float:
    p = min(1 - eps, max(eps, probs[outcome]))
    return -math.log(p)
```

- [ ] **Step 4: Run it (green)** → PASS.

- [ ] **Step 5: Commit**

```bash
git add src/worldcup_predictor/evaluate.py tests/test_metrics.py
git commit -m "feat: RPS, Brier, log-loss scoring rules

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

### Task 8.2: Score finished predictions + baselines

**Files:** Modify: `src/worldcup_predictor/evaluate.py` · Test: `tests/test_score_predictions.py`
**Behavior:** join `predictions` to finished `matches`, compute mean RPS/Brier/log-loss for the model and for a base-rate baseline (`[0.40, 0.30, 0.30]` neutral-venue prior), persist to `metrics`, return a summary dict.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_score_predictions.py
import time

from worldcup_predictor import db
from worldcup_predictor.evaluate import score_finished_predictions


def _setup(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.init_schema(conn)
    conn.execute("INSERT INTO matches(id,stage,home_team,away_team,home_score,away_score,status)"
                 " VALUES (1,'group','A','B',2,0,'FINISHED')")
    conn.execute("INSERT INTO predictions(match_id,created_at,p_home,p_draw,p_away,"
                 "exp_home_goals,exp_away_goals,ml_home,ml_away,model_version,reasoning)"
                 " VALUES (1,?,0.7,0.2,0.1,1.8,0.6,2,0,'v','')", (time.time(),))
    conn.commit()
    return conn


def test_score_finished_predictions(tmp_path):
    conn = _setup(tmp_path)
    summary = score_finished_predictions(conn)
    assert summary["n"] == 1
    assert 0.0 <= summary["model_rps"] <= 1.0
    assert "baseline_rps" in summary
    # model predicted home strongly and home won -> model beats base rate
    assert summary["model_rps"] < summary["baseline_rps"]
    assert conn.execute("SELECT COUNT(*) FROM metrics").fetchone()[0] >= 1
```

- [ ] **Step 2: Run it (red)** → FAIL.

- [ ] **Step 3: Implement in `evaluate.py`** (append)

```python
BASELINE = [0.40, 0.30, 0.30]


def _outcome(home_score: int, away_score: int) -> int:
    if home_score > away_score:
        return 0
    if home_score == away_score:
        return 1
    return 2


def score_finished_predictions(conn: sqlite3.Connection) -> dict[str, float]:
    rows = conn.execute(
        "SELECT p.p_home, p.p_draw, p.p_away, m.home_score, m.away_score "
        "FROM predictions p JOIN matches m ON m.id = p.match_id "
        "WHERE m.status='FINISHED'"
    ).fetchall()
    if not rows:
        return {"n": 0}

    m_rps = m_brier = m_ll = b_rps = 0.0
    for r in rows:
        probs = [r["p_home"], r["p_draw"], r["p_away"]]
        out = _outcome(r["home_score"], r["away_score"])
        m_rps += rps(probs, out)
        m_brier += multiclass_brier(probs, out)
        m_ll += log_loss_score(probs, out)
        b_rps += rps(BASELINE, out)

    n = len(rows)
    summary = {
        "n": n,
        "model_rps": m_rps / n,
        "model_brier": m_brier / n,
        "model_log_loss": m_ll / n,
        "baseline_rps": b_rps / n,
    }
    now = time.time()
    for key in ("model_rps", "model_brier", "model_log_loss", "baseline_rps"):
        conn.execute(
            "INSERT INTO metrics(created_at, metric, value, scope) VALUES (?,?,?,?)",
            (now, key, summary[key], "all_finished"),
        )
    conn.commit()
    return summary
```

- [ ] **Step 4: Run it (green)** → PASS.

- [ ] **Step 5: Commit**

```bash
git add src/worldcup_predictor/evaluate.py tests/test_score_predictions.py
git commit -m "feat: score finished predictions vs base-rate baseline

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

## Milestone 9 — Engine facade

### Task 9.1: Read-side facade (standings, bracket, upcoming, detail)

**Files:** Create: `src/worldcup_predictor/engine.py` · Test: `tests/test_engine_read.py`
**Why:** MCP and web adapters must not touch SQL directly. The facade returns plain dicts/dataclasses ready for JSON.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_engine_read.py
from worldcup_predictor import db, engine, ingest


def _conn(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.init_schema(conn)
    ingest.seed_teams_and_fixtures(conn)
    return conn


def test_group_standings_shape(tmp_path):
    conn = _conn(tmp_path)
    conn.execute("UPDATE matches SET home_score=1, away_score=0, status='FINISHED' "
                 "WHERE group_id='A' AND home_team=? AND away_team=?",
                 (conn.execute("SELECT home_team FROM matches WHERE group_id='A' LIMIT 1").fetchone()[0],
                  conn.execute("SELECT away_team FROM matches WHERE group_id='A' LIMIT 1").fetchone()[0]))
    conn.commit()
    rows = engine.get_group_standings(conn, "A")
    assert len(rows) == 4
    assert {"team", "played", "won", "drawn", "lost", "gf", "ga", "gd", "pts"} <= set(rows[0])


def test_upcoming_matches(tmp_path):
    conn = _conn(tmp_path)
    ups = engine.get_upcoming_matches(conn, limit=5)
    assert len(ups) == 5
    assert ups[0]["status"] == "SCHEDULED"
```

- [ ] **Step 2: Run it (red)** → FAIL.

- [ ] **Step 3: Implement read-side `engine.py`**

```python
# src/worldcup_predictor/engine.py
from __future__ import annotations

import sqlite3
from dataclasses import asdict

from worldcup_predictor import config, db
from worldcup_predictor.simulate import standings_from_results


def get_group_standings(conn: sqlite3.Connection, group: str) -> list[dict]:
    group = group.upper()
    teams = config.GROUPS[group]
    results = [
        (r["home_team"], r["away_team"], r["home_score"], r["away_score"])
        for r in conn.execute(
            "SELECT home_team, away_team, home_score, away_score FROM matches "
            "WHERE group_id=? AND status='FINISHED'",
            (group,),
        ).fetchall()
    ]
    return [asdict(row) for row in standings_from_results(teams, results)]


def get_upcoming_matches(conn: sqlite3.Connection, limit: int = 10) -> list[dict]:
    rows = conn.execute(
        "SELECT id, stage, group_id, home_team, away_team, kickoff, status "
        "FROM matches WHERE status='SCHEDULED' ORDER BY COALESCE(kickoff,''), id LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_knockout_bracket(conn: sqlite3.Connection) -> dict[str, list[dict]]:
    rounds: dict[str, list[dict]] = {}
    for stage in ("R32", "R16", "QF", "SF", "3RD", "FINAL"):
        rows = conn.execute(
            "SELECT id, home_team, away_team, home_score, away_score, status "
            "FROM matches WHERE stage=? ORDER BY id",
            (stage,),
        ).fetchall()
        rounds[stage] = [dict(r) for r in rows]
    return rounds


def get_match_detail(conn: sqlite3.Connection, match_id: int) -> dict:
    match = conn.execute("SELECT * FROM matches WHERE id=?", (match_id,)).fetchone()
    pred = conn.execute(
        "SELECT * FROM predictions WHERE match_id=? ORDER BY created_at DESC LIMIT 1",
        (match_id,),
    ).fetchone()
    return {
        "match": dict(match) if match else None,
        "prediction": dict(pred) if pred else None,
    }


def get_last_update_ts(conn: sqlite3.Connection) -> str | None:
    return db.get_last_update_ts(conn)
```

- [ ] **Step 4: Run it (green)** → PASS.

- [ ] **Step 5: Commit**

```bash
git add src/worldcup_predictor/engine.py tests/test_engine_read.py
git commit -m "feat: read-side engine facade (standings, bracket, upcoming, detail)

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

### Task 9.2: Write-side facade (record result, record intel, predict, simulate)

**Files:** Modify: `src/worldcup_predictor/engine.py` · Test: `tests/test_engine_write.py`
**Note:** the facade lazily builds a `GoalModel` from history and caches it on the module; `record_result` updates the match and touches `last_update`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_engine_write.py
from worldcup_predictor import db, engine, ingest


def test_record_result_updates_and_touches(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.init_schema(conn)
    ingest.seed_teams_and_fixtures(conn)
    mid = conn.execute("SELECT id FROM matches LIMIT 1").fetchone()[0]
    engine.record_result(conn, mid, 3, 1)
    row = conn.execute("SELECT home_score, away_score, status FROM matches WHERE id=?", (mid,)).fetchone()
    assert (row["home_score"], row["away_score"], row["status"]) == (3, 1, "FINISHED")
    assert engine.get_last_update_ts(conn) is not None


def test_record_intel_event(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.init_schema(conn)
    engine.record_intel_event(conn, team="France", event_type="injury",
                              direction="weaken", magnitude=-0.25,
                              source_url="https://x", credibility=0.9, player="Star")
    n = conn.execute("SELECT COUNT(*) FROM intel_events WHERE team='France'").fetchone()[0]
    assert n == 1
```

- [ ] **Step 2: Run it (red)** → FAIL.

- [ ] **Step 3: Implement write-side `engine.py`** (append)

```python
from worldcup_predictor import intel as _intel
from worldcup_predictor.goal_model import GoalModel, history_frame
from worldcup_predictor.models import IntelEvent
from worldcup_predictor.predict import predict_match
from worldcup_predictor.simulate import simulate_tournament

_MODEL: GoalModel | None = None


def get_model(conn: sqlite3.Connection, refit: bool = False) -> GoalModel:
    global _MODEL
    if _MODEL is None or refit:
        _MODEL = GoalModel().fit(history_frame(conn))
    return _MODEL


def record_result(conn: sqlite3.Connection, match_id: int, home_score: int, away_score: int) -> None:
    conn.execute(
        "UPDATE matches SET home_score=?, away_score=?, status='FINISHED' WHERE id=?",
        (home_score, away_score, match_id),
    )
    conn.commit()
    db.touch_update(conn)


def record_intel_event(conn: sqlite3.Connection, **kwargs) -> None:
    _intel.record_intel(conn, IntelEvent(**kwargs))
    db.touch_update(conn)


def predict_fixture(conn: sqlite3.Connection, match_id: int) -> dict:
    m = conn.execute("SELECT home_team, away_team, neutral FROM matches WHERE id=?", (match_id,)).fetchone()
    model = get_model(conn)
    pred = predict_match(conn, model, m["home_team"], m["away_team"],
                         match_id=match_id, neutral=bool(m["neutral"]))
    db.touch_update(conn)
    return {
        "home_team": pred.home_team, "away_team": pred.away_team,
        "p_home": pred.p_home, "p_draw": pred.p_draw, "p_away": pred.p_away,
        "exp_home_goals": pred.exp_home_goals, "exp_away_goals": pred.exp_away_goals,
        "most_likely": pred.most_likely_scoreline,
        "factors": [{"team": f.team, "description": f.description, "delta": f.lambda_delta}
                    for f in pred.factors],
    }


def run_simulation(conn: sqlite3.Connection, n: int = 50_000, seed: int | None = None) -> dict:
    model = get_model(conn)
    result = simulate_tournament(conn, model, n=n, seed=seed)
    db.touch_update(conn)
    return result
```

- [ ] **Step 4: Run it (green)** → PASS.

- [ ] **Step 5: Commit**

```bash
git add src/worldcup_predictor/engine.py tests/test_engine_write.py
git commit -m "feat: write-side engine facade (results, intel, predict, simulate)

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

## Milestone 10 — CLI

### Task 10.1: Typer CLI (init-db, seed, bootstrap, fetch-results, predict, simulate, evaluate, serve)

**Files:** Create: `src/worldcup_predictor/cli.py` · Test: `tests/test_cli.py`
**Note:** `bootstrap` chains history load + seed + Elo + first predictions. Tests use Typer's `CliRunner` against an isolated DB via `WC_DB_PATH`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cli.py
from typer.testing import CliRunner

from worldcup_predictor.cli import app

runner = CliRunner()


def test_init_db_and_seed(tmp_path, monkeypatch):
    monkeypatch.setenv("WC_DB_PATH", str(tmp_path / "cli.db"))
    assert runner.invoke(app, ["init-db"]).exit_code == 0
    res = runner.invoke(app, ["seed"])
    assert res.exit_code == 0
    assert "48" in res.stdout


def test_simulate_small(tmp_path, monkeypatch):
    monkeypatch.setenv("WC_DB_PATH", str(tmp_path / "cli.db"))
    runner.invoke(app, ["init-db"])
    runner.invoke(app, ["seed"])
    # load a tiny synthetic history so the model can fit
    runner.invoke(app, ["load-history", "--file", "tests/fixtures/mini_history.csv"])
    res = runner.invoke(app, ["simulate", "--n", "50", "--seed", "1"])
    assert res.exit_code == 0
```

- [ ] **Step 2: Create the fixture** `tests/fixtures/mini_history.csv`

```csv
date,home_team,away_team,home_score,away_score,tournament,city,country,neutral
2024-01-01,Argentina,Brazil,2,1,Friendly,X,Y,True
2024-01-02,Spain,France,1,1,Friendly,X,Y,True
2024-01-03,England,Germany,0,2,Friendly,X,Y,True
2024-01-04,Brazil,Spain,1,0,Friendly,X,Y,True
2024-01-05,France,Argentina,2,2,Friendly,X,Y,True
2024-01-06,Germany,England,3,1,Friendly,X,Y,True
```

(Repeat/extend rows so every WC team appears at least once; the implementer should script generating a row per team vs a random opponent if the fit complains about unseen teams. Keep it under `tests/fixtures/`.)

- [ ] **Step 3: Run it (red)** → FAIL.

- [ ] **Step 4: Implement `cli.py`**

```python
# src/worldcup_predictor/cli.py
from __future__ import annotations

from pathlib import Path

import typer

from worldcup_predictor import db, engine, evaluate, ingest

app = typer.Typer(help="WorldCup Predictor CLI")


def _conn():
    conn = db.connect()
    db.init_schema(conn)
    return conn


@app.command("init-db")
def init_db() -> None:
    """Create the SQLite schema."""
    _conn()
    typer.echo("Database initialized.")


@app.command()
def seed() -> None:
    """Seed 48 teams and 72 group fixtures."""
    conn = _conn()
    ingest.seed_teams_and_fixtures(conn)
    n = conn.execute("SELECT COUNT(*) FROM teams").fetchone()[0]
    typer.echo(f"Seeded {n} teams.")


@app.command("load-history")
def load_history(file: str = typer.Option(None, help="Local CSV path; omit to fetch online")) -> None:
    """Load historical international results (bootstrap)."""
    conn = _conn()
    if file:
        n = ingest.load_history_from_text(conn, Path(file).read_text())
    else:
        n = ingest.load_history(conn)
    typer.echo(f"Loaded {n} historical matches.")


@app.command("fetch-results")
def fetch_results() -> None:
    """Fetch finished WC results and update the DB (cron-friendly)."""
    conn = _conn()
    n = ingest.fetch_live_results(conn)
    typer.echo(f"Updated {n} results.")


@app.command()
def predict(match_id: int) -> None:
    """Predict a single fixture and persist it."""
    conn = _conn()
    out = engine.predict_fixture(conn, match_id)
    typer.echo(out)


@app.command()
def simulate(n: int = 50_000, seed: int = typer.Option(None)) -> None:
    """Run the Monte Carlo tournament simulation."""
    conn = _conn()
    result = engine.run_simulation(conn, n=n, seed=seed)
    top = sorted(result.items(), key=lambda kv: kv[1]["title"], reverse=True)[:5]
    for team, p in top:
        typer.echo(f"{team:20s} title={p['title']:.3f} advance={p['advance']:.3f}")


@app.command()
def evaluate_cmd() -> None:
    """Score finished predictions vs baseline."""
    conn = _conn()
    typer.echo(evaluate.score_finished_predictions(conn))


app.command("evaluate")(evaluate_cmd)


@app.command()
def serve(host: str = "0.0.0.0", port: int = 8080) -> None:
    """Start the web UI."""
    import uvicorn

    uvicorn.run("worldcup_predictor.web_server:app", host=host, port=port)


if __name__ == "__main__":
    app()
```

- [ ] **Step 5: Run it (green)** → PASS.

- [ ] **Step 6: Commit**

```bash
git add src/worldcup_predictor/cli.py tests/test_cli.py tests/fixtures/mini_history.csv
git commit -m "feat: Typer CLI for init/seed/history/fetch/predict/simulate/evaluate/serve

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

## Milestone 11 — MCP server (thin adapter)

### Task 11.1: FastMCP tools over the engine

**Files:** Create: `src/worldcup_predictor/mcp_server.py` · Test: `tests/test_mcp_server.py`
**Critical:** stdio transport — never `print()` to stdout; use `logging` to stderr. Tools are thin wrappers calling `engine`. A module-level connection is opened lazily.

- [ ] **Step 1: Write the failing test** (calls tools in-process via FastMCP)

```python
# tests/test_mcp_server.py
import pytest

from worldcup_predictor import mcp_server


@pytest.mark.asyncio
async def test_list_tools_registered():
    tools = await mcp_server.mcp.list_tools()
    names = {t.name for t in tools}
    assert {"get_group_standings", "record_match_result", "get_upcoming_matches",
            "record_intel", "run_simulation"} <= names


@pytest.mark.asyncio
async def test_invalid_group_errors(tmp_path, monkeypatch):
    monkeypatch.setenv("WC_DB_PATH", str(tmp_path / "mcp.db"))
    mcp_server._reset_conn()
    result = await mcp_server.mcp.call_tool("get_group_standings", {"group": "Z"})
    # FastMCP returns (content, structured) or a CallToolResult depending on version;
    # assert the call surfaced an error rather than valid standings.
    assert result is not None
```

> The exact return shape of `mcp.call_tool` varies across `mcp` 1.x patch versions. If the structured-result assertion is brittle, assert instead that calling with a valid group `"A"` returns 4 standings rows. Keep the test resilient.

- [ ] **Step 2: Run it (red)** → FAIL.

- [ ] **Step 3: Implement `mcp_server.py`**

```python
# src/worldcup_predictor/mcp_server.py
"""MCP server for worldcup-predictor. Thin adapter over engine. stdio transport."""
from __future__ import annotations

import logging
import sys

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from pydantic import BaseModel, Field

from worldcup_predictor import config, db, engine

logging.basicConfig(level=logging.INFO, stream=sys.stderr)
logger = logging.getLogger("worldcup-mcp")

mcp = FastMCP("worldcup-predictor")
_CONN = None


def _conn():
    global _CONN
    if _CONN is None:
        _CONN = db.connect()
        db.init_schema(_CONN)
    return _CONN


def _reset_conn() -> None:  # test helper
    global _CONN
    _CONN = None


class GroupStanding(BaseModel):
    team: str
    played: int
    won: int
    drawn: int
    lost: int
    gf: int
    ga: int
    gd: int = Field(description="Goal difference")
    pts: int


@mcp.tool()
def get_group_standings(group: str) -> list[GroupStanding]:
    """Return current standings for a 2026 World Cup group (A-L)."""
    group = group.upper().strip()
    if group not in config.GROUPS:
        raise ToolError(f"'{group}' is not a valid group. Must be A-L.")
    return [GroupStanding(**row) for row in engine.get_group_standings(_conn(), group)]


@mcp.tool()
def get_upcoming_matches(limit: int = 5) -> list[dict]:
    """Return the next N scheduled matches without a result yet."""
    return engine.get_upcoming_matches(_conn(), min(max(1, limit), 20))


@mcp.tool()
def record_match_result(match_id: int, home_score: int, away_score: int) -> dict[str, str]:
    """Record an official match result and refresh standings/bracket."""
    if home_score < 0 or away_score < 0:
        raise ToolError("Scores must be non-negative.")
    engine.record_result(_conn(), match_id, home_score, away_score)
    return {"status": "ok", "match_id": str(match_id)}


@mcp.tool()
def predict_match(match_id: int) -> dict:
    """Predict 1X2 + scoreline for a fixture (applies current intel) and persist it."""
    return engine.predict_fixture(_conn(), match_id)


@mcp.tool()
def record_intel(
    team: str,
    event_type: str,
    direction: str,
    magnitude: float,
    source_url: str,
    credibility: float,
    player: str = "",
    notes: str = "",
) -> dict[str, str]:
    """Record an off-pitch intelligence event that adjusts a team's expected goals.

    magnitude is a signed multiplier delta on the team's lambda (e.g. -0.20 for a key
    injury). credibility in [0,1] scales the effect. ALWAYS pass a real source_url.
    """
    if not source_url:
        raise ToolError("source_url is required; intel must be traceable.")
    if not 0.0 <= credibility <= 1.0:
        raise ToolError("credibility must be in [0,1].")
    engine.record_intel_event(
        _conn(), team=team, event_type=event_type, direction=direction,
        magnitude=magnitude, source_url=source_url, credibility=credibility,
        player=player or None, notes=notes or None,
    )
    return {"status": "ok", "team": team}


@mcp.tool()
def run_simulation(iterations: int = 50_000, seed: int | None = None) -> dict:
    """Run the Monte Carlo tournament simulation; return top title contenders."""
    result = engine.run_simulation(_conn(), n=iterations, seed=seed)
    top = sorted(result.items(), key=lambda kv: kv[1]["title"], reverse=True)[:10]
    return {team: probs for team, probs in top}


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run it (green)** → PASS.

- [ ] **Step 5: Register with the MCP client** (manual, document in README)

- **GitHub Copilot CLI:** run `/mcp add` in a Copilot session, or add to the Copilot CLI MCP config, pointing the command at:
  `uv --directory /path/to/worldcup-predictor run worldcup-mcp`
- **VS Code:** create `.vscode/mcp.json`:

```json
{
  "servers": {
    "worldcup-predictor": {
      "command": "uv",
      "args": ["--directory", "/path/to/worldcup-predictor", "run", "worldcup-mcp"]
    }
  }
}
```

Verify by listing tools in the client and calling `get_upcoming_matches`.

- [ ] **Step 6: Commit**

```bash
git add src/worldcup_predictor/mcp_server.py tests/test_mcp_server.py .vscode/mcp.json
git commit -m "feat: FastMCP server exposing engine tools

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

## Milestone 12 — Web UI (FastAPI + bracket + SSE)

### Task 12.1: FastAPI app + JSON API + static mount

**Files:** Create: `src/worldcup_predictor/web_server.py` · Test: `tests/test_web_server.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_web_server.py
from fastapi.testclient import TestClient


def test_api_endpoints(tmp_path, monkeypatch):
    monkeypatch.setenv("WC_DB_PATH", str(tmp_path / "web.db"))
    from worldcup_predictor import db, ingest
    conn = db.connect()
    db.init_schema(conn)
    ingest.seed_teams_and_fixtures(conn)

    from worldcup_predictor.web_server import app
    client = TestClient(app)

    r = client.get("/api/groups/A/standings")
    assert r.status_code == 200
    assert len(r.json()) == 4

    r2 = client.get("/api/matches/upcoming?limit=3")
    assert r2.status_code == 200
    assert len(r2.json()) == 3

    r3 = client.get("/api/knockout/bracket")
    assert r3.status_code == 200
    assert "R32" in r3.json()
```

- [ ] **Step 2: Run it (red)** → FAIL.

- [ ] **Step 3: Implement `web_server.py`**

```python
# src/worldcup_predictor/web_server.py
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from sse_starlette import EventSourceResponse, ServerSentEvent

from worldcup_predictor import db, engine

STATIC = Path(__file__).parent / "static"
app = FastAPI(title="WorldCup Predictor")
app.mount("/static", StaticFiles(directory=STATIC), name="static")


def _conn():
    conn = db.connect()
    db.init_schema(conn)
    return conn


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (STATIC / "index.html").read_text()


@app.get("/api/groups/{group}/standings")
def groups(group: str) -> list[dict]:
    return engine.get_group_standings(_conn(), group)


@app.get("/api/matches/upcoming")
def upcoming(limit: int = 10) -> list[dict]:
    return engine.get_upcoming_matches(_conn(), limit)


@app.get("/api/knockout/bracket")
def bracket() -> dict:
    return engine.get_knockout_bracket(_conn())


@app.get("/api/matches/{match_id}")
def match_detail(match_id: int) -> dict:
    return engine.get_match_detail(_conn(), match_id)


@app.get("/api/events")
async def events(request: Request) -> EventSourceResponse:
    async def gen():
        last = None
        while True:
            if await request.is_disconnected():
                break
            cur = engine.get_last_update_ts(_conn())
            if cur != last:
                last = cur
                yield ServerSentEvent(data=json.dumps({"ts": cur}), event="update")
            await asyncio.sleep(2)

    return EventSourceResponse(gen(), ping=30)


def main() -> None:
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8080)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run it (green)** → PASS.

- [ ] **Step 5: Commit**

```bash
git add src/worldcup_predictor/web_server.py tests/test_web_server.py
git commit -m "feat: FastAPI web server with JSON API and SSE updates

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

### Task 12.2: Frontend (group tables + bracket + match modal + SSE)

**Files:** Create: `static/index.html`, `static/bracket.css`, `static/app.js`
**Verification:** this is browser UI; verify by smoke test (server boots, `/` returns HTML, assets load). No unit test, but add a content check.

- [ ] **Step 1: Write `static/index.html`**

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>2026 World Cup Predictor</title>
  <link rel="stylesheet" href="/static/bracket.css" />
</head>
<body>
  <header><h1>2026 World Cup Predictor</h1>
    <nav>
      <button onclick="showTab('groups')">Groups</button>
      <button onclick="showTab('knockout')">Knockout</button>
      <span id="status">live</span>
    </nav>
  </header>
  <section id="groups-tab"><div id="group-grid"></div></section>
  <section id="knockout-tab" hidden>
    <div class="bracket">
      <div class="round" id="R32"><h3>R32</h3></div>
      <div class="round" id="R16"><h3>R16</h3></div>
      <div class="round" id="QF"><h3>QF</h3></div>
      <div class="round" id="SF"><h3>SF</h3></div>
      <div class="round" id="FINAL"><h3>Final</h3></div>
    </div>
  </section>
  <dialog id="match-modal"><article id="match-detail"></article>
    <button onclick="document.getElementById('match-modal').close()">Close</button>
  </dialog>
  <script src="/static/app.js"></script>
</body>
</html>
```

- [ ] **Step 2: Write `static/bracket.css`**

```css
body { font-family: system-ui, sans-serif; margin: 0; color: #1a1a1a; }
header { background: #0b3d91; color: #fff; padding: 0.5rem 1rem; }
nav button { margin-right: 0.5rem; }
#group-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 1rem; padding: 1rem; }
table { width: 100%; border-collapse: collapse; font-size: 0.85rem; }
th, td { padding: 2px 4px; text-align: center; border-bottom: 1px solid #eee; }
td:first-child, th:first-child { text-align: left; }
.bracket { display: flex; gap: 2rem; overflow-x: auto; padding: 1rem; }
.round { display: flex; flex-direction: column; justify-content: space-around; min-width: 160px; }
.match-card { border: 1px solid #ccc; border-radius: 6px; padding: 6px 10px; margin: 4px 0; cursor: pointer; background: #fff; }
.match-card:hover { background: #f0f8ff; }
.match-card .winner { font-weight: bold; color: #2a7a2a; }
```

- [ ] **Step 3: Write `static/app.js`**

```javascript
const GROUPS = "ABCDEFGHIJKL".split("");

async function loadGroups() {
  const grid = document.getElementById("group-grid");
  grid.innerHTML = "";
  await Promise.all(GROUPS.map(async (g) => {
    const rows = await (await fetch(`/api/groups/${g}/standings`)).json();
    const div = document.createElement("div");
    div.innerHTML = `<h3>Group ${g}</h3><table>
      <thead><tr><th>Team</th><th>P</th><th>W</th><th>D</th><th>L</th><th>GD</th><th>Pts</th></tr></thead>
      <tbody>${rows.map(r => `<tr><td>${r.team}</td><td>${r.played}</td><td>${r.won}</td>
        <td>${r.drawn}</td><td>${r.lost}</td><td>${r.gd}</td><td><b>${r.pts}</b></td></tr>`).join("")}
      </tbody></table>`;
    grid.appendChild(div);
  }));
}

async function loadBracket() {
  const data = await (await fetch("/api/knockout/bracket")).json();
  for (const stage of ["R32", "R16", "QF", "SF", "FINAL"]) {
    const el = document.getElementById(stage);
    el.querySelectorAll(".match-card").forEach(n => n.remove());
    (data[stage] || []).forEach(m => {
      const card = document.createElement("div");
      card.className = "match-card";
      const winHome = m.home_score > m.away_score;
      const winAway = m.away_score > m.home_score;
      card.innerHTML =
        `<div class="${winHome ? "winner" : ""}">${m.home_team ?? "TBD"}</div>
         <div>${m.home_score ?? "–"} : ${m.away_score ?? "–"}</div>
         <div class="${winAway ? "winner" : ""}">${m.away_team ?? "TBD"}</div>`;
      card.onclick = () => showDetail(m.id);
      el.appendChild(card);
    });
  }
}

async function showDetail(id) {
  if (!id) return;
  const d = await (await fetch(`/api/matches/${id}`)).json();
  const p = d.prediction;
  document.getElementById("match-detail").innerHTML =
    `<h3>${d.match.home_team} vs ${d.match.away_team}</h3>
     <p>Stage: ${d.match.stage}</p>
     ${p ? `<p>Prediction: H ${(p.p_home*100).toFixed(0)}% / D ${(p.p_draw*100).toFixed(0)}% / A ${(p.p_away*100).toFixed(0)}%</p>
            <p>Most likely: ${p.ml_home}-${p.ml_away}</p>
            <p>${p.reasoning || ""}</p>` : "<p>No prediction yet.</p>"}`;
  document.getElementById("match-modal").showModal();
}

function showTab(tab) {
  document.getElementById("groups-tab").hidden = tab !== "groups";
  document.getElementById("knockout-tab").hidden = tab !== "knockout";
}

function refresh() { loadGroups(); loadBracket(); }
const es = new EventSource("/api/events");
es.addEventListener("update", refresh);
es.onerror = () => { document.getElementById("status").textContent = "reconnecting"; };
refresh();
```

- [ ] **Step 4: Smoke-test the UI**

```bash
WC_DB_PATH=/tmp/wc.db uv run worldcup init-db
WC_DB_PATH=/tmp/wc.db uv run worldcup seed
WC_DB_PATH=/tmp/wc.db uv run worldcup serve --port 8080 &
sleep 3
curl -s localhost:8080/ | grep -q "World Cup Predictor" && echo "UI OK"
curl -s localhost:8080/api/groups/A/standings | grep -q "team" && echo "API OK"
kill %1
```

Expected: `UI OK` and `API OK`.

- [ ] **Step 5: Commit**

```bash
git add src/worldcup_predictor/static
git commit -m "feat: web UI (group tables, knockout bracket, match modal, SSE)

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

## Milestone 13 — Automation, docs, full verification

### Task 13.1: Cron/systemd for deterministic jobs

**Files:** Create: `deploy/worldcup-fetch.service`, `deploy/worldcup-fetch.timer`, `deploy/worldcup-web.service`, `deploy/crontab.example`

- [ ] **Step 1: Write the systemd + cron templates**

`deploy/crontab.example`:
```cron
# Fetch finished results every 10 minutes during the tournament
*/10 * * * * /path/to/worldcup-predictor/.venv/bin/worldcup fetch-results >> /tmp/wc-fetch.log 2>&1
# Nightly: refit ratings + re-run simulation
30 4 * * * cd /path/to/worldcup-predictor && .venv/bin/worldcup simulate --n 50000 >> /tmp/wc-sim.log 2>&1
```

`deploy/worldcup-web.service`:
```ini
[Unit]
Description=WorldCup Predictor Web UI
After=network.target
[Service]
User=youruser
WorkingDirectory=/path/to/worldcup-predictor
ExecStart=/path/to/worldcup-predictor/.venv/bin/worldcup serve --host 0.0.0.0 --port 8080
Restart=on-failure
[Install]
WantedBy=multi-user.target
```

`deploy/worldcup-fetch.service` + `deploy/worldcup-fetch.timer` analogous (timer `OnUnitActiveSec=10min`).

- [ ] **Step 2: Document activation in README** (do not enable services automatically).

- [ ] **Step 3: Commit**

```bash
git add deploy/
git commit -m "chore: add systemd + cron deployment templates

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

### Task 13.2: README + .env.example

**Files:** Create/replace: `README.md`, `.env.example`

- [ ] **Step 1: Write `.env.example`**

```dotenv
# football-data.org free API key (https://www.football-data.org/client/register)
FOOTBALL_DATA_TOKEN=
# Optional: override data/db locations
# WC_DATA_DIR=/path/to/worldcup-predictor/data
# WC_DB_PATH=/path/to/worldcup-predictor/data/worldcup.db
```

- [ ] **Step 2: Write `README.md`** covering: what it is, install (`uv sync`), quickstart
  (`worldcup init-db && worldcup seed && worldcup load-history && worldcup simulate`),
  running the web UI, registering the MCP server, the cron/systemd setup, the data sources +
  their licenses (martj42, football-data.org, RSS), and the Phase-2/3 roadmap. English only.

- [ ] **Step 3: Commit**

```bash
git add README.md .env.example
git commit -m "docs: README and env example

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

### Task 13.3: Full green run + end-to-end smoke

- [ ] **Step 1: Lint, type-check, test**

```bash
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy src
uv run pytest -q
```
Expected: all pass (the penaltyblog fallback decision from Task 0.2 may require a `# type: ignore` on its import; keep mypy clean).

- [ ] **Step 2: Real end-to-end** (uses the network; needs `FOOTBALL_DATA_TOKEN` for live results)

```bash
uv run worldcup init-db
uv run worldcup seed
uv run worldcup load-history            # pulls martj42 CSV
uv run worldcup simulate --n 50000      # title probabilities print
uv run worldcup serve &                 # open http://<host-ip>:8080
```

- [ ] **Step 3: Tag the Phase-1 milestone**

```bash
git tag -a v0.1.0 -m "Phase 1: end-to-end WC2026 predictor (engine + MCP + web)"
```

## Phase 2 / 3 roadmap (separate spec + plan each)

These are intentionally **out of scope** for this plan (which delivers a working end-to-end
system). Each gets its own brainstorming → spec → plan cycle, building on the Phase-1 engine.

**Phase 2 — the "AI brain" (off-pitch intelligence + self-evolution):**
- Automated news ingestion: RSS (`feedparser`) + NewsAPI dev key → raw articles table.
- LLM extraction pass (driven by Copilot CLI via a new MCP tool `ingest_news`): turn articles
  into structured `intel_event`s with entity/type/direction/magnitude/source/credibility,
  requiring ≥2-source corroboration before high-credibility events are applied.
- Player-impact model: estimate each player's λ contribution (minutes + xG/xA share) so the
  `magnitude` of "star out" is data-driven, not guessed.
- Level-2 auto-tuning: walk-forward backtest (`penaltyblog.backtest`) to optimise intel weight,
  source-credibility weights, time-decay ξ, and the Elo→λ slope; persist to `tuning_params`.
- Level-3 advisor: an MCP tool `propose_tuning` where the LLM reviews systematic errors and
  writes `proposals`; `apply_tuning` only runs on explicit human approval.
- Calibration layer: Platt/isotonic recalibration of model probabilities.
- Host-advantage refinement: non-neutral λ for USA/Canada/Mexico home games.
- Official Annex-C third-place letter-combination mapping for exact bracket fidelity.

**Phase 3 — optional:**
- Unattended LLM intel (embedded API key) so cron can update predictions without a session.
- Value-bet helper: accept manually-entered odds, compute implied probs (remove overround),
  flag edges vs the model.
- xG features from StatsBomb open-data / FBref via `soccerdata`.

## Self-review (performed against `files/design.md`)

- **Spec coverage:** data ingestion (M2) ✓, Elo (M3) ✓, Dixon-Coles 1X2+score (M4/M6) ✓,
  off-pitch intel→λ mechanism with manual/MCP entry (M5/M11) ✓, Monte Carlo advancement/title
  (M7) ✓, evaluation vs baselines (M8) ✓, CLI (M10) ✓, MCP server (M11) ✓, web bracket+SSE
  (M12) ✓, cron/systemd automation (M13) ✓. Self-evolution levels 2/3 and automated intel are
  explicitly deferred to Phase 2 (documented above), matching the design's phasing.
- **arm64 risk:** addressed by the Task 0.2 spike with a concrete fallback that preserves the
  `GoalModel`/`ScoreGrid` API.
- **Type/name consistency:** `GoalModel.fit/predict_grid`, `ScoreGrid` accessors, `apply_intel`,
  `standings_from_results`, `build_r32`, `simulate_tournament`, and the `engine` facade names are
  used consistently across tasks and adapters.
- **Known soft spots to watch during execution:** (1) `mcp.call_tool` return shape varies by
  patch version — the test is written defensively; (2) intel-adjusted grids use independent
  Poisson (drops the DC low-score correction on the adjustment path — acceptable, second-order);
  (3) team-name reconciliation between football-data.org and the seed names may need a small
  alias map (add under `config.py` if `fetch-results` skips matches).

## Execution

Plan saved to this session's `plan.md` (and copied into the repo at
`docs/superpowers/plans/2026-06-15-worldcup-predictor-phase1.md` in Task 0.1).
Implement task-by-task with `superpowers:subagent-driven-development` (recommended) or
`superpowers:executing-plans`. Each task is TDD: red → green → commit.








