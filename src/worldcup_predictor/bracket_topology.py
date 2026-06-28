from __future__ import annotations

from collections.abc import Callable

# R32 composition (FIFA Annex C slots). Index i corresponds to fixture number 73+i.
# "3" marks a best-third-place slot. Moved here from simulate so both the simulation and the
# knockout tree share one definition of the bracket structure.
R32_TEMPLATE: list[tuple[str, str]] = [
    ("RU_A", "RU_B"),  # 73
    ("W_E", "3"),  # 74
    ("W_F", "RU_C"),  # 75
    ("W_C", "RU_F"),  # 76
    ("W_I", "3"),  # 77
    ("RU_E", "RU_I"),  # 78
    ("W_A", "3"),  # 79
    ("W_L", "3"),  # 80
    ("W_D", "3"),  # 81
    ("W_G", "3"),  # 82
    ("RU_K", "RU_L"),  # 83
    ("W_H", "RU_J"),  # 84
    ("W_B", "3"),  # 85
    ("W_J", "RU_H"),  # 86
    ("W_K", "3"),  # 87
    ("RU_D", "RU_G"),  # 88
]

R32_FIXTURES: tuple[int, ...] = tuple(range(73, 89))  # 73..88
R16_FIXTURES: tuple[int, ...] = tuple(range(89, 97))  # 89..96
QF_FIXTURES: tuple[int, ...] = tuple(range(97, 101))  # 97..100
SF_FIXTURES: tuple[int, ...] = (101, 102)
THIRD_FIXTURE: int = 103
FINAL_FIXTURE: int = 104

# Each non-R32 fixture's two feeder fixtures (the winner of each advances into it). The 3rd-place
# match (103) is the two SF losers, handled separately, so it is intentionally absent here.
FEEDERS: dict[int, tuple[int, int]] = {
    89: (73, 75),
    90: (74, 77),
    91: (76, 78),
    92: (79, 80),
    93: (83, 84),
    94: (81, 82),
    95: (86, 88),
    96: (85, 87),
    97: (89, 90),
    98: (93, 94),
    99: (91, 92),
    100: (95, 96),
    101: (97, 98),
    102: (99, 100),
    104: (101, 102),
}


def progress(r32_winners: list[str], pick: Callable[[str, str], str]) -> dict[int, str]:
    """Resolve every knockout fixture's winner from the 16 R32 winners using the official feeders.

    ``r32_winners[i]`` is the winner of fixture ``R32_FIXTURES[i]``. ``pick(a, b)`` returns the
    winner of a single tie. Returns a map from every fixture number (73..102, 104) to its winner.
    """
    win: dict[int, str] = {R32_FIXTURES[i]: r32_winners[i] for i in range(16)}
    for fx in (*R16_FIXTURES, *QF_FIXTURES, *SF_FIXTURES, FINAL_FIXTURE):
        fa, fb = FEEDERS[fx]
        win[fx] = pick(win[fa], win[fb])
    return win
