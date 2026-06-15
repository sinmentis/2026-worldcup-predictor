from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any, Iterator

import numpy as np

from worldcup_predictor.models import GroupRow

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


def _h2h(team: str, others: set[str], results: list[Result]) -> tuple[int, int]:
    pts = gd = 0
    for h, a, hg, ag in results:
        if h == team and a in others:
            gd += hg - ag
            pts += 3 if hg > ag else (1 if hg == ag else 0)
        elif a == team and h in others:
            gd += ag - hg
            pts += 3 if ag > hg else (1 if hg == ag else 0)
    return pts, gd


def standings_from_results(
    teams: list[str], results: list[Result], rng: random.Random | None = None
) -> list[GroupRow]:
    rng = rng or random.Random()
    acc = _accumulate(teams, results)

    def key(team: str) -> tuple[Any, ...]:
        a = acc[team]
        tied = {t for t in teams if acc[t].pts == a.pts and t != team} | {team}
        h2h_pts, h2h_gd = _h2h(team, tied - {team}, results) if len(tied) > 1 else (0, 0)
        return (a.pts, a.gf - a.ga, a.gf, h2h_pts, h2h_gd, rng.random())

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
