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
