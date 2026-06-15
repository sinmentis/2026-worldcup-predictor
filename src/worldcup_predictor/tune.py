"""Phase 2b auto-tuning: pick model hyperparameters by walk-forward out-of-sample RPS.

Today this tunes the Dixon-Coles recency decay (``TIME_DECAY_XI``). Tuned values live in the
``tuning_params`` table (key ``model_params``); ``engine.get_model`` reads them and refits when
they change. Grid search keeps the process transparent and stable.
"""

from __future__ import annotations

import json
import sqlite3
import time
from typing import Any

from worldcup_predictor import backtest, config

MODEL_PARAMS_KEY = "model_params"
# Half-lives ~ ln(2)/xi days: 0.0005~1386d, 0.001~693d, 0.002~347d, 0.003~231d, 0.005~139d.
DECAY_GRID = [0.0005, 0.0010, 0.0015, 0.0020, 0.0030, 0.0050]
IMPROVE_EPS = 0.0005  # only adopt a new value if it beats the current OOS RPS by this margin


def load_model_params(conn: sqlite3.Connection) -> dict[str, Any]:
    row = conn.execute(
        "SELECT value FROM tuning_params WHERE key=?", (MODEL_PARAMS_KEY,)
    ).fetchone()
    if not row or not row[0]:
        return {}
    try:
        d = json.loads(row[0])
    except (ValueError, TypeError):
        return {}
    return d if isinstance(d, dict) else {}


def store_model_params(
    conn: sqlite3.Connection, params: dict[str, Any], meta: dict[str, Any] | None = None
) -> None:
    payload: dict[str, Any] = dict(params)
    if meta:
        payload.update(meta)
    conn.execute(
        "INSERT INTO tuning_params(key,value,updated_at) VALUES (?,?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
        (MODEL_PARAMS_KEY, json.dumps(payload), time.time()),
    )
    conn.commit()


def current_xi(conn: sqlite3.Connection) -> float:
    return float(load_model_params(conn).get("time_decay_xi", config.TIME_DECAY_XI))


def tune_decay(
    conn: sqlite3.Connection,
    grid: list[float] | None = None,
    refit_days: int = 45,
    test_years: int = 2,
) -> dict[str, Any]:
    """Sweep decay values over the walk-forward backtest; report each one's OOS RPS."""
    values = list(grid or DECAY_GRID)
    cur = current_xi(conn)
    if not any(abs(g - cur) < 1e-12 for g in values):
        values.append(cur)  # always evaluate the current value for a fair comparison
    values = sorted(set(values))

    results: list[dict[str, Any]] = []
    for xi in values:
        oos = backtest.walk_forward_predictions(
            conn, xi=xi, refit_days=refit_days, test_years=test_years
        )
        m = backtest.metrics(oos)
        results.append({"xi": xi, "rps": m.get("model_rps"), "n": m.get("n", 0)})

    valid = [r for r in results if r["n"] and r["rps"] is not None]
    best = min(valid, key=lambda r: r["rps"]) if valid else None
    cur_rps = next((r["rps"] for r in results if abs(r["xi"] - cur) < 1e-12 and r["n"]), None)
    return {"results": results, "best": best, "current_xi": cur, "current_rps": cur_rps}
