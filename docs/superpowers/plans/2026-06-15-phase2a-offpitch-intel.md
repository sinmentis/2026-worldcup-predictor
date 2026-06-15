# Phase 2a — Automated Off-Pitch Intelligence — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Automatically turn football news into structured, source-cited, credibility-weighted player statuses that adjust the model's expected goals — with a confidence/corroboration trust gate and a human review queue.

**Architecture:** A deterministic RSS fetcher stores raw articles in SQLite (no LLM, cron-able). In a Copilot CLI session, the LLM reads raw articles via MCP tools and upserts state-based player statuses (one current row per `(team, player)`). `intel.apply_intel` (same name/signature/import path) is reimplemented to sum the new player-status factor plus the legacy `intel_events` factor, so `predict`/`simulate` are unchanged. A tier×status `MAGNITUDE_TABLE` turns the LLM's classification into the number; a trust gate auto-applies trustworthy items and queues weak ones.

**Tech Stack:** Python 3.12, `uv`, `feedparser` (already a dep), stdlib `sqlite3`/`json`/`datetime`, `mcp` FastMCP, `typer` CLI, `pytest`/`ruff`/`mypy --strict`.

**Companion spec:** `docs/superpowers/specs/2026-06-15-phase2a-offpitch-intel-design.md` (read it alongside this plan).

---

## Conventions (every task)

- Work DIRECTLY in `/home/shunlyu/work/worldcup-predictor` on `master`. Do NOT create a worktree/branch.
- TDD: failing test → run red → implement → run green → commit. Conventional Commits + trailer
  `Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>`.
- `uv run pytest -q`, `uv run ruff check src tests`, `uv run ruff format src tests`, `uv run mypy src` (strict) — all green before each commit.
- All code/identifiers/comments in English. Build EXACTLY what's specified (YAGNI).
- Tests must not hit the network (use fixtures / direct payloads).

## Module map (lock these names/signatures)

```
src/worldcup_predictor/
  config.py        # ADD: RSS_FEEDS, LAMBDA_MIN, ADJUST_CLAMP (moved here), MAGNITUDE_TABLE lives in player_status
  db.py            # ADD: news_articles, player_status tables to SCHEMA
  news.py          # NEW: parse_feed_text(), dedup, store_articles(), fetch_news()
  player_status.py # NEW: MAGNITUDE_TABLE, status_mult(), derive_credibility(), upsert_status(),
                   #      team_status_factor(), list_pending(), approve(), reject(), purge_expired()
  intel.py         # MODIFY apply_intel(): sum legacy events + player_status.team_status_factor()
  engine.py        # ADD: fetch_news, get_unprocessed_news, upsert_player_status, mark_news_processed,
                   #      list_pending_intel, approve_intel, reject_intel
  mcp_server.py    # ADD tools: get_unprocessed_news, upsert_player_status, mark_news_processed,
                   #      list_pending_intel, approve_intel, reject_intel
  cli.py           # ADD commands: fetch-news, intel-pending, intel-approve, intel-reject
```

## Milestone 2a.1 — Data model + RSS news fetcher

### Task 1: Schema for `news_articles` and `player_status`

**Files:** Modify `src/worldcup_predictor/db.py` (the `SCHEMA` string) · Test `tests/test_db.py`

- [ ] **Step 1: Add to `tests/test_db.py`** (extend `test_init_schema_creates_tables`'s expected set or add a new test)

```python
def test_phase2a_tables_exist(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.init_schema(conn)
    names = {
        r[0]
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert {"news_articles", "player_status"} <= names
    # player_status enforces one current row per (team, player)
    conn.execute(
        "INSERT INTO player_status(team,player,tier,status,credibility,sources,as_of,pending)"
        " VALUES ('France','X','key','out',0.9,'[]',0,0)"
    )
    import sqlite3
    import pytest

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO player_status(team,player,tier,status,credibility,sources,as_of,pending)"
            " VALUES ('France','X','key','out',0.9,'[]',0,0)"
        )
```

- [ ] **Step 2: Run red** — `uv run pytest tests/test_db.py::test_phase2a_tables_exist -v` → FAIL.

- [ ] **Step 3: Append these two tables to the `SCHEMA` string in `db.py`** (before the closing `"""`)

```sql
CREATE TABLE IF NOT EXISTS news_articles (
    id INTEGER PRIMARY KEY,
    source TEXT,
    url TEXT UNIQUE,
    title TEXT,
    summary TEXT,
    published_at TEXT,
    fetched_at REAL,
    processed INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS player_status (
    id INTEGER PRIMARY KEY,
    team TEXT NOT NULL,
    player TEXT NOT NULL,
    tier TEXT NOT NULL,
    status TEXT NOT NULL,
    credibility REAL NOT NULL,
    sources TEXT NOT NULL,
    official INTEGER DEFAULT 0,
    valid_until TEXT,
    as_of REAL NOT NULL,
    pending INTEGER DEFAULT 0,
    notes TEXT,
    UNIQUE(team, player)
);
```

- [ ] **Step 4: Run green** — `uv run pytest tests/test_db.py -v` → PASS.

- [ ] **Step 5: Commit**

```bash
git add src/worldcup_predictor/db.py tests/test_db.py
git commit -m "feat: add news_articles and player_status tables

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

### Task 2: Config — RSS feeds + relocate clamp constants

**Files:** Modify `src/worldcup_predictor/config.py` · Test `tests/test_config.py`
**Why:** `player_status.py` needs the clamp constants but must not import `intel.py` (that would be a
cycle, since `intel.py` will import `player_status`). Move `LAMBDA_MIN`/`ADJUST_CLAMP` to `config`.

- [ ] **Step 1: Add to `tests/test_config.py`**

```python
def test_phase2a_config():
    from worldcup_predictor import config

    assert config.LAMBDA_MIN == 0.05
    assert config.ADJUST_CLAMP == (-0.6, 0.6)
    assert len(config.RSS_FEEDS) >= 3
    assert all(u.startswith("http") for u in config.RSS_FEEDS.values())
```

- [ ] **Step 2: Run red** → FAIL.

- [ ] **Step 3: Append to `config.py`**

```python
# Off-pitch intel tuning (Phase 2a). ADJUST_CLAMP bounds the net per-team lambda multiplier delta.
LAMBDA_MIN = 0.05
ADJUST_CLAMP = (-0.6, 0.6)

# Free RSS news feeds for off-pitch intelligence (no API key needed).
RSS_FEEDS = {
    "BBC Sport": "https://feeds.bbci.co.uk/sport/football/rss.xml",
    "Sky Sports": "https://www.skysports.com/rss/12040",
    "Guardian Football": "https://www.theguardian.com/football/rss",
    "ESPN Soccer": "https://www.espn.com/espn/rss/soccer/news",
}
```

- [ ] **Step 4: Update `src/worldcup_predictor/intel.py`** to use the config constants (keep behavior identical):

Replace the module-level constants
```python
LAMBDA_MIN = 0.05
ADJUST_CLAMP = (-0.6, 0.6)  # bound the net multiplier delta per team
```
with
```python
from worldcup_predictor.config import ADJUST_CLAMP, LAMBDA_MIN  # noqa: F401  (re-exported for back-compat)
```
(Place the import with the other imports; `intel.py` already imports from `worldcup_predictor`. The
`# noqa: F401` keeps them importable as `intel.LAMBDA_MIN` if anything referenced them.)

- [ ] **Step 5: Run green** — `uv run pytest tests/test_config.py tests/test_intel.py -q` → PASS (intel tests unchanged in behavior).

- [ ] **Step 6: Commit**

```bash
git add src/worldcup_predictor/config.py src/worldcup_predictor/intel.py tests/test_config.py
git commit -m "feat: add RSS feeds config; relocate intel clamp constants to config

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

### Task 3: `news.py` — parse + store articles (no network)

**Files:** Create `src/worldcup_predictor/news.py` · Test `tests/test_news.py`

- [ ] **Step 1: Write `tests/test_news.py`**

```python
from worldcup_predictor import db, news

RSS = """<?xml version="1.0"?>
<rss version="2.0"><channel>
  <item><title>France star ruled out of World Cup with injury</title>
    <description>Key forward will miss the tournament.</description>
    <link>https://example.com/a1</link><pubDate>Mon, 15 Jun 2026 10:00:00 GMT</pubDate></item>
  <item><title>Brazil name unchanged squad</title>
    <description>No changes.</description>
    <link>https://example.com/a2</link><pubDate>Mon, 15 Jun 2026 11:00:00 GMT</pubDate></item>
</channel></rss>"""


def test_parse_feed_text_returns_items():
    items = news.parse_feed_text("BBC Sport", RSS)
    assert len(items) == 2
    assert items[0]["title"].startswith("France star")
    assert items[0]["url"] == "https://example.com/a1"
    assert items[0]["source"] == "BBC Sport"


def test_store_articles_dedups_by_url(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.init_schema(conn)
    items = news.parse_feed_text("BBC Sport", RSS)
    assert news.store_articles(conn, items) == 2
    assert news.store_articles(conn, items) == 0  # same URLs => no duplicates
    assert conn.execute("SELECT COUNT(*) FROM news_articles").fetchone()[0] == 2
```

- [ ] **Step 2: Run red** → FAIL.

- [ ] **Step 3: Implement `src/worldcup_predictor/news.py`**

```python
from __future__ import annotations

import sqlite3
import time
from typing import Any

import feedparser

from worldcup_predictor import config


def parse_feed_text(source: str, text: str) -> list[dict[str, Any]]:
    parsed = feedparser.parse(text)
    items: list[dict[str, Any]] = []
    for e in parsed.entries:
        url = e.get("link")
        if not url:
            continue
        items.append(
            {
                "source": source,
                "url": url,
                "title": e.get("title", ""),
                "summary": e.get("summary", e.get("description", "")),
                "published_at": e.get("published", ""),
            }
        )
    return items


def store_articles(conn: sqlite3.Connection, items: list[dict[str, Any]]) -> int:
    count = 0
    for it in items:
        cur = conn.execute(
            "INSERT OR IGNORE INTO news_articles"
            "(source, url, title, summary, published_at, fetched_at, processed)"
            " VALUES (?,?,?,?,?,?,0)",
            (it["source"], it["url"], it["title"], it["summary"], it["published_at"], time.time()),
        )
        count += cur.rowcount
    conn.commit()
    return count


def fetch_news(conn: sqlite3.Connection) -> int:
    """Fetch all configured RSS feeds and store new articles. Per-feed failures are skipped."""
    import httpx

    total = 0
    for source, url in config.RSS_FEEDS.items():
        try:
            resp = httpx.get(url, timeout=30.0, follow_redirects=True)
            resp.raise_for_status()
            total += store_articles(conn, parse_feed_text(source, resp.text))
        except Exception:  # noqa: BLE001 - one bad feed must not break the run
            continue
    return total
```

- [ ] **Step 4: Run green** — `uv run pytest tests/test_news.py -q` → PASS.

- [ ] **Step 5: Commit**

```bash
git add src/worldcup_predictor/news.py tests/test_news.py
git commit -m "feat: RSS news fetcher (parse, store, url-dedup)

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

### Task 4: `fetch-news` CLI command

**Files:** Modify `src/worldcup_predictor/cli.py` · Test `tests/test_cli.py`
**Note:** the command calls `news.fetch_news` (network); the test only checks the command is wired
and exits cleanly when feeds are unreachable (per-feed failures are swallowed, returns 0).

- [ ] **Step 1: Add to `tests/test_cli.py`**

```python
def test_fetch_news_command_wired(tmp_path, monkeypatch):
    monkeypatch.setenv("WC_DB_PATH", str(tmp_path / "cli.db"))
    # Point all feeds at an unreachable host so the command runs offline and returns 0.
    from worldcup_predictor import config

    monkeypatch.setattr(config, "RSS_FEEDS", {"x": "http://127.0.0.1:0/none.xml"})
    runner.invoke(app, ["init-db"])
    res = runner.invoke(app, ["fetch-news"])
    assert res.exit_code == 0
    assert "0" in res.stdout
```

- [ ] **Step 2: Run red** → FAIL.

- [ ] **Step 3: Add the command to `cli.py`** (import `news` at top: `from worldcup_predictor import ... news ...`)

```python
@app.command("fetch-news")
def fetch_news() -> None:
    """Fetch configured RSS feeds and store new articles (cron-friendly)."""
    conn = _conn()
    n = news.fetch_news(conn)
    typer.echo(f"Stored {n} new articles.")
```

- [ ] **Step 4: Run green** — `uv run pytest tests/test_cli.py -q` → PASS.

- [ ] **Step 5: Commit**

```bash
git add src/worldcup_predictor/cli.py tests/test_cli.py
git commit -m "feat: add 'worldcup fetch-news' CLI command

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

## Milestone 2a.2 — Player-status store + reimplemented `apply_intel`

### Task 5: Magnitude table + pure helpers

**Files:** Create `src/worldcup_predictor/player_status.py` · Test `tests/test_player_status.py`

- [ ] **Step 1: Write `tests/test_player_status.py`**

```python
from worldcup_predictor import player_status as ps


def test_status_mult():
    assert ps.status_mult("key", "out") == 0.72
    assert ps.status_mult("fringe", "doubtful") == 0.98
    assert ps.status_mult("key", "available") == 1.0  # unknown pair => no effect
    assert ps.status_mult("nonsense", "out") == 1.0


def test_derive_credibility():
    assert ps.derive_credibility(1, official=False) == 0.50
    assert ps.derive_credibility(2, official=False) == 0.80
    assert ps.derive_credibility(1, official=True) == 0.95
```

- [ ] **Step 2: Run red** → FAIL.

- [ ] **Step 3: Create `src/worldcup_predictor/player_status.py`**

```python
from __future__ import annotations

import json
import sqlite3
import time
from datetime import date, datetime, timedelta

from worldcup_predictor import config
from worldcup_predictor.config import ADJUST_CLAMP, LAMBDA_MIN  # noqa: F401
from worldcup_predictor.models import IntelFactor

TIERS = {"key", "regular", "fringe"}
STATUSES = {"out", "doubtful", "suspended", "available"}

MAGNITUDE_TABLE: dict[tuple[str, str], float] = {
    ("key", "out"): 0.72, ("key", "suspended"): 0.72, ("key", "doubtful"): 0.88,
    ("regular", "out"): 0.85, ("regular", "suspended"): 0.85, ("regular", "doubtful"): 0.93,
    ("fringe", "out"): 0.96, ("fringe", "suspended"): 0.96, ("fringe", "doubtful"): 0.98,
}

ACTIVE_CRED_THRESHOLD = 0.70
ACTIVE_CONF_THRESHOLD = 0.60
DEFAULT_EXPIRY_DAYS = 14


def status_mult(tier: str, status: str) -> float:
    return MAGNITUDE_TABLE.get((tier, status), 1.0)


def derive_credibility(n_sources: int, official: bool) -> float:
    if official:
        return 0.95
    if n_sources >= 2:
        return 0.80
    return 0.50
```

- [ ] **Step 4: Run green** — `uv run pytest tests/test_player_status.py -q` → PASS.

- [ ] **Step 5: Commit**

```bash
git add src/worldcup_predictor/player_status.py tests/test_player_status.py
git commit -m "feat: player_status magnitude table and credibility helpers

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

### Task 6: `upsert_status` — state machine + trust gate + expiry

**Files:** Modify `src/worldcup_predictor/player_status.py` · Test `tests/test_player_status.py`

- [ ] **Step 1: Add tests**

```python
from worldcup_predictor import db


def _conn(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.init_schema(conn)
    return conn


def test_single_source_is_pending(tmp_path):
    conn = _conn(tmp_path)
    out = ps.upsert_status(conn, "France", "Star", "key", "out", 0.9, "https://a")
    assert out["status"] == "pending"  # single non-official source => credibility 0.5 < 0.70
    row = conn.execute("SELECT pending, credibility FROM player_status").fetchone()
    assert row["pending"] == 1
    assert row["credibility"] == 0.50


def test_second_source_corroborates_to_active(tmp_path):
    conn = _conn(tmp_path)
    ps.upsert_status(conn, "France", "Star", "key", "out", 0.9, "https://a")
    out = ps.upsert_status(conn, "France", "Star", "key", "out", 0.9, "https://b")
    assert out["status"] == "active"
    row = conn.execute("SELECT pending, credibility, sources FROM player_status").fetchone()
    assert row["pending"] == 0
    assert row["credibility"] == 0.80
    assert len(json.loads(row["sources"])) == 2  # one row, two sources (no stacking)


def test_official_source_is_active_immediately(tmp_path):
    conn = _conn(tmp_path)
    out = ps.upsert_status(conn, "Spain", "Keeper", "key", "out", 0.9, "https://fed", official=True)
    assert out["status"] == "active"
    assert conn.execute("SELECT credibility FROM player_status").fetchone()[0] == 0.95


def test_available_clears_the_row(tmp_path):
    conn = _conn(tmp_path)
    ps.upsert_status(conn, "Brazil", "Striker", "key", "out", 0.9, "https://a", official=True)
    ps.upsert_status(conn, "Brazil", "Striker", "key", "available", 0.9, "https://b")
    assert conn.execute("SELECT COUNT(*) FROM player_status WHERE team='Brazil'").fetchone()[0] == 0


def test_upsert_validates_inputs(tmp_path):
    conn = _conn(tmp_path)
    import pytest

    with pytest.raises(ValueError):
        ps.upsert_status(conn, "France", "X", "superstar", "out", 0.9, "https://a")
    with pytest.raises(ValueError):
        ps.upsert_status(conn, "France", "X", "key", "out", 0.9, "")
```

- [ ] **Step 2: Run red** → FAIL.

- [ ] **Step 3: Append to `player_status.py`**

```python
def _default_valid_until(conn: sqlite3.Connection, team: str) -> str:
    row = conn.execute(
        "SELECT MIN(kickoff) FROM matches WHERE status='SCHEDULED' AND kickoff IS NOT NULL "
        "AND (home_team=? OR away_team=?)",
        (team, team),
    ).fetchone()
    if row and row[0]:
        try:
            d = datetime.fromisoformat(str(row[0]).replace("Z", "+00:00")).date()
            return (d + timedelta(days=1)).isoformat()
        except ValueError:
            pass
    return (date.today() + timedelta(days=DEFAULT_EXPIRY_DAYS)).isoformat()


def upsert_status(
    conn: sqlite3.Connection,
    team: str,
    player: str,
    tier: str,
    status: str,
    confidence: float,
    source_url: str,
    official: bool = False,
    notes: str | None = None,
    valid_until: str | None = None,
) -> dict[str, object]:
    team = config.canonical_team(team)
    if tier not in TIERS:
        raise ValueError(f"tier must be one of {sorted(TIERS)}")
    if status not in STATUSES:
        raise ValueError(f"status must be one of {sorted(STATUSES)}")
    if not 0.0 <= confidence <= 1.0:
        raise ValueError("confidence must be in [0, 1]")
    if not source_url:
        raise ValueError("source_url is required; intel must be traceable")

    if status == "available":
        conn.execute("DELETE FROM player_status WHERE team=? AND player=?", (team, player))
        conn.commit()
        return {"status": "cleared", "team": team, "player": player}

    row = conn.execute(
        "SELECT sources, official FROM player_status WHERE team=? AND player=?", (team, player)
    ).fetchone()
    sources: list[str] = json.loads(row["sources"]) if row else []
    if source_url not in sources:
        sources.append(source_url)
    official_ever = official or bool(row["official"]) if row else official
    cred = derive_credibility(len(sources), official_ever)
    pending = 0 if (cred >= ACTIVE_CRED_THRESHOLD and confidence >= ACTIVE_CONF_THRESHOLD) else 1
    if valid_until is None:
        valid_until = _default_valid_until(conn, team)

    conn.execute(
        "INSERT INTO player_status"
        "(team,player,tier,status,credibility,sources,official,valid_until,as_of,pending,notes)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?)"
        " ON CONFLICT(team,player) DO UPDATE SET"
        " tier=excluded.tier, status=excluded.status, credibility=excluded.credibility,"
        " sources=excluded.sources, official=excluded.official, valid_until=excluded.valid_until,"
        " as_of=excluded.as_of, pending=excluded.pending, notes=excluded.notes",
        (
            team, player, tier, status, cred, json.dumps(sources), int(official_ever),
            valid_until, time.time(), pending, notes,
        ),
    )
    conn.commit()
    return {
        "status": "active" if pending == 0 else "pending",
        "credibility": cred,
        "team": team,
        "player": player,
    }
```

- [ ] **Step 4: Run green** — `uv run pytest tests/test_player_status.py -q` → PASS.

- [ ] **Step 5: Commit**

```bash
git add src/worldcup_predictor/player_status.py tests/test_player_status.py
git commit -m "feat: player_status upsert with trust gate, corroboration, expiry

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

### Task 7: `team_status_factor` + reimplement `intel.apply_intel`

**Files:** Modify `src/worldcup_predictor/player_status.py`, `src/worldcup_predictor/intel.py`
· Test `tests/test_player_status.py`, `tests/test_intel.py`

- [ ] **Step 1: Add tests**

```python
# tests/test_player_status.py
def test_team_status_factor_weakens_team(tmp_path):
    conn = _conn(tmp_path)
    ps.upsert_status(conn, "France", "Star", "key", "out", 0.9, "https://fed", official=True)
    delta, factors = ps.team_status_factor(conn, "France")
    # key/out mult 0.72, credibility 0.95 => delta = 0.95 * (0.72 - 1) = -0.266
    assert abs(delta - (0.95 * (0.72 - 1.0))) < 1e-9
    assert len(factors) == 1


def test_team_status_factor_ignores_pending(tmp_path):
    conn = _conn(tmp_path)
    ps.upsert_status(conn, "France", "X", "key", "out", 0.9, "https://a")  # single => pending
    delta, factors = ps.team_status_factor(conn, "France")
    assert delta == 0.0
    assert factors == []
```

```python
# tests/test_intel.py  (apply_intel now also reads player_status; legacy still works)
def test_apply_intel_includes_player_status(tmp_path):
    from worldcup_predictor import db, player_status
    from worldcup_predictor import intel as intel_mod

    conn = db.connect(tmp_path / "t.db")
    db.init_schema(conn)
    player_status.upsert_status(conn, "France", "Star", "key", "out", 0.9, "https://fed", official=True)
    lh, la, factors = intel_mod.apply_intel(2.0, 1.0, home="France", away="Iraq", conn=conn)
    assert lh < 2.0
    assert la == 1.0
    assert any(f.team == "France" for f in factors)
```

- [ ] **Step 2: Run red** → FAIL.

- [ ] **Step 3a: Append `team_status_factor` to `player_status.py`**

```python
def team_status_factor(
    conn: sqlite3.Connection, team: str
) -> tuple[float, list[IntelFactor]]:
    team = config.canonical_team(team)
    today = date.today().isoformat()
    rows = conn.execute(
        "SELECT player, tier, status, credibility FROM player_status "
        "WHERE team=? AND pending=0 AND (valid_until IS NULL OR valid_until >= ?)",
        (team, today),
    ).fetchall()
    delta = 0.0
    factors: list[IntelFactor] = []
    for r in rows:
        contrib = float(r["credibility"]) * (status_mult(r["tier"], r["status"]) - 1.0)
        delta += contrib
        factors.append(
            IntelFactor(
                team=team,
                description=f"{r['player']}: {r['status']} ({r['tier']})",
                lambda_delta=contrib,
            )
        )
    lo, hi = ADJUST_CLAMP
    return max(lo, min(hi, delta)), factors
```

- [ ] **Step 3b: Reimplement `apply_intel` in `intel.py`** (keep `_team_factor`, `record_intel`,
  `active_intel_for` exactly as they are). Add `from worldcup_predictor import player_status` to the
  imports, and replace the existing `apply_intel` body with:

```python
def apply_intel(
    lam_h: float, lam_a: float, home: str, away: str, conn: sqlite3.Connection
) -> tuple[float, float, list[IntelFactor]]:
    dh_e, fh_e = _team_factor(conn, home)        # legacy intel_events (manual overrides)
    da_e, fa_e = _team_factor(conn, away)
    dh_s, fh_s = player_status.team_status_factor(conn, home)  # state-based statuses
    da_s, fa_s = player_status.team_status_factor(conn, away)
    lo, hi = ADJUST_CLAMP
    dh = max(lo, min(hi, dh_e + dh_s))
    da = max(lo, min(hi, da_e + da_s))
    lam_h = max(LAMBDA_MIN, lam_h * (1 + dh))
    lam_a = max(LAMBDA_MIN, lam_a * (1 + da))
    return lam_h, lam_a, fh_e + fh_s + fa_e + fa_s
```

- [ ] **Step 4: Run green** — `uv run pytest tests/test_player_status.py tests/test_intel.py tests/test_predict.py -q` → PASS (legacy intel tests + new status tests both green).

- [ ] **Step 5: Commit**

```bash
git add src/worldcup_predictor/player_status.py src/worldcup_predictor/intel.py tests/test_player_status.py tests/test_intel.py
git commit -m "feat: apply_intel reads player_status factor plus legacy events

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

### Task 8: Pending review + expiry purge

**Files:** Modify `src/worldcup_predictor/player_status.py` · Test `tests/test_player_status.py`

- [ ] **Step 1: Add tests**

```python
def test_list_approve_reject_pending(tmp_path):
    conn = _conn(tmp_path)
    ps.upsert_status(conn, "France", "X", "key", "out", 0.9, "https://a")  # pending
    pend = ps.list_pending(conn)
    assert len(pend) == 1
    sid = pend[0]["id"]
    ps.approve(conn, sid)
    assert conn.execute("SELECT pending FROM player_status WHERE id=?", (sid,)).fetchone()[0] == 0
    ps.reject(conn, sid)
    assert conn.execute("SELECT COUNT(*) FROM player_status WHERE id=?", (sid,)).fetchone()[0] == 0


def test_purge_expired(tmp_path):
    conn = _conn(tmp_path)
    ps.upsert_status(
        conn, "France", "X", "key", "out", 0.9, "https://fed", official=True,
        valid_until="2000-01-01",
    )
    assert ps.purge_expired(conn) == 1
    assert conn.execute("SELECT COUNT(*) FROM player_status").fetchone()[0] == 0
```

- [ ] **Step 2: Run red** → FAIL.

- [ ] **Step 3: Append to `player_status.py`**

```python
def list_pending(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM player_status WHERE pending=1 ORDER BY as_of DESC"
    ).fetchall()


def approve(conn: sqlite3.Connection, status_id: int) -> None:
    cur = conn.execute("UPDATE player_status SET pending=0 WHERE id=?", (status_id,))
    if cur.rowcount == 0:
        raise ValueError(f"No pending status with id {status_id}")
    conn.commit()


def reject(conn: sqlite3.Connection, status_id: int) -> None:
    cur = conn.execute("DELETE FROM player_status WHERE id=?", (status_id,))
    if cur.rowcount == 0:
        raise ValueError(f"No status with id {status_id}")
    conn.commit()


def purge_expired(conn: sqlite3.Connection) -> int:
    today = date.today().isoformat()
    cur = conn.execute(
        "DELETE FROM player_status WHERE valid_until IS NOT NULL AND valid_until < ?", (today,)
    )
    conn.commit()
    return cur.rowcount
```

- [ ] **Step 4: Run green** → PASS.

- [ ] **Step 5: Commit**

```bash
git add src/worldcup_predictor/player_status.py tests/test_player_status.py
git commit -m "feat: player_status pending review (list/approve/reject) and expiry purge

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

## Milestone 2a.3 — Engine facade + MCP tools + CLI

### Task 9: Engine facade for news + statuses

**Files:** Modify `src/worldcup_predictor/engine.py` · Test `tests/test_engine_intel.py` (new)

- [ ] **Step 1: Write `tests/test_engine_intel.py`**

```python
from worldcup_predictor import db, engine, news

RSS = """<?xml version="1.0"?><rss version="2.0"><channel>
  <item><title>t1</title><description>d1</description><link>https://x/1</link></item>
</channel></rss>"""


def _conn(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.init_schema(conn)
    return conn


def test_get_and_mark_news(tmp_path):
    conn = _conn(tmp_path)
    news.store_articles(conn, news.parse_feed_text("BBC Sport", RSS))
    items = engine.get_unprocessed_news(conn, limit=10)
    assert len(items) == 1 and items[0]["url"] == "https://x/1"
    assert engine.mark_news_processed(conn, [items[0]["id"]]) == 1
    assert engine.get_unprocessed_news(conn, limit=10) == []


def test_upsert_and_pending_flow(tmp_path):
    conn = _conn(tmp_path)
    out = engine.upsert_player_status(
        conn, team="France", player="X", tier="key", status="out",
        confidence=0.9, source_url="https://a",
    )
    assert out["status"] == "pending"
    pend = engine.list_pending_intel(conn)
    assert len(pend) == 1
    engine.approve_intel(conn, pend[0]["id"])
    assert engine.list_pending_intel(conn) == []
```

- [ ] **Step 2: Run red** → FAIL.

- [ ] **Step 3: Add to `engine.py`** (imports: `from worldcup_predictor import news as _news`, `from worldcup_predictor import player_status as _ps`)

```python
def fetch_news(conn: sqlite3.Connection) -> int:
    n = _news.fetch_news(conn)
    db.touch_update(conn)
    return n


def get_unprocessed_news(conn: sqlite3.Connection, limit: int = 20) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT id, source, url, title, summary, published_at FROM news_articles "
        "WHERE processed=0 ORDER BY id LIMIT ?",
        (max(1, min(limit, 100)),),
    ).fetchall()
    return [dict(r) for r in rows]


def mark_news_processed(conn: sqlite3.Connection, ids: list[int]) -> int:
    for i in ids:
        conn.execute("UPDATE news_articles SET processed=1 WHERE id=?", (i,))
    conn.commit()
    return len(ids)


def upsert_player_status(conn: sqlite3.Connection, **kwargs: Any) -> dict[str, object]:
    out = _ps.upsert_status(conn, **kwargs)
    db.touch_update(conn)
    return out


def list_pending_intel(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return [dict(r) for r in _ps.list_pending(conn)]


def approve_intel(conn: sqlite3.Connection, status_id: int) -> None:
    _ps.approve(conn, status_id)
    db.touch_update(conn)


def reject_intel(conn: sqlite3.Connection, status_id: int) -> None:
    _ps.reject(conn, status_id)
    db.touch_update(conn)
```

- [ ] **Step 4: Run green** → PASS.

- [ ] **Step 5: Commit**

```bash
git add src/worldcup_predictor/engine.py tests/test_engine_intel.py
git commit -m "feat: engine facade for news ingestion and player-status review

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

### Task 10: MCP tools for in-session extraction

**Files:** Modify `src/worldcup_predictor/mcp_server.py` · Test `tests/test_mcp_server.py`

- [ ] **Step 1: Add a test**

```python
@pytest.mark.asyncio
async def test_intel_tools_registered():
    tools = await mcp_server.mcp.list_tools()
    names = {t.name for t in tools}
    assert {
        "get_unprocessed_news", "upsert_player_status", "mark_news_processed",
        "list_pending_intel", "approve_intel", "reject_intel",
    } <= names
```

- [ ] **Step 2: Run red** → FAIL.

- [ ] **Step 3: Add tools to `mcp_server.py`** (after the existing tools; `TIERS`/`STATUSES` come from `player_status`)

```python
from worldcup_predictor import player_status as _ps


@mcp.tool()
def get_unprocessed_news(limit: int = 20) -> list[dict]:
    """Return raw news articles not yet processed, for off-pitch intel extraction."""
    return engine.get_unprocessed_news(_conn(), limit)


@mcp.tool()
def upsert_player_status(
    team: str,
    player: str,
    tier: str,
    status: str,
    confidence: float,
    source_url: str,
    official: bool = False,
    notes: str = "",
) -> dict:
    """Record/update a player's current status from news, adjusting that team's expected goals.

    tier: 'key' | 'regular' | 'fringe' (you judge importance from the article).
    status: 'out' | 'doubtful' | 'suspended' | 'available' ('available' clears a prior status).
    confidence in [0,1]; ALWAYS pass a real source_url. official=True only for club/federation sources.
    High confidence AND (>=2 sources OR official) applies immediately; otherwise it is queued for review.
    """
    if tier not in _ps.TIERS:
        raise ToolError(f"tier must be one of {sorted(_ps.TIERS)}")
    if status not in _ps.STATUSES:
        raise ToolError(f"status must be one of {sorted(_ps.STATUSES)}")
    if not source_url:
        raise ToolError("source_url is required; intel must be traceable.")
    if not 0.0 <= confidence <= 1.0:
        raise ToolError("confidence must be in [0,1].")
    return engine.upsert_player_status(
        _conn(), team=team, player=player, tier=tier, status=status,
        confidence=confidence, source_url=source_url, official=official,
        notes=notes or None,
    )


@mcp.tool()
def mark_news_processed(article_ids: list[int]) -> dict[str, int]:
    """Mark news articles as processed so they are not re-extracted."""
    return {"processed": engine.mark_news_processed(_conn(), article_ids)}


@mcp.tool()
def list_pending_intel() -> list[dict]:
    """List player-status items awaiting human approval (low-confidence / single-source)."""
    return engine.list_pending_intel(_conn())


@mcp.tool()
def approve_intel(status_id: int) -> dict[str, str]:
    """Approve a pending player-status item so it starts affecting predictions."""
    engine.approve_intel(_conn(), status_id)
    return {"status": "approved", "id": str(status_id)}


@mcp.tool()
def reject_intel(status_id: int) -> dict[str, str]:
    """Reject (delete) a pending or active player-status item."""
    engine.reject_intel(_conn(), status_id)
    return {"status": "rejected", "id": str(status_id)}
```

- [ ] **Step 4: Run green** — `uv run pytest tests/test_mcp_server.py -q` → PASS.

- [ ] **Step 5: Commit**

```bash
git add src/worldcup_predictor/mcp_server.py tests/test_mcp_server.py
git commit -m "feat: MCP tools for news extraction and player-status review

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

### Task 11: CLI for the pending queue

**Files:** Modify `src/worldcup_predictor/cli.py` · Test `tests/test_cli.py`

- [ ] **Step 1: Add a test**

```python
def test_intel_pending_and_approve(tmp_path, monkeypatch):
    monkeypatch.setenv("WC_DB_PATH", str(tmp_path / "cli.db"))
    runner.invoke(app, ["init-db"])
    from worldcup_predictor import db, engine

    conn = db.connect(tmp_path / "cli.db")
    engine.upsert_player_status(
        conn, team="France", player="X", tier="key", status="out",
        confidence=0.9, source_url="https://a",
    )
    res = runner.invoke(app, ["intel-pending"])
    assert res.exit_code == 0
    assert "France" in res.stdout
    sid = engine.list_pending_intel(conn)[0]["id"]
    assert runner.invoke(app, ["intel-approve", str(sid)]).exit_code == 0
```

- [ ] **Step 2: Run red** → FAIL.

- [ ] **Step 3: Add commands to `cli.py`**

```python
@app.command("intel-pending")
def intel_pending() -> None:
    """List player-status items awaiting approval."""
    conn = _conn()
    for r in engine.list_pending_intel(conn):
        typer.echo(
            f"[{r['id']}] {r['team']} - {r['player']} {r['status']} ({r['tier']}) "
            f"cred={r['credibility']:.2f} sources={r['sources']}"
        )


@app.command("intel-approve")
def intel_approve(status_id: int) -> None:
    """Approve a pending player-status item."""
    engine.approve_intel(_conn(), status_id)
    typer.echo(f"Approved {status_id}.")


@app.command("intel-reject")
def intel_reject(status_id: int) -> None:
    """Reject (delete) a player-status item."""
    engine.reject_intel(_conn(), status_id)
    typer.echo(f"Rejected {status_id}.")
```

- [ ] **Step 4: Run green** → PASS.

- [ ] **Step 5: Commit**

```bash
git add src/worldcup_predictor/cli.py tests/test_cli.py
git commit -m "feat: CLI intel-pending/approve/reject commands

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

## Milestone 2a.4 — End-to-end + docs

### Task 12: Integration — a news-driven status shifts a real prediction

**Files:** Test `tests/test_intel_e2e.py` (new) — no production code, just an end-to-end assertion.

- [ ] **Step 1: Write `tests/test_intel_e2e.py`**

```python
from worldcup_predictor import db, engine, ingest

HISTORY = """date,home_team,away_team,home_score,away_score,tournament,city,country,neutral
2024-01-01,France,Senegal,2,0,Friendly,X,Y,True
2024-02-01,Senegal,France,0,2,Friendly,X,Y,True
2024-03-01,France,Norway,3,1,Friendly,X,Y,True
2024-04-01,Senegal,Norway,1,1,Friendly,X,Y,True
2024-05-01,France,Iraq,4,0,Friendly,X,Y,True
2024-06-01,Senegal,Iraq,2,0,Friendly,X,Y,True
2024-07-01,Norway,Iraq,1,0,Friendly,X,Y,True
2024-08-01,Norway,France,0,2,Friendly,X,Y,True
"""


def test_active_status_shifts_prediction_and_pending_does_not(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.init_schema(conn)
    ingest.seed_teams_and_fixtures(conn)
    ingest.load_history_from_text(conn, HISTORY)

    mid = conn.execute(
        "SELECT id FROM matches WHERE home_team='France' AND away_team='Senegal'"
    ).fetchone()[0]

    base = engine.predict_fixture(conn, mid)["p_home"]

    # A single-source rumour is queued (pending) and must NOT move the prediction.
    engine.upsert_player_status(
        conn, team="France", player="Star", tier="key", status="out",
        confidence=0.9, source_url="https://rumour",
    )
    assert engine.predict_fixture(conn, mid)["p_home"] == base

    # An official source makes it active and France's win prob drops.
    engine.upsert_player_status(
        conn, team="France", player="Star", tier="key", status="out",
        confidence=0.9, source_url="https://federation", official=True,
    )
    after = engine.predict_fixture(conn, mid)["p_home"]
    assert after < base
```

- [ ] **Step 2: Run it** — `uv run pytest tests/test_intel_e2e.py -v` → PASS. (If the pending-equality
  assertion is flaky because `get_model` refit differs, note it cannot — same DB, history unchanged,
  so the cached model is identical; only intel differs.)

- [ ] **Step 3: Commit**

```bash
git add tests/test_intel_e2e.py
git commit -m "test: end-to-end off-pitch intel shifts a prediction (active vs pending)

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

### Task 13: Docs, cron, and release

**Files:** Modify `README.md`, `.env.example`, `deploy/crontab.example`

- [ ] **Step 1: Update `.env.example`** — add the optional NewsAPI line:

```dotenv
# Optional: NewsAPI developer key (https://newsapi.org) to supplement free RSS feeds.
# Not required; the system works on RSS alone.
NEWSAPI_KEY=
```

- [ ] **Step 2: Update `deploy/crontab.example`** — add a news fetch line:

```cron
# Fetch off-pitch news every 30 minutes (deterministic, no LLM)
*/30 * * * * /home/shunlyu/work/worldcup-predictor/.venv/bin/worldcup fetch-news >> /tmp/wc-news.log 2>&1
```

- [ ] **Step 3: Add a "Phase 2a — Off-pitch intelligence" section to `README.md`** documenting: the
  pipeline (cron `fetch-news` → raw articles; in a Copilot CLI session ask it to "process the latest
  news", which uses the MCP tools `get_unprocessed_news` → `upsert_player_status` → `mark_news_processed`);
  the trust gate (high confidence AND (≥2 sources OR official) → active, else pending); the
  `MAGNITUDE_TABLE` tier×status mapping; reviewing the queue with `worldcup intel-pending` /
  `intel-approve <id>` / `intel-reject <id>`; and that statuses expire at the team's next match
  (default +14 days). All English.

- [ ] **Step 4: Full green run**

```bash
uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy src && uv run pytest -q
```
Expected: all pass.

- [ ] **Step 5: Commit + tag**

```bash
git add README.md .env.example deploy/crontab.example
git commit -m "docs: document Phase 2a off-pitch intelligence pipeline + news cron

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
git tag -a v0.2.0 -m "Phase 2a: automated off-pitch intelligence (RSS + in-session LLM extraction)"
```

---

## Self-review (against the spec)

- **Spec coverage:** news fetch (Task 3/4) ✓; data model (Task 1) ✓; state-based statuses + trust
  gate + corroboration + expiry (Tasks 6/8) ✓; magnitude table = hybrid C (Task 5) ✓; reimplemented
  `apply_intel` keeping legacy (Task 7) ✓; MCP extraction tools (Task 10) ✓; CLI (Task 4/11) ✓;
  engine facade (Task 9) ✓; end-to-end (Task 12) ✓; docs/cron (Task 13) ✓. Out-of-scope items
  (2b/2c, unattended LLM, player dataset, web UI surfacing) are not implemented — correct.
- **No placeholders:** every task has concrete test + implementation code and exact commands.
- **Type/name consistency:** `upsert_status`/`team_status_factor`/`status_mult`/`derive_credibility`
  in `player_status`; `apply_intel` signature unchanged; engine facade names match the MCP/CLI
  callers; `MAGNITUDE_TABLE` keys are `(tier, status)` everywhere.
- **Watch during execution:** (1) `intel.py` must import `player_status` without a cycle —
  `player_status` imports only `config`/`models`, never `intel`; (2) the `official_ever` ternary in
  Task 6 (`official or bool(row["official"]) if row else official`) — keep the parenthesization so a
  missing row falls back to the passed `official`; (3) `kickoff` is currently NULL in the DB, so
  expiry defaults to +14 days until match kickoff times are populated (acceptable for 2a).

## Roadmap after 2a

- **2b** auto-tune `MAGNITUDE_TABLE` and the trust thresholds via walk-forward backtest (own spec/plan).
- **2c** level-3 LLM advisor: review systematic errors, propose changes, human-gated (own spec/plan).
- Optional: unattended LLM extraction (embedded API key); surface pending/active intel in the web UI;
  populate `matches.kickoff` from football-data so expiry tracks the real schedule.




