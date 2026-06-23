# SPEC: Defensive ("Concede") Channel for Off-Pitch Intel

Status: Proposed (panel-synthesized; pending owner review)
Date: 2026-06-23
Scope files: `db.py`, `player_status.py`, `team_signal.py`, `intel.py`, `mcp_server.py`, `tests/`
Quality bar: `ruff`, `ruff format`, `mypy --strict`, `pytest` all green.

> Authored by an engineering panel (Software Architect, Backend Architect, Database, Minimal-Change),
> grounded in the live code. Recommendations only — no code changed by this document.

---

## 1. Overview & Motivation

Off-pitch intel today is **one-directional**: every signal only scales a team's **own**
attacking lambda (λ = goals that team scores). Both `player_status.team_status_factor` and
`team_signal.team_signal_factor` compute `contrib = credibility × (mult − 1)`, and `apply_intel`
applies `lam_team *= (1 + delta)`. There is no way to express "this team will **concede more**" —
e.g. a first-choice centre-back ruled out should raise the **opponent's** expected goals, not lower
the team's own scoring.

This spec adds a second, **defensive ("concede") channel** so a team's defensive signal raises the
**opponent's** λ (and a strengthened defence lowers it), while preserving today's attack-only
behaviour **byte-for-byte**.

The underlying base model is penaltyblog's `DixonColesGoalModel` (separate per-team attack/defence
ratings); this feature operates purely in the **intel adjustment layer** that post-multiplies the
fitted λ's, so no model re-fit is involved.

---

## 2. Goals / Non-Goals

### Goals
- **G1** Add an `affects ∈ {attack, defense, both}` dimension (DEFAULT `'attack'`) to both
  `player_status` and `team_signal`.
- **G2** A team's `defense` signal raises the **opponent's** λ, with correct sign semantics
  (weaker defence ⇒ opponent scores **more**; stronger defence ⇒ opponent scores **less**).
- **G3** Apply attack and defence as **two independent multiplicative channels**, each clamped to
  `ADJUST_CLAMP = (−0.6, +0.6)`, retaining the `LAMBDA_MIN = 0.05` floor.
- **G4** Thread `affects` (default `'attack'`) through the **write path that actually exists**:
  `upsert_status` / `upsert_signal` → engine facades (already transparent) → **MCP tools**
  (the live ingestion surface + the auto-intel scheduled-job contract).
- **G5** Re-tag the real Schlotterbeck signal (currently a `team_signal` proxy) to the new defence
  channel and re-simulate.
- **G6** Quality bar unchanged.

### Non-Goals
- **NG1** No new defensive magnitude table. Reuse `MAGNITUDE_TABLE` and `TEAM_SIGNAL_MAGNITUDE`
  **symmetrically** for both channels.
- **NG2** Legacy `intel_events` path stays attack-only. `_team_factor` keeps its `(delta, factors)`
  shape and routes entirely to the attack channel. **Do not** add `affects` to `intel_events`.
- **NG3** No per-player position column. Position only informs the scheduled-job *tagging heuristic*;
  it is never persisted.
- **NG4** No changes to the Dixon-Coles fit, `retilt_grid`, host advantage, calibration, or value betting.
- **NG5** No general migration framework — one targeted, idempotent column migration only.
- **NG6 (deferred follow-ups, not v1):** web-UI badge; the *production* re-tag of Partey
  (`affects='both'`); any future CLI write command for intel. See §9.

---

## 3. Architecture & Data Flow

### 3.1 Today (single attack channel)
`apply_intel` sums three per-team deltas and clamps the sum:
```
dh = clamp(dh_events + dh_status + dh_signal);  lam_h *= (1 + dh)
da = clamp(da_events + da_status + da_signal);  lam_a *= (1 + da)
```
All three sources only ever push a team's **own** λ.

### 3.2 Proposed (attack + defence channels)
Each **state-based** factor function returns **two deltas plus factors**:
`(atk_delta, def_delta, factors)`. Per active row, with `base = credibility × (mult − 1)`
(negative for weaken/out):

| `affects` | atk contribution | def contribution            | Rationale |
|-----------|------------------|-----------------------------|-----------|
| `attack`  | `+= base`        | `+= 0`                      | Today's behaviour |
| `defense` | `+= 0`           | `+= −base` (**sign flip**)  | Weaker defence ⇒ opponent scores **more** ⇒ positive def_delta |
| `both`    | `+= 0.5·base`    | `+= 0.5·(−base)`            | Split evenly, no double-count |

`apply_intel` then combines per channel, **multiplicatively** — a team's own attack channel and the
**opponent's** defence channel both bear on a team's λ:
```
lam_h *= (1 + atk[home]) * (1 + def[away])    # away's leaky defence lifts home's goals
lam_a *= (1 + atk[away]) * (1 + def[home])    # home's leaky defence lifts away's goals
lam_h  = max(LAMBDA_MIN, lam_h)
lam_a  = max(LAMBDA_MIN, lam_a)
```

### 3.3 Flow diagram
```
 player_status.team_status_factor(conn,T) ─┐ (atk, def, factors)
 team_signal.team_signal_factor(conn,T)  ──┤ (atk, def, factors)
 intel._team_factor(conn,T)  [LEGACY] ─────┘ (delta, factors) ──► atk only
                                           │
                              per-team accumulate → atk[T], def[T]
                              clamp EACH channel to ADJUST_CLAMP (−0.6,+0.6)
                                           │
         ┌─────────────────────────────────┴──────────────────────────────┐
         ▼                                                                  ▼
 lam_h *= (1+atk[home])·(1+def[away])             lam_a *= (1+atk[away])·(1+def[home])
         └────────────────► max(LAMBDA_MIN, ·) ◄───────────────────────────┘
                                           │
                                           ▼
   predict.adjusted_grid → retilt_grid(grid, lam_old, lam_new)   [unchanged]
```

### 3.4 Interface contract (Backend implements against this)
```python
def team_status_factor(conn, team) -> tuple[float, float, list[IntelFactor]]:
    """(atk_delta, def_delta, factors).
       base = credibility * (status_mult(tier,status) - 1.0) per active row, routed by `affects`.
       atk_delta multiplies the team's OWN λ; def_delta multiplies the OPPONENT's λ.
       Each delta clamped to ADJUST_CLAMP inside the function."""

def team_signal_factor(conn, team) -> tuple[float, float, list[IntelFactor]]:
    """Same contract; base = credibility * (signal_mult(direction, magnitude_tier) - 1.0)."""
```
Contract rules:
- Each channel delta is independently clamped to `ADJUST_CLAMP` **inside** the factor function
  (matching today's per-function clamp). `apply_intel` re-clamps each channel after merging the
  legacy events delta into attack (clamp is idempotent ⇒ safe).
- `_team_factor` (legacy events) keeps `(delta, factors)` and contributes **only** to attack (NG2).
- `IntelFactor` (`models.py`) is **unchanged** in v1. Channel is conveyed via the factor
  `description` text (e.g. `"... [defense → opponent]"`) and by emitting per-channel factors. A
  structured `IntelFactor.affects` field is deferred to whenever the web badge is built (§9).

---

## 4. Detailed Design

### 4.1 Schema & migration (`db.py`)

**Problem.** `init_schema(conn)` runs only `executescript(SCHEMA)` where SCHEMA is all
`CREATE TABLE IF NOT EXISTS`. That is a **no-op on an existing table**, and the prod DB
(`data/worldcup.db`) already exists (3 `player_status` + 9 `team_signal` rows, no `affects`).
Editing the CREATE TABLE alone will **never** add the column to prod.

**Reliability finding.** `init_schema(conn)` is called by all three prod entrypoints right after
`connect()`: `cli.py _conn`, `web_server.py lifespan`, `mcp_server.py _conn`. So hooking the
migration into `init_schema` fully covers production. `connect()` stays side-effect-free.

**Fresh-DB path** — add to both `CREATE TABLE` blocks (last column before the `UNIQUE(...)` line):
```sql
    notes TEXT,
    affects TEXT NOT NULL DEFAULT 'attack'
        CHECK (affects IN ('attack','defense','both')),
    UNIQUE(team, player)     -- (UNIQUE(team, category) for team_signal)
```
The `CHECK` is cheap on a brand-new table and gives fresh DBs a DB-level guard.

**Existing-DB path** — idempotent migration (no `ADD COLUMN IF NOT EXISTS` in SQLite, so guard with
`PRAGMA table_info`):
```python
_AFFECTS_TABLES = ("player_status", "team_signal")

def _has_column(conn, table, column) -> bool:
    return any(r[1] == column for r in conn.execute(f"PRAGMA table_info({table})"))

def migrate(conn) -> None:
    """Idempotent; safe on fresh and existing DBs. Only adds `affects` where missing."""
    for table in _AFFECTS_TABLES:
        if not conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone():
            continue
        if not _has_column(conn, table, "affects"):
            conn.execute(
                f"ALTER TABLE {table} ADD COLUMN affects TEXT NOT NULL DEFAULT 'attack'"
            )
    conn.commit()

def init_schema(conn) -> None:
    conn.executescript(SCHEMA)   # fresh DBs get `affects` (with CHECK)
    migrate(conn)                # existing DBs get `affects` via ALTER
    conn.commit()
```
- `ALTER TABLE … ADD COLUMN … NOT NULL DEFAULT 'attack'` is allowed (constant default) and
  **backfills every existing row to `'attack'`** ⇒ legacy intel stays attack-only automatically.
- The migration `ALTER` deliberately omits `CHECK` (SQLite can't validate it retroactively without a
  full table rebuild). **Allowed-value enforcement for migrated DBs = application validation (§4.4),
  which is the source of truth.** The fresh-DB `CHECK` is a secondary guard only.
- Table names are hard-coded constants (no untrusted interpolation).
- Optional defense-in-depth: a `cli migrate` Typer command (calls `db.migrate`) for deliberate
  operator runs. Nice-to-have, not required (init_schema already covers prod).

**Backfill verification** (run post-migration on prod):
```sql
SELECT 'player_status' tbl, COUNT(*) total, SUM(affects='attack') attack FROM player_status
UNION ALL SELECT 'team_signal', COUNT(*), SUM(affects='attack') FROM team_signal;
-- pass: total == attack for both; and zero rows where affects IS NULL or NOT IN domain.
```
**Backup first** (mandatory before prod migration):
`sqlite3 "$WC_DB_PATH" ".backup '$WC_DB_PATH.bak'"`.

**Rollback** = redeploy previous code and leave the (defaulted, unused) column in place; an unread
column is inert. Do not rely on `DROP COLUMN` (prod SQLite version unknown).

### 4.2 Factor functions (`player_status.py`, `team_signal.py`)

New signatures (middle element is the defence delta; order fixed):
```python
def team_status_factor(conn, team) -> tuple[float, float, list[IntelFactor]]: ...
def team_signal_factor(conn, team) -> tuple[float, float, list[IntelFactor]]: ...
```

Per-row computation (selection predicate unchanged:
`pending=0 AND (valid_until IS NULL OR valid_until >= today)`):
```
base = credibility * (mult - 1)          # mult = status_mult(...) | signal_mult(...)
affects = row.affects                     # SELECT uses COALESCE(affects,'attack')

if affects == "attack":   atk_row, def_row = base,        0.0
elif affects == "defense": atk_row, def_row = 0.0,        -base      # sign flip
elif affects == "both":    atk_row, def_row = 0.5*base,   0.5*(-base)

atk_delta += atk_row
def_delta += def_row
```
Sign rationale: a defensive **loss** (`out`, mult<1 ⇒ base<0) must **raise** the opponent ⇒
`def_row = −base > 0`. A defensive **gain** (`strengthen`, mult>1 ⇒ base>0) ⇒ `def_row < 0`
(opponent scores less). Attack keeps the legacy sign.

Clamp each accumulator independently before returning:
```python
lo, hi = ADJUST_CLAMP
return max(lo, min(hi, atk_delta)), max(lo, min(hi, def_delta)), factors
```

SELECT must fetch `affects` with a defensive coalesce:
```sql
SELECT player, tier, status, credibility, COALESCE(affects,'attack') AS affects
FROM player_status WHERE team=? AND pending=0 AND (valid_until IS NULL OR valid_until >= ?)
-- team_signal: SELECT category, direction, magnitude_tier, credibility, COALESCE(affects,'attack') ...
```
> **`COALESCE` only guards a NULL value — it does NOT protect a DB whose `affects` column is missing**
> (that raises `OperationalError: no such column`). The migration (§4.1) is therefore a **hard
> prerequisite** before any factor SELECT runs. This is guaranteed in production because all three
> entrypoints call `init_schema` (which runs `migrate`) right after `connect()`; no code path queries
> these tables without it.

**IntelFactor transparency.** Emit one factor per **non-zero** channel contribution (a pure-defense
row emits only the `[defense → opponent]` factor; `both` emits two halves). `lambda_delta` =
the signed value added to that channel. `IntelFactor.team` stays the **originating** team; a
`[defense → opponent]` factor's positive `lambda_delta` is understood to apply to the opponent.
Suppress the always-zero opposite channel to avoid noise. **Attack/default factors stay byte-identical
to today** — same `team`, `description` (do **not** add an `[attack]` tag), `lambda_delta`, and order;
only `defense`/`both` rows gain a channel tag in their description.

Worked examples:

| # | Source | tier/dir + status | cred | mult | base | affects | atk | def (→opp) |
|---|--------|-------------------|------|------|------|---------|-----|-----------|
| 1 | player_status | key/out | 0.80 | 0.72 | −0.224 | attack  | **−0.224** | 0 |
| 2 | player_status | key/out | 0.80 | 0.72 | −0.224 | defense | 0 | **+0.224** |
| 3 | player_status | regular/out | 0.80 | 0.85 | −0.120 | both | **−0.060** | **+0.060** |
| 4 | team_signal | strengthen/major | 0.80 | 1.06 | +0.048 | defense | 0 | **−0.048** |

> **Credibility in these examples is illustrative.** `upsert_*` **derives** the stored `credibility`
> from the number of independent sources via `derive_credibility(n_sources, official)`
> (0.50 single / 0.80 ≥2 / 0.95 official) — it does **not** store the passed `confidence` arg (which
> only feeds the pending-vs-active gate). So a single-source row takes effect at cred **0.50**, e.g.
> a single-source `key/out` defence ⇒ `+0.14` (not `+0.224`).

### 4.3 `apply_intel` rewrite (`intel.py`)

Signature unchanged: `apply_intel(lam_h, lam_a, home, away, conn) -> (lam_h, lam_a, factors)`.
```python
def apply_intel(lam_h, lam_a, home, away, conn):
    lo, hi = ADJUST_CLAMP
    ev_atk_h, fe_h = _team_factor(conn, home)        # legacy events: attack-only
    ev_atk_a, fe_a = _team_factor(conn, away)
    ps_atk_h, ps_def_h, fps_h = player_status.team_status_factor(conn, home)
    ps_atk_a, ps_def_a, fps_a = player_status.team_status_factor(conn, away)
    ts_atk_h, ts_def_h, fts_h = team_signal.team_signal_factor(conn, home)
    ts_atk_a, ts_def_a, fts_a = team_signal.team_signal_factor(conn, away)

    atk_home = max(lo, min(hi, ev_atk_h + ps_atk_h + ts_atk_h))
    atk_away = max(lo, min(hi, ev_atk_a + ps_atk_a + ts_atk_a))
    def_home = max(lo, min(hi,            ps_def_h + ts_def_h))   # events have no defence
    def_away = max(lo, min(hi,            ps_def_a + ts_def_a))

    lam_h = max(LAMBDA_MIN, lam_h * (1 + atk_home) * (1 + def_away))
    lam_a = max(LAMBDA_MIN, lam_a * (1 + atk_away) * (1 + def_home))
    return lam_h, lam_a, fe_h + fps_h + fts_h + fe_a + fps_a + fts_a
```
**Clamp layering (critical).** The attack channel keeps **both** clamp layers in the same order as
today (each factor fn clamps its own delta; `apply_intel` re-clamps the per-team attack sum). This
preserves byte-for-byte attack behaviour (see §6.1). The defence channel is new and gets its own
identical two-layer clamp. We do **not** clamp the product `(1+atk)(1+def)` — channels stay
orthogonal (owner-confirmed; see §8 R3).

End-to-end (Schlotterbeck after re-tag): Germany has `key/out affects=defense`. With the single real
BBC source, stored cred = 0.50 ⇒ `def_home = 0.50×(0.72−1)×(−1) = +0.14`, `atk_home = 0` (a 2nd
independent source would lift cred to 0.80 ⇒ +0.224). Vs Ecuador (no intel): `lam_germany *= 1.0`
(attack unchanged), `lam_ecuador *= 1.14` (+14%). Correct — losing a CB raises what the opponent
scores, not what Germany scores.

### 4.4 `affects` threading (validation, upsert, MCP)

Single source of truth for allowed values, defined once in `player_status.py` and imported by
`team_signal.py`:
```python
AFFECTS = {"attack", "defense", "both"}
```
`upsert_status` / `upsert_signal` gain a trailing **`affects: str | None = None`** parameter (placed
after `valid_until` so positional callers are unaffected). Validate **when provided**, before any DB
write (and, in `upsert_status`, before the `status == "available"` early-return):
```python
if affects is not None and affects not in AFFECTS:
    raise ValueError(f"affects must be one of {sorted(AFFECTS)}")
```
**Conflict-update semantics (preserve-existing).** `upsert_status` already fetches the current row
(for `sources`/`official`/`pending`); extend that read to include `affects`, then resolve:
```python
affects_to_store = affects if affects is not None else (row["affects"] if row else "attack")
```
Add `affects` to the INSERT column list and to `ON CONFLICT DO UPDATE SET affects=excluded.affects`
using that **resolved** value. Net rule: a **new** row with no tag defaults to `'attack'`; a later
corroboration that **omits** `affects` **keeps** the stored tag (so a `defense`/`both` row is never
silently reset to `attack`). `team_signal.upsert_signal` mirrors this (it also reads the current row).
**Type:** plain `str | None` + runtime `AFFECTS` guard (matches the existing
`tier`/`status`/`category` convention and the `**kwargs: Any` facade / MCP-string boundaries).

**Engine facades** (`engine.upsert_player_status` / `engine.upsert_team_signal`) take `**kwargs` and
need **no signature change and no edit** — `affects` threads through transparently.

**MCP tools** (`mcp_server.upsert_player_status` / `upsert_team_signal`) — the **only** live write
surface and the auto-intel scheduled-job contract — add `affects: str = "attack"`, a `ToolError`
guard, and extend the docstring with the tagging heuristic:
> `affects`: `'attack'` (own scoring, default) | `'defense'` (raises the **opponent's** expected
> goals — injured/absent defenders, defensive collapse) | `'both'` (half each). Position heuristic:
> **GK/CB → defense, DM/CM → both, FWD/AM/W → attack.**

> There is **no** CLI write command for status/signal today (only `intel-pending/approve/reject`),
> so there is nothing to thread there. If a CLI ingestion path is ever added, give it `--affects`
> then. (Cut from v1.)

---

## 5. Re-tagging real signals

### 5.1 Schlotterbeck — replace the proxy with a real defence row (v1)
Current state: `team_signal` Germany/tactical/weaken/minor (the "APPROX proxy … TODO: add concede
channel" row), source `https://www.bbc.co.uk/sport/football/articles/cgk6rzd5g58o`,
`valid_until=2026-07-19`.

Procedure:
1. Delete the proxy: `DELETE FROM team_signal WHERE team='Germany' AND category='tactical'`.
2. Insert a player_status defence row:
   ```python
   player_status.upsert_status(
       conn, team="Germany", player="Nico Schlotterbeck",
       tier="key", status="out", confidence=0.80,
       source_url="https://www.bbc.co.uk/sport/football/articles/cgk6rzd5g58o",
       valid_until="2026-07-19", affects="defense",
       notes="1st-choice CB out for tournament; modeled via concede channel (raises opponents' xG).")
   ```
3. **Trust-gate handling.** Credibility is **derived from the source count**, not the `confidence`
   arg: a single non-official source ⇒ `derive_credibility(1, False)=0.50` < `ACTIVE_CRED_THRESHOLD=0.70`
   ⇒ row lands `pending=1` and will not affect λ until approved. Since this is a confirmed BBC ruling
   and the human (owner) is gating it, **approve it** (`intel-approve ps:<id>`, i.e. set `pending=0`)
   as the final step. (A second **genuine** independent source — never a fabricated URL — both
   auto-clears the gate AND raises cred to 0.80.) Active effect with the single source:
   `def_home = +0.14` on Germany's opponents (cred 0.50); `+0.224` if a 2nd source is added.

### 5.2 Partey — defer the production re-tag (code path ships in v1)
Ship and test the `affects='both'` code path in v1, but defer flipping the real Partey row
(`player_status` Ghana/regular/out) to `both` — it is a marginal DM signal and shouldn't gate v1
correctness. Follow-up: `UPDATE player_status SET affects='both' WHERE team='Ghana' AND player='Thomas Partey'`
(effect: atk −0.06, def +0.06 at cred 0.8).

### 5.3 Re-simulate
After the channel is verified and Schlotterbeck is active, re-run `worldcup simulate --n 100000`.
Expected: Germany's opponents gain xG, Germany's advance/title probability dips modestly.

---

## 6. Backward Compatibility & Rollout

### 6.1 Provable attack-only invariance
- New column DEFAULTs to `'attack'` at schema and migration level ⇒ every existing row behaves as
  before (`def_delta = 0`).
- Algebraic no-op: with `def=0`, `(1 + def) = 1.0` exactly (IEEE-754 `1.0 + 0.0` exact; `x*1.0 == x`
  exact for finite `x`). The formula collapses to today's `lam *= (1 + atk)`.
- The attack channel preserves both clamp layers in the same order (§4.3) ⇒ no rounding drift near
  the ±0.6 boundary.
- Legacy `intel_events` unchanged (NG2).

### 6.2 Order of operations
1. **Backup** prod DB. 2. **Migrate** (`db.migrate`, idempotent, backfills `'attack'`); verify.
3. **Ship code** (factors, `apply_intel`, upsert/validation, MCP) with full suite green.
4. **Re-tag Schlotterbeck** (insert + approve). 5. **Re-simulate**.

### 6.3 Rollback
Additive, non-destructive. Redeploy old code; leave the inert column. No down-migration.

---

## 7. Test Plan (TDD)

Update the **7 existing direct call sites** of the factor functions
(`tests/test_player_status.py`, `tests/test_team_signal.py`, and any in `test_intel*`) to unpack the
3-tuple. The existing exact-equality intel tests (`test_no_intel_is_noop`,
`test_credibility_scales_effect`, `test_adjust_clamp_bounds_delta_on_multiple_events`) must pass
**unchanged** — they are the strongest invariance guard; editing them is a red flag.

New tests (the minimal set that proves correctness):
1. **Regression / attack invariance (most important).** One of each source, all `affects='attack'`;
   assert the opponent's λ is **exactly** unchanged and the team's λ **exactly** equals today's
   single-channel formula (`==`, not `approx`).
2. **Defence channel.** `affects='defense'` weaken and strengthen: assert the team's own λ is
   unchanged, the opponent's λ moves, and the **sign** is correct (weak ⇒ opponent up; strong ⇒
   opponent down).
3. **Both.** One `affects='both'` row: assert `atk == base/2` and `def == −base/2` (exact).
4. **Migration.** Build an old-shape DB (no `affects`), run `init_schema`/`migrate`, assert the
   column exists (`notnull=1`) and every pre-existing row reads `'attack'`; run `migrate` twice for
   idempotency.
5. **E2E.** Mixed attack+defence+both across both teams through `predict`/`apply_intel`; assert the
   final λ pair and that `factors` still flows to the prediction.
6. **(fresh-DB CHECK, optional)** Inserting an out-of-domain `affects` into a fresh DB raises
   `IntegrityError` (documents the fresh-vs-migrated asymmetry).
7. **Conflict preserves tag.** Upsert a `defense` row, then upsert the same player again **without**
   `affects` (corroboration); assert the stored `affects` is still `'defense'` (not reset to attack).
8. **Migration is a hard prerequisite.** A factor SELECT against a DB whose `affects` column is
   missing raises `OperationalError`; after `init_schema`/`migrate` it returns rows defaulting to
   `'attack'`. (Proves migration must precede use — `COALESCE` does not save a missing column.)
9. **MCP validation.** The MCP `upsert_*` tools accept `affects`, default to `'attack'`, and reject an
   invalid value with `ToolError`.

The E2E (test 5) must include **one team carrying both its own attacking signal and an opponent-facing
defensive signal in the same match**, asserting both channels compose correctly on both λ's.

---

## 8. Risks & Edge Cases

| # | Risk | Mitigation |
|---|------|-----------|
| R1 | **Sign-convention bug** (most likely defect): defence must add `−base`. | Test #2 pins direction for both weaken and strengthen. |
| R2 | **`both` double-count** if full `base` added to both channels. | 0.5/0.5 split mandated (§4.2); test #3 pins exact halves. |
| R3 | **Clamp layering / product.** Clamp each channel independently (not the product); a team's own attack and its opponent's defence compose multiplicatively, so a single λ can swing up to `(1±0.6)`-per-channel. | Owner-confirmed: keep channels orthogonal, no product clamp. Documented behaviour. |
| R4 | **Team with both attacking and defensive signals.** Own λ uses its `atk`; opponent's λ uses its `def`. | Per-channel per-team accumulators; e2e covers it. |
| R5 | **Prod DB already exists** ⇒ schema-string edit alone won't add the column; missing migration ⇒ `OperationalError: no such column: affects`. | Migration is step 1; gated by the migration test. |
| R6 | **Double-clamp rounding** on the attack channel if the two clamp layers are collapsed/reordered. | Keep attack structure identical; regression test #1 uses `==`. |
| R7 | **`LAMBDA_MIN` floor on the product** (two shrinking factors). | Single `max(LAMBDA_MIN, ·)` after the product (§4.3). |
| R8 | **Schlotterbeck trust gate**: single-source ⇒ pending, silently inactive. | Re-tag procedure explicitly approves (or adds a 2nd genuine source). |
| R9 | **Corroboration reset**: a later upsert omitting `affects` overwriting a `defense`/`both` tag back to `attack`. | `affects` defaults to `None` ⇒ preserve the stored tag on update (§4.4); test #7 pins it. |

---

## 9. Scope: v1 vs Deferred

**v1 (tight):** `db.py` (schema + `migrate` + `init_schema` hook) · `player_status.py` ·
`team_signal.py` · `intel.py` · `mcp_server.py` (param + docstrings) · Schlotterbeck re-tag ·
the 5–6 tests above.

**Deferred follow-ups (separate issues):**
- Web-UI badge for the channel (display-only; would add `IntelFactor.affects` then).
- Partey production re-tag to `both` (code path + test ship in v1).
- CLI write commands for intel (none exist today); add `--affects` if/when introduced.

**Explicitly NOT touched:** `engine.py` (transparent `**kwargs`), `predict.py`, `simulate.py`,
`web_server.py`, `models.IntelFactor`, the magnitude tables, `intel_events`.

---

## 10. Open Questions for Owner
1. Confirm **no product clamp** (R3) — recommended, keeps channels orthogonal.
2. **Schlotterbeck activation**: approve the single real BBC source now (cred 0.50 ⇒ opponents
   **+14%**), or hold for a 2nd **genuine** independent source (cred 0.80 ⇒ **+22.4%**)? No fabricated
   URLs — only a real second outlet.
3. OK to **defer** the web badge and Partey production re-tag to follow-ups (recommended)?
