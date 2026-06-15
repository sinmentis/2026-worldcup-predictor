from __future__ import annotations

import sqlite3

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


def k_for_tournament(tournament: str) -> int:
    t = tournament.lower()
    if "qualification" in t or "qualifier" in t:
        return config.K_TABLE["qualifier"]
    if "world cup" in t:
        return config.K_TABLE["world_cup"]
    if any(x in t for x in ("euro", "copa", "afcon", "nations", "gold cup", "asian cup")):
        return config.K_TABLE["continental_final"]
    if "friendly" in t:
        return config.K_TABLE["friendly"]
    return config.K_TABLE["minor_tournament"]


def _result(home_score: int, away_score: int) -> tuple[float, float]:
    if home_score > away_score:
        return 1.0, 0.0
    if home_score < away_score:
        return 0.0, 1.0
    return 0.5, 0.5


def compute_elo_ratings(conn: sqlite3.Connection) -> dict[str, float]:
    rows = conn.execute(
        "SELECT date, home_team, away_team, home_score, away_score, tournament, neutral "
        "FROM historical_matches ORDER BY date, id"
    ).fetchall()

    elo: dict[str, float] = {}
    games: dict[str, int] = {}

    def get(team: str) -> float:
        return elo.get(team, config.DEFAULT_ELO)

    for r in rows:
        h, a = r["home_team"], r["away_team"]
        neutral = bool(r["neutral"])
        we_h = elo_expected(get(h), get(a), neutral=neutral)
        we_a = 1.0 - we_h
        w_h, w_a = _result(r["home_score"], r["away_score"])
        g = goal_diff_multiplier(r["home_score"] - r["away_score"])
        k = k_for_tournament(r["tournament"] or "")
        elo[h] = elo_update(get(h), k, g, w_h, we_h)
        elo[a] = elo_update(get(a), k, g, w_a, we_a)
        games[h] = games.get(h, 0) + 1
        games[a] = games.get(a, 0) + 1

    mean = sum(elo.values()) / len(elo) if elo else config.DEFAULT_ELO
    for team, rating in elo.items():
        n = games.get(team, 0)
        shrunk = (n * rating + config.ELO_SHRINK_GAMES * mean) / (n + config.ELO_SHRINK_GAMES)
        elo[team] = shrunk
        conn.execute(
            "INSERT INTO teams(name, elo) VALUES(?, ?) "
            "ON CONFLICT(name) DO UPDATE SET elo=excluded.elo",
            (team, shrunk),
        )
    conn.commit()
    return elo
