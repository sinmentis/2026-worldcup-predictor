"""Parametric post-hoc calibration of 1X2 probabilities.

Two interpretable knobs, fit on out-of-sample backtest predictions to minimise RPS:
- ``temperature`` (tau >= 1): flattens over-confident distributions toward uniform.
- ``draw_mult`` (gamma >= 1): raises the draw share (the goal model under-weights draws).

Identity at ``gamma=1, tau=1`` so an un-fitted system is unchanged. Parameters live in the
``tuning_params`` table (key ``calibration``); ``predict`` loads them and applies the transform
to the final 1X2 after intel.

Import direction: ``calibrate`` -> ``evaluate`` (for RPS). ``predict`` -> ``calibrate``. No cycle.
"""

from __future__ import annotations

import json
import sqlite3
import time

from worldcup_predictor import evaluate

CALIB_KEY = "calibration"
GAMMA_GRID = [round(1.0 + 0.05 * i, 2) for i in range(13)]  # 1.00 .. 1.60
TAU_GRID = [round(1.0 + 0.05 * i, 2) for i in range(21)]  # 1.00 .. 2.00

Probs = tuple[float, float, float]


def apply(p_home: float, p_draw: float, p_away: float, params: dict[str, float] | None) -> Probs:
    if not params:
        return (p_home, p_draw, p_away)
    gamma = float(params.get("draw_mult", 1.0))
    tau = float(params.get("temperature", 1.0))
    inv = 1.0 / tau if tau else 1.0
    q = [p_home**inv, p_draw**inv, p_away**inv]
    q[1] *= gamma
    s = q[0] + q[1] + q[2]
    if s <= 0:
        return (p_home, p_draw, p_away)
    return (q[0] / s, q[1] / s, q[2] / s)


def fit(
    oos: list[dict[str, float]],
    gamma_grid: list[float] | None = None,
    tau_grid: list[float] | None = None,
) -> dict[str, float]:
    """Grid-search (gamma, tau) to minimise mean out-of-sample RPS."""
    gamma_grid = gamma_grid or GAMMA_GRID
    tau_grid = tau_grid or TAU_GRID
    best: tuple[float, float, float] | None = None
    for gamma in gamma_grid:
        for tau in tau_grid:
            params = {"draw_mult": gamma, "temperature": tau}
            total = 0.0
            for r in oos:
                pc = apply(float(r["p_home"]), float(r["p_draw"]), float(r["p_away"]), params)
                total += evaluate.rps(list(pc), int(r["outcome"]))
            mean = total / len(oos) if oos else 0.0
            if best is None or mean < best[0]:
                best = (mean, gamma, tau)
    assert best is not None
    return {"draw_mult": best[1], "temperature": best[2], "rps": best[0]}


def store(
    conn: sqlite3.Connection, params: dict[str, float], meta: dict[str, object] | None = None
) -> None:
    payload: dict[str, object] = {
        "draw_mult": float(params.get("draw_mult", 1.0)),
        "temperature": float(params.get("temperature", 1.0)),
    }
    if meta:
        payload.update(meta)
    conn.execute(
        "INSERT INTO tuning_params(key,value,updated_at) VALUES (?,?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
        (CALIB_KEY, json.dumps(payload), time.time()),
    )
    conn.commit()


def load(conn: sqlite3.Connection) -> dict[str, float] | None:
    row = conn.execute("SELECT value FROM tuning_params WHERE key=?", (CALIB_KEY,)).fetchone()
    if not row or not row[0]:
        return None
    try:
        d = json.loads(row[0])
    except (ValueError, TypeError):
        return None
    return {
        "draw_mult": float(d.get("draw_mult", 1.0)),
        "temperature": float(d.get("temperature", 1.0)),
    }
