from __future__ import annotations

import math
import sqlite3
import time


def rps(probs: list[float], outcome: int) -> float:
    """Ranked Probability Score for ordered 1X2 outcome (0=H,1=D,2=A)."""
    cum_p = 0.0
    cum_o = 0.0
    total = 0.0
    for k in range(len(probs) - 1):
        cum_p += probs[k]
        cum_o += 1.0 if outcome == k else 0.0
        total += (cum_p - cum_o) ** 2
    return total / (len(probs) - 1)


def multiclass_brier(probs: list[float], outcome: int) -> float:
    return sum((p - (1.0 if i == outcome else 0.0)) ** 2 for i, p in enumerate(probs))


def log_loss_score(probs: list[float], outcome: int, eps: float = 1e-15) -> float:
    p = min(1 - eps, max(eps, probs[outcome]))
    return -math.log(p)


BASELINE = [0.40, 0.30, 0.30]


def _outcome(home_score: int, away_score: int) -> int:
    if home_score > away_score:
        return 0
    if home_score == away_score:
        return 1
    return 2


def score_finished_predictions(conn: sqlite3.Connection) -> dict[str, float]:
    rows = conn.execute(
        "SELECT p.p_home, p.p_draw, p.p_away, m.home_score, m.away_score "
        "FROM predictions p JOIN matches m ON m.id = p.match_id "
        "WHERE m.status='FINISHED'"
    ).fetchall()
    if not rows:
        return {"n": 0}

    m_rps = m_brier = m_ll = b_rps = 0.0
    for r in rows:
        p_home, p_draw, p_away, home_score, away_score = r
        probs = [p_home, p_draw, p_away]
        out = _outcome(home_score, away_score)
        m_rps += rps(probs, out)
        m_brier += multiclass_brier(probs, out)
        m_ll += log_loss_score(probs, out)
        b_rps += rps(BASELINE, out)

    n = len(rows)
    summary = {
        "n": n,
        "model_rps": m_rps / n,
        "model_brier": m_brier / n,
        "model_log_loss": m_ll / n,
        "baseline_rps": b_rps / n,
    }
    now = time.time()
    for key in ("model_rps", "model_brier", "model_log_loss", "baseline_rps"):
        conn.execute(
            "INSERT INTO metrics(created_at, metric, value, scope) VALUES (?,?,?,?)",
            (now, key, summary[key], "all_finished"),
        )
    conn.commit()
    return summary
