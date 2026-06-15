# Calibration Framework — Design Spec

**Date:** 2026-06-16
**Status:** Approved (user picked Approach A) -> building
**Builds on:** Phase 1 goal model + evaluate.py. Foundation for Phase 2b auto-tuning.

## 1. Problem (from the MD1 accuracy investigation)

On 14 finished matches the model's hit-rate was 43% and RPS (0.197) edged above the
flat baseline (0.189). Root causes, data-verified:
- **Draws under-weighted:** mean predicted `p_draw` 0.25 < baseline 0.30 < realized 0.43.
  On the 6 draws, baseline RPS 0.125 beat model 0.208 — the sole reason model RPS > baseline.
- **Over-confident favourites:** Spain 0.86 / Switzerland 0.81 both drew (RPS 0.366 / 0.332).
- **No host advantage:** `config.HOSTS` / `is_host` exist but prediction always passes
  `neutral=True`, so USA (host) was picked to lose to Paraguay (lost 4-1 the other way).
- Note: hit-rate (argmax) is a harsh metric on a draw-heavy sample; draws are rarely the argmax.

## 2. Decisions (locked)

Parametric post-hoc calibration (Approach A) + a reusable walk-forward backtest harness +
a small host-advantage term. Two interpretable calibration knobs; fit by backtest.

## 3. Modules

### `backtest.py` — walk-forward harness (no look-ahead)
- `walk_forward_predictions(conn, since=None, refit_days=30, test_years=2)` -> list of
  `{date, home, away, p_home, p_draw, p_away, outcome}`. For each refit chunk, fit the goal
  model ONLY on matches with `date < chunk_start`, predict that chunk. Slide forward.
  Test window defaults to the last `test_years` of history to bound cost.
- `reliability(oos, n_bins=10)` -> bins of predicted confidence vs observed frequency + ECE.
- `metrics(oos, calibrator=None)` -> mean RPS / Brier / log-loss for model, baseline, and
  (if calibrator given) calibrated model.
- **Correctness invariant:** every prediction's training rows predate the match date. A test
  asserts this directly on a synthetic history.

### `calibrate.py` — parametric calibrator
- Transform `apply(p_home, p_draw, p_away, params)`:
  1. temperature: `qi = pi ** (1/tau)` (tau>1 flattens / reduces over-confidence)
  2. draw boost: `q_draw *= gamma` (gamma>1 raises draws)
  3. renormalise to sum 1. Identity at `gamma=1, tau=1` (back-compatible).
- `fit(oos)` -> `{draw_mult, temperature}` via 2D grid search minimising mean out-of-sample RPS
  (gamma in [1.0, 1.6], tau in [1.0, 2.0]).
- `load(conn)` / `store(conn, params, meta)` using `tuning_params` (key `calibration`, JSON value).
  `load` returns `None` when unset -> predict stays raw.

### Host advantage
- `config.HOST_ADVANTAGE = 1.10` (lambda multiplier). In `predict.adjusted_grid` (shared by
  predict and simulate): when exactly one of (home, away) is in `config.HOSTS`, multiply the
  host's expected goals by `HOST_ADVANTAGE` (re-tilt the grid, reusing `retilt_grid`). Bounded,
  config-driven, benefits both predict and simulate.

## 4. Integration

- Host advantage: in `adjusted_grid` (predict + simulate).
- Calibration: in `predict_match`, applied to the final 1X2 AFTER intel. The most-likely
  scoreline stays from the grid. Stored `predictions` rows use calibrated probs.
- **Scope boundary:** the simulation Monte Carlo keeps sampling the raw (host-adjusted) grid;
  grid-level calibration is out of scope here (documented follow-up). The forecast tab therefore
  uses host advantage but not the 1X2 draw calibration.
- Back-compatible: with no stored calibration, `predict_match` output is unchanged except for
  host advantage on host matches.

## 5. CLI

`worldcup backtest [--since DATE] [--refit-days N] [--test-years Y] [--fit-calibration]`
- Always prints: model vs baseline RPS/Brier/log-loss, a reliability table + ECE.
- With `--fit-calibration`: fits (gamma, tau), stores them, and prints before/after RPS + ECE.

## 6. Testing

- calibrate: draw boost raises p_draw; temperature flattens an over-confident vector; identity at
  defaults; renormalised; `fit` on draw-deficient synthetic data selects gamma>1 and lowers RPS.
- backtest: walk-forward produces only out-of-sample predictions (assert training-date < match-date);
  reliability/ECE basic correctness on synthetic data.
- host advantage: a host vs non-host raises the host's win prob vs the neutral baseline.
- integration: after storing a draw-boost calibration, `predict_match` p_draw increases; raw when unset.

## 7. Out of scope (follow-up)

Grid-level / simulation calibration; learned calibrators (isotonic/Dirichlet, Approach B);
data-estimated host advantage; web reliability-curve view; auto-retuning cadence (Phase 2b).
