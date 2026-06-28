# Knockout Bracket Topology Fix — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the wrong (consecutive-pairing) knockout feeder topology in both the Monte-Carlo simulation and the knockout tree by introducing one shared source of truth, and improve the tree UI (no English names, kickoff times, fit one screen, tab moved earlier).

**Architecture:** A new pure `bracket_topology.py` holds the R32 composition template, fixture-number constants, the official `FEEDERS` map, and a pure `progress()` helper that resolves every knockout fixture's winner from the 16 R32 winners. `simulate.py` and `bracket.py` both consume it; `bracket.py` additionally maps feed rows to fixture numbers via group standings.

**Tech Stack:** Python 3.12 (uv-managed), SQLite, FastAPI, vanilla JS/CSS. Existing modules: `simulate`, `bracket`, `config`, `models`, `predict`, `static/app.js`.

## Global Constraints

- Python 3.12, uv-managed: run everything via `uv run ...` (the venv has **no pip**).
- Quality bar before every commit: `uv run ruff check src/ tests/`, `uv run ruff format src/ tests/`, `uv run mypy src/`, `uv run pytest -q`.
- mypy runs `--strict`: full type annotations on every new/changed function.
- 100-char line limit, double quotes (ruff-enforced).
- Conventional Commits; append `Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>` to every commit message (after a blank line).
- Tests use `tmp_path` DBs only; never touch `data/worldcup.db`.
- Knockout matches predict at neutral venue (`neutral=True`); knockout predictions never persist (no `match_id`).
- The official feeder structure (verified against Wikipedia's 2026 knockout bracket):
  `89=(73,75) 90=(74,77) 91=(76,78) 92=(79,80) 93=(83,84) 94=(81,82) 95=(86,88) 96=(85,87)`,
  `97=(89,90) 98=(93,94) 99=(91,92) 100=(95,96)`, `101=(97,98) 102=(99,100)`, `104=(101,102)`,
  3rd place `103` = losers of `101` and `102`.

---

### Task 1: Create `bracket_topology.py` (shared source of truth)

**Files:**
- Create: `src/worldcup_predictor/bracket_topology.py`
- Test: `tests/test_bracket_topology.py` (new)

**Interfaces:**
- Produces:
  - `R32_TEMPLATE: list[tuple[str, str]]` — the 16 R32 slot pairs (moved verbatim from `simulate._R32_TEMPLATE`), index `i` ↔ fixture `73+i`.
  - `R32_FIXTURES = tuple(range(73, 89))`, `R16_FIXTURES = tuple(range(89, 97))`, `QF_FIXTURES = tuple(range(97, 101))`, `SF_FIXTURES = (101, 102)`, `THIRD_FIXTURE = 103`, `FINAL_FIXTURE = 104`.
  - `FEEDERS: dict[int, tuple[int, int]]` — the 15 feeder entries above.
  - `progress(r32_winners: list[str], pick: Callable[[str, str], str]) -> dict[int, str]` — resolves every fixture's winner from the 16 R32 winners using `FEEDERS`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_bracket_topology.py`:

```python
from worldcup_predictor import bracket_topology as bt


def test_fixture_ranges():
    assert bt.R32_FIXTURES == tuple(range(73, 89))
    assert bt.R16_FIXTURES == tuple(range(89, 97))
    assert bt.QF_FIXTURES == tuple(range(97, 101))
    assert bt.SF_FIXTURES == (101, 102)
    assert bt.THIRD_FIXTURE == 103 and bt.FINAL_FIXTURE == 104


def test_feeders_match_official_structure():
    assert bt.FEEDERS == {
        89: (73, 75), 90: (74, 77), 91: (76, 78), 92: (79, 80),
        93: (83, 84), 94: (81, 82), 95: (86, 88), 96: (85, 87),
        97: (89, 90), 98: (93, 94), 99: (91, 92), 100: (95, 96),
        101: (97, 98), 102: (99, 100),
        104: (101, 102),
    }
    # 3rd place is handled separately (SF losers), so it is NOT a feeder entry.
    assert bt.THIRD_FIXTURE not in bt.FEEDERS


def test_r32_template_has_16_pairs():
    assert len(bt.R32_TEMPLATE) == 16
    assert bt.R32_TEMPLATE[0] == ("RU_A", "RU_B")  # fixture 73
    assert bt.R32_TEMPLATE[1] == ("W_E", "3")  # fixture 74


def test_progress_pairs_via_official_feeders_not_consecutive():
    # Winner of fixture 73+i is labelled "W{73+i}".
    r32 = [f"W{73 + i}" for i in range(16)]
    seen: list[tuple[str, str]] = []

    def pick(a: str, b: str) -> str:
        seen.append((a, b))
        return a  # deterministic: first side always advances

    win = bt.progress(r32, pick)
    # R16 fixture 89 is decided between winners of fixtures 73 and 75 (official),
    # NOT 73 and 74 (the old consecutive bug).
    assert ("W73", "W75") in seen
    assert ("W73", "W74") not in seen
    # Every fixture resolved; champion (104) flows from its feeders.
    assert set(win) == set(range(73, 89)) | set(bt.FEEDERS)
    assert win[89] == "W73" and win[104] == win[101]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_bracket_topology.py -q`
Expected: FAIL (`bracket_topology` module not found).

- [ ] **Step 3: Implement `bracket_topology.py`**

Create `src/worldcup_predictor/bracket_topology.py`:

```python
from __future__ import annotations

from collections.abc import Callable

# R32 composition (FIFA Annex C slots). Index i corresponds to fixture number 73+i.
# "3" marks a best-third-place slot. Moved here from simulate so both the simulation and the
# knockout tree share one definition of the bracket structure.
R32_TEMPLATE: list[tuple[str, str]] = [
    ("RU_A", "RU_B"),  # 73
    ("W_E", "3"),  # 74
    ("W_F", "RU_C"),  # 75
    ("W_C", "RU_F"),  # 76
    ("W_I", "3"),  # 77
    ("RU_E", "RU_I"),  # 78
    ("W_A", "3"),  # 79
    ("W_L", "3"),  # 80
    ("W_D", "3"),  # 81
    ("W_G", "3"),  # 82
    ("RU_K", "RU_L"),  # 83
    ("W_H", "RU_J"),  # 84
    ("W_B", "3"),  # 85
    ("W_J", "RU_H"),  # 86
    ("W_K", "3"),  # 87
    ("RU_D", "RU_G"),  # 88
]

R32_FIXTURES: tuple[int, ...] = tuple(range(73, 89))  # 73..88
R16_FIXTURES: tuple[int, ...] = tuple(range(89, 97))  # 89..96
QF_FIXTURES: tuple[int, ...] = tuple(range(97, 101))  # 97..100
SF_FIXTURES: tuple[int, ...] = (101, 102)
THIRD_FIXTURE: int = 103
FINAL_FIXTURE: int = 104

# Each non-R32 fixture's two feeder fixtures (the winner of each advances into it). The 3rd-place
# match (103) is the two SF losers, handled separately, so it is intentionally absent here.
FEEDERS: dict[int, tuple[int, int]] = {
    89: (73, 75),
    90: (74, 77),
    91: (76, 78),
    92: (79, 80),
    93: (83, 84),
    94: (81, 82),
    95: (86, 88),
    96: (85, 87),
    97: (89, 90),
    98: (93, 94),
    99: (91, 92),
    100: (95, 96),
    101: (97, 98),
    102: (99, 100),
    104: (101, 102),
}


def progress(r32_winners: list[str], pick: Callable[[str, str], str]) -> dict[int, str]:
    """Resolve every knockout fixture's winner from the 16 R32 winners using the official feeders.

    ``r32_winners[i]`` is the winner of fixture ``R32_FIXTURES[i]``. ``pick(a, b)`` returns the
    winner of a single tie. Returns a map from every fixture number (73..102, 104) to its winner.
    """
    win: dict[int, str] = {R32_FIXTURES[i]: r32_winners[i] for i in range(16)}
    for fx in (*R16_FIXTURES, *QF_FIXTURES, *SF_FIXTURES, FINAL_FIXTURE):
        fa, fb = FEEDERS[fx]
        win[fx] = pick(win[fa], win[fb])
    return win
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_bracket_topology.py -q`
Expected: PASS.

- [ ] **Step 5: Lint, type, commit**

```bash
uv run ruff check src/ tests/ && uv run ruff format src/ tests/ && uv run mypy src/
git add src/worldcup_predictor/bracket_topology.py tests/test_bracket_topology.py
git commit -m "feat(topology): shared bracket feeder structure + progress helper

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

---

### Task 2: Fix `simulate.py` round progression

**Files:**
- Modify: `src/worldcup_predictor/simulate.py` (remove local `_R32_TEMPLATE`, import from topology, replace the consecutive-pairing loop)
- Test: `tests/test_simulate.py` (add a progression test; create the file if it does not exist — check first with `ls tests/test_simulate.py`)

**Interfaces:**
- Consumes: `bracket_topology.R32_TEMPLATE`, `R32_FIXTURES`, `R16_FIXTURES`, `QF_FIXTURES`, `SF_FIXTURES`, `FINAL_FIXTURE`, `FEEDERS`, `progress` (Task 1).
- Produces: unchanged public API (`simulate_tournament`, `build_r32`, `best_thirds`, `standings_from_results`); internal progression now uses official feeders.

- [ ] **Step 1: Write the failing test**

First check whether the simulate test file exists: `ls tests/test_simulate.py 2>/dev/null` — if absent, create it with this content; if present, append the test function.

```python
from worldcup_predictor import simulate
from worldcup_predictor import bracket_topology as bt


def test_simulate_uses_official_feeders_for_r16():
    # 16 distinct R32 winners; a pick that records every matchup and lets the first side advance.
    r32 = [f"W{73 + i}" for i in range(16)]
    seen: list[frozenset[str]] = []

    def pick(a: str, b: str) -> str:
        seen.append(frozenset((a, b)))
        return a

    win = bt.progress(r32, pick)
    # The R16 must pair fixtures (73,75),(74,77),... — the official structure — and must NOT pair
    # (73,74) the way the old `zip(it, it)` consecutive logic did.
    assert frozenset(("W73", "W75")) in seen
    assert frozenset(("W74", "W77")) in seen
    assert frozenset(("W73", "W74")) not in seen
    assert win[bt.FINAL_FIXTURE] == "W73"  # first side always advances → fixture 73's winner
```

(Note: this asserts the shared `progress` helper that `simulate` now uses for its bracket; a full Monte-Carlo run is covered by the existing simulate tests.)

- [ ] **Step 2: Run test to verify it fails or passes appropriately**

Run: `uv run pytest tests/test_simulate.py::test_simulate_uses_official_feeders_for_r16 -q`
Expected: PASS already if Task 1 is merged (it tests `bt.progress`); this test pins the behavior `simulate` must use. Proceed to wire `simulate` to it in Step 3, then confirm the full suite stays green.

- [ ] **Step 3: Rewrite the progression in `simulate.py`**

In `src/worldcup_predictor/simulate.py`:

a. Add the import near the other `worldcup_predictor` imports (around line 15, after `from worldcup_predictor.models import GroupRow`):

```python
from worldcup_predictor import bracket_topology as _bt
```

b. Delete the local `_R32_TEMPLATE` list (the 18-line block starting `_R32_TEMPLATE: list[tuple[str, str]] = [`). In `build_r32`, change the loop header `for left, right in _R32_TEMPLATE:` to `for left, right in _bt.R32_TEMPLATE:`.

c. Replace the progression loop. Find this block:

```python
        bracket = build_r32(winners, runners, [r.team for r in qual_thirds])
        # Winning round R32/R16/QF/SF/Final credits reaching r16/qf/sf/final/title.
        for round_key in ("r16", "qf", "sf", "final", "title"):
            winners_round = [_knockout_winner(a, b, probs, grids, rng) for a, b in bracket]
            for w in winners_round:
                counts[w][round_key] += 1
            it = iter(winners_round)
            bracket = list(zip(it, it))  # noqa: B905 - pair winners for the next round
```

with:

```python
        r32 = build_r32(winners, runners, [r.team for r in qual_thirds])
        r32_winners = [_knockout_winner(a, b, probs, grids, rng) for a, b in r32]
        # Resolve the whole knockout tree via the official feeders (not consecutive pairing).
        win = _bt.progress(r32_winners, lambda a, b: _knockout_winner(a, b, probs, grids, rng))
        # Winning an R32/R16/QF/SF/Final match credits reaching r16/qf/sf/final/title.
        for fx in _bt.R32_FIXTURES:
            counts[win[fx]]["r16"] += 1
        for fx in _bt.R16_FIXTURES:
            counts[win[fx]]["qf"] += 1
        for fx in _bt.QF_FIXTURES:
            counts[win[fx]]["sf"] += 1
        for fx in _bt.SF_FIXTURES:
            counts[win[fx]]["final"] += 1
        counts[win[_bt.FINAL_FIXTURE]]["title"] += 1
```

Note: `win` already incorporates `r32_winners` at the R32 fixtures (the first `r32_winners` list is reused inside `progress` via the initial map), so do not double-count — the `r16` credit above reads `win[fx]` for `fx in R32_FIXTURES`, which equals the R32 winners.

- [ ] **Step 4: Run the focused test + full suite**

Run: `uv run pytest tests/test_simulate.py tests/test_bracket_topology.py -q`
Expected: PASS.

Run: `uv run pytest -q`
Expected: PASS (the existing simulate/forecast tests still pass; the bracket pairing changed but the simulate tests assert shapes/totals, not specific opponents).

- [ ] **Step 5: Lint, type, commit**

```bash
uv run ruff check src/ tests/ && uv run ruff format src/ tests/ && uv run mypy src/
git add src/worldcup_predictor/simulate.py tests/test_simulate.py
git commit -m "fix(simulate): use official knockout feeders, not consecutive pairing

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

---

### Task 3: `bracket.py` — group standings + R32 fixture mapping helpers

**Files:**
- Modify: `src/worldcup_predictor/bracket.py` (add helpers; existing functions unchanged this task)
- Test: `tests/test_bracket.py` (add tests)

**Interfaces:**
- Consumes: `bracket_topology.R32_TEMPLATE` (Task 1); `simulate.standings_from_results`; `config.GROUPS`.
- Produces:
  - `_group_winners_runners(conn) -> tuple[dict[str, str], dict[str, str]]` — `(winners, runners)` keyed by group id, only for groups whose 6 matches are all FINISHED.
  - `_r32_signatures(winners, runners) -> dict[int, tuple[str, str | frozenset[str]]]` — per fixture 73-88, either `("anchor", team)` (the W_x/RU_x side of a "W_x vs 3rd" fixture) or `("pair", frozenset({teamA, teamB}))`.
  - `fixture_of_r32_row(home, away, sigs) -> int | None` — the fixture number a feed R32 row belongs to, or None if not yet identifiable.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_bracket.py`:

```python
def test_group_winners_runners(tmp_path):
    from worldcup_predictor import bracket, db, ingest

    conn = db.connect(tmp_path / "g.db")
    db.init_schema(conn)
    ingest.seed_teams_and_fixtures(conn)
    # Make group A complete: Mexico wins all, South Africa second. (4 teams → 6 matches.)
    a = ["Mexico", "South Africa", "South Korea", "Czech Republic"]
    import itertools

    for h, away in itertools.combinations(a, 2):
        hs, as_ = (3, 0) if h == "Mexico" else (1, 0) if h == "South Africa" else (0, 0)
        conn.execute(
            "UPDATE matches SET home_score=?, away_score=?, status='FINISHED' "
            "WHERE stage='group' AND home_team=? AND away_team=?",
            (hs, as_, h, away),
        )
    conn.commit()
    winners, runners = bracket._group_winners_runners(conn)
    assert winners["A"] == "Mexico"
    assert runners["A"] == "South Africa"
    assert "B" not in winners  # group B not finished → absent


def test_fixture_of_r32_row_maps_by_signature():
    from worldcup_predictor import bracket

    winners = {g: f"W_{g}" for g in "ABCDEFGHIJKL"}
    runners = {g: f"RU_{g}" for g in "ABCDEFGHIJKL"}
    sigs = bracket._r32_signatures(winners, runners)
    # Fixture 73 = RU_A vs RU_B (a "pair" fixture): identified by its two-team set.
    assert bracket.fixture_of_r32_row("RU_A", "RU_B", sigs) == 73
    assert bracket.fixture_of_r32_row("RU_B", "RU_A", sigs) == 73  # order-independent
    # Fixture 74 = W_E vs 3rd (an "anchor" fixture): identified by the W_E side, any third.
    assert bracket.fixture_of_r32_row("W_E", "RU_C", sigs) == 74  # "RU_C" stands in for a 3rd
    assert bracket.fixture_of_r32_row("SomeThird", "W_E", sigs) == 74
    # A row whose teams match nothing yet → None.
    assert bracket.fixture_of_r32_row("Nobody", "Nobody2", sigs) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_bracket.py::test_group_winners_runners tests/test_bracket.py::test_fixture_of_r32_row_maps_by_signature -q`
Expected: FAIL (`_group_winners_runners` / `_r32_signatures` / `fixture_of_r32_row` not defined).

- [ ] **Step 3: Implement the helpers**

In `src/worldcup_predictor/bracket.py`, add imports near the top:

```python
from worldcup_predictor import bracket_topology as _bt
from worldcup_predictor import config
from worldcup_predictor.simulate import standings_from_results
```

Add these functions (after `advance_prob`):

```python
def _group_winners_runners(
    conn: sqlite3.Connection,
) -> tuple[dict[str, str], dict[str, str]]:
    """Winner and runner-up per group, only for groups whose six matches are all FINISHED."""
    winners: dict[str, str] = {}
    runners: dict[str, str] = {}
    for gid, teams in config.GROUPS.items():
        rows = conn.execute(
            "SELECT home_team, away_team, home_score, away_score FROM matches "
            "WHERE stage='group' AND group_id=? AND status='FINISHED'",
            (gid,),
        ).fetchall()
        if len(rows) < 6:
            continue  # group not complete → standings not final
        results = [
            (r["home_team"], r["away_team"], int(r["home_score"]), int(r["away_score"]))
            for r in rows
        ]
        table = standings_from_results(list(teams), results)
        winners[gid] = table[0].team
        runners[gid] = table[1].team
    return winners, runners


def _resolve_slot(token: str, winners: dict[str, str], runners: dict[str, str]) -> str | None:
    """Resolve a template token ('W_E' / 'RU_C') to a team, or None for the '3' wildcard or an
    unfinished group."""
    if token == "3":
        return None
    side, gid = token.split("_")
    return winners.get(gid) if side == "W" else runners.get(gid)


def _r32_signatures(
    winners: dict[str, str], runners: dict[str, str]
) -> dict[int, tuple[str, Any]]:
    """Per R32 fixture (73-88), a signature to match a feed row: an ('anchor', team) for the
    W_x/RU_x side of a 'W_x vs 3rd' fixture, or a ('pair', frozenset) of both resolved teams."""
    sigs: dict[int, tuple[str, Any]] = {}
    for i, (ta, tb) in enumerate(_bt.R32_TEMPLATE):
        fixture = _bt.R32_FIXTURES[i]
        a = _resolve_slot(ta, winners, runners)
        b = _resolve_slot(tb, winners, runners)
        if ta == "3" or tb == "3":
            sigs[fixture] = ("anchor", b if ta == "3" else a)
        else:
            sigs[fixture] = ("pair", frozenset({a, b}))
    return sigs


def fixture_of_r32_row(
    home: str | None, away: str | None, sigs: dict[int, tuple[str, Any]]
) -> int | None:
    """The R32 fixture number a feed row belongs to, or None if not yet identifiable."""
    teams = {t for t in (home, away) if t is not None}
    if not teams:
        return None
    for fixture, (kind, sig) in sigs.items():
        if kind == "anchor" and sig is not None and sig in teams:
            return fixture
        if kind == "pair" and None not in sig and sig <= teams:
            return fixture
    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_bracket.py -q`
Expected: PASS (new helper tests + the existing bracket tests).

- [ ] **Step 5: Lint, type, commit**

```bash
uv run ruff check src/ tests/ && uv run ruff format src/ tests/ && uv run mypy src/
git add src/worldcup_predictor/bracket.py tests/test_bracket.py
git commit -m "feat(bracket): group standings + R32 fixture-mapping helpers

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

---

### Task 4: `bracket.py` — feeder-driven projection + kickoff field

**Files:**
- Modify: `src/worldcup_predictor/bracket.py` (refactor `_decide` to keyword result-params; rewrite `build_predicted_bracket`)
- Test: `tests/test_bracket.py` (rewrite the two existing projection tests to real teams + official feeders; add a kickoff test)

**Interfaces:**
- Consumes: `_group_winners_runners`, `_r32_signatures`, `fixture_of_r32_row` (Task 3); `bracket_topology.FEEDERS`, `R32_FIXTURES`, `R16_FIXTURES`, `QF_FIXTURES`, `SF_FIXTURES`, `FINAL_FIXTURE`; `advance_prob` (existing).
- Produces: `build_predicted_bracket(conn, model) -> dict` with the same top-level keys (`rounds`, `third_place`, `real_fixtures`, `total_fixtures`); each node gains a `kickoff` field; the tree always renders the full 31-slot skeleton ordered by fixture number; projection follows `FEEDERS`. `_decide` gains keyword result-params (`ext_id`, `kickoff`, `status`, `home_score`, `away_score`, `winner_team`).

> **Why real teams in tests:** the new design maps each R32 feed row to its fixture number by matching teams to *group standings*. Synthetic names like "Strong"/"Weak" belong to no group, so they no longer map — the existing two tests (which used them) must be rewritten with real group teams, and the model must be fit on those real teams (else `predict_match` raises "not in training data").

- [ ] **Step 1: Add shared test helpers + rewrite the failing tests**

At the top of `tests/test_bracket.py`, add two helpers (after the existing imports):

```python
def _finish_all_groups(conn):
    """Mark every group match FINISHED (home wins 1-0) so standings/fixture signatures resolve."""
    import itertools

    from worldcup_predictor import config

    for teams in config.GROUPS.values():
        for h, a in itertools.combinations(teams, 2):
            conn.execute(
                "UPDATE matches SET home_score=1, away_score=0, status='FINISHED' "
                "WHERE stage='group' AND home_team=? AND away_team=?",
                (h, a),
            )
    conn.commit()


def _all_teams_model():
    """A GoalModel fit on all 48 finalists so any real team predicts."""
    import numpy as np
    import pandas as pd

    from worldcup_predictor import config
    from worldcup_predictor.goal_model import GoalModel

    teams = [t for g in config.GROUPS.values() for t in g]
    rng = np.random.default_rng(0)
    rows = []
    for t in teams:
        for _ in range(4):
            opp = teams[int(rng.integers(0, len(teams)))]
            if opp == t:
                continue
            rows.append(("2024-01-01", t, opp, int(rng.integers(0, 4)), int(rng.integers(0, 3)), True))
    return GoalModel().fit(
        pd.DataFrame(
            rows, columns=["date", "home_team", "away_team", "home_goals", "away_goals", "neutral"]
        )
    )
```

Now **delete** the existing `test_build_uses_real_teams_and_predicts` and `test_actual_result_overrides_predicted_winner` (they assert the old consecutive pairing with synthetic teams) and replace them with these real-team tests:

```python
def test_projection_pairs_via_official_feeders(tmp_path):
    from worldcup_predictor import bracket, db, ingest

    conn = db.connect(tmp_path / "p.db")
    db.init_schema(conn)
    ingest.seed_teams_and_fixtures(conn)
    _finish_all_groups(conn)
    winners, runners = bracket._group_winners_runners(conn)
    f73 = (runners["A"], runners["B"])  # fixture 73 = RU_A vs RU_B
    f75 = (winners["F"], runners["C"])  # fixture 75 = W_F vs RU_C  (official: 73 & 75 meet in R16)
    for ext, (h, a), ko in (
        (9073, f73, "2026-06-28T19:00:00Z"),
        (9075, f75, "2026-06-28T22:00:00Z"),
    ):
        conn.execute(
            "INSERT INTO matches(stage,home_team,away_team,kickoff,neutral,status,ext_id) "
            "VALUES ('R32',?,?,?,1,'SCHEDULED',?)",
            (h, a, ko, ext),
        )
    conn.commit()

    out = bracket.build_predicted_bracket(conn, _all_teams_model())
    r32 = next(r for r in out["rounds"] if r["stage"] == "R32")["matches"]
    # Full 16-slot R32 skeleton, ordered by fixture number (slot R32-1 = fixture 73, R32-3 = 75).
    assert len(r32) == 16
    assert r32[0]["home"] in f73 and r32[0]["away"] in f73  # fixture 73 first
    assert r32[2]["home"] in f75 and r32[2]["away"] in f75  # fixture 75 third
    assert any(m.get("kickoff") == "2026-06-28T19:00:00Z" for m in r32)
    # R16 fixture 89 = winners of fixtures 73 and 75 (NOT 73 and 74 — the old bug).
    r16 = next(r for r in out["rounds"] if r["stage"] == "R16")["matches"]
    m89 = r16[0]
    assert m89["home"] in f73 and m89["away"] in f75


def test_actual_result_overrides_predicted_winner(tmp_path):
    from worldcup_predictor import bracket, db, ingest

    conn = db.connect(tmp_path / "o.db")
    db.init_schema(conn)
    ingest.seed_teams_and_fixtures(conn)
    _finish_all_groups(conn)
    winners, runners = bracket._group_winners_runners(conn)
    home73, away73 = runners["A"], runners["B"]  # fixture 73
    # Fixture 73 FINISHED: the AWAY side wins on penalties (1-1, winner=away), overriding any pick.
    conn.execute(
        "INSERT INTO matches(stage,home_team,away_team,kickoff,neutral,status,"
        "home_score,away_score,winner_team,ext_id) "
        "VALUES ('R32',?,?,?,1,'FINISHED',1,1,?,9073)",
        (home73, away73, "2026-06-28T19:00:00Z", away73),
    )
    conn.execute(
        "INSERT INTO matches(stage,home_team,away_team,kickoff,neutral,status,ext_id) "
        "VALUES ('R32',?,?,?,1,'SCHEDULED',9075)",
        (winners["F"], runners["C"], "2026-06-28T22:00:00Z"),
    )
    conn.commit()

    out = bracket.build_predicted_bracket(conn, _all_teams_model())
    r16 = next(r for r in out["rounds"] if r["stage"] == "R16")["matches"]
    # R16 fixture 89's home comes from fixture 73's ACTUAL winner (the away side), not a prediction.
    assert r16[0]["home"] == away73
```

(Keep the existing `test_advance_prob_sums_to_one_and_splits_draw`, `test_best_thirds_picks_top_8`, `test_build_r32_has_16_matches`, and the Task 3 helper tests unchanged.)

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_bracket.py::test_projection_pairs_via_official_feeders tests/test_bracket.py::test_actual_result_overrides_predicted_winner -q`
Expected: FAIL (current `build_predicted_bracket` orders by kickoff and pairs consecutively; nodes lack `kickoff`).

- [ ] **Step 3: Refactor `_decide` to keyword result-params**

Replace the existing `_decide` signature and result-reading block in `src/worldcup_predictor/bracket.py` so it takes explicit optional result fields instead of a `sqlite3.Row` (this removes the row dependency and lets projected slots pass no result). New `_decide`:

```python
def _decide(
    conn: sqlite3.Connection,
    model: GoalModel,
    home: str | None,
    away: str | None,
    *,
    ext_id: int | None = None,
    kickoff: str | None = None,
    status: str = "SCHEDULED",
    home_score: int | None = None,
    away_score: int | None = None,
    winner_team: str | None = None,
) -> dict[str, Any]:
    """Build one match node: predict when both teams are resolved; use the actual result when
    FINISHED. Result fields are passed explicitly so projected slots (no feed row) need none."""
    node: dict[str, Any] = {
        "ext_id": ext_id,
        "kickoff": kickoff,
        "home": home,
        "away": away,
        "status": status,
        "home_score": home_score,
        "away_score": away_score,
        "advance_home": None,
        "advance_away": None,
        "ml_home": None,
        "ml_away": None,
        "p_home": None,
        "p_draw": None,
        "p_away": None,
        "factors": [],
        "winner": None,
    }
    if status == "FINISHED":
        if winner_team:
            node["winner"] = winner_team
        elif home_score is not None and away_score is not None:
            node["winner"] = home if home_score >= away_score else away
    if home is None or away is None:
        return node
    pred = predict_match(conn, model, home, away, neutral=True)
    ah, aa = advance_prob(pred.p_home, pred.p_draw, pred.p_away)
    node.update(
        advance_home=ah,
        advance_away=aa,
        ml_home=pred.ml_home,
        ml_away=pred.ml_away,
        p_home=pred.p_home,
        p_draw=pred.p_draw,
        p_away=pred.p_away,
        factors=[
            {"team": f.team, "description": f.description, "lambda_delta": f.lambda_delta}
            for f in pred.factors
        ],
    )
    if node["winner"] is None:  # not yet played → predicted winner drives downstream slots
        node["winner"] = home if ah >= aa else away
    return node


def _decide_row(
    conn: sqlite3.Connection, model: GoalModel, home: str | None, away: str | None,
    row: sqlite3.Row,
) -> dict[str, Any]:
    """Convenience: build a node from a feed row's result fields."""
    return _decide(
        conn, model, home, away,
        ext_id=row["ext_id"], kickoff=row["kickoff"], status=row["status"],
        home_score=row["home_score"], away_score=row["away_score"], winner_team=row["winner_team"],
    )
```

- [ ] **Step 4: Rewrite `build_predicted_bracket` + add overlay helpers**

Replace the entire `build_predicted_bracket` body, and add the overlay helpers above it:

```python
def _overlay_index(
    by_stage: dict[str, list[sqlite3.Row]],
) -> dict[str, dict[frozenset[str], sqlite3.Row]]:
    """Index each downstream stage's feed rows by their (known) two-team set, to overlay real
    teams/results/kickoff onto the projected slot once the feed has filled it."""
    out: dict[str, dict[frozenset[str], sqlite3.Row]] = {}
    for stage in ("R16", "QF", "SF", "FINAL"):
        idx: dict[frozenset[str], sqlite3.Row] = {}
        for row in by_stage.get(stage, []):
            if row["home_team"] is not None and row["away_team"] is not None:
                idx[frozenset({row["home_team"], row["away_team"]})] = row
        out[stage] = idx
    return out


def _match_overlay(
    idx: dict[frozenset[str], sqlite3.Row], home: str | None, away: str | None
) -> sqlite3.Row | None:
    if home is None or away is None:
        return None
    return idx.get(frozenset({home, away}))


def build_predicted_bracket(conn: sqlite3.Connection, model: GoalModel) -> dict[str, Any]:
    """Compose the knockout tree: feed teams where known, predicted winners projected forward via
    the official feeder topology, every slot ordered by fixture number."""
    by_stage = _load(conn)
    total = sum(len(by_stage.get(s, [])) for s in _ROUND_ORDER) + len(by_stage.get("3RD", []))
    winners_g, runners_g = _group_winners_runners(conn)
    sigs = _r32_signatures(winners_g, runners_g)

    row_by_fixture: dict[int, sqlite3.Row] = {}
    for row in by_stage.get("R32", []):
        fx = fixture_of_r32_row(row["home_team"], row["away_team"], sigs)
        if fx is not None:
            row_by_fixture[fx] = row

    node_by_fixture: dict[int, dict[str, Any]] = {}
    winner_by_fixture: dict[int, str | None] = {}
    real = 0

    # R32: one node per fixture 73-88 (mapped feed row if present, else a TBD shell).
    for idx, fx in enumerate(_bt.R32_FIXTURES):
        row = row_by_fixture.get(fx)
        if row is not None:
            home, away = row["home_team"], row["away_team"]
            node = _decide_row(conn, model, home, away, row)
            node["home_known"] = home is not None
            node["away_known"] = away is not None
            if home is not None and away is not None:
                real += 1
        else:
            node = _decide(conn, model, None, None)
            node["home_known"] = node["away_known"] = False
        node["slot"] = f"R32-{idx + 1}"
        node_by_fixture[fx] = node
        winner_by_fixture[fx] = node["winner"]

    # R16 → Final via FEEDERS, overlaying the feed's real row when its teams match the projection.
    overlay = _overlay_index(by_stage)
    for stage, fixtures in (
        ("R16", _bt.R16_FIXTURES),
        ("QF", _bt.QF_FIXTURES),
        ("SF", _bt.SF_FIXTURES),
        ("FINAL", (_bt.FINAL_FIXTURE,)),
    ):
        for idx, fx in enumerate(fixtures):
            fa, fb = _bt.FEEDERS[fx]
            home = winner_by_fixture.get(fa)
            away = winner_by_fixture.get(fb)
            row = _match_overlay(overlay.get(stage, {}), home, away)
            home_known = away_known = False
            if row is not None and row["home_team"] is not None and row["away_team"] is not None:
                home, away, home_known, away_known = row["home_team"], row["away_team"], True, True
                real += 1
            node = _decide_row(conn, model, home, away, row) if row is not None else _decide(
                conn, model, home, away
            )
            node["home_known"] = home_known
            node["away_known"] = away_known
            node["slot"] = f"{stage}-{idx + 1}"
            node_by_fixture[fx] = node
            winner_by_fixture[fx] = node["winner"]

    rounds_out = [
        {"stage": stage, "label": _LABELS[stage], "matches": [node_by_fixture[fx] for fx in fixtures]}
        for stage, fixtures in (
            ("R32", _bt.R32_FIXTURES),
            ("R16", _bt.R16_FIXTURES),
            ("QF", _bt.QF_FIXTURES),
            ("SF", _bt.SF_FIXTURES),
            ("FINAL", (_bt.FINAL_FIXTURE,)),
        )
    ]

    # Third place = the two SF losers; overlay the feed's 3RD row if present.
    sf_losers: list[str | None] = []
    for fx in _bt.SF_FIXTURES:
        node = node_by_fixture[fx]
        w = node["winner"]
        sf_losers.append(node["away"] if w == node["home"] else (node["home"] if w else None))
    third_rows = by_stage.get("3RD", [])
    third: dict[str, Any] | None = None
    if third_rows or any(sf_losers):
        h = sf_losers[0] if sf_losers else None
        a = sf_losers[1] if len(sf_losers) > 1 else None
        home_known = away_known = False
        if third_rows:
            row = third_rows[0]
            if row["home_team"] is not None and row["away_team"] is not None:
                h, a, home_known, away_known = row["home_team"], row["away_team"], True, True
                real += 1
            third = _decide_row(conn, model, h, a, row)
        else:
            third = _decide(conn, model, h, a)
        third["slot"] = "3RD"
        third["home_known"] = home_known
        third["away_known"] = away_known

    return {
        "rounds": rounds_out,
        "third_place": third,
        "real_fixtures": real,
        "total_fixtures": total,
    }
```

Note: `_load` still selects `kickoff` (it does) so `_decide_row` can read `row["kickoff"]`. `has_knockout_fixtures` and `empty_bracket` are unchanged.

- [ ] **Step 5: Run the bracket tests + full suite**

Run: `uv run pytest tests/test_bracket.py -q`
Expected: PASS (rewritten feeder tests + Task 3 helpers + unchanged advance_prob/best_thirds tests).

Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 6: Lint, type, commit**

```bash
uv run ruff check src/ tests/ && uv run ruff format src/ tests/ && uv run mypy src/
git add src/worldcup_predictor/bracket.py tests/test_bracket.py
git commit -m "fix(bracket): feeder-driven projection ordered by fixture + kickoff field

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

---

### Task 5: Tree UI — drop English, kickoff times, fit one screen, move tab

**Files:**
- Modify: `src/worldcup_predictor/static/app.js` (`bracketTeamRow`, `bracketNode` — remove English, add kickoff)
- Modify: `src/worldcup_predictor/static/styles.css` (`#bracket .match` width, `#bracket .round` gap, remove `.en` usage, compact to avoid horizontal scroll)
- Modify: `src/worldcup_predictor/static/index.html` (move the 淘汰赛 nav button to 2nd position)

**Interfaces:**
- Consumes: `/api/knockout/bracket` nodes now include `kickoff` (Task 4); existing `zh()`, `rankBadge()`, `flag()`, `esc()` helpers.
- Produces: the knockout tab renders compact nodes (flag + 中文 + FIFA rank + advance% + kickoff), fits desktop width without horizontal scroll, tab is 2nd in the nav.

- [ ] **Step 1: Remove English names + add kickoff in `app.js`**

In `src/worldcup_predictor/static/app.js`, replace `bracketTeamRow` (drop the `.en` line) and update `bracketNode` (add a compact kickoff line). New `bracketTeamRow`:

```javascript
function bracketTeamRow(name, advPct, isWin) {
  const nm = name ? `${zh(name)} ${rankBadge(name)}` : "待定";
  const fl = name ? flag(name) : "🏳️";
  const p = advPct == null ? "" : `${Math.round(advPct * 100)}%`;
  return `<div class="trow ${isWin ? "win" : ""}"><span class="flag">${fl}</span>
    <span class="names"><span class="zh">${nm}</span></span><span class="pct">${p}</span></div>`;
}
```

In `bracketNode`, add a compact kickoff string and render it in the foot. Replace the `bracketNode` body's `score`/return with:

```javascript
function bracketNode(m) {
  const proj = !(m.home_known && m.away_known);
  const winHome = m.winner && m.winner === m.home;
  const winAway = m.winner && m.winner === m.away;
  const badge = m.status === "FINISHED" ? `<span class="badge done">已赛</span>`
    : proj ? `<span class="badge proj">推测</span>` : `<span class="badge real">真实</span>`;
  const score = m.status === "FINISHED" && m.home_score != null
    ? `比分 ${m.home_score}-${m.away_score}`
    : (m.ml_home != null ? `预测 ${m.ml_home}-${m.ml_away}` : "");
  const when = m.kickoff
    ? new Date(m.kickoff).toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" })
    : "";
  return `<div class="match ${proj ? "proj" : ""}" data-node='${esc(JSON.stringify(m))}'>
    ${bracketTeamRow(m.home, m.advance_home, winHome)}
    ${bracketTeamRow(m.away, m.advance_away, winAway)}
    <div class="foot"><span class="score">${score}</span>${badge}</div>
    ${when ? `<div class="kick">${when}</div>` : ""}</div>`;
}
```

- [ ] **Step 2: Compact the bracket CSS in `styles.css`**

In `src/worldcup_predictor/static/styles.css`, change these `#bracket` rules to fit all five columns on a normal desktop width without horizontal scroll, and add a `.kick` style. Edits:

- `#bracket .match { ... min-width: 214px ... }` → change `min-width: 214px` to `min-width: 150px;`.
- `#bracket .round:not(.final) { padding-right: 54px; }` → change to `padding-right: 30px;`.
- Adjust connector elbow widths to match the smaller gap: any `#bracket ... ::after { ... width: 27px ... }` / `::before { ... width: 27px ... }` → change `27px` to `15px` (half the new 30px gap).
- Remove or neutralize the `#bracket .en` rule (since the element is gone): delete the `#bracket .en { ... }` line.
- Add a compact kickoff style after `#bracket .match`:

```css
#bracket .kick { font-size: 10px; color: var(--muted); text-align: right; margin-top: 2px; }
```

After editing, confirm there are no other `27px` connector references left under `#bracket` (grep), and that the five columns total roughly `5*150 + 4*30 = 870px` plus connectors — comfortably within ~1200px.

- [ ] **Step 3: Move the 淘汰赛 tab to 2nd position in `index.html`**

In `src/worldcup_predictor/static/index.html`, move the knockout nav button so it is the second tab (right after 即将开赛). The nav block becomes:

```html
      <button data-tab="upcoming" class="active">即将开赛</button>
      <button data-tab="knockout">淘汰赛</button>
      <button data-tab="forecast">夺冠预测</button>
      <button data-tab="value">价值投注</button>
      <button data-tab="paper">纸面跟单</button>
      <button data-tab="accuracy">战绩对比</button>
      <button data-tab="groups">小组积分</button>
```

(The `<section>` elements and the tab→loader map are unchanged — tab switching is keyed by `data-tab`, not DOM order.)

- [ ] **Step 4: Verify JS syntax + web tests + lint/type**

Run: `node --check src/worldcup_predictor/static/app.js`
Expected: no output (valid JS).

Run: `uv run pytest tests/test_web_server.py -q && uv run ruff check src/ tests/ && uv run mypy src/`
Expected: PASS / clean.

- [ ] **Step 5: Commit**

```bash
git add src/worldcup_predictor/static/app.js src/worldcup_predictor/static/styles.css src/worldcup_predictor/static/index.html
git commit -m "feat(web): compact knockout tree — drop English, add kickoff, move tab

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

---

## Post-implementation

- **Controller visual verification:** serve the branch on a spare port against a temp DB with synthetic R32 teams (as done for the original bracket), open the 淘汰赛 tab, and screenshot to confirm: no horizontal scroll on a ~1280px viewport, no English names, kickoff times shown, feeder pairings correct (e.g. fixtures 73 & 75 winners meet in R16), tab in 2nd position.
- **Re-simulate after merge** so the corrected feeders take effect on the live title odds: `worldcup simulate --n 100000`, then restart the service.
- **Topology re-validation:** once all 16 R32 teams populate, spot-check a printed `worldcup bracket` R16 against the official Wikipedia bracket to confirm the fixture mapping holds end-to-end.
