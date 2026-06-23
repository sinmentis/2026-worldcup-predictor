# Defensive ("Concede") Channel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an off-pitch intel signal tagged `affects='defense'` raise the *opponent's* expected goals (today intel only scales a team's *own* attacking lambda), so a centre-back's absence is modelled as "concede more", not "score less".

**Architecture:** Add an `affects ∈ {attack, defense, both}` column (default `'attack'`) to `player_status` and `team_signal`. The two state-based factor functions return `(atk_delta, def_delta, factors)`; `apply_intel` applies `lam_h *= (1+atk[home])*(1+def[away])` and the symmetric form, clamping each channel independently. Legacy `intel_events` stays attack-only. Existing `affects='attack'` behaviour is byte-for-byte unchanged (`x*(1+0)=x`).

**Tech Stack:** Python 3.12, SQLite (stdlib `sqlite3`), penaltyblog Dixon-Coles base model, `pytest`, `ruff`, `mypy --strict`, `uv` for env/commands.

Spec: `docs/superpowers/specs/2026-06-23-defensive-channel-design.md`.

## Global Constraints

- Python `3.12`; ruff `line-length=100`, `quote-style="double"`; `mypy --strict` must pass.
- **Quality gate at the end of every code task** (all must be clean):
  `uv run ruff check src/ tests/` · `uv run ruff format src/ tests/` · `uv run mypy src/` · `uv run pytest -q`.
- **No new dependencies.** Reuse `MAGNITUDE_TABLE` / `TEAM_SIGNAL_MAGNITUDE` symmetrically — no new magnitude table.
- **Backward compatibility is mandatory:** `affects` defaults to `'attack'`; attack-only lambdas must be byte-identical to today. The existing `tests/test_intel.py` exact-equality tests must pass **unchanged** — editing them is a red flag.
- `ADJUST_CLAMP = (-0.6, 0.6)`, `LAMBDA_MIN = 0.05` (in `config.py`). Clamp **each channel** independently; do **not** clamp the product.
- Legacy `intel_events` / `intel._team_factor` stay attack-only. Do **not** add `affects` to `intel_events`.
- `affects` allowed values are exactly `{"attack", "defense", "both"}`.
- Tests use `tmp_path` DBs (no `WC_DB_PATH`). Production data ops (Task 5) use `WC_DB_PATH=data/worldcup.db`.

---

### Task 1: Schema column + idempotent migration

**Files:**
- Modify: `src/worldcup_predictor/db.py` (the `SCHEMA` string for `player_status` and `team_signal`; add `_has_column` + `migrate`; hook `init_schema`)
- Test: `tests/test_db.py`

**Interfaces:**
- Produces: `db.migrate(conn: sqlite3.Connection) -> None` (idempotent); `init_schema` now also runs `migrate`. Both `player_status` and `team_signal` gain column `affects TEXT NOT NULL DEFAULT 'attack'`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_db.py` (ensure `import sqlite3` and `import pytest` exist at the top of the file; add them if missing):

```python
def test_migrate_is_idempotent(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.init_schema(conn)  # runs migrate once internally
    db.migrate(conn)      # second explicit run must be a no-op
    db.migrate(conn)      # third for good measure
    for table in ("player_status", "team_signal"):
        cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
        assert cols.count("affects") == 1


def test_migrate_adds_affects_to_legacy_db(tmp_path):
    conn = db.connect(tmp_path / "old.db")
    # Pre-feature tables: prod shape WITHOUT `affects`.
    conn.executescript(
        """
        CREATE TABLE player_status (
            id INTEGER PRIMARY KEY, team TEXT NOT NULL, player TEXT NOT NULL,
            tier TEXT NOT NULL, status TEXT NOT NULL, credibility REAL NOT NULL,
            sources TEXT NOT NULL, official INTEGER DEFAULT 0, valid_until TEXT,
            as_of REAL NOT NULL, pending INTEGER DEFAULT 0, notes TEXT,
            UNIQUE(team, player)
        );
        CREATE TABLE team_signal (
            id INTEGER PRIMARY KEY, team TEXT NOT NULL, category TEXT NOT NULL,
            direction TEXT NOT NULL, magnitude_tier TEXT NOT NULL,
            credibility REAL NOT NULL, sources TEXT NOT NULL,
            official INTEGER DEFAULT 0, valid_until TEXT, as_of REAL NOT NULL,
            pending INTEGER DEFAULT 0, notes TEXT,
            UNIQUE(team, category)
        );
        """
    )
    conn.execute(
        "INSERT INTO player_status"
        "(team,player,tier,status,credibility,sources,official,valid_until,as_of,pending,notes)"
        " VALUES ('Brazil','Neymar','key','out',0.9,'[]',1,NULL,0.0,0,NULL)"
    )
    conn.execute(
        "INSERT INTO team_signal"
        "(team,category,direction,magnitude_tier,credibility,sources,official,valid_until,as_of,pending,notes)"
        " VALUES ('Brazil','morale','weaken','moderate',0.8,'[]',0,NULL,0.0,0,NULL)"
    )
    conn.commit()
    assert "affects" not in [r[1] for r in conn.execute("PRAGMA table_info(player_status)")]

    db.migrate(conn)

    for table in ("player_status", "team_signal"):
        info = {r[1]: r for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        assert "affects" in info
        assert info["affects"][3] == 1  # notnull flag
        rows = conn.execute(f"SELECT affects FROM {table}").fetchall()
        assert rows and all(r[0] == "attack" for r in rows)


def test_fresh_db_rejects_bad_affects(tmp_path):
    conn = db.connect(tmp_path / "fresh.db")
    db.init_schema(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO player_status"
            "(team,player,tier,status,credibility,sources,official,valid_until,as_of,pending,notes,affects)"
            " VALUES ('ARG','Messi','key','out',0.9,'[]',1,NULL,0.0,0,NULL,'midfield')"
        )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_db.py::test_migrate_is_idempotent tests/test_db.py::test_migrate_adds_affects_to_legacy_db tests/test_db.py::test_fresh_db_rejects_bad_affects -v`
Expected: FAIL — `AttributeError: module 'worldcup_predictor.db' has no attribute 'migrate'` (and the fresh-DB CHECK test fails because the column/constraint don't exist yet).

- [ ] **Step 3: Add the column to the SCHEMA string (both tables)**

In `src/worldcup_predictor/db.py`, in the `player_status` `CREATE TABLE`, change:
```sql
    pending INTEGER DEFAULT 0,
    notes TEXT,
    UNIQUE(team, player)
```
to:
```sql
    pending INTEGER DEFAULT 0,
    notes TEXT,
    affects TEXT NOT NULL DEFAULT 'attack'
        CHECK (affects IN ('attack','defense','both')),
    UNIQUE(team, player)
```
And in the `team_signal` `CREATE TABLE`, change:
```sql
    pending INTEGER DEFAULT 0,
    notes TEXT,
    UNIQUE(team, category)
```
to:
```sql
    pending INTEGER DEFAULT 0,
    notes TEXT,
    affects TEXT NOT NULL DEFAULT 'attack'
        CHECK (affects IN ('attack','defense','both')),
    UNIQUE(team, category)
```

- [ ] **Step 4: Add the migration function and hook it into `init_schema`**

In `src/worldcup_predictor/db.py`, replace the existing `init_schema`:
```python
def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()
```
with:
```python
_AFFECTS_TABLES = ("player_status", "team_signal")


def _has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    return any(r[1] == column for r in conn.execute(f"PRAGMA table_info({table})"))


def migrate(conn: sqlite3.Connection) -> None:
    """Idempotently bring an existing DB up to schema. Safe on fresh and existing DBs.

    `CREATE TABLE IF NOT EXISTS` is a no-op on an existing table, so a column added to
    SCHEMA never reaches the live prod DB; this adds `affects` via ALTER where missing.
    `ALTER TABLE ... ADD COLUMN ... NOT NULL DEFAULT 'attack'` backfills existing rows.
    """
    for table in _AFFECTS_TABLES:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        if not exists:
            continue
        if not _has_column(conn, table, "affects"):
            conn.execute(
                f"ALTER TABLE {table} ADD COLUMN affects TEXT NOT NULL DEFAULT 'attack'"
            )
    conn.commit()


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)  # fresh DBs get `affects` (with CHECK)
    migrate(conn)               # existing DBs get `affects` via ALTER
    conn.commit()
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_db.py -v`
Expected: PASS (all `test_db.py` tests, including the 3 new ones).

- [ ] **Step 6: Quality gate**

Run: `uv run ruff check src/ tests/ && uv run ruff format src/ tests/ && uv run mypy src/`
Expected: all clean (`All checks passed!`, `Success: no issues found`).

- [ ] **Step 7: Migrate the live prod DB (one-off, backup first)**

Run:
```bash
cd /home/shunlyu/work/website/worldcup-predictor
sqlite3 data/worldcup.db ".backup 'data/worldcup.db.bak'"
WC_DB_PATH=data/worldcup.db uv run python -c "from worldcup_predictor import db; c=db.connect('data/worldcup.db'); db.migrate(c); print('migrated')"
WC_DB_PATH=data/worldcup.db uv run python -c "from worldcup_predictor import db; c=db.connect('data/worldcup.db'); print([(t, c.execute(f'SELECT COUNT(*), SUM(affects=\"attack\") FROM {t}').fetchone()) for t in ('player_status','team_signal')])"
```
Expected: `migrated`, then each table prints `(total, total)` (every existing row backfilled to `'attack'`).

- [ ] **Step 8: Commit**

```bash
git add src/worldcup_predictor/db.py tests/test_db.py
git commit -m "feat(db): add affects column + idempotent migration"
```

---

### Task 2: Thread `affects` through the write path

**Files:**
- Modify: `src/worldcup_predictor/player_status.py` (add `AFFECTS`; `upsert_status` param + validation + persist with preserve-existing)
- Modify: `src/worldcup_predictor/team_signal.py` (import `AFFECTS`; `upsert_signal` param + validation + persist with preserve-existing)
- Test: `tests/test_player_status.py`, `tests/test_team_signal.py`

**Interfaces:**
- Consumes: the `affects` column from Task 1.
- Produces: `AFFECTS = {"attack", "defense", "both"}` in `player_status`; `upsert_status(..., affects: str | None = None)` and `upsert_signal(..., affects: str | None = None)`. New rows default to `'attack'`; an update that omits `affects` **preserves** the stored value.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_player_status.py` (uses the existing `_conn(tmp_path)` helper):
```python
def test_affects_defaults_to_attack(tmp_path):
    conn = _conn(tmp_path)
    ps.upsert_status(conn, "France", "Mbappe", "key", "out", 0.9, "https://fed", official=True)
    row = conn.execute("SELECT affects FROM player_status WHERE player='Mbappe'").fetchone()
    assert row["affects"] == "attack"


def test_affects_defense_is_stored(tmp_path):
    conn = _conn(tmp_path)
    ps.upsert_status(
        conn, "Germany", "Schlotterbeck", "key", "out", 0.9, "https://fed",
        official=True, affects="defense",
    )
    row = conn.execute("SELECT affects FROM player_status WHERE player='Schlotterbeck'").fetchone()
    assert row["affects"] == "defense"


def test_affects_preserved_on_corroboration(tmp_path):
    conn = _conn(tmp_path)
    ps.upsert_status(
        conn, "Germany", "Schlotterbeck", "key", "out", 0.9, "https://a",
        official=True, affects="defense",
    )
    # later corroboration omits affects -> must NOT reset to 'attack'
    ps.upsert_status(conn, "Germany", "Schlotterbeck", "key", "out", 0.9, "https://b", official=True)
    row = conn.execute("SELECT affects FROM player_status WHERE player='Schlotterbeck'").fetchone()
    assert row["affects"] == "defense"


def test_invalid_affects_raises(tmp_path):
    conn = _conn(tmp_path)
    with pytest.raises(ValueError):
        ps.upsert_status(conn, "France", "Mbappe", "key", "out", 0.9, "https://a", affects="midfield")
```

Add to `tests/test_team_signal.py` (uses the existing `_conn(tmp_path)` helper):
```python
def test_signal_affects_defaults_to_attack(tmp_path):
    conn = _conn(tmp_path)
    ts.upsert_signal(conn, "Brazil", "tactical", "weaken", "minor", 0.9, "https://fed", official=True)
    row = conn.execute("SELECT affects FROM team_signal WHERE team='Brazil'").fetchone()
    assert row["affects"] == "attack"


def test_signal_affects_defense_preserved_on_update(tmp_path):
    conn = _conn(tmp_path)
    ts.upsert_signal(
        conn, "Germany", "tactical", "weaken", "minor", 0.9, "https://a",
        official=True, affects="defense",
    )
    ts.upsert_signal(conn, "Germany", "tactical", "weaken", "minor", 0.9, "https://b", official=True)
    row = conn.execute("SELECT affects FROM team_signal WHERE team='Germany'").fetchone()
    assert row["affects"] == "defense"


def test_signal_invalid_affects_raises(tmp_path):
    conn = _conn(tmp_path)
    with pytest.raises(ValueError):
        ts.upsert_signal(conn, "Brazil", "tactical", "weaken", "minor", 0.9, "https://a", affects="x")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_player_status.py -k "affects" tests/test_team_signal.py -k "affects" -v`
Expected: FAIL — `upsert_status() got an unexpected keyword argument 'affects'` (and likewise for `upsert_signal`).

- [ ] **Step 3: Add `AFFECTS` and update `player_status.upsert_status`**

In `src/worldcup_predictor/player_status.py`, after the `STATUSES = {...}` line add:
```python
AFFECTS = {"attack", "defense", "both"}
```
Change the `upsert_status` signature (add the trailing param):
```python
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
    affects: str | None = None,
) -> dict[str, object]:
```
After the `if not source_url:` validation block (and before `if status == "available":`), add:
```python
    if affects is not None and affects not in AFFECTS:
        raise ValueError(f"affects must be one of {sorted(AFFECTS)}")
```
Change the current-row SELECT to also read `affects`:
```python
    row = conn.execute(
        "SELECT sources, official, pending, affects FROM player_status WHERE team=? AND player=?",
        (team, player),
    ).fetchone()
```
After the `pending = ...` line, resolve the value to store (preserve existing tag on omitted update):
```python
    affects_to_store = affects if affects is not None else (row["affects"] if row else "attack")
```
Update the INSERT statement to include `affects` in the column list, the values placeholders, the `ON CONFLICT` SET clause, and the parameter tuple:
```python
    conn.execute(
        "INSERT INTO player_status"
        "(team,player,tier,status,credibility,sources,official,valid_until,as_of,pending,notes,affects)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)"
        " ON CONFLICT(team,player) DO UPDATE SET"
        " tier=excluded.tier, status=excluded.status, credibility=excluded.credibility,"
        " sources=excluded.sources, official=excluded.official, valid_until=excluded.valid_until,"
        " as_of=excluded.as_of, pending=excluded.pending, notes=excluded.notes,"
        " affects=excluded.affects",
        (
            team,
            player,
            tier,
            status,
            cred,
            json.dumps(sources),
            int(official_ever),
            valid_until,
            time.time(),
            pending,
            notes,
            affects_to_store,
        ),
    )
```

- [ ] **Step 4: Update `team_signal.upsert_signal`**

In `src/worldcup_predictor/team_signal.py`, extend the existing import from `player_status` to include `AFFECTS`:
```python
from worldcup_predictor.player_status import (
    ACTIVE_CONF_THRESHOLD,
    ACTIVE_CRED_THRESHOLD,
    AFFECTS,
    derive_credibility,
)
```
Change the `upsert_signal` signature (add the trailing param):
```python
def upsert_signal(
    conn: sqlite3.Connection,
    team: str,
    category: str,
    direction: str,
    magnitude_tier: str,
    confidence: float,
    source_url: str,
    official: bool = False,
    notes: str | None = None,
    valid_until: str | None = None,
    affects: str | None = None,
) -> dict[str, object]:
```
After the existing input validation (after the `source_url`/value checks, before the current-row SELECT), add:
```python
    if affects is not None and affects not in AFFECTS:
        raise ValueError(f"affects must be one of {sorted(AFFECTS)}")
```
Change the current-row SELECT to read `affects`:
```python
    row = conn.execute(
        "SELECT sources, official, pending, affects FROM team_signal WHERE team=? AND category=?",
        (team, category),
    ).fetchone()
```
After the `pending = ...` line add:
```python
    affects_to_store = affects if affects is not None else (row["affects"] if row else "attack")
```
Update the INSERT to include `affects` (column list, placeholders, `ON CONFLICT` SET, params tuple):
```python
    conn.execute(
        "INSERT INTO team_signal"
        "(team,category,direction,magnitude_tier,credibility,sources,official,"
        " valid_until,as_of,pending,notes,affects)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)"
        " ON CONFLICT(team,category) DO UPDATE SET"
        " direction=excluded.direction, magnitude_tier=excluded.magnitude_tier,"
        " credibility=excluded.credibility, sources=excluded.sources,"
        " official=excluded.official, valid_until=excluded.valid_until,"
        " as_of=excluded.as_of, pending=excluded.pending, notes=excluded.notes,"
        " affects=excluded.affects",
        (
            team,
            category,
            direction,
            magnitude_tier,
            cred,
            json.dumps(sources),
            int(official_ever),
            valid_until,
            time.time(),
            pending,
            notes,
            affects_to_store,
        ),
    )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_player_status.py tests/test_team_signal.py -v`
Expected: PASS (new `affects` tests + all existing tests still green — the existing tests omit `affects`, which now defaults via `None → "attack"`).

- [ ] **Step 6: Quality gate**

Run: `uv run ruff check src/ tests/ && uv run ruff format src/ tests/ && uv run mypy src/`
Expected: all clean.

- [ ] **Step 7: Commit**

```bash
git add src/worldcup_predictor/player_status.py src/worldcup_predictor/team_signal.py tests/test_player_status.py tests/test_team_signal.py
git commit -m "feat(intel): accept affects tag on upsert with preserve-on-update"
```

---

### Task 3: Two-channel factor functions + `apply_intel`

**Files:**
- Modify: `src/worldcup_predictor/player_status.py` (`team_status_factor`)
- Modify: `src/worldcup_predictor/team_signal.py` (`team_signal_factor`)
- Modify: `src/worldcup_predictor/intel.py` (`apply_intel`)
- Modify: `tests/test_player_status.py` (lines 74, 83 — unpack 3-tuple), `tests/test_team_signal.py` (lines 69, 79, 92, 101, 160 — unpack 3-tuple)
- Test: `tests/test_intel.py` (new defense/both/e2e tests; existing tests pass unchanged)

**Interfaces:**
- Consumes: `affects` from the rows (Task 1/2); `status_mult` / `signal_mult` (existing).
- Produces: `team_status_factor(conn, team) -> tuple[float, float, list[IntelFactor]]` and `team_signal_factor(conn, team) -> tuple[float, float, list[IntelFactor]]` (atk_delta, def_delta, factors). `apply_intel` keeps its signature `(lam_h, lam_a, factors)` but cross-wires the defence channel.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_intel.py`:
```python
def test_defense_signal_raises_opponent_not_self(tmp_path):
    from worldcup_predictor import player_status

    conn = db.connect(tmp_path / "t.db")
    db.init_schema(conn)
    # Key CB out, tagged defense: Germany's OWN lambda unchanged; opponent's lambda up.
    player_status.upsert_status(
        conn, "Germany", "CB", "key", "out", 0.9, "https://fed", official=True, affects="defense"
    )
    lh, la, factors = intel.apply_intel(2.0, 1.0, home="Germany", away="Ecuador", conn=conn)
    # Germany (home) attack unchanged:
    assert lh == 2.0
    # Ecuador (away) scores more: cred 0.95, key/out mult 0.72 -> def = -0.95*(0.72-1) = +0.266
    assert la == 1.0 * (1 + 0.95 * (1 - 0.72))
    assert any("defense" in f.description for f in factors)


def test_defense_strengthen_lowers_opponent(tmp_path):
    from worldcup_predictor import team_signal

    conn = db.connect(tmp_path / "t.db")
    db.init_schema(conn)
    # A strong defence (strengthen) tagged defense lowers the opponent's lambda.
    team_signal.upsert_signal(
        conn, "Italy", "tactical", "strengthen", "major", 0.9, "https://fed",
        official=True, affects="defense",
    )
    lh, la, _ = intel.apply_intel(2.0, 1.0, home="France", away="Italy", conn=conn)
    # France (home) lambda is lowered by Italy's (away) defence: signal_mult strengthen/major = 1.06
    # base = 0.95*(1.06-1)=+0.057 ; def = -base = -0.057 -> lam_h *= (1 - 0.057)
    assert lh == 2.0 * (1 + (-(0.95 * (1.06 - 1.0))))
    assert la == 1.0  # Italy's own attack unchanged


def test_both_splits_half_each(tmp_path):
    from worldcup_predictor import player_status

    conn = db.connect(tmp_path / "t.db")
    db.init_schema(conn)
    # regular/out, affects=both: own attack -base/2, opponent +(-base)/2 (=+|base|/2)
    player_status.upsert_status(
        conn, "Ghana", "DM", "regular", "out", 0.9, "https://fed", official=True, affects="both"
    )
    lh, la, _ = intel.apply_intel(2.0, 1.0, home="Ghana", away="USA", conn=conn)
    base = 0.95 * (0.85 - 1.0)  # regular/out mult 0.85
    assert lh == 2.0 * (1 + 0.5 * base)          # Ghana own attack, half
    assert la == 1.0 * (1 + 0.5 * (-base))       # USA scores more, half


def test_attack_and_defense_compose_in_one_match(tmp_path):
    from worldcup_predictor import player_status, team_signal

    conn = db.connect(tmp_path / "t.db")
    db.init_schema(conn)
    # Home team has its OWN attacking loss AND the away team has a defensive loss.
    player_status.upsert_status(
        conn, "Spain", "ST", "key", "out", 0.9, "https://fed", official=True, affects="attack"
    )
    team_signal.upsert_signal(
        conn, "Qatar", "tactical", "weaken", "moderate", 0.9, "https://fed",
        official=True, affects="defense",
    )
    lh, la, _ = intel.apply_intel(2.0, 1.0, home="Spain", away="Qatar", conn=conn)
    atk_spain = 0.95 * (0.72 - 1.0)            # key/out
    def_qatar = -(0.95 * (0.93 - 1.0))         # weaken/moderate signal_mult 0.93 -> def positive
    assert lh == 2.0 * (1 + atk_spain) * (1 + def_qatar)
    assert la == 1.0  # Qatar's own attack unchanged (no attack signal)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_intel.py -k "defense or both or compose" -v`
Expected: FAIL — `ValueError: too many values to unpack` or the asserted opponent change does not happen (defence channel not implemented yet).

- [ ] **Step 3: Rewrite `team_status_factor` (player_status.py)**

Replace the current `team_status_factor` with:
```python
def team_status_factor(
    conn: sqlite3.Connection, team: str
) -> tuple[float, float, list[IntelFactor]]:
    team = config.canonical_team(team)
    today = date.today().isoformat()
    rows = conn.execute(
        "SELECT player, tier, status, credibility, COALESCE(affects,'attack') AS affects "
        "FROM player_status "
        "WHERE team=? AND pending=0 AND (valid_until IS NULL OR valid_until >= ?)",
        (team, today),
    ).fetchall()
    atk = 0.0
    dfn = 0.0
    factors: list[IntelFactor] = []
    for r in rows:
        base = float(r["credibility"]) * (status_mult(r["tier"], r["status"]) - 1.0)
        affects = r["affects"]
        desc = f"{r['player']}: {r['status']} ({r['tier']})"
        if affects == "attack":
            atk += base
            factors.append(IntelFactor(team=team, description=desc, lambda_delta=base))
        elif affects == "defense":
            dfn += -base
            factors.append(
                IntelFactor(team=team, description=f"{desc} [defense -> opponent]", lambda_delta=-base)
            )
        else:  # both
            atk += 0.5 * base
            dfn += 0.5 * (-base)
            factors.append(IntelFactor(team=team, description=desc, lambda_delta=0.5 * base))
            factors.append(
                IntelFactor(
                    team=team, description=f"{desc} [defense -> opponent]", lambda_delta=0.5 * (-base)
                )
            )
    lo, hi = ADJUST_CLAMP
    return max(lo, min(hi, atk)), max(lo, min(hi, dfn)), factors
```

- [ ] **Step 4: Rewrite `team_signal_factor` (team_signal.py)**

Replace the current `team_signal_factor` with:
```python
def team_signal_factor(
    conn: sqlite3.Connection, team: str
) -> tuple[float, float, list[IntelFactor]]:
    team = config.canonical_team(team)
    today = date.today().isoformat()
    rows = conn.execute(
        "SELECT category, direction, magnitude_tier, credibility, "
        "COALESCE(affects,'attack') AS affects FROM team_signal "
        "WHERE team=? AND pending=0 AND (valid_until IS NULL OR valid_until >= ?)",
        (team, today),
    ).fetchall()
    atk = 0.0
    dfn = 0.0
    factors: list[IntelFactor] = []
    for r in rows:
        base = float(r["credibility"]) * (signal_mult(r["direction"], r["magnitude_tier"]) - 1.0)
        affects = r["affects"]
        desc = f"{r['category']}: {r['direction']} ({r['magnitude_tier']})"
        if affects == "attack":
            atk += base
            factors.append(IntelFactor(team=team, description=desc, lambda_delta=base))
        elif affects == "defense":
            dfn += -base
            factors.append(
                IntelFactor(team=team, description=f"{desc} [defense -> opponent]", lambda_delta=-base)
            )
        else:  # both
            atk += 0.5 * base
            dfn += 0.5 * (-base)
            factors.append(IntelFactor(team=team, description=desc, lambda_delta=0.5 * base))
            factors.append(
                IntelFactor(
                    team=team, description=f"{desc} [defense -> opponent]", lambda_delta=0.5 * (-base)
                )
            )
    lo, hi = ADJUST_CLAMP
    return max(lo, min(hi, atk)), max(lo, min(hi, dfn)), factors
```

- [ ] **Step 5: Rewrite `apply_intel` (intel.py)**

Replace the current `apply_intel` with:
```python
def apply_intel(
    lam_h: float, lam_a: float, home: str, away: str, conn: sqlite3.Connection
) -> tuple[float, float, list[IntelFactor]]:
    lo, hi = ADJUST_CLAMP
    ev_atk_h, fe_h = _team_factor(conn, home)  # legacy intel_events: attack-only
    ev_atk_a, fe_a = _team_factor(conn, away)
    ps_atk_h, ps_def_h, fps_h = player_status.team_status_factor(conn, home)
    ps_atk_a, ps_def_a, fps_a = player_status.team_status_factor(conn, away)
    ts_atk_h, ts_def_h, fts_h = team_signal.team_signal_factor(conn, home)
    ts_atk_a, ts_def_a, fts_a = team_signal.team_signal_factor(conn, away)

    atk_home = max(lo, min(hi, ev_atk_h + ps_atk_h + ts_atk_h))
    atk_away = max(lo, min(hi, ev_atk_a + ps_atk_a + ts_atk_a))
    def_home = max(lo, min(hi, ps_def_h + ts_def_h))  # legacy events have no defence
    def_away = max(lo, min(hi, ps_def_a + ts_def_a))

    lam_h = max(LAMBDA_MIN, lam_h * (1 + atk_home) * (1 + def_away))
    lam_a = max(LAMBDA_MIN, lam_a * (1 + atk_away) * (1 + def_home))
    return lam_h, lam_a, fe_h + fps_h + fts_h + fe_a + fps_a + fts_a
```

- [ ] **Step 6: Update the existing 3 direct-call test sites to the 3-tuple**

In `tests/test_player_status.py`:
- `test_team_status_factor_weakens_team` — replace:
  ```python
      delta, factors = ps.team_status_factor(conn, "France")
      # key/out mult 0.72, credibility 0.95 => delta = 0.95 * (0.72 - 1) = -0.266
      assert abs(delta - (0.95 * (0.72 - 1.0))) < 1e-9
      assert len(factors) == 1
  ```
  with:
  ```python
      atk, dfn, factors = ps.team_status_factor(conn, "France")
      # key/out mult 0.72, credibility 0.95 => atk = 0.95 * (0.72 - 1) = -0.266 (attack default)
      assert abs(atk - (0.95 * (0.72 - 1.0))) < 1e-9
      assert dfn == 0.0
      assert len(factors) == 1
  ```
- `test_team_status_factor_ignores_pending` — replace:
  ```python
      delta, factors = ps.team_status_factor(conn, "France")
      assert delta == 0.0
      assert factors == []
  ```
  with:
  ```python
      atk, dfn, factors = ps.team_status_factor(conn, "France")
      assert atk == 0.0
      assert dfn == 0.0
      assert factors == []
  ```

In `tests/test_team_signal.py`, update each `delta, factors = ts.team_signal_factor(...)` (and the one `delta, _ = ...`) to the 3-tuple, asserting on the attack delta and `dfn == 0.0`:
- `test_factor_strengthen_is_positive` (line ~69): replace `delta, factors = ts.team_signal_factor(conn, "Brazil")` with `delta, dfn, factors = ts.team_signal_factor(conn, "Brazil")` and add `assert dfn == 0.0` after the `assert delta > 0.0` line. (Keep the existing `delta` assertions — `delta` is now the attack delta, identical for these attack-default rows.)
- `test_factor_weaken_is_negative` (line ~79): replace `delta, _ = ts.team_signal_factor(conn, "Iran")` with `delta, _dfn, _factors = ts.team_signal_factor(conn, "Iran")`.
- `test_two_categories_coexist_and_sum` (line ~92): replace `delta, factors = ts.team_signal_factor(conn, "Brazil")` with `delta, dfn, factors = ts.team_signal_factor(conn, "Brazil")` and add `assert dfn == 0.0` after the `assert len(factors) == 2` line.
- `test_factor_ignores_pending` (line ~101): replace `delta, factors = ts.team_signal_factor(conn, "Brazil")` with `delta, dfn, factors = ts.team_signal_factor(conn, "Brazil")` and add `assert dfn == 0.0`.
- the `valid_until` expiry test (line ~160): replace `delta, factors = ts.team_signal_factor(conn, "Brazil")` with `delta, dfn, factors = ts.team_signal_factor(conn, "Brazil")` and add `assert dfn == 0.0`.

- [ ] **Step 7: Run the full suite — new pass + existing byte-identical**

Run: `uv run pytest -q`
Expected: PASS, all tests. In particular `tests/test_intel.py` exact-equality tests
(`test_no_intel_is_noop`, `test_credibility_scales_effect`,
`test_adjust_clamp_bounds_delta_on_multiple_events`, `test_lambda_min_floor_applied_after_clamped_adjustment`,
`test_apply_intel_sums_all_three_sources`) pass **unchanged** (attack-only invariance: `(1+def)=1`).

- [ ] **Step 8: Quality gate**

Run: `uv run ruff check src/ tests/ && uv run ruff format src/ tests/ && uv run mypy src/`
Expected: all clean.

- [ ] **Step 9: Commit**

```bash
git add src/worldcup_predictor/player_status.py src/worldcup_predictor/team_signal.py src/worldcup_predictor/intel.py tests/
git commit -m "feat(intel): two-channel apply_intel with defensive concede channel"
```

---

### Task 4: Expose `affects` on the MCP intel tools (scheduled-job contract)

**Files:**
- Modify: `src/worldcup_predictor/mcp_server.py` (`upsert_player_status`, `upsert_team_signal` tools)
- Test: `tests/test_engine_intel.py` (or a new `tests/test_mcp_affects.py`)

**Interfaces:**
- Consumes: `engine.upsert_player_status` / `engine.upsert_team_signal` (forward `**kwargs`; no change), `player_status.AFFECTS`, `team_signal` `AFFECTS`.
- Produces: MCP tools accept `affects: str = "attack"`, validate it, and forward it.

- [ ] **Step 1: Write the failing test**

Create `tests/test_mcp_affects.py`:
```python
import pytest

from mcp.server.fastmcp.exceptions import ToolError
from worldcup_predictor import mcp_server


def test_mcp_player_status_accepts_and_validates_affects(tmp_path, monkeypatch):
    monkeypatch.setenv("WC_DB_PATH", str(tmp_path / "t.db"))
    # valid defense tag is forwarded and stored active (official => gate passes)
    out = mcp_server.upsert_player_status(
        team="Germany", player="Schlotterbeck", tier="key", status="out",
        confidence=0.9, source_url="https://fed", official=True, affects="defense",
    )
    assert out["team"] == "Germany"
    conn = mcp_server._conn()
    row = conn.execute("SELECT affects FROM player_status WHERE player='Schlotterbeck'").fetchone()
    assert row["affects"] == "defense"
    # invalid value rejected
    with pytest.raises(ToolError):
        mcp_server.upsert_player_status(
            team="Germany", player="X", tier="key", status="out",
            confidence=0.9, source_url="https://fed", affects="midfield",
        )


def test_mcp_team_signal_accepts_affects(tmp_path, monkeypatch):
    monkeypatch.setenv("WC_DB_PATH", str(tmp_path / "t.db"))
    out = mcp_server.upsert_team_signal(
        team="Italy", category="tactical", direction="strengthen", magnitude_tier="major",
        confidence=0.9, source_url="https://fed", official=True, affects="defense",
    )
    assert out["team"] == "Italy"
    with pytest.raises(ToolError):
        mcp_server.upsert_team_signal(
            team="Italy", category="tactical", direction="strengthen", magnitude_tier="major",
            confidence=0.9, source_url="https://fed", affects="x",
        )
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_mcp_affects.py -v`
Expected: FAIL — `upsert_player_status() got an unexpected keyword argument 'affects'`.

- [ ] **Step 3: Add `affects` to the two MCP tools**

In `src/worldcup_predictor/mcp_server.py`, `upsert_player_status`: add `affects: str = "attack"` to the signature (after `notes: str = ""`), add a validation guard after the `confidence` check, extend the docstring, and forward the arg:
```python
def upsert_player_status(
    team: str,
    player: str,
    tier: str,
    status: str,
    confidence: float,
    source_url: str,
    official: bool = False,
    notes: str = "",
    affects: str = "attack",
) -> dict[str, object]:
    """Record/update a player's current status from news, adjusting expected goals.

    tier: 'key' | 'regular' | 'fringe' (you judge importance from the article).
    status: 'out' | 'doubtful' | 'suspended' | 'available' ('available' clears a prior status).
    affects: 'attack' (default — lowers this team's own scoring; forwards/wingers/attacking mids)
             | 'defense' (raises the OPPONENT's expected goals; goalkeepers and defenders)
             | 'both' (half each; defensive midfielders / box-to-box). Position heuristic:
             GK/CB -> defense, DM/CM -> both, FWD/AM/W -> attack.
    confidence in [0,1]; ALWAYS pass a real source_url. official=True only for
    club/federation sources. High confidence AND (>=2 sources OR official) applies
    immediately; otherwise it is queued for review.
    """
    if tier not in _ps.TIERS:
        raise ToolError(f"tier must be one of {sorted(_ps.TIERS)}")
    if status not in _ps.STATUSES:
        raise ToolError(f"status must be one of {sorted(_ps.STATUSES)}")
    if affects not in _ps.AFFECTS:
        raise ToolError(f"affects must be one of {sorted(_ps.AFFECTS)}")
    if not source_url:
        raise ToolError("source_url is required; intel must be traceable.")
    if not 0.0 <= confidence <= 1.0:
        raise ToolError("confidence must be in [0,1].")
    return engine.upsert_player_status(
        _conn(),
        team=team,
        player=player,
        tier=tier,
        status=status,
        confidence=confidence,
        source_url=source_url,
        official=official,
        notes=notes or None,
        affects=affects,
    )
```
In `upsert_team_signal`: add `affects: str = "attack"` to the signature (after `notes: str = ""`), add a guard after the `magnitude_tier` check, extend the docstring (one line: `affects: 'attack' (default) | 'defense' (raises the opponent's expected goals — defensive frailty) | 'both' (half each).`), and forward `affects=affects` in the `engine.upsert_team_signal(...)` call. The guard:
```python
    if affects not in _ts.AFFECTS:
        raise ToolError(f"affects must be one of {sorted(_ts.AFFECTS)}")
```
(`_ts.AFFECTS` resolves via the import added in Task 2.)

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_mcp_affects.py -v`
Expected: PASS.

- [ ] **Step 5: Quality gate + full suite**

Run: `uv run ruff check src/ tests/ && uv run ruff format src/ tests/ && uv run mypy src/ && uv run pytest -q`
Expected: all clean, all tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/worldcup_predictor/mcp_server.py tests/test_mcp_affects.py
git commit -m "feat(mcp): expose affects on intel tools with position-tagging guidance"
```

---

### Task 5: Re-tag Schlotterbeck on prod + re-simulate (production data step)

**Files:** none (data operation on `data/worldcup.db`). Requires Tasks 1–4 merged and the prod DB migrated (Task 1 Step 7).

**Interfaces:** Consumes the live `worldcup` CLI / engine. No code change.

> Owner decision baked in (spec §10 Q2): activate with the **single real BBC source** ⇒ cred 0.50 ⇒
> opponents **+14%**. Do **not** fabricate a second URL. If the owner later supplies a genuine second
> outlet, re-run the upsert with it to reach cred 0.80 (+22.4%).

- [ ] **Step 1: Capture the pre-change title board**

Run:
```bash
cd /home/shunlyu/work/website/worldcup-predictor
WC_DB_PATH=data/worldcup.db uv run python -c "from worldcup_predictor import db; c=db.connect('data/worldcup.db'); print([(t, round(p*100,1)) for t,p in c.execute('SELECT team,title_prob FROM sim_results ORDER BY title_prob DESC LIMIT 8')])"
```
Expected: prints the current top-8 (Germany around 4.1%).

- [ ] **Step 2: Replace the proxy team_signal with a real defence player_status row, then approve it**

Run:
```bash
WC_DB_PATH=data/worldcup.db uv run python -c "
from worldcup_predictor import db, player_status
conn = db.connect('data/worldcup.db')
# 1) delete the approximate proxy team_signal (Germany/tactical)
conn.execute(\"DELETE FROM team_signal WHERE team='Germany' AND category='tactical'\")
conn.commit()
# 2) add the real defence-channel player_status row (single real BBC source -> cred 0.50 -> pending)
player_status.upsert_status(
    conn, team='Germany', player='Nico Schlotterbeck', tier='key', status='out',
    confidence=0.90,
    source_url='https://www.bbc.co.uk/sport/football/articles/cgk6rzd5g58o',
    valid_until='2026-07-19', affects='defense',
    notes='1st-choice CB out for tournament; concede channel (raises opponents xG), not own attack.')
# 3) approve it (human gate): set pending=0 for this confirmed BBC ruling
conn.execute(\"UPDATE player_status SET pending=0 WHERE team='Germany' AND player='Nico Schlotterbeck'\")
conn.commit()
r = conn.execute(\"SELECT tier,status,credibility,affects,pending,valid_until FROM player_status WHERE player='Nico Schlotterbeck'\").fetchone()
print('schlotterbeck:', dict(r))
"
```
Expected: `schlotterbeck: {'tier': 'key', 'status': 'out', 'credibility': 0.5, 'affects': 'defense', 'pending': 0, 'valid_until': '2026-07-19'}`.

- [ ] **Step 3: Re-simulate and compare the board**

Run:
```bash
WC_DB_PATH=data/worldcup.db uv run worldcup simulate --n 100000
WC_DB_PATH=data/worldcup.db uv run python -c "from worldcup_predictor import db; c=db.connect('data/worldcup.db'); print([(t, round(p*100,1)) for t,p in c.execute('SELECT team,title_prob FROM sim_results ORDER BY title_prob DESC LIMIT 8')])"
```
Expected: simulation completes; Germany's title/advance probability **dips modestly** versus Step 1 (its group opponents now score more), confirming the concede channel is live end-to-end. Spot-check via the public site `https://worldcup.shunlyu.com/api/forecast` (no restart needed — reads the DB per request).

- [ ] **Step 4: (No commit — data only.)** The prod DB (`data/worldcup.db`) is gitignored. Optionally note the change in the session log / memory.

---

## Self-Review (completed by plan author)

**Spec coverage:** §4.1 schema/migration → Task 1; §4.4 write-path threading + preserve-on-update → Task 2; §4.2/§4.3 factor functions + apply_intel two-channel → Task 3; §4.4 MCP surface + position heuristic → Task 4; §5.1 Schlotterbeck re-tag + §5.3 re-simulate → Task 5. §6.1 invariance is enforced by Task 3 Step 7 (existing exact-equality tests unchanged). Test plan §7 items 1–9 map to: #1 → existing `test_intel.py` (unchanged) + Task 3 Step 7; #2 → Task 3 Step 1 (defense raise + strengthen lower); #3 → Task 3 (both half); #4 → Task 1; #5 → Task 3 (`test_attack_and_defense_compose_in_one_match`); #7 → Task 2 (preserve); #8 → Task 1 (`test_migrate_adds_affects_to_legacy_db` proves migration adds the column; pre-migration crash is the motivation, covered by the legacy-DB test); #9 → Task 4. Deferred (spec §9): web badge, Partey production re-tag, CLI write commands — intentionally out of scope.

**Placeholder scan:** none — every code/step block contains the actual code or exact command.

**Type consistency:** factor functions consistently `tuple[float, float, list[IntelFactor]]` (Task 3 producer; consumed only by `apply_intel`, updated in the same task, and by the test sites updated in Task 3 Step 6). `affects: str | None = None` on the two upserts (Task 2) vs `affects: str = "attack"` on the MCP tools (Task 4) — deliberate: the MCP boundary always supplies a concrete value (default `"attack"`), while the library upserts use `None` to mean "preserve existing on update". `AFFECTS` defined once in `player_status` and imported by `team_signal` (Task 2) and referenced as `_ps.AFFECTS` / `_ts.AFFECTS` in MCP (Task 4).
