# Totals Calibration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Activate the inert 1X2 calibrator and add a parallel post-hoc totals calibrator (temperature + over-bias) fit on out-of-sample backtest data and applied in the totals betting path, to tame the totals overconfidence behind the paper-trading CLV leak.

**Architecture:** A new `calibrate_totals` module mirrors the existing `calibrate` module (apply/fit/store/load, identity until fitted, params in the `tuning_params` key-value table). The walk-forward backtest emits two new per-match fields so the totals calibrator can be fit alongside the 1X2 one in `engine.run_backtest`; `valuebet.value_bets_totals` applies the calibrator to `grid.over(line)` at the call site.

**Tech Stack:** Python 3.12, uv, sqlite3, pytest, ruff, mypy --strict. No new dependencies.

## Global Constraints

- New module `src/worldcup_predictor/calibrate_totals.py` mirrors `calibrate.py`: identity at default params, params persisted in the existing `tuning_params` table under key `calibration_totals`, `load` returns `None` when the key is absent (so the feature is inert/safe until fitted).
- `apply(p_over, params)`: binary temperature `tau` via power `1/tau` with a `tau == 0` guard, then `over_mult` `m` on the over leg, renormalised; returns raw `p_over` when `params` is falsy or the renormaliser `s <= 0`.
- `over_mult` spans both sides of 1.0 (do not bake in a direction); `temperature >= 1`.
- `fit` grids: `temperature` 1.00..2.00 step 0.05; `over_mult` 0.80..1.25 step 0.05. Objective: minimise mean out-of-sample **log-loss** on the over/under outcome at the **2.5** line. Reads `p_over_2_5` and `total_goals` from each OOS row.
- Backtest OOS rows gain `p_over_2_5 = grid.over(2.5)` and `total_goals = home_goals + away_goals`; existing 1X2 keys unchanged.
- `engine.run_backtest(fit_calibration=True)` fits+stores BOTH calibrators and reports totals before/after metrics under `report["calibration_totals"]`.
- `valuebet.value_bets_totals` applies the calibrator to `grid.over(line)` at the call site (does NOT mutate the shared grid); loads params once per call.
- Quality bar (all must pass): `uv run ruff check src/ tests/`, `uv run ruff format --check src/ tests/`, `uv run mypy src/` (--strict), `uv run pytest -q`.
- All identifiers, comments, commit messages in English. Conventional Commits + `Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>` trailer.

---

### Task 1: `calibrate_totals` module

**Files:**
- Create: `src/worldcup_predictor/calibrate_totals.py`
- Test: `tests/test_calibrate_totals.py`

**Interfaces:**
- Produces:
  - `apply(p_over: float, params: dict[str, float] | None) -> float`
  - `fit(oos: list[dict[str, float]], temp_grid: list[float] | None = None, over_grid: list[float] | None = None) -> dict[str, float]` — reads `p_over_2_5` and `total_goals` from each row; returns `{"temperature", "over_mult", "logloss"}`.
  - `store(conn, params: dict[str, float], meta: dict[str, object] | None = None) -> None`
  - `load(conn) -> dict[str, float] | None`
  - Module constant `LINE = 2.5`, key `CALIB_TOTALS_KEY = "calibration_totals"`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_calibrate_totals.py`:

```python
import math

from worldcup_predictor import calibrate_totals as ct
from worldcup_predictor import db


def test_apply_identity_when_unset():
    assert ct.apply(0.62, None) == 0.62


def test_apply_identity_at_default_params():
    out = ct.apply(0.62, {"temperature": 1.0, "over_mult": 1.0})
    assert abs(out - 0.62) < 1e-9


def test_temperature_flattens_overconfident_over_toward_half():
    out = ct.apply(0.80, {"temperature": 2.0, "over_mult": 1.0})
    assert 0.5 < out < 0.80  # pulled toward 0.5, still above it


def test_temperature_flattens_overconfident_under_toward_half():
    out = ct.apply(0.20, {"temperature": 2.0, "over_mult": 1.0})
    assert 0.20 < out < 0.5  # a confident-under prob also rises toward 0.5


def test_over_mult_above_one_raises_over():
    out = ct.apply(0.50, {"temperature": 1.0, "over_mult": 1.2})
    assert out > 0.50


def test_over_mult_below_one_lowers_over():
    out = ct.apply(0.50, {"temperature": 1.0, "over_mult": 0.8})
    assert out < 0.50


def test_apply_handles_extremes_and_tau_zero():
    assert ct.apply(1.0, {"temperature": 1.5, "over_mult": 1.0}) == 1.0
    assert ct.apply(0.0, {"temperature": 1.5, "over_mult": 1.0}) == 0.0
    # tau == 0 must not divide by zero; treated as identity exponent
    assert abs(ct.apply(0.6, {"temperature": 0.0, "over_mult": 1.0}) - 0.6) < 1e-9


def test_fit_returns_identity_on_calibrated_data():
    # model says P(over)=0.6 and overs really happen 60% of the time -> no correction
    oos = [{"p_over_2_5": 0.6, "total_goals": 3}] * 60 + [{"p_over_2_5": 0.6, "total_goals": 2}] * 40
    params = ct.fit(oos)
    assert params["temperature"] == 1.0
    assert params["over_mult"] == 1.0


def test_fit_tames_overconfidence_and_improves_logloss():
    # model is wildly overconfident (P(over)=0.8) but overs happen only ~50%
    oos = [{"p_over_2_5": 0.8, "total_goals": 3}] * 50 + [{"p_over_2_5": 0.8, "total_goals": 2}] * 50
    params = ct.fit(oos)
    assert params["temperature"] > 1.0  # learns to flatten

    def mean_ll(p):
        tot = 0.0
        for r in oos:
            po = max(1e-12, min(1 - 1e-12, ct.apply(r["p_over_2_5"], p)))
            y = 1 if r["total_goals"] > ct.LINE else 0
            tot += -(y * math.log(po) + (1 - y) * math.log(1 - po))
        return tot / len(oos)

    assert mean_ll(params) < mean_ll(None)  # calibration improves out-of-sample log-loss


def test_store_and_load_roundtrip(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.init_schema(conn)
    assert ct.load(conn) is None  # unset -> None (totals stay raw)
    ct.store(conn, {"temperature": 1.4, "over_mult": 1.1}, meta={"n_test": 100})
    assert ct.load(conn) == {"temperature": 1.4, "over_mult": 1.1}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_calibrate_totals.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'worldcup_predictor.calibrate_totals'`

- [ ] **Step 3: Write the module**

Create `src/worldcup_predictor/calibrate_totals.py`:

```python
"""Parametric post-hoc calibration of the over/under (totals) probability.

Mirrors ``calibrate`` (the 1X2 calibrator) for the binary over/under at the 2.5 line:
- ``temperature`` (tau >= 1): flattens an over-confident over/under split toward 50/50.
- ``over_mult`` (m): multiplies the over leg to correct a systematic over/under lean
  (spans both sides of 1.0 -- the fit decides the direction).

Identity at ``temperature=1, over_mult=1`` so an un-fitted system is unchanged. Parameters
live in the ``tuning_params`` table (key ``calibration_totals``); ``valuebet`` loads them and
applies the transform to ``grid.over(line)`` before computing the edge.

Import direction: ``valuebet`` -> ``calibrate_totals``. No cycle.
"""

from __future__ import annotations

import json
import math
import sqlite3
import time

CALIB_TOTALS_KEY = "calibration_totals"
LINE = 2.5
TEMP_GRID = [round(1.0 + 0.05 * i, 2) for i in range(21)]  # 1.00 .. 2.00
OVER_GRID = [round(0.80 + 0.05 * i, 2) for i in range(10)]  # 0.80 .. 1.25


def apply(p_over: float, params: dict[str, float] | None) -> float:
    if not params:
        return p_over
    tau = float(params.get("temperature", 1.0))
    m = float(params.get("over_mult", 1.0))
    inv = 1.0 / tau if tau else 1.0
    q_over = p_over**inv
    q_under = (1.0 - p_over) ** inv
    q_over *= m
    s = q_over + q_under
    if s <= 0:
        return p_over
    return q_over / s


def mean_logloss(oos: list[dict[str, float]], params: dict[str, float] | None) -> float:
    """Mean log-loss of the calibrated over-probability vs the over/under outcome at 2.5."""
    if not oos:
        return 0.0
    total = 0.0
    for r in oos:
        po = apply(float(r["p_over_2_5"]), params)
        po = max(1e-12, min(1.0 - 1e-12, po))
        y = 1 if float(r["total_goals"]) > LINE else 0
        total += -(y * math.log(po) + (1 - y) * math.log(1.0 - po))
    return total / len(oos)


def fit(
    oos: list[dict[str, float]],
    temp_grid: list[float] | None = None,
    over_grid: list[float] | None = None,
) -> dict[str, float]:
    """Grid-search (temperature, over_mult) to minimise mean out-of-sample log-loss at 2.5."""
    temp_grid = temp_grid or TEMP_GRID
    over_grid = over_grid or OVER_GRID
    best: tuple[float, float, float] | None = None
    for tau in temp_grid:
        for m in over_grid:
            params = {"temperature": tau, "over_mult": m}
            loss = mean_logloss(oos, params)
            if best is None or loss < best[0]:
                best = (loss, tau, m)
    assert best is not None
    return {"temperature": best[1], "over_mult": best[2], "logloss": best[0]}


def store(
    conn: sqlite3.Connection, params: dict[str, float], meta: dict[str, object] | None = None
) -> None:
    payload: dict[str, object] = {
        "temperature": float(params.get("temperature", 1.0)),
        "over_mult": float(params.get("over_mult", 1.0)),
    }
    if meta:
        payload.update(meta)
    conn.execute(
        "INSERT INTO tuning_params(key,value,updated_at) VALUES (?,?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
        (CALIB_TOTALS_KEY, json.dumps(payload), time.time()),
    )
    conn.commit()


def load(conn: sqlite3.Connection) -> dict[str, float] | None:
    row = conn.execute(
        "SELECT value FROM tuning_params WHERE key=?", (CALIB_TOTALS_KEY,)
    ).fetchone()
    if not row or not row[0]:
        return None
    try:
        d = json.loads(row[0])
    except (ValueError, TypeError):
        return None
    return {
        "temperature": float(d.get("temperature", 1.0)),
        "over_mult": float(d.get("over_mult", 1.0)),
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_calibrate_totals.py -q`
Expected: PASS (10 tests)

- [ ] **Step 5: Lint, format, type-check**

Run: `uv run ruff check src/worldcup_predictor/calibrate_totals.py tests/test_calibrate_totals.py && uv run ruff format src/worldcup_predictor/calibrate_totals.py tests/test_calibrate_totals.py && uv run mypy src/worldcup_predictor/calibrate_totals.py`
Expected: all clean

- [ ] **Step 6: Commit**

```bash
git add src/worldcup_predictor/calibrate_totals.py tests/test_calibrate_totals.py
git commit -m "feat(calibrate): add post-hoc totals calibrator (temperature + over-bias)

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

---

### Task 2: Emit totals fields from the walk-forward backtest

**Files:**
- Modify: `src/worldcup_predictor/backtest.py:79-89` (the `out.append({...})` in `walk_forward_predictions`)
- Test: `tests/test_backtest.py` (extend `_FakeGrid`; extend `test_walk_forward_outputs_out_of_sample`)

**Interfaces:**
- Consumes: nothing new (the `grid` and `m["home_goals"]`/`m["away_goals"]` are already in scope at backtest.py:72-87).
- Produces: each OOS row dict now additionally has `p_over_2_5: float` and `total_goals: int`. Used by Task 3.

- [ ] **Step 1: Write the failing test changes**

In `tests/test_backtest.py`, give `_FakeGrid` an `over` method (add inside the class, after the class attributes):

```python
class _FakeGrid:
    home_win = 0.5
    draw = 0.3
    away_win = 0.2

    def over(self, line: float) -> float:
        return 0.55
```

Then extend the assertions in `test_walk_forward_outputs_out_of_sample` (after the existing `assert {"date", "home", "away"} <= set(r)` line):

```python
        assert {"p_over_2_5", "total_goals"} <= set(r)
        assert r["p_over_2_5"] == 0.55
        assert isinstance(r["total_goals"], int)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_backtest.py::test_walk_forward_outputs_out_of_sample -q`
Expected: FAIL with `KeyError`/`assert ... <= set(r)` (the new keys are absent)

- [ ] **Step 3: Add the two fields**

In `src/worldcup_predictor/backtest.py`, change the `out.append({...})` block (currently lines 79-89) to add the two keys:

```python
            out.append(
                {
                    "date": str(pd.Timestamp(m["date"]).date()),
                    "home": home,
                    "away": away,
                    "p_home": grid.home_win,
                    "p_draw": grid.draw,
                    "p_away": grid.away_win,
                    "outcome": _outcome(int(m["home_goals"]), int(m["away_goals"])),
                    "p_over_2_5": grid.over(2.5),
                    "total_goals": int(m["home_goals"]) + int(m["away_goals"]),
                }
            )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_backtest.py -q`
Expected: PASS (all backtest tests)

- [ ] **Step 5: Lint, format, type-check**

Run: `uv run ruff check src/worldcup_predictor/backtest.py tests/test_backtest.py && uv run ruff format src/worldcup_predictor/backtest.py tests/test_backtest.py && uv run mypy src/worldcup_predictor/backtest.py`
Expected: all clean

- [ ] **Step 6: Commit**

```bash
git add src/worldcup_predictor/backtest.py tests/test_backtest.py
git commit -m "feat(backtest): emit p_over_2_5 and total_goals per out-of-sample match

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

---

### Task 3: Fit + store the totals calibrator in `run_backtest`

**Files:**
- Modify: `src/worldcup_predictor/engine.py:279-298` (the `if fit_calibration:` block in `run_backtest`)
- Test: `tests/test_backtest.py` (extend `test_engine_run_backtest_reports_and_fits`)

**Interfaces:**
- Consumes: `calibrate_totals.fit/store` (Task 1); OOS rows with `p_over_2_5`/`total_goals` (Task 2).
- Produces: `report["calibration_totals"] = {"temperature", "over_mult", "logloss_before", "logloss_after", "n_test"}`; the `calibration_totals` row persisted in `tuning_params`.

- [ ] **Step 1: Write the failing test changes**

In `tests/test_backtest.py`, update `test_engine_run_backtest_reports_and_fits`. Add `p_over_2_5`/`total_goals` to the synthetic OOS rows (overconfident-over: model says 0.8 over, but overs land only 1/3), and add totals assertions. Replace the body from the `oos = ...` lines through the end with:

```python
    # 1X2: draw-heavy. Totals: model says P(over)=0.8 but overs land only 1/3 of the time.
    oos = [
        {"p_home": 0.7, "p_draw": 0.15, "p_away": 0.15, "outcome": 1,
         "p_over_2_5": 0.8, "total_goals": 2}
    ] * 20
    oos += [
        {"p_home": 0.7, "p_draw": 0.15, "p_away": 0.15, "outcome": 0,
         "p_over_2_5": 0.8, "total_goals": 4}
    ] * 10
    monkeypatch.setattr(backtest, "walk_forward_predictions", lambda *a, **k: oos)

    rep = engine.run_backtest(conn, fit_calibration=True)
    assert rep["n"] == 30
    assert "model_rps" in rep and "reliability" in rep and "ece" in rep
    assert "calibration" in rep
    assert calibrate.load(conn) is not None  # 1X2 params persisted
    assert rep["calibration"]["draw_mult"] > 1.0  # learned to raise draws
    assert rep["calibration"]["rps_after"] <= rep["calibration"]["rps_before"]

    from worldcup_predictor import calibrate_totals

    assert "calibration_totals" in rep
    assert calibrate_totals.load(conn) is not None  # totals params persisted
    assert rep["calibration_totals"]["temperature"] > 1.0  # learned to flatten
    assert rep["calibration_totals"]["logloss_after"] <= rep["calibration_totals"]["logloss_before"]
```

(20 overs-land-under + 10 overs-land-over = overs only 1/3 of the time, so a model that says 0.8 over is overconfident and the fit must flatten — `temperature > 1.0`.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_backtest.py::test_engine_run_backtest_reports_and_fits -q`
Expected: FAIL with `KeyError: 'calibration_totals'`

- [ ] **Step 3: Fit + store the totals calibrator**

In `src/worldcup_predictor/engine.py`, add the import near the existing `from worldcup_predictor import calibrate as _calibrate` (top of file):

```python
from worldcup_predictor import calibrate_totals as _calibrate_totals
```

Then inside `run_backtest`, at the end of the `if fit_calibration:` block (immediately after `report["calibration"] = {**knobs, **meta}`, still inside the `if`), append:

```python
        t_params = _calibrate_totals.fit(oos)
        t_knobs = {
            "temperature": t_params["temperature"],
            "over_mult": t_params["over_mult"],
        }
        ll_before = _calibrate_totals.mean_logloss(oos, None)
        ll_after = _calibrate_totals.mean_logloss(oos, t_knobs)
        t_meta = {"n_test": len(oos), "logloss_before": ll_before, "logloss_after": ll_after}
        _calibrate_totals.store(conn, t_knobs, meta=t_meta)
        report["calibration_totals"] = {**t_knobs, **t_meta}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_backtest.py -q`
Expected: PASS (all backtest tests, including the updated fit test)

- [ ] **Step 5: Lint, format, type-check**

Run: `uv run ruff check src/worldcup_predictor/engine.py tests/test_backtest.py && uv run ruff format src/worldcup_predictor/engine.py tests/test_backtest.py && uv run mypy src/worldcup_predictor/engine.py`
Expected: all clean

- [ ] **Step 6: Commit**

```bash
git add src/worldcup_predictor/engine.py tests/test_backtest.py
git commit -m "feat(engine): fit and store the totals calibrator in run_backtest

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

---

### Task 4: Apply the totals calibrator in the betting path

**Files:**
- Modify: `src/worldcup_predictor/valuebet.py` (import + `value_bets_totals`, the `our_over = grid.over(line)` at line 217)
- Test: `tests/test_valuebet.py`

**Interfaces:**
- Consumes: `calibrate_totals.apply/load/store` (Task 1).
- Produces: no new public interface; `value_bets_totals` now applies the stored totals calibrator to the over probability before computing the edge.

- [ ] **Step 1: Write the failing test**

In `tests/test_valuebet.py`, add a test. It stores a deliberately strong "lower the over" calibrator, then asserts the over probability used by `value_bets_totals` is pulled below the raw `grid.over(line)` (so a borderline over bet disappears):

```python
def test_value_bets_totals_applies_calibrator(tmp_path):
    from worldcup_predictor import calibrate_totals as ct

    conn = _conn(tmp_path)
    model = _model()  # Strong vs Weak -> high raw P(over 2.5)
    conn.execute(
        "INSERT INTO odds_totals(match_id,bookmaker,line,price_over,price_under,fetched_at)"
        " VALUES (1,'bookA',2.5,1.90,1.90,1.0),(1,'bookB',2.5,1.92,1.88,1.0)"
    )
    conn.commit()

    raw = valuebet.value_bets_totals(conn, model, min_edge=0.05)
    raw_over = [b for b in raw if b["outcome"] == "over"]
    assert raw_over, "expected a raw over value bet before calibration"

    # A strong flatten + lower-over calibrator must shrink our over prob toward the market,
    # removing the over edge.
    ct.store(conn, {"temperature": 2.0, "over_mult": 0.80})
    cal = valuebet.value_bets_totals(conn, model, min_edge=0.05)
    cal_over = [b for b in cal if b["outcome"] == "over"]
    assert not cal_over, "calibration should remove the fake over edge"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_valuebet.py::test_value_bets_totals_applies_calibrator -q`
Expected: FAIL (calibration not applied yet, so the over bet still appears)

- [ ] **Step 3: Apply the calibrator**

In `src/worldcup_predictor/valuebet.py`, add the import alongside the existing `from worldcup_predictor.predict import adjusted_grid, predict_match` (near line 20):

```python
from worldcup_predictor import calibrate_totals
```

Then in `value_bets_totals`, just before the `for r in rows:` loop (after `bets: list[dict[str, Any]] = []`, around line 201), load the params once:

```python
    totals_params = calibrate_totals.load(conn)
```

And change the `our_over` assignment (line 217) from:

```python
        our_over = grid.over(line)
```

to:

```python
        our_over = calibrate_totals.apply(grid.over(line), totals_params)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_valuebet.py -q`
Expected: PASS (all valuebet tests, including the new one)

- [ ] **Step 5: Lint, format, type-check**

Run: `uv run ruff check src/worldcup_predictor/valuebet.py tests/test_valuebet.py && uv run ruff format src/worldcup_predictor/valuebet.py tests/test_valuebet.py && uv run mypy src/worldcup_predictor/valuebet.py`
Expected: all clean

- [ ] **Step 6: Commit**

```bash
git add src/worldcup_predictor/valuebet.py tests/test_valuebet.py
git commit -m "feat(valuebet): apply the totals calibrator to the over/under probability

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

---

## Final verification (after all tasks)

- [ ] Full suite + quality bar:

Run: `uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/ && uv run mypy src/ && uv run pytest -q`
Expected: ruff clean, format clean, mypy `Success`, all tests pass.

## Deployment (after merge — runs on prod)

The calibrators are inert until fitted. To activate on prod:

- [ ] `uv run worldcup backtest --fit-calibration --test-years 3` (one-off, ~20-40 min walk-forward; fits BOTH the 1X2 and totals calibrators and stores them). Note the reported `calibration` and `calibration_totals` before/after metrics.
- [ ] Re-simulate so the activated 1X2 calibrator reaches live title odds: `uv run worldcup simulate --n 100000`.
- [ ] Restart the live service: `systemctl --user restart worldcup.service`.
- [ ] Sanity-check: `uv run worldcup value-bets --min-edge 0.05` shows fewer/smaller totals over/under edges than before; `worldcup paper-status` baseline unchanged (settlement is historical).
