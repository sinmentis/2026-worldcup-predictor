# Knockout Bracket Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ingest the World Cup knockout bracket from the data feed, predict every match (advance % + likely score) all the way to the final, and render it as a single-direction left→right tree with 中英文 + FIFA rank.

**Architecture:** The football-data feed already returns all 104 matches with stages and fills in real teams as each round is decided. We ingest the knockout matches into the existing `matches` table (keyed by the feed's `ext_id`), forward-project our predicted winners into not-yet-decided slots using a fixed bracket topology, predict each match with the existing goal model, and serve it to a new tree UI. Manual fetch/update; real results override predictions.

**Tech Stack:** Python 3.12 (uv-managed), SQLite, FastAPI, httpx, vanilla JS/CSS. Existing modules: `ingest`, `db`, `predict`, `engine`, `simulate`, `web_server`, `static/app.js`.

## Global Constraints

- Python 3.12, uv-managed: run everything via `uv run ...` (the venv has **no pip**).
- Quality bar (run before every commit): `uv run ruff check src/ tests/`, `uv run ruff format src/ tests/`, `uv run mypy src/`, `uv run pytest -q`.
- mypy runs `--strict` (configured in `pyproject.toml`): full type annotations on every new function.
- 100-char line limit, double quotes (ruff-enforced).
- Conventional Commits; append trailer `Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>` to every commit.
- Anti-fabrication: never invent match results — only ingest what the feed provides.
- `WC_DB_PATH=data/worldcup.db` is the prod DB; tests use `tmp_path` DBs, never the prod DB.
- Knockout matches are played at neutral venues → predict with `neutral=True` (the model already strips WC home advantage).

---

### Task 1: Add `ext_id` + `winner_team` columns to `matches`

**Files:**
- Modify: `src/worldcup_predictor/db.py` (the `SCHEMA` string's `matches` table + `migrate()`)
- Test: `tests/test_db.py`

**Interfaces:**
- Produces: `matches` rows gain nullable `ext_id INTEGER` (feed match id, stable upsert key for knockout rows) and `winner_team TEXT` (decisive winner for penalty-settled knockouts). `db.migrate(conn)` adds both idempotently on existing DBs.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_db.py`:

```python
def test_migrate_adds_knockout_columns(tmp_path):
    from worldcup_predictor import db

    conn = db.connect(tmp_path / "m.db")
    # Simulate a pre-migration matches table without the new columns.
    conn.executescript(
        "CREATE TABLE matches (id INTEGER PRIMARY KEY, stage TEXT NOT NULL, "
        "group_id TEXT, slot TEXT, home_team TEXT, away_team TEXT, kickoff TEXT, "
        "neutral INTEGER DEFAULT 1, home_score INTEGER, away_score INTEGER, "
        "status TEXT DEFAULT 'SCHEDULED');"
    )
    conn.commit()
    assert not db._has_column(conn, "matches", "ext_id")

    db.migrate(conn)

    assert db._has_column(conn, "matches", "ext_id")
    assert db._has_column(conn, "matches", "winner_team")
    # Idempotent: running again is a no-op (must not raise).
    db.migrate(conn)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_db.py::test_migrate_adds_knockout_columns -v`
Expected: FAIL (`ext_id` column not added by `migrate`).

- [ ] **Step 3: Implement the migration**

In `src/worldcup_predictor/db.py`, find the `matches` table definition inside the `SCHEMA` string and add the two columns so fresh DBs get them (add after the `status` line, keeping the trailing `)`):

```sql
    status TEXT DEFAULT 'SCHEDULED',  -- 'SCHEDULED' | 'FINISHED'
    ext_id INTEGER,                   -- football-data match id (knockout upsert key)
    winner_team TEXT                  -- decisive winner (penalty-settled knockouts)
```

Then in `migrate()`, after the existing `_AFFECTS_TABLES` loop and before `conn.commit()`, add:

```python
    has_matches = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='matches'"
    ).fetchone()
    if has_matches:
        if not _has_column(conn, "matches", "ext_id"):
            conn.execute("ALTER TABLE matches ADD COLUMN ext_id INTEGER")
        if not _has_column(conn, "matches", "winner_team"):
            conn.execute("ALTER TABLE matches ADD COLUMN winner_team TEXT")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_db.py -q`
Expected: PASS (all db tests, including the new one).

- [ ] **Step 5: Apply the migration to the prod DB and verify**

Run:
```bash
WC_DB_PATH=data/worldcup.db uv run python -c "from worldcup_predictor import db; import os; c=db.connect(os.environ['WC_DB_PATH']); db.init_schema(c); print('ext_id', db._has_column(c,'matches','ext_id')); print('winner_team', db._has_column(c,'matches','winner_team'))"
```
Expected: `ext_id True` and `winner_team True`.

- [ ] **Step 6: Lint, type, commit**

```bash
uv run ruff check src/ tests/ && uv run ruff format src/ tests/ && uv run mypy src/
git add src/worldcup_predictor/db.py tests/test_db.py
git commit -m "feat(db): add ext_id and winner_team columns to matches

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

---

### Task 2: Ingest knockout fixtures from the feed

**Files:**
- Modify: `src/worldcup_predictor/ingest.py` (`apply_fixtures_payload`, `apply_results_payload`, `fetch_fixtures`; add `_KNOCKOUT_STAGES`, `_STAGE_MAP`, `apply_knockout_fixtures`)
- Modify: `src/worldcup_predictor/cli.py` (`fetch-fixtures` echo)
- Test: `tests/test_knockout_ingest.py` (new)

**Interfaces:**
- Consumes: `db` schema with `ext_id`/`winner_team` (Task 1); `config.canonical_team`.
- Produces: `ingest.apply_knockout_fixtures(conn, payload) -> int` (upserts non-group matches by `ext_id`, returns count touched). `ingest._STAGE_MAP: dict[str, str]`. `fetch_fixtures` now returns `tuple[int, int]` = (group kickoffs set, knockout rows upserted).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_knockout_ingest.py`:

```python
from worldcup_predictor import db, ingest


def _conn(tmp_path):
    conn = db.connect(tmp_path / "k.db")
    db.init_schema(conn)
    ingest.seed_teams_and_fixtures(conn)
    return conn


def _ko(ext_id, stage, home, away, status="TIMED", hs=None, as_=None, winner=None):
    return {
        "id": ext_id,
        "stage": stage,
        "utcDate": "2026-06-28T19:00:00Z",
        "status": status,
        "homeTeam": {"name": home} if home else None,
        "awayTeam": {"name": away} if away else None,
        "score": {"winner": winner, "fullTime": {"home": hs, "away": as_}},
    }


def test_stage_map_covers_all_knockout_rounds():
    assert ingest._STAGE_MAP == {
        "GROUP_STAGE": "group", "LAST_32": "R32", "LAST_16": "R16",
        "QUARTER_FINALS": "QF", "SEMI_FINALS": "SF", "THIRD_PLACE": "3RD", "FINAL": "FINAL",
    }


def test_apply_knockout_inserts_with_real_teams(tmp_path):
    conn = _conn(tmp_path)
    n = ingest.apply_knockout_fixtures(conn, {"matches": [_ko(900, "LAST_32", "Spain", "Japan")]})
    assert n == 1
    row = conn.execute(
        "SELECT stage, home_team, away_team, kickoff, neutral, status, ext_id "
        "FROM matches WHERE ext_id=900"
    ).fetchone()
    assert row["stage"] == "R32"
    assert (row["home_team"], row["away_team"]) == ("Spain", "Japan")
    assert row["neutral"] == 1 and row["status"] == "SCHEDULED"


def test_apply_knockout_stores_null_teams_for_tbd(tmp_path):
    conn = _conn(tmp_path)
    ingest.apply_knockout_fixtures(conn, {"matches": [_ko(901, "SEMI_FINALS", None, None)]})
    row = conn.execute("SELECT stage, home_team, away_team FROM matches WHERE ext_id=901").fetchone()
    assert row["stage"] == "SF"
    assert row["home_team"] is None and row["away_team"] is None


def test_apply_knockout_is_idempotent_and_fills_in(tmp_path):
    conn = _conn(tmp_path)
    # First fetch: teams TBD.
    ingest.apply_knockout_fixtures(conn, {"matches": [_ko(902, "LAST_16", None, None)]})
    # Later fetch: teams now known + finished with a penalty winner on a 1-1 draw.
    ingest.apply_knockout_fixtures(
        conn,
        {"matches": [_ko(902, "LAST_16", "Brazil", "Croatia", status="FINISHED",
                         hs=1, as_=1, winner="AWAY_TEAM")]},
    )
    rows = conn.execute("SELECT * FROM matches WHERE ext_id=902").fetchall()
    assert len(rows) == 1  # updated in place, not duplicated
    r = rows[0]
    assert (r["home_team"], r["away_team"]) == ("Brazil", "Croatia")
    assert r["status"] == "FINISHED" and (r["home_score"], r["away_score"]) == (1, 1)
    assert r["winner_team"] == "Croatia"  # penalty winner from score.winner


def test_group_functions_ignore_knockout_matches(tmp_path):
    conn = _conn(tmp_path)
    # A knockout rematch of a real group pair must NOT overwrite the group row.
    grp = conn.execute(
        "SELECT home_team, away_team FROM matches WHERE stage='group' LIMIT 1"
    ).fetchone()
    payload = {"matches": [{
        "stage": "QUARTER_FINALS", "utcDate": "2026-07-09T19:00:00Z", "status": "FINISHED",
        "homeTeam": {"name": grp["home_team"]}, "awayTeam": {"name": grp["away_team"]},
        "score": {"winner": "HOME_TEAM", "fullTime": {"home": 3, "away": 0}},
    }]}
    ingest.apply_results_payload(conn, payload)  # group-only: must skip this knockout match
    g = conn.execute(
        "SELECT status FROM matches WHERE stage='group' AND home_team=? AND away_team=?",
        (grp["home_team"], grp["away_team"]),
    ).fetchone()
    assert g["status"] != "FINISHED"  # untouched by the knockout payload
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_knockout_ingest.py -q`
Expected: FAIL (`apply_knockout_fixtures` / `_STAGE_MAP` not defined).

- [ ] **Step 3: Implement ingestion**

In `src/worldcup_predictor/ingest.py`, add near the top (after the existing imports/`logger`):

```python
_STAGE_MAP: dict[str, str] = {
    "GROUP_STAGE": "group", "LAST_32": "R32", "LAST_16": "R16",
    "QUARTER_FINALS": "QF", "SEMI_FINALS": "SF", "THIRD_PLACE": "3RD", "FINAL": "FINAL",
}
_KNOCKOUT_STAGES: frozenset[str] = frozenset(
    s for s, v in _STAGE_MAP.items() if v != "group"
)


def _winner_team(score: dict[str, Any], home: str | None, away: str | None) -> str | None:
    w = score.get("winner")
    if w == "HOME_TEAM":
        return home
    if w == "AWAY_TEAM":
        return away
    return None


def apply_knockout_fixtures(conn: sqlite3.Connection, payload: dict[str, Any]) -> int:
    """Upsert knockout matches (R32..FINAL) from the feed, keyed by the feed match id.

    Teams are stored when the feed knows them (else NULL for not-yet-decided slots). Scores,
    status and the decisive winner are filled in once a match is FINISHED. Idempotent: a later
    fetch updates the same row in place.
    """
    touched = 0
    for m in payload.get("matches", []):
        stage = m.get("stage")
        if stage not in _KNOCKOUT_STAGES:
            continue
        ext_id = m.get("id")
        if ext_id is None:
            continue
        mapped = _STAGE_MAP[stage]
        ht = m.get("homeTeam") or {}
        at = m.get("awayTeam") or {}
        home = config.canonical_team(ht.get("name")) if ht.get("name") else None
        away = config.canonical_team(at.get("name")) if at.get("name") else None
        kickoff = m.get("utcDate")
        score = m.get("score") or {}
        ft = score.get("fullTime") or {}
        finished = m.get("status") == "FINISHED"
        hs = ft.get("home") if finished else None
        as_ = ft.get("away") if finished else None
        status = "FINISHED" if finished else "SCHEDULED"
        winner = _winner_team(score, home, away) if finished else None
        conn.execute(
            "INSERT INTO matches(stage, slot, group_id, home_team, away_team, kickoff, "
            " neutral, home_score, away_score, status, ext_id, winner_team) "
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?) "
            " ON CONFLICT(ext_id) DO UPDATE SET stage=excluded.stage, home_team=excluded.home_team,"
            " away_team=excluded.away_team, kickoff=excluded.kickoff, home_score=excluded.home_score,"
            " away_score=excluded.away_score, status=excluded.status, winner_team=excluded.winner_team",
            (mapped, None, None, home, away, kickoff, 1, hs, as_, status, ext_id, winner),
        )
        touched += 1
    conn.commit()
    if touched:
        _db.touch_update(conn)
    return touched
```

Note: the `ON CONFLICT(ext_id)` upsert needs a unique index on `ext_id`. Add it once near the top
of `apply_knockout_fixtures` (idempotent). SQLite treats every NULL as distinct, so a full unique
index still permits the many group rows that have `ext_id IS NULL`:

```python
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_matches_ext_id ON matches(ext_id)")
```

Place that line as the first statement inside `apply_knockout_fixtures` (before the loop).

Now guard the two group functions so knockout matches never reach their team-pair logic. In
`apply_fixtures_payload`, as the first line inside `for m in payload.get("matches", []):` add:

```python
        if m.get("stage") in _KNOCKOUT_STAGES:
            continue
```

Add the identical guard as the first line of the loop in `apply_results_payload`.

Finally, wire `fetch_fixtures` to ingest both and return both counts:

```python
def fetch_fixtures(conn: sqlite3.Connection, token: str | None = None) -> tuple[int, int]:
    """Fetch ALL WC fixtures; populate group kickoffs/results and knockout fixtures.

    Returns (group_kickoffs_set, knockout_rows_upserted).
    """
    token = token or os.environ.get("FOOTBALL_DATA_TOKEN", "")
    url = f"{config.FOOTBALL_DATA_BASE}/competitions/{config.FOOTBALL_DATA_COMP}/matches"
    headers = {"X-Auth-Token": token} if token else {}
    resp = httpx.get(url, headers=headers, timeout=60.0)
    resp.raise_for_status()
    payload = resp.json()
    groups = apply_fixtures_payload(conn, payload)
    knockout = apply_knockout_fixtures(conn, payload)
    return groups, knockout
```

- [ ] **Step 4: Update the CLI echo**

In `src/worldcup_predictor/cli.py`, change the `fetch-fixtures` command body to unpack the tuple:

```python
@app.command("fetch-fixtures")
def fetch_fixtures() -> None:
    """Fetch all WC fixtures and populate kickoff times, results, and knockout bracket."""
    conn = _conn()
    groups, knockout = ingest.fetch_fixtures(conn)
    typer.echo(f"Set kickoff on {groups} group fixtures; upserted {knockout} knockout fixtures.")
```

- [ ] **Step 5: Run tests + lint + type**

Run: `uv run pytest tests/test_knockout_ingest.py tests/test_fetch_results.py -q`
Expected: PASS (new knockout tests + existing fixture tests still green).

Run: `uv run ruff check src/ tests/ && uv run mypy src/`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
uv run ruff format src/ tests/
git add src/worldcup_predictor/ingest.py src/worldcup_predictor/cli.py tests/test_knockout_ingest.py
git commit -m "feat(ingest): upsert knockout fixtures from the feed by ext_id

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

---

### Task 3: Bracket topology + predicted-bracket builder

**Files:**
- Create: `src/worldcup_predictor/bracket.py`
- Test: `tests/test_bracket.py` (new)

**Interfaces:**
- Consumes: `predict.predict_match(conn, model, home, away, neutral=True)` → `MatchPrediction` with `p_home/p_draw/p_away/ml_home/ml_away/factors`; `goal_model.GoalModel`.
- Produces:
  - `bracket.advance_prob(p_home: float, p_draw: float, p_away: float) -> tuple[float, float]`
  - `bracket.build_predicted_bracket(conn: sqlite3.Connection, model: GoalModel) -> dict[str, Any]` returning the API shape `{rounds, third_place, real_fixtures, total_fixtures}` (see Task 4 for the exact field list).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_bracket.py`:

```python
import numpy as np
import pandas as pd

from worldcup_predictor import bracket, db, ingest
from worldcup_predictor.goal_model import GoalModel


def _model():
    rng = np.random.default_rng(7)
    rows = []
    for _ in range(80):
        rows.append(("2024-01-01", "Strong", "Weak", int(rng.integers(2, 5)), int(rng.integers(0, 2)), False))
        rows.append(("2024-01-01", "Weak", "Strong", int(rng.integers(0, 2)), int(rng.integers(2, 5)), False))
    df = pd.DataFrame(rows, columns=["date", "home_team", "away_team", "home_goals", "away_goals", "neutral"])
    return GoalModel().fit(df)


def _conn(tmp_path):
    conn = db.connect(tmp_path / "b.db")
    db.init_schema(conn)
    return conn


def test_advance_prob_sums_to_one_and_splits_draw():
    ah, aa = bracket.advance_prob(0.5, 0.2, 0.3)
    assert abs(ah + aa - 1.0) < 1e-9
    # Stronger 90' side takes a larger share of the draw → advances more often than its 90' win.
    assert ah > 0.5
    # Even match → coin flip on the draw share.
    eh, ea = bracket.advance_prob(0.4, 0.2, 0.4)
    assert abs(eh - 0.5) < 1e-9 and abs(ea - 0.5) < 1e-9


def test_build_uses_real_teams_and_predicts(tmp_path):
    conn = _conn(tmp_path)
    # Two R32 matches with known teams the model knows.
    ingest.apply_knockout_fixtures(conn, {"matches": [
        {"id": 1, "stage": "LAST_32", "utcDate": "2026-06-28T10:00:00Z", "status": "TIMED",
         "homeTeam": {"name": "Strong"}, "awayTeam": {"name": "Weak"}, "score": {}},
        {"id": 2, "stage": "LAST_32", "utcDate": "2026-06-28T14:00:00Z", "status": "TIMED",
         "homeTeam": {"name": "Weak"}, "awayTeam": {"name": "Strong"}, "score": {}},
        {"id": 3, "stage": "LAST_16", "utcDate": "2026-07-04T10:00:00Z", "status": "TIMED",
         "homeTeam": None, "awayTeam": None, "score": {}},
    ]})
    out = bracket.build_predicted_bracket(conn, _model())
    r32 = next(r for r in out["rounds"] if r["stage"] == "R32")
    m0 = r32["matches"][0]
    assert m0["home"] == "Strong" and m0["home_known"] and m0["away_known"]
    assert abs(m0["advance_home"] + m0["advance_away"] - 1.0) < 1e-9
    assert m0["advance_home"] > m0["advance_away"]  # Strong favoured
    # R16 match teams are projected from R32 predicted winners (Strong wins both R32 matches).
    r16 = next(r for r in out["rounds"] if r["stage"] == "R16")
    m16 = r16["matches"][0]
    assert m16["home"] == "Strong" and m16["away"] == "Strong"
    assert m16["home_known"] is False and m16["away_known"] is False
    assert out["total_fixtures"] == 3 and out["real_fixtures"] == 2


def test_actual_result_overrides_predicted_winner(tmp_path):
    conn = _conn(tmp_path)
    ingest.apply_knockout_fixtures(conn, {"matches": [
        # R32-1 FINISHED: Weak beat Strong on penalties (winner overrides the model's pick).
        {"id": 1, "stage": "LAST_32", "utcDate": "2026-06-28T10:00:00Z", "status": "FINISHED",
         "homeTeam": {"name": "Strong"}, "awayTeam": {"name": "Weak"},
         "score": {"winner": "AWAY_TEAM", "fullTime": {"home": 1, "away": 1}}},
        {"id": 2, "stage": "LAST_32", "utcDate": "2026-06-28T14:00:00Z", "status": "TIMED",
         "homeTeam": {"name": "Strong"}, "awayTeam": {"name": "Weak"}, "score": {}},
        {"id": 3, "stage": "LAST_16", "utcDate": "2026-07-04T10:00:00Z", "status": "TIMED",
         "homeTeam": None, "awayTeam": None, "score": {}},
    ]})
    out = bracket.build_predicted_bracket(conn, _model())
    r16 = next(r for r in out["rounds"] if r["stage"] == "R16")
    # R16-1 home comes from R32-1's ACTUAL winner (Weak), not the predicted (Strong).
    assert r16["matches"][0]["home"] == "Weak"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_bracket.py -q`
Expected: FAIL (`bracket` module not found).

- [ ] **Step 3: Implement `bracket.py`**

Create `src/worldcup_predictor/bracket.py`:

```python
from __future__ import annotations

import sqlite3
from typing import Any

from worldcup_predictor.goal_model import GoalModel
from worldcup_predictor.predict import predict_match

# Round order and Chinese labels for the knockout tree.
_ROUND_ORDER: list[str] = ["R32", "R16", "QF", "SF", "FINAL"]
_LABELS: dict[str, str] = {"R32": "32强", "R16": "16强", "QF": "八强", "SF": "四强", "FINAL": "决赛"}


def advance_prob(p_home: float, p_draw: float, p_away: float) -> tuple[float, float]:
    """Probability each side advances a knockout tie: 90' win + (draw → extra-time/shootout),
    where the draw is split by each side's regulation win share (a coin flip if even)."""
    denom = p_home + p_away
    share = p_home / denom if denom > 0 else 0.5
    adv_home = p_home + p_draw * share
    return adv_home, 1.0 - adv_home


def _load(conn: sqlite3.Connection) -> dict[str, list[sqlite3.Row]]:
    rows = conn.execute(
        "SELECT id, ext_id, stage, home_team, away_team, kickoff, home_score, away_score, "
        " status, winner_team FROM matches WHERE stage IN ('R32','R16','QF','SF','3RD','FINAL')"
    ).fetchall()
    by_stage: dict[str, list[sqlite3.Row]] = {}
    for r in rows:
        by_stage.setdefault(r["stage"], []).append(r)
    for stage, ms in by_stage.items():
        ms.sort(key=lambda r: (r["kickoff"] or "", r["ext_id"] or 0))
    return by_stage


def _decide(
    conn: sqlite3.Connection, model: GoalModel, home: str | None, away: str | None,
    row: sqlite3.Row,
) -> dict[str, Any]:
    """Build one match node: predict when both teams resolved; use the actual result when FINISHED."""
    node: dict[str, Any] = {
        "ext_id": row["ext_id"], "home": home, "away": away,
        "status": row["status"], "home_score": row["home_score"], "away_score": row["away_score"],
        "advance_home": None, "advance_away": None, "ml_home": None, "ml_away": None,
        "p_home": None, "p_draw": None, "p_away": None, "factors": [], "winner": None,
    }
    if row["status"] == "FINISHED":
        # Actual outcome: explicit penalty winner, else the higher score.
        if row["winner_team"]:
            node["winner"] = row["winner_team"]
        elif row["home_score"] is not None and row["away_score"] is not None:
            node["winner"] = home if row["home_score"] >= row["away_score"] else away
    if home is None or away is None:
        return node
    pred = predict_match(conn, model, home, away, neutral=True)
    ah, aa = advance_prob(pred.p_home, pred.p_draw, pred.p_away)
    node.update(
        advance_home=ah, advance_away=aa, ml_home=pred.ml_home, ml_away=pred.ml_away,
        p_home=pred.p_home, p_draw=pred.p_draw, p_away=pred.p_away,
        factors=[{"team": f.team, "description": f.description, "lambda_delta": f.lambda_delta}
                 for f in pred.factors],
    )
    if node["winner"] is None:  # not yet played → predicted winner drives downstream slots
        node["winner"] = home if ah >= aa else away
    return node


def build_predicted_bracket(conn: sqlite3.Connection, model: GoalModel) -> dict[str, Any]:
    """Compose the knockout tree: feed teams where known, predicted winners projected forward."""
    by_stage = _load(conn)
    rounds_out: list[dict[str, Any]] = []
    prev_winners: list[str | None] = []  # winners of the previous round, in slot order
    sf_losers: list[str | None] = []
    real = 0
    total = sum(len(by_stage.get(s, [])) for s in _ROUND_ORDER) + len(by_stage.get("3RD", []))

    for stage in _ROUND_ORDER:
        matches = by_stage.get(stage, [])
        out_matches: list[dict[str, Any]] = []
        winners: list[str | None] = []
        for k, row in enumerate(matches):
            home, away = row["home_team"], row["away_team"]
            home_known, away_known = home is not None, away is not None
            if stage != "R32":  # fill TBD sides from the previous round's winners
                if home is None and 2 * k < len(prev_winners):
                    home = prev_winners[2 * k]
                if away is None and 2 * k + 1 < len(prev_winners):
                    away = prev_winners[2 * k + 1]
            if home_known and away_known:
                real += 1
            node = _decide(conn, model, home, away, row)
            node["slot"] = f"{stage}-{k + 1}"
            node["home_known"] = home_known
            node["away_known"] = away_known
            out_matches.append(node)
            winners.append(node["winner"])
            if stage == "SF":  # track losers for the third-place match
                loser = away if node["winner"] == home else (home if node["winner"] else None)
                sf_losers.append(loser)
        rounds_out.append({"stage": stage, "label": _LABELS[stage], "matches": out_matches})
        prev_winners = winners

    third = None
    third_rows = by_stage.get("3RD", [])
    if third_rows:
        row = third_rows[0]
        h = row["home_team"] or (sf_losers[0] if len(sf_losers) > 0 else None)
        a = row["away_team"] or (sf_losers[1] if len(sf_losers) > 1 else None)
        if row["home_team"] is not None and row["away_team"] is not None:
            real += 1
        third = _decide(conn, model, h, a, row)
        third["slot"] = "3RD"
        third["home_known"] = row["home_team"] is not None
        third["away_known"] = row["away_team"] is not None

    return {"rounds": rounds_out, "third_place": third, "real_fixtures": real, "total_fixtures": total}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_bracket.py -q`
Expected: PASS.

- [ ] **Step 5: Lint, type, commit**

```bash
uv run ruff check src/ tests/ && uv run ruff format src/ tests/ && uv run mypy src/
git add src/worldcup_predictor/bracket.py tests/test_bracket.py
git commit -m "feat(bracket): predicted-bracket builder with forward projection

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

---

### Task 4: Engine facade + web route

**Files:**
- Modify: `src/worldcup_predictor/engine.py` (add `get_predicted_bracket`)
- Modify: `src/worldcup_predictor/web_server.py` (point `/api/knockout/bracket` at it)
- Test: `tests/test_engine_read.py`

**Interfaces:**
- Consumes: `bracket.build_predicted_bracket`, `engine.get_model`.
- Produces: `engine.get_predicted_bracket(conn) -> dict[str, Any]` (the shape from Task 3); `/api/knockout/bracket` returns it.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_engine_read.py`:

```python
def test_get_predicted_bracket_shape(tmp_path, monkeypatch):
    import numpy as np
    import pandas as pd

    from worldcup_predictor import engine as eng
    from worldcup_predictor import ingest
    from worldcup_predictor.goal_model import GoalModel

    conn = db.connect(tmp_path / "pb.db")
    db.init_schema(conn)
    ingest.apply_knockout_fixtures(conn, {"matches": [
        {"id": 1, "stage": "LAST_32", "utcDate": "2026-06-28T10:00:00Z", "status": "TIMED",
         "homeTeam": {"name": "Strong"}, "awayTeam": {"name": "Weak"}, "score": {}},
    ]})
    rng = np.random.default_rng(3)
    rows = []
    for _ in range(60):
        rows.append(("2024-01-01", "Strong", "Weak", int(rng.integers(2, 5)), 0, False))
        rows.append(("2024-01-01", "Weak", "Strong", 0, int(rng.integers(2, 5)), False))
    model = GoalModel().fit(pd.DataFrame(
        rows, columns=["date", "home_team", "away_team", "home_goals", "away_goals", "neutral"]))
    monkeypatch.setattr(eng, "get_model", lambda _c, refit=False: model)

    out = eng.get_predicted_bracket(conn)
    assert {"rounds", "third_place", "real_fixtures", "total_fixtures"} <= set(out)
    assert out["total_fixtures"] == 1 and out["real_fixtures"] == 1
    assert out["rounds"][0]["stage"] == "R32"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_engine_read.py::test_get_predicted_bracket_shape -v`
Expected: FAIL (`get_predicted_bracket` not defined).

- [ ] **Step 3: Implement the facade + route**

In `src/worldcup_predictor/engine.py`, add an import near the other module imports:

```python
from worldcup_predictor import bracket as _bracket
```

and add the function (near `get_knockout_bracket`):

```python
def get_predicted_bracket(conn: sqlite3.Connection) -> dict[str, Any]:
    """Knockout tree: real fixtures from the feed + our prediction for every match, with our
    predicted winners projected forward into not-yet-decided slots."""
    return _bracket.build_predicted_bracket(conn, get_model(conn))
```

In `src/worldcup_predictor/web_server.py`, change the bracket route to use it:

```python
@app.get("/api/knockout/bracket")
def bracket() -> dict[str, Any]:
    with closing(_conn()) as conn:
        return engine.get_predicted_bracket(conn)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_engine_read.py tests/test_web_server.py -q`
Expected: PASS.

- [ ] **Step 5: Lint, type, commit**

```bash
uv run ruff check src/ tests/ && uv run ruff format src/ tests/ && uv run mypy src/
git add src/worldcup_predictor/engine.py src/worldcup_predictor/web_server.py tests/test_engine_read.py
git commit -m "feat(engine): get_predicted_bracket facade + wire knockout route

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

---

### Task 5: Web UI — single-direction left→right tree

**Files:**
- Modify: `src/worldcup_predictor/static/app.js` (rewrite `loadBracket`; add node + detail rendering)
- Modify: `src/worldcup_predictor/static/styles.css` (tree + connector + node styles)
- Modify: `src/worldcup_predictor/static/index.html` (knockout tab heading)
- Reference: `src/worldcup_predictor/static/bracket-mockup.html` (the approved visual — match its structure/markup)

**Interfaces:**
- Consumes: `GET /api/knockout/bracket` (Task 4 shape); existing `zh()`, `rankBadge()`, `flag()`, `pct0()`, `esc()`, `STAGES` in `app.js`.
- Produces: the knockout tab renders the tree. No new exported JS API.

- [ ] **Step 1: Port the bracket CSS**

Copy the bracket/tree/connector/node/`.modal` CSS rules from `static/bracket-mockup.html`'s `<style>` block into `static/styles.css`, namespacing each selector under a `#bracket` ancestor (e.g. `.bracket` → `#bracket .bracket`, `.match` → `#bracket .match`, `.modal` → `#bracket-modal`) so they don't collide with existing site styles. Map the mockup's CSS variables to the site's existing ones already in `styles.css` (`--bg`, `--accent`, `--win`, `--gold`, `--muted`, `--line`, `--text`).

- [ ] **Step 2: Rewrite `loadBracket` to render the tree**

In `static/app.js`, replace the body of `loadBracket()` (currently fetching `/api/bracket-projection`) with a renderer for `/api/knockout/bracket`. Reuse the mockup's `node`/`teamRow`/connector markup, but source `zh`/`rankBadge`/`flag` from the existing `app.js` helpers (do not redefine them). Skeleton:

```javascript
function bracketTeamRow(name, advPct, isWin) {
  const en = name ? `<span class="en">${esc(name)}</span>` : "";
  const nm = name ? `${zh(name)} ${rankBadge(name)}` : "待定";
  const fl = name ? flag(name) : "🏳️";
  const p = advPct == null ? "" : `${Math.round(advPct * 100)}%`;
  return `<div class="trow ${isWin ? "win" : ""}"><span class="flag">${fl}</span>
    <span class="names"><span class="zh">${nm}</span>${en}</span><span class="pct">${p}</span></div>`;
}

function bracketNode(m) {
  const proj = !(m.home_known && m.away_known);
  const winHome = m.winner && m.winner === m.home;
  const winAway = m.winner && m.winner === m.away;
  const badge = m.status === "FINISHED" ? `<span class="badge done">已赛</span>`
    : proj ? `<span class="badge proj">推测</span>` : `<span class="badge real">真实</span>`;
  const score = m.status === "FINISHED" && m.home_score != null
    ? `比分 ${m.home_score}-${m.away_score}`
    : (m.ml_home != null ? `预测 ${m.ml_home}-${m.ml_away}` : "");
  return `<div class="match ${proj ? "proj" : ""}" data-node='${esc(JSON.stringify(m))}'>
    ${bracketTeamRow(m.home, m.advance_home, winHome)}
    ${bracketTeamRow(m.away, m.advance_away, winAway)}
    <div class="foot"><span class="score">${score}</span>${badge}</div></div>`;
}

async function loadBracket() {
  const el = document.getElementById("bracket");
  const data = await (await fetch("/api/knockout/bracket")).json();
  if (!data.rounds || data.total_fixtures === 0) {
    el.innerHTML = `<p class="muted">淘汰赛对阵将在小组赛结束后生成。</p>`;
    return;
  }
  const col = (label, matches) => {
    const slots = matches.map((m, i) =>
      `<div class="slot ${i % 2 ? "bot" : "top"}">${bracketNode(m)}</div>`).join("");
    return `<div class="round"><div class="rlabel">${esc(label)}</div>${slots}</div>`;
  };
  const finalRound = data.rounds.find((r) => r.stage === "FINAL");
  const finalHtml = finalRound && finalRound.matches.length
    ? `<div class="round final"><div class="rlabel">&nbsp;</div><div class="slot"><div>
         <div class="final-card"><div class="rlabel">决赛 FINAL</div><div class="trophy">🏆</div>
           ${bracketNode(finalRound.matches[0])}</div>
         ${data.third_place ? `<div class="third-card"><div class="rlabel">季军赛 3RD</div>
           ${bracketNode(data.third_place)}</div>` : ""}
       </div></div></div>`
    : "";
  const cols = data.rounds.filter((r) => r.stage !== "FINAL")
    .map((r) => col(`${r.stage} · ${r.label}`, r.matches)).join("");
  el.innerHTML = `<div class="bracket-counter">真实赛程 <b>${data.real_fixtures}</b> / ${data.total_fixtures}</div>
    <div class="bracket">${cols}${finalHtml}</div>
    <div id="bracket-modal" class="modal" onclick="if(event.target===this)closeBracketModal()">
      <div class="sheet" id="bracket-sheet"></div></div>`;
  el.querySelectorAll(".match").forEach((n) =>
    n.addEventListener("click", () => openBracketModal(JSON.parse(n.dataset.node))));
}

function openBracketModal(m) {
  const an = m.home ? zh(m.home) : "待定", bn = m.away ? zh(m.away) : "待定";
  const sheet = document.getElementById("bracket-sheet");
  const probs = m.p_home == null ? "" : `<div class="bar">
    <div class="h" style="width:${Math.round(m.p_home * 100)}%">${an} ${Math.round(m.p_home * 100)}%</div>
    <div class="d" style="width:${Math.round(m.p_draw * 100)}%">平 ${Math.round(m.p_draw * 100)}%</div>
    <div class="a" style="width:${Math.round(m.p_away * 100)}%">${Math.round(m.p_away * 100)}% ${bn}</div></div>`;
  const adv = m.advance_home == null ? "" :
    `<div class="kv"><span class="muted">晋级概率</span><span><b>${an} ${Math.round(m.advance_home*100)}%</b> · ${bn} ${Math.round(m.advance_away*100)}%</span></div>`;
  const fac = (m.factors || []).map((f) =>
    `<li>· ${esc(zh(f.team))}: ${esc(f.description)} (Δλ=${f.lambda_delta.toFixed(2)})</li>`).join("");
  const note = (m.home_known && m.away_known) ? "" :
    `<div class="adv">⚠ 推测对阵：球队由上一轮预测的胜者推演，真实结果出来后会更新。</div>`;
  sheet.innerHTML = `<h3>${an} <span class="muted">vs</span> ${bn}</h3>${probs}${adv}
    ${m.ml_home != null ? `<div class="kv"><span class="muted">最可能比分</span><span>${m.ml_home}-${m.ml_away}</span></div>` : ""}
    ${fac ? `<div class="factors"><div class="muted">情报因素</div><ul>${fac}</ul></div>` : ""}
    ${note}<button class="close" onclick="closeBracketModal()">关闭</button>`;
  document.getElementById("bracket-modal").classList.add("on");
}
function closeBracketModal() { document.getElementById("bracket-modal").classList.remove("on"); }
```

Keep `loadBracket` registered in the existing tab→loader map (it already is: `knockout: loadBracket`).

- [ ] **Step 3: Update the knockout tab heading**

In `static/index.html`, change the knockout section heading so it reflects the predicted bracket:

```html
    <section id="knockout-tab" hidden>
      <h2>淘汰赛树状图 <small>（真实对阵 + 我的预测，一路推到决赛）</small></h2>
      <div id="bracket"></div>
    </section>
```

- [ ] **Step 4: Verify visually against the live server**

Restart the local service and load the knockout tab (the prod DB has no knockout rows yet, so first confirm the empty-state message, then ingest live fixtures and re-check):

```bash
export XDG_RUNTIME_DIR="/run/user/$(id -u)"
systemctl --user restart worldcup.service && sleep 5
curl -s localhost:8080/api/knockout/bracket | python3 -c "import sys,json;d=json.load(sys.stdin);print('total',d.get('total_fixtures'),'real',d.get('real_fixtures'))"
```
Expected: a JSON object with `total_fixtures`/`real_fixtures` (0/0 until `fetch-fixtures` has ingested knockout rows). Then run `WC_DB_PATH=data/worldcup.db uv run worldcup fetch-fixtures`, restart, and confirm the count rises and the tab renders the tree (open https://worldcup.shunlyu.com and click 淘汰赛).

- [ ] **Step 5: Run the JS-affecting tests + lint/type, then commit**

Run: `uv run pytest tests/test_web_server.py -q`
Expected: PASS (web smoke tests still green).

```bash
uv run ruff check src/ tests/ && uv run mypy src/
git add src/worldcup_predictor/static/app.js src/worldcup_predictor/static/styles.css src/worldcup_predictor/static/index.html
git commit -m "feat(web): single-direction knockout bracket tree

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

---

### Task 6: `worldcup bracket` CLI + end-to-end verification

**Files:**
- Modify: `src/worldcup_predictor/cli.py` (add `bracket` command)
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `engine.get_predicted_bracket`.
- Produces: `worldcup bracket` prints the predicted bracket round-by-round.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_cli.py` (follow the file's existing CliRunner pattern):

```python
def test_bracket_command_empty(tmp_path, monkeypatch):
    from typer.testing import CliRunner
    from worldcup_predictor import cli, db, ingest

    monkeypatch.setenv("WC_DB_PATH", str(tmp_path / "c.db"))
    conn = db.connect(tmp_path / "c.db")
    db.init_schema(conn)
    ingest.seed_teams_and_fixtures(conn)
    conn.close()
    result = CliRunner().invoke(cli.app, ["bracket"])
    assert result.exit_code == 0
    assert "No knockout fixtures" in result.stdout
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cli.py::test_bracket_command_empty -v`
Expected: FAIL (no `bracket` command).

- [ ] **Step 3: Implement the CLI command**

In `src/worldcup_predictor/cli.py`, add:

```python
@app.command("bracket")
def bracket() -> None:
    """Print the predicted knockout bracket (real fixtures + projected winners to the final)."""
    conn = _conn()
    data = engine.get_predicted_bracket(conn)
    if not data["total_fixtures"]:
        typer.echo("No knockout fixtures yet (group stage still in progress).")
        return
    typer.echo(f"Knockout bracket — real {data['real_fixtures']}/{data['total_fixtures']}")
    for rnd in data["rounds"]:
        typer.echo(f"\n[{rnd['stage']}]")
        for m in rnd["matches"]:
            h, a = m["home"] or "TBD", m["away"] or "TBD"
            tag = "FINISHED" if m["status"] == "FINISHED" else ("real" if m["home_known"] and m["away_known"] else "proj")
            ah = f"{round(m['advance_home'] * 100)}%" if m["advance_home"] is not None else "-"
            typer.echo(f"  {h} vs {a}  [{tag}] adv_home={ah}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_cli.py::test_bracket_command_empty -v`
Expected: PASS.

- [ ] **Step 5: Full suite + quality gate**

Run: `uv run pytest -q && uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/ && uv run mypy src/`
Expected: all PASS / clean.

- [ ] **Step 6: End-to-end on the prod DB (manual verification)**

```bash
cd /home/shunlyu/work/website/worldcup-predictor && export WC_DB_PATH=data/worldcup.db
uv run worldcup fetch-fixtures      # ingests knockout skeleton + any decided teams
uv run worldcup bracket | head -30  # prints the bracket; real count > 0 once R32 teams populate
```
Expected: knockout count rises; the bracket prints with real fixtures where teams are known and `proj` where not.

- [ ] **Step 7: Commit**

```bash
git add src/worldcup_predictor/cli.py tests/test_cli.py
git commit -m "feat(cli): worldcup bracket command

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

---

## Post-implementation

- **Remove the throwaway mockup**: `git rm`-or-`rm src/worldcup_predictor/static/bracket-mockup.html` is not tracked, so just delete it: `rm src/worldcup_predictor/static/bracket-mockup.html` once the real tab is live.
- **Topology validation (the spec's flagged risk):** once the feed populates R32 teams (~June 28), confirm the within-stage ordering `(kickoff, ext_id)` matches the FIFA bracket (i.e. R16-k really is fed by R32-(2k-1)/(2k)). If the feed orders differently, adjust the sort/feeder mapping in `bracket._load` / `build_predicted_bracket`. Spot-check by comparing a printed `worldcup bracket` R16 projection against the official bracket.
- **Deploy:** `systemctl --user restart worldcup.service` (then the ~2 min model warm window applies to live predictions, including the bracket).
