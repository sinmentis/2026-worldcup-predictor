from __future__ import annotations

from worldcup_predictor import config


def elo_expected(r_team: float, r_opp: float, neutral: bool = True) -> float:
    dr = r_team - r_opp
    if not neutral:
        dr += config.HOME_ADVANTAGE_ELO
    return 1.0 / (10 ** (-dr / 400) + 1)


def goal_diff_multiplier(gd: int) -> float:
    gd = abs(gd)
    if gd <= 1:
        return 1.0
    if gd == 2:
        return 1.5
    return (11 + gd) / 8


def elo_update(r: float, k: int, g: float, w: float, we: float) -> float:
    return r + k * g * (w - we)
