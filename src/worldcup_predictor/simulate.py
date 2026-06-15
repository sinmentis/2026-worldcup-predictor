from __future__ import annotations

import random
import sqlite3
import time
from collections.abc import Iterator
from dataclasses import dataclass
from itertools import combinations
from typing import Any

import numpy as np

from worldcup_predictor import config
from worldcup_predictor.goal_model import GoalModel
from worldcup_predictor.models import GroupRow
from worldcup_predictor.predict import adjusted_grid

Result = tuple[str, str, int, int]  # home, away, home_goals, away_goals


@dataclass
class _Acc:
    played: int = 0
    won: int = 0
    drawn: int = 0
    lost: int = 0
    gf: int = 0
    ga: int = 0
    pts: int = 0


def _accumulate(teams: list[str], results: list[Result]) -> dict[str, _Acc]:
    table = {t: _Acc() for t in teams}
    for h, a, hg, ag in results:
        table[h].played += 1
        table[a].played += 1
        table[h].gf += hg
        table[h].ga += ag
        table[a].gf += ag
        table[a].ga += hg
        if hg > ag:
            table[h].won += 1
            table[a].lost += 1
            table[h].pts += 3
        elif hg < ag:
            table[a].won += 1
            table[h].lost += 1
            table[a].pts += 3
        else:
            table[h].drawn += 1
            table[a].drawn += 1
            table[h].pts += 1
            table[a].pts += 1
    return table


def _h2h(team: str, others: set[str], results: list[Result]) -> tuple[int, int, int]:
    pts = gd = gf = 0
    for h, a, hg, ag in results:
        if h == team and a in others:
            gd += hg - ag
            gf += hg
            pts += 3 if hg > ag else (1 if hg == ag else 0)
        elif a == team and h in others:
            gd += ag - hg
            gf += ag
            pts += 3 if ag > hg else (1 if hg == ag else 0)
    return pts, gd, gf


def standings_from_results(
    teams: list[str], results: list[Result], rng: random.Random | None = None
) -> list[GroupRow]:
    rng = rng or random.Random()
    acc = _accumulate(teams, results)

    def key(team: str) -> tuple[Any, ...]:
        a = acc[team]
        a_gd = a.gf - a.ga
        # FIFA Annex C: head-to-head applies only among teams still tied after the
        # overall criteria (points, overall GD, overall GF), not all equal-on-points teams.
        tied = {
            t
            for t in teams
            if (acc[t].pts, acc[t].gf - acc[t].ga, acc[t].gf) == (a.pts, a_gd, a.gf)
        }
        if len(tied) > 1:
            h2h_pts, h2h_gd, h2h_gf = _h2h(team, tied - {team}, results)
        else:
            h2h_pts, h2h_gd, h2h_gf = 0, 0, 0
        return (a.pts, a_gd, a.gf, h2h_pts, h2h_gd, h2h_gf, rng.random())

    ordered = sorted(teams, key=key, reverse=True)
    return [
        GroupRow(
            team=t,
            played=acc[t].played,
            won=acc[t].won,
            drawn=acc[t].drawn,
            lost=acc[t].lost,
            gf=acc[t].gf,
            ga=acc[t].ga,
            gd=acc[t].gf - acc[t].ga,
            pts=acc[t].pts,
        )
        for t in ordered
    ]


# Fixed Annex C R32 pairing template. "3" marks a best-third slot (filled in order).
_R32_TEMPLATE: list[tuple[str, str]] = [
    ("RU_A", "RU_B"),
    ("W_E", "3"),
    ("W_F", "RU_C"),
    ("W_C", "RU_F"),
    ("W_I", "3"),
    ("RU_E", "RU_I"),
    ("W_A", "3"),
    ("W_L", "3"),
    ("W_D", "3"),
    ("W_G", "3"),
    ("RU_K", "RU_L"),
    ("W_H", "RU_J"),
    ("W_B", "3"),
    ("W_J", "RU_H"),
    ("W_K", "3"),
    ("RU_D", "RU_G"),
]


def best_thirds(thirds: dict[str, GroupRow], rng: random.Random | None = None) -> list[GroupRow]:
    rng = rng or random.Random()
    ranked = sorted(
        thirds.values(),
        key=lambda r: (r.pts, r.gd, r.gf, rng.random()),
        reverse=True,
    )
    return ranked[:8]


def build_r32(
    winners: dict[str, str], runners: dict[str, str], thirds: list[str]
) -> list[tuple[str, str]]:
    third_iter = iter(thirds)
    out: list[tuple[str, str]] = []
    for left, right in _R32_TEMPLATE:
        a = _resolve(left, winners, runners, third_iter)
        b = _resolve(right, winners, runners, third_iter)
        out.append((a, b))
    return out


def _resolve(
    token: str, winners: dict[str, str], runners: dict[str, str], thirds: Iterator[str]
) -> str:
    if token == "3":
        return next(thirds)
    side, gid = token.split("_")
    return winners[gid] if side == "W" else runners[gid]


def _sample_score(matrix: np.ndarray, rng: np.random.Generator) -> tuple[int, int]:
    flat = matrix.ravel()
    idx = rng.choice(flat.size, p=flat / flat.sum())
    h, a = np.unravel_index(idx, matrix.shape)
    return int(h), int(a)


def _knockout_winner(
    a: str,
    b: str,
    probs: dict[tuple[str, str], tuple[float, float, float]],
    grids: dict[tuple[str, str], np.ndarray],
    rng: np.random.Generator,
) -> str:
    p_h, _p_d, p_a = probs[(a, b)]
    r = rng.random()
    if r < p_h:
        return a
    if r < p_h + p_a:
        return b
    return a if rng.random() < 0.5 else b  # penalties ~ 50/50


def _load_played_groups(
    conn: sqlite3.Connection,
) -> tuple[dict[str, list[Result]], dict[str, set[frozenset[str]]]]:
    """Return finished group results and the set of played pairs, keyed by group."""
    played: dict[str, list[Result]] = {g: [] for g in config.GROUPS}
    pairs: dict[str, set[frozenset[str]]] = {g: set() for g in config.GROUPS}
    rows = conn.execute(
        "SELECT group_id, home_team, away_team, home_score, away_score "
        "FROM matches WHERE stage='group' AND status='FINISHED' "
        "AND home_score IS NOT NULL AND away_score IS NOT NULL"
    ).fetchall()
    for r in rows:
        gid = r["group_id"]
        if gid not in played:
            continue
        played[gid].append(
            (r["home_team"], r["away_team"], int(r["home_score"]), int(r["away_score"]))
        )
        pairs[gid].add(frozenset((r["home_team"], r["away_team"])))
    return played, pairs


def simulate_tournament(
    conn: sqlite3.Connection, model: GoalModel, n: int = 50_000, seed: int | None = None
) -> dict[str, dict[str, float]]:
    rng = np.random.default_rng(seed)
    teams = [t for ts in config.GROUPS.values() for t in ts]

    # Pre-compute intel-adjusted grids and 1X2 probs for every ordered pair once, so the
    # tournament odds reflect the same off-pitch intel that single-match predictions use.
    grids: dict[tuple[str, str], np.ndarray] = {}
    probs: dict[tuple[str, str], tuple[float, float, float]] = {}
    for x in teams:
        for y in teams:
            if x == y:
                continue
            g, _factors = adjusted_grid(conn, model, x, y, neutral=True)
            grids[(x, y)] = g.matrix
            probs[(x, y)] = (g.home_win, g.draw, g.away_win)

    counts = {t: dict(advance=0, r16=0, qf=0, sf=0, final=0, title=0) for t in teams}
    played, played_pairs = _load_played_groups(conn)

    for _ in range(n):
        winners: dict[str, str] = {}
        runners: dict[str, str] = {}
        thirds_rows: dict[str, GroupRow] = {}

        for gid, gteams in config.GROUPS.items():
            # Condition on already-finished matches; only sample the ones not yet played.
            results: list[Result] = list(played[gid])
            for h, a in combinations(gteams, 2):
                if frozenset((h, a)) in played_pairs[gid]:
                    continue
                hg, ag = _sample_score(grids[(h, a)], rng)
                results.append((h, a, hg, ag))
            table = standings_from_results(
                gteams, results, random.Random(int(rng.integers(1 << 30)))
            )
            winners[gid] = table[0].team
            runners[gid] = table[1].team
            thirds_rows[gid] = table[2]

        for t in list(winners.values()) + list(runners.values()):
            counts[t]["advance"] += 1
        qual_thirds = best_thirds(thirds_rows, random.Random(int(rng.integers(1 << 30))))
        for r in qual_thirds:
            counts[r.team]["advance"] += 1

        bracket = build_r32(winners, runners, [r.team for r in qual_thirds])
        # Winning round R32/R16/QF/SF/Final credits reaching r16/qf/sf/final/title.
        for round_key in ("r16", "qf", "sf", "final", "title"):
            winners_round = [_knockout_winner(a, b, probs, grids, rng) for a, b in bracket]
            for w in winners_round:
                counts[w][round_key] += 1
            it = iter(winners_round)
            bracket = list(zip(it, it))  # noqa: B905 - pair winners for the next round

    result = {t: {k: v / n for k, v in counts[t].items()} for t in teams}
    now = time.time()
    conn.execute("DELETE FROM sim_results")
    for t, p in result.items():
        conn.execute(
            "INSERT INTO sim_results(created_at, team, advance_prob, r16_prob, qf_prob,"
            " sf_prob, final_prob, title_prob, n_iter) VALUES (?,?,?,?,?,?,?,?,?)",
            (now, t, p["advance"], p["r16"], p["qf"], p["sf"], p["final"], p["title"], n),
        )
    conn.commit()
    return result
