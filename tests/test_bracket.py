from worldcup_predictor.models import GroupRow
from worldcup_predictor.simulate import best_thirds, build_r32


def _row(team, pts, gd, gf):
    return GroupRow(team, 3, 0, 0, 0, gf, gf - gd, gd, pts)


def test_best_thirds_picks_top_8():
    thirds = {
        g: _row(f"T{g}", pts=pts, gd=0, gf=0)
        for g, pts in zip("ABCDEFGHIJKL", [9, 8, 7, 6, 5, 4, 3, 2, 1, 0, 0, 0])
    }
    chosen = best_thirds(thirds)
    assert len(chosen) == 8
    assert "TA" in {r.team for r in chosen}
    assert "TL" not in {r.team for r in chosen}


def test_build_r32_has_16_matches():
    winners = {g: f"W{g}" for g in "ABCDEFGHIJKL"}
    runners = {g: f"RU{g}" for g in "ABCDEFGHIJKL"}
    thirds = [f"3rd{i}" for i in range(8)]
    bracket = build_r32(winners, runners, thirds)
    assert len(bracket) == 16
    # every match is a 2-tuple of team names
    assert all(len(m) == 2 and all(m) for m in bracket)
