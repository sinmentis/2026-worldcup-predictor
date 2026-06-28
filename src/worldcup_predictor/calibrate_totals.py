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
    return float(q_over / s)


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
