# Totals calibration + activating the 1X2 calibrator — design

## Problem

Paper-trading diagnosis (memory entry 31) found the model's value bets show no durable
edge against a ~40-book consensus, with two concentrated leaks visible in closing-line
value (CLV):

- **Totals**: CLV **-2.8%**, beat-close 24%. We bet UNDER 41 times at avg model
  confidence **62%**, but only **44%** landed. This 2026 WC group stage ran **2.99**
  goals/game (56% over 2.5); the model trains on 2018+ international history averaging
  **2.73** (49% over). The market sits ~50% at the 2.5 line and is well-calibrated; our
  model says 62% — i.e. value-bet *selection* picks the games where our (overconfident)
  totals distribution most disagrees with a sharp market, and those max-disagreement
  spots are where our model is wrong.
- **1X2**: CLV **+3.8%**, beat-close 67% — actually healthy (its -7.9% ROI is variance
  on 51 bets). The raw Dixon-Coles 1X2 is reasonably calibrated.

Two infrastructure facts amplify the totals leak:

1. **The calibrator is inert.** `calibrate.load` returns identity
   `{draw_mult: 1.0, temperature: 1.0}` — never fitted on real out-of-sample data.
2. **Calibration only ever covers 1X2.** `predict.predict_match` applies
   `calibrate.apply` to home/draw/away only (predict.py:67-70). Totals probabilities
   come straight from `grid.over(line)` in `valuebet.value_bets_totals` (valuebet.py:217)
   with **zero** post-hoc correction.

## Goal

Two changes the user approved ("1+2"):

1. **Activate the 1X2 calibrator** — fit it on out-of-sample backtest data and store it,
   closing the inert state.
2. **Extend calibration to totals** — a post-hoc calibrator on the over/under
   probability, fit on out-of-sample data, applied in the totals betting path.

The realistic objective is to **stop the systematic bleeding** (tame totals
overconfidence so value-selection stops picking fake under-edges), not to beat the
market. Success is measured by **CLV and out-of-sample calibration (Brier/log-loss,
reliability)**, not short-run ROI.

## Approach (chosen: post-hoc totals calibrator)

Mirror the existing 1X2 calibrator with a new, parallel `calibrate_totals` module.

### New module `calibrate_totals.py`

Two interpretable knobs on the binary over/under at a line, identity at defaults:

- `temperature` (tau >= 1): flattens an over-confident over/under split toward 50/50.
  This is the knob that matters: it shrinks the model's 62% back toward the market,
  removing the fake edge that selection exploits.
- `over_mult` (m): a multiplier on P(over) that corrects any systematic over/under lean
  the fit detects. Unlike `draw_mult` (which is one-directional `>= 1` because the 1X2
  bias is known to under-weight draws), `over_mult` spans **both sides of 1.0** — on
  historical data the model is roughly centred, so we must not bake in the "raise over"
  direction; let the fit decide.

```
apply(p_over, params) -> calibrated p_over:
    if not params: return p_over
    tau, m = params["temperature"], params["over_mult"]
    inv = 1.0/tau if tau else 1.0          # guard tau=0, matching calibrate.apply
    q_over  = p_over    ** inv
    q_under = (1-p_over)** inv
    q_over *= m
    s = q_over + q_under
    return p_over if s <= 0 else q_over / s
```

- `fit(oos_totals, ...)`: grid-search `(temperature, over_mult)` to minimise mean
  out-of-sample **log-loss** on the over/under outcome at the 2.5 line (the dominant
  betting line). Returns the best knobs + the loss. Grids:
  `temperature` 1.00..2.00 (step 0.05, like the 1X2 `TAU_GRID`); `over_mult`
  0.80..1.25 (step 0.05) so either lean can be corrected.
- `store` / `load`: in the existing `tuning_params` key-value table, key
  `calibration_totals`. `load` returns `None` when absent → `apply` is identity → the
  feature is inert and safe until fitted (same contract as `calibrate`).

### Fit data — extend the backtest OOS rows

`backtest.walk_forward_predictions` already has the per-match `grid` and the actual
`home_goals`/`away_goals` in scope (backtest.py:76-88). Add two keys per OOS row,
backward-compatible (the 1X2 path ignores them):

- `p_over_2_5 = grid.over(2.5)`
- `total_goals = home_goals + away_goals`

A row's over/under outcome at 2.5 is `1 if total_goals > 2.5 else 0` (no pushes at the
half-line).

### Wire into `engine.run_backtest(fit_calibration=True)`

After fitting + storing the 1X2 calibrator, also:
- fit the totals calibrator on the new OOS fields,
- store it under `calibration_totals`,
- report totals Brier/log-loss and reliability before/after in the returned report.

So the single existing command `worldcup backtest --fit-calibration` fits **both**
calibrators (this is also how direction 1 — activating the 1X2 calibrator — gets done).

### Apply in the totals betting path

In `valuebet.value_bets_totals`, replace `our_over = grid.over(line)` with
`our_over = calibrate_totals.apply(grid.over(line), calibrate_totals.load(conn))`.
Load once per call, not per line. Applied at the call site (a calibrated scalar), **not**
by mutating the shared grid — so the simulation and 1X2 paths are untouched (blast radius
limited to totals value betting).

The 2.5-fit params are applied at every line. Temperature is approximately line-agnostic
and the 2.5 line dominates betting; this simplification is documented, not hidden.

## What this does and does not fix (honest scope)

- **Fixes**: totals *overconfidence*. By shrinking extreme over/under probabilities
  toward the market, value-selection stops flagging the fake under-edges that bled CLV.
  This is fittable on history and is the direct mechanism behind the totals leak.
- **Does not fix**: the *level* shift from this tournament running hot (2.99 vs the
  2.73 training world). A calibrator fit on history calibrates to history, where the
  model is roughly centred correctly, so the `over_mult` term may come back near 1.0. A
  tournament-aware goals adjustment (recency-weighted fit or an in-tournament goals
  update) is the natural **follow-up (direction 5)** and is explicitly out of scope here.

## Out of scope (flagged, not done)

- Spreads calibration (CLV -4.0%) — same overconfidence pattern; a follow-up once the
  totals pattern is proven.
- Bet-selection changes (edge threshold, claimed-edge cap), market filtering, and
  staking changes (full-Kelly amplifies fake edges) — these are betting-strategy
  decisions for the user, not calibration.
- Tournament-level goals adjustment (direction 5, see above).

## Testing

- `calibrate_totals.apply`: identity at defaults; `tau>1` pulls 0.8 toward 0.5;
  `over_mult>1` raises P(over); renormalises; guards `p_over` in {0,1} and `tau=0`.
- `calibrate_totals.fit`: returns ~identity on perfectly-calibrated synthetic OOS;
  returns `temperature>1` on deliberately over-confident synthetic OOS; minimises loss.
- `store`/`load` round-trip; `load` returns `None` when the key is absent.
- `backtest.walk_forward_predictions` rows include `p_over_2_5` and `total_goals`.
- `engine.run_backtest(fit_calibration=True)` stores `calibration_totals` and reports
  before/after totals metrics.
- `valuebet.value_bets_totals` applies the calibrator: with a stored non-identity param,
  the over probability used for the edge is shifted vs the raw `grid.over(line)`.

## Quality bar

`uv run ruff check src/ tests/`, `uv run ruff format`, `uv run mypy src/` (--strict),
`uv run pytest -q`. Fitting on prod is a one-off `worldcup backtest --fit-calibration`
run (~20-40 min walk-forward under load) — not a per-request cost; then restart the
service and re-simulate so the activated 1X2 calibrator reaches live title odds.
