from __future__ import annotations

import math
import sqlite3
import time
from typing import Any


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


def per_match_breakdown(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """For each finished match, compare our ORIGINAL (earliest stored) prediction with the
    actual result. Read-only (no metrics side effects)."""
    rows = conn.execute(
        "SELECT m.id AS match_id, m.home_team, m.away_team, m.group_id, m.kickoff, "
        "       m.home_score, m.away_score, "
        "       p.p_home, p.p_draw, p.p_away, p.ml_home, p.ml_away "
        "FROM predictions p "
        "JOIN matches m ON m.id = p.match_id "
        "JOIN (SELECT match_id, MIN(id) AS mn FROM predictions GROUP BY match_id) first "
        "  ON first.match_id = p.match_id AND first.mn = p.id "
        "WHERE m.status='FINISHED' "
        "ORDER BY COALESCE(m.kickoff,''), m.id"
    ).fetchall()
    out: list[dict[str, object]] = []
    for r in rows:
        probs = [r["p_home"], r["p_draw"], r["p_away"]]
        outcome = _outcome(r["home_score"], r["away_score"])
        pred_pick = max(range(3), key=lambda i: probs[i])
        out.append(
            {
                "match_id": r["match_id"],
                "home_team": r["home_team"],
                "away_team": r["away_team"],
                "group": r["group_id"],
                "kickoff": r["kickoff"],
                "home_score": r["home_score"],
                "away_score": r["away_score"],
                "p_home": probs[0],
                "p_draw": probs[1],
                "p_away": probs[2],
                "ml_home": r["ml_home"],
                "ml_away": r["ml_away"],
                "outcome": outcome,
                "pred_pick": pred_pick,
                "pick_correct": pred_pick == outcome,
                "exact_scoreline": r["ml_home"] == r["home_score"]
                and r["ml_away"] == r["away_score"],
                "model_rps": rps(probs, outcome),
                "baseline_rps": rps(BASELINE, outcome),
            }
        )
    return out


def score_finished_predictions(conn: sqlite3.Connection) -> dict[str, float]:
    # Score only the latest prediction per finished match (by row id) so repeated
    # predictions for the same match don't get counted multiple times.
    rows = conn.execute(
        "SELECT p.p_home, p.p_draw, p.p_away, m.home_score, m.away_score "
        "FROM predictions p "
        "JOIN matches m ON m.id = p.match_id "
        "JOIN (SELECT match_id, MAX(id) AS mx FROM predictions GROUP BY match_id) latest "
        "  ON latest.match_id = p.match_id AND latest.mx = p.id "
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
