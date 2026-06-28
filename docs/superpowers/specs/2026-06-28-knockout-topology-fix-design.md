# Knockout Bracket Topology Fix — Design

## Problem

The knockout bracket's feeder structure is wrong in **two** places, same root cause:

1. **`simulate.py` (Monte Carlo, headline title odds).** `_R32_TEMPLATE` lists the 16 R32 matches
   in FIFA fixture-number order (73→88), but the round-progression loop pairs **consecutive**
   winners (`bracket = list(zip(it, it))`, `simulate.py:264-265`). The official bracket has
   **non-consecutive** feeders, so 5 of the 8 R16 pairings are wrong, skewing advancement/title
   odds.
2. **`bracket.py` (the knockout tree).** It orders R32 by kickoff and projects winners into
   `2k/2k+1` consecutive slots (`build_predicted_bracket`). Both the ordering and the pairing are
   wrong, so projected R16+ matchups are incorrect (e.g. it currently projects "Canada vs Brazil"
   for R16-1, which is not a real bracket pairing).

The official structure (FIFA regulations, confirmed against Wikipedia's 2026 knockout bracket):

```
R16: 89=W73,W75  90=W74,W77  91=W76,W78  92=W79,W80
     93=W83,W84  94=W81,W82  95=W86,W88  96=W85,W87
QF:  97=W89,W90  98=W93,W94  99=W91,W92  100=W95,W96
SF:  101=W97,W98  102=W99,W100
Final: 104=W101,W102      Third place: 103 = losers of 101, 102
```

## Goal

One shared source of truth for the bracket feeder topology, consumed by both the simulation
(correct title odds) and the tree (correct projection). Plus tree UI improvements: drop English
team names, show kickoff time per match, fit on one screen without horizontal scroll, and move the
淘汰赛 tab earlier in the nav.

## Architecture

A new `bracket_topology.py` holds the pure, dependency-free fixture/feeder constants. `simulate.py`
imports it to replace consecutive pairing with feeder-driven progression. `bracket.py` imports it
(plus the existing `standings_from_results` from `simulate.py`) to map feed rows to fixtures and
project via the real feeders.

```
bracket_topology.py  (pure constants: R32_FIXTURES..FINAL_FIXTURE, FEEDERS)
        ▲                         ▲
        │                         │
   simulate.py ───────────────►  bracket.py
   (round progression)          (also imports standings_from_results from simulate.py)
```

No import cycle: `bracket_topology` depends on nothing; `simulate` depends on `bracket_topology`;
`bracket` depends on both. `simulate` never imports `bracket`.

### Component 1 — `bracket_topology.py` (new)

```python
R32_FIXTURES = tuple(range(73, 89))   # 73..88, index i ↔ _R32_TEMPLATE[i]
R16_FIXTURES = tuple(range(89, 97))   # 89..96
QF_FIXTURES = tuple(range(97, 101))   # 97..100
SF_FIXTURES = (101, 102)
THIRD_FIXTURE = 103
FINAL_FIXTURE = 104

# Each non-R32 fixture's two feeder fixtures (winner of each feeds in). 103 (3rd) handled
# separately as the two SF losers, so it is intentionally absent here.
FEEDERS: dict[int, tuple[int, int]] = {
    89: (73, 75), 90: (74, 77), 91: (76, 78), 92: (79, 80),
    93: (83, 84), 94: (81, 82), 95: (86, 88), 96: (85, 87),
    97: (89, 90), 98: (93, 94), 99: (91, 92), 100: (95, 96),
    101: (97, 98), 102: (99, 100),
    104: (101, 102),
}
```

### Component 2 — `simulate.py` round progression

`_R32_TEMPLATE` index `i` corresponds to fixture `73+i`. After computing the 16 R32 winners,
build each later round by following `FEEDERS` instead of consecutive `zip`:

- R32 winners (`bracket` in template order) → credit `r16`; record `winner_by_fixture[73+i]`.
- For each later round (`R16_FIXTURES→qf`, `QF_FIXTURES→sf`, `SF_FIXTURES→final`,
  `(FINAL_FIXTURE,)→title`): for each fixture `fx`, fetch its two feeder winners from
  `winner_by_fixture`, run the existing `_knockout_winner`, store `winner_by_fixture[fx]`, and
  credit the round counter.

The round-key semantics are unchanged ("winning an R32 match reaches R16", etc.); only the pairing
changes. `_R32_TEMPLATE`, `build_r32`, `best_thirds`, `_knockout_winner`, the Dixon-Coles model,
and the counts dict are all untouched.

**Out of scope (documented, pre-existing):** `build_r32` assigns the eight qualifying third-place
teams to the eight `"3"` slots *in order* rather than via the full Annex C combination table. That
is a separate, smaller inaccuracy (which *group's* third lands in which slot), not the feeder bug.
Leave as-is.

### Component 3 — `bracket.py` predicted-bracket builder

Replace kickoff-ordering + consecutive projection with fixture-mapped + feeder-driven projection.

1. **Group standings.** Compute `W[g]`, `RU[g]` per finished group via the existing
   `simulate.standings_from_results`.
2. **Map each feed R32 row → its fixture (73–88).** Resolve each fixture's `_R32_TEMPLATE` slots
   to teams. Each fixture has a unique signature:
   - The 8 "W_x vs 3rd" fixtures (74,77,79,80,81,82,85,87): identified by the group-winner `W_x`
     present in the row (the other side is the wildcard third).
   - The 8 paired fixtures (73,75,76,78,83,84,86,88): identified by the row's two-team set equal to
     the fixture's resolved two-team set.
   A feed R32 row is mappable once its group's standings are final (i.e. as soon as the feed has
   given it teams). TBD rows (no teams yet) are simply not mapped this pass — self-heals on the
   next fetch.
3. **Order the R32 column by fixture number** (true bracket order), not kickoff.
4. **Resolve `winner_by_fixture`** for R32: real winner if the matched row is FINISHED, else the
   predicted winner from `advance_prob` (unchanged formula), else None if teams unknown.
5. **Project R16→Final via `FEEDERS`.** Each downstream fixture's two sides are the winners of its
   feeder fixtures (real or projected). Overlay the feed's real teams/results/kickoff by matching a
   feed row of that stage whose team-set equals the two resolved feeder winners (gives real result
   + date once the match is decided). `103` (3rd place) = the two SF (`101`,`102`) losers.
6. **Node shape** is unchanged (`home/away/home_known/away_known/status/scores/advance_*/ml_*/
   p_*/factors/winner/slot`) plus a new **`kickoff`** field (the matched feed row's kickoff, or
   null for a not-yet-decided future slot). Real feed teams still set `*_known=True`; projected
   sides are `*_known=False`; FINISHED results override predicted winners downstream.

**Date limitation (documented):** a fixture's kickoff comes from its matched feed row, so a
not-yet-decided *future* slot (e.g. an SF before its QFs are played) shows predicted teams without a
confirmed time until the feed fills it. Imminent matches (R32, then each round as it's decided)
always have their time. This keeps dates feed-driven and anti-fragile (no hard-coded schedule).

### Component 4 — Tree UI (`static/app.js`, `styles.css`, `index.html`)

- **Remove English names.** Node shows flag + 中文 + `FIFA #rank` only (drop the `.en` line). Reuse
  existing `zh()`/`rankBadge()`/`flag()`.
- **Show kickoff time.** Each node displays its `kickoff` formatted compactly (e.g. `6/28 19:00`,
  via the site's existing date formatting) beside/under the predicted-score line. Omit when null.
- **Fit one screen, no horizontal scroll.** With English gone, shrink node width (~150–160px) and
  connector gaps so all five columns (R32→Final) fit a normal desktop width (~1200px). R32's 16
  rows scroll vertically (allowed). Best-effort on mobile (degrade to fit-width; never require
  left/right scrolling on desktop).
- **Move 淘汰赛 tab to 2nd position** in the nav (right after 即将开赛), since it's now a headline
  feature. The tab→loader map and section id are otherwise unchanged.

## Testing

- **`bracket_topology`:** assert `FEEDERS` has the exact 15 entries; each round's fixtures map to
  the documented feeders; `103` absent (3rd handled separately); round-fixture tuples are correct.
- **`simulate`:** a deterministic test with a controlled `_knockout_winner` (or seed) verifying that
  a known R32 winner set produces the **correct** R16 opponents per `FEEDERS` — and that the old
  consecutive pairing would have produced different (wrong) opponents. Confirms the headline fix.
- **`bracket`:** feed-row→fixture mapping (a "W_x vs 3rd" row maps by its winner side; a paired row
  by its two-team set); projection pairs via `FEEDERS` not consecutively (e.g. winners of fixtures
  73 and 75 meet, not 73 and 74); FINISHED result overrides projected winner downstream; 3RD = SF
  losers; `kickoff` populated from the matched row.
- **UI:** verified visually via screenshot against a populated bracket (real teams set in a temp
  DB), confirming no horizontal scroll, no English names, kickoff times shown, tab in 2nd position.

## Out of scope (YAGNI)

- Full Annex C third-place slot assignment in `simulate.build_r32` (separate pre-existing
  simplification).
- Hard-coded FIFA kickoff schedule for not-yet-decided future slots (kept feed-driven).
- Any change to value betting, paper trading, or the Monte-Carlo math beyond the feeder pairing.
