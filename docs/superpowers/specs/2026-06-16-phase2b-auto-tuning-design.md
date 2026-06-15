# Phase 2b — Auto-Tuning (level-2 self-evolution) — Design Spec

**Date:** 2026-06-16
**Status:** Approved -> building
**Builds on:** the calibration framework / walk-forward backtest (v0.5.0).

## 1. Goal

Use the walk-forward backtest to automatically tune the model's hyperparameters by
out-of-sample skill, replacing hand-set defaults. Guard-railed and human-triggerable;
schedulable for full "self-update".

## 2. Decisions (locked)

- **Tunable knob:** `TIME_DECAY_XI` (Dixon-Coles recency decay) — the impactful, backtest-tunable
  parameter. Calibration (gamma, tau) is re-fit in the same pipeline. Host advantage and intel
  knobs are NOT backtest-tunable on international history -> left at config defaults.
- **Method:** grid search over the walk-forward backtest, pick min out-of-sample RPS. Transparent
  and stable (no black-box optimiser).
- **Guardrail:** adopt a new xi only if it beats the current xi's OOS RPS by >= `IMPROVE_EPS`.
- **Human gate:** `--dry-run` (default) previews; `--apply` commits. cron weekly with `--apply`
  gives full auto-evolution.
- **Backtest fairness fix:** predict each historical match with its real `neutral` flag (was
  forced `True`), so tuning is measured fairly.

## 3. Modules

### `goal_model.py`
- `GoalModel.fit(history, xi=None)` — `xi` defaults to `config.TIME_DECAY_XI`.

### `backtest.py`
- `walk_forward_predictions(..., xi=None, neutral=None)` — pass `xi` to each fit; when `neutral`
  is `None`, predict with the match's real venue flag (fairness fix); `True/False` still forces it.

### `tune.py` (new)
- `DECAY_GRID` (e.g. 0.0005 .. 0.0050; half-lives ~1386 .. 139 days), `IMPROVE_EPS`,
  `MODEL_PARAMS_KEY = "model_params"`.
- `tune_decay(conn, grid=None, refit_days=45, test_years=2)` -> `{results:[{xi,rps,n}], best, current_xi}`.
  Always includes the current xi in the sweep so its RPS is comparable.
- `load_model_params(conn)` / `store_model_params(conn, params, meta)` via `tuning_params`
  (key `model_params`, JSON). `load` returns `{}` when unset.

### `engine.py`
- `get_model` reads the tuned `time_decay_xi` (falling back to config) and refits when it changes
  (cache keyed on DB path AND xi).
- `run_tuning(conn, apply=False, ...)` -> report with `best`, `current_rps`, `would_adopt`,
  `applied`. Applies + resets the model cache only when `apply and would_adopt`.

### `cli.py`
- `worldcup tune [--apply] [--refit-days N] [--test-years Y]` — prints the sweep, best xi,
  current vs best RPS, and whether it adopted.

## 4. Testing

- `GoalModel.fit` accepts `xi` (different xi -> different weights/fit).
- backtest passes `xi` through and uses the real `neutral` flag when `neutral=None`.
- `tune_decay` picks the min-OOS-RPS xi on synthetic data (mock model so it's fast/deterministic).
- guardrail: `run_tuning` does not adopt when the best is within `IMPROVE_EPS` of current; adopts +
  stores + resets cache when it clearly improves (with `apply=True`).
- `engine.get_model` refits when the stored xi changes.

## 5. Out of scope

Data-estimated host advantage; intel-knob tuning (no labels); Bayesian/evolutionary optimisers;
Phase 2c LLM advisor.
