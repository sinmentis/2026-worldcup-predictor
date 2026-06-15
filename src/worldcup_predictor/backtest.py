"""Walk-forward backtest harness (no look-ahead) + calibration reporting.

For each refit chunk the goal model is trained ONLY on matches strictly before the chunk's
start date, then used to predict that chunk. This produces genuinely out-of-sample
predictions used to (a) measure calibration (reliability curve + ECE) and skill (RPS/Brier/
log-loss vs a flat baseline) and (b) fit the parametric calibrator.

Foundation for Phase 2b auto-tuning.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from typing import Any

import pandas as pd

from worldcup_predictor import calibrate
from worldcup_predictor.evaluate import (
    BASELINE,
    _outcome,
    log_loss_score,
    multiclass_brier,
    rps,
)
from worldcup_predictor.goal_model import GoalModel, history_frame

MIN_TRAIN = 200  # don't fit on a tiny history; skip chunks without enough prior data


def iter_chunks(
    df: pd.DataFrame, cutoff: pd.Timestamp, refit_days: int, train_years: int
) -> Iterator[tuple[pd.DataFrame, pd.DataFrame]]:
    """Yield (train, test_chunk) pairs. Invariant: train dates are strictly before the chunk."""
    max_date = df["date"].max()
    start = cutoff
    step = pd.Timedelta(days=refit_days)
    while start <= max_date:
        end = start + step
        chunk = df[(df["date"] >= start) & (df["date"] < end)]
        if not chunk.empty:
            train_lo = start - pd.DateOffset(years=train_years)
            train = df[(df["date"] < start) & (df["date"] >= train_lo)]
            yield train, chunk
        start = end


def walk_forward_predictions(
    conn: sqlite3.Connection,
    since: str | None = None,
    refit_days: int = 30,
    test_years: int = 2,
    train_years: int = 4,
    neutral: bool = True,
) -> list[dict[str, Any]]:
    df = history_frame(conn)
    if df.empty:
        return []
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)
    max_date = df["date"].max()
    cutoff = pd.to_datetime(since) if since else (max_date - pd.DateOffset(years=test_years))

    out: list[dict[str, Any]] = []
    for train, chunk in iter_chunks(df, cutoff, refit_days, train_years):
        if len(train) < MIN_TRAIN:
            continue
        model = GoalModel().fit(train)
        for m in chunk.to_dict("records"):
            home, away = m["home_team"], m["away_team"]
            try:
                grid = model.predict_grid(home, away, neutral=neutral)
            except Exception:
                continue
            out.append(
                {
                    "date": str(pd.Timestamp(m["date"]).date()),
                    "home": home,
                    "away": away,
                    "p_home": grid.home_win,
                    "p_draw": grid.draw,
                    "p_away": grid.away_win,
                    "outcome": _outcome(int(m["home_goals"]), int(m["away_goals"])),
                }
            )
    return out


def reliability(oos: list[dict[str, Any]], n_bins: int = 10) -> dict[str, Any]:
    """Confidence-calibration: bin the top predicted prob vs whether that pick was correct."""
    bins: list[list[tuple[float, float]]] = [[] for _ in range(n_bins)]
    for r in oos:
        probs = [r["p_home"], r["p_draw"], r["p_away"]]
        conf = max(probs)
        pred = probs.index(conf)
        correct = 1.0 if pred == r["outcome"] else 0.0
        idx = min(int(conf * n_bins), n_bins - 1)
        bins[idx].append((conf, correct))
    rows: list[dict[str, Any]] = []
    ece = 0.0
    total = len(oos)
    for i, b in enumerate(bins):
        if not b:
            continue
        avg_conf = sum(c for c, _ in b) / len(b)
        acc = sum(ok for _, ok in b) / len(b)
        rows.append(
            {
                "lo": i / n_bins,
                "hi": (i + 1) / n_bins,
                "n": len(b),
                "confidence": avg_conf,
                "accuracy": acc,
            }
        )
        if total:
            ece += abs(acc - avg_conf) * len(b) / total
    return {"bins": rows, "ece": ece}


def metrics(oos: list[dict[str, Any]], params: dict[str, float] | None = None) -> dict[str, Any]:
    """Mean RPS/Brier/log-loss for the raw model and the flat baseline; calibrated RPS if given."""
    n = len(oos)
    if not n:
        return {"n": 0}
    m_rps = m_brier = m_ll = b_rps = c_rps = 0.0
    for r in oos:
        probs = [r["p_home"], r["p_draw"], r["p_away"]]
        o = int(r["outcome"])
        m_rps += rps(probs, o)
        m_brier += multiclass_brier(probs, o)
        m_ll += log_loss_score(probs, o)
        b_rps += rps(list(BASELINE), o)
        if params is not None:
            cp = list(calibrate.apply(probs[0], probs[1], probs[2], params))
            c_rps += rps(cp, o)
    res: dict[str, Any] = {
        "n": n,
        "model_rps": m_rps / n,
        "model_brier": m_brier / n,
        "model_log_loss": m_ll / n,
        "baseline_rps": b_rps / n,
    }
    if params is not None:
        res["calibrated_rps"] = c_rps / n
    return res
