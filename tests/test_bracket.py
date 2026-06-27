import numpy as np
import pandas as pd

from worldcup_predictor import bracket, db, ingest
from worldcup_predictor.goal_model import GoalModel
from worldcup_predictor.models import GroupRow
from worldcup_predictor.simulate import best_thirds, build_r32


def _row(team, pts, gd, gf):
    return GroupRow(team, 3, 0, 0, 0, gf, gf - gd, gd, pts)


def test_best_thirds_picks_top_8():
    thirds = {
        g: _row(f"T{g}", pts=pts, gd=0, gf=0)
        for g, pts in zip("ABCDEFGHIJKL", [9, 8, 7, 6, 5, 4, 3, 2, 1, 0, 0, 0], strict=True)
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


def _model():
    rng = np.random.default_rng(7)
    rows = []
    for _ in range(80):
        rows.append(
            (
                "2024-01-01",
                "Strong",
                "Weak",
                int(rng.integers(2, 5)),
                int(rng.integers(0, 2)),
                False,
            )
        )
        rows.append(
            (
                "2024-01-01",
                "Weak",
                "Strong",
                int(rng.integers(0, 2)),
                int(rng.integers(2, 5)),
                False,
            )
        )
    df = pd.DataFrame(
        rows, columns=["date", "home_team", "away_team", "home_goals", "away_goals", "neutral"]
    )
    return GoalModel().fit(df)


def _conn(tmp_path):
    conn = db.connect(tmp_path / "b.db")
    db.init_schema(conn)
    return conn


def test_advance_prob_sums_to_one_and_splits_draw():
    ah, aa = bracket.advance_prob(0.5, 0.2, 0.3)
    assert abs(ah + aa - 1.0) < 1e-9
    # Stronger 90' side takes a larger share of the draw → advances more often than its 90' win.
    assert ah > 0.5
    # Even match → coin flip on the draw share.
    eh, ea = bracket.advance_prob(0.4, 0.2, 0.4)
    assert abs(eh - 0.5) < 1e-9 and abs(ea - 0.5) < 1e-9


def test_build_uses_real_teams_and_predicts(tmp_path):
    conn = _conn(tmp_path)
    # Two R32 matches with known teams the model knows.
    ingest.apply_knockout_fixtures(
        conn,
        {
            "matches": [
                {
                    "id": 1,
                    "stage": "LAST_32",
                    "utcDate": "2026-06-28T10:00:00Z",
                    "status": "TIMED",
                    "homeTeam": {"name": "Strong"},
                    "awayTeam": {"name": "Weak"},
                    "score": {},
                },
                {
                    "id": 2,
                    "stage": "LAST_32",
                    "utcDate": "2026-06-28T14:00:00Z",
                    "status": "TIMED",
                    "homeTeam": {"name": "Weak"},
                    "awayTeam": {"name": "Strong"},
                    "score": {},
                },
                {
                    "id": 3,
                    "stage": "LAST_16",
                    "utcDate": "2026-07-04T10:00:00Z",
                    "status": "TIMED",
                    "homeTeam": None,
                    "awayTeam": None,
                    "score": {},
                },
            ]
        },
    )
    out = bracket.build_predicted_bracket(conn, _model())
    r32 = next(r for r in out["rounds"] if r["stage"] == "R32")
    m0 = r32["matches"][0]
    assert m0["home"] == "Strong" and m0["home_known"] and m0["away_known"]
    assert abs(m0["advance_home"] + m0["advance_away"] - 1.0) < 1e-9
    assert m0["advance_home"] > m0["advance_away"]  # Strong favoured
    # R16 match teams are projected from R32 predicted winners (Strong wins both R32 matches).
    r16 = next(r for r in out["rounds"] if r["stage"] == "R16")
    m16 = r16["matches"][0]
    assert m16["home"] == "Strong" and m16["away"] == "Strong"
    assert m16["home_known"] is False and m16["away_known"] is False
    assert out["total_fixtures"] == 3 and out["real_fixtures"] == 2


def test_actual_result_overrides_predicted_winner(tmp_path):
    conn = _conn(tmp_path)
    ingest.apply_knockout_fixtures(
        conn,
        {
            "matches": [
                # R32-1 FINISHED: Weak beat Strong on penalties (winner overrides the model's pick).
                {
                    "id": 1,
                    "stage": "LAST_32",
                    "utcDate": "2026-06-28T10:00:00Z",
                    "status": "FINISHED",
                    "homeTeam": {"name": "Strong"},
                    "awayTeam": {"name": "Weak"},
                    "score": {"winner": "AWAY_TEAM", "fullTime": {"home": 1, "away": 1}},
                },
                {
                    "id": 2,
                    "stage": "LAST_32",
                    "utcDate": "2026-06-28T14:00:00Z",
                    "status": "TIMED",
                    "homeTeam": {"name": "Strong"},
                    "awayTeam": {"name": "Weak"},
                    "score": {},
                },
                {
                    "id": 3,
                    "stage": "LAST_16",
                    "utcDate": "2026-07-04T10:00:00Z",
                    "status": "TIMED",
                    "homeTeam": None,
                    "awayTeam": None,
                    "score": {},
                },
            ]
        },
    )
    out = bracket.build_predicted_bracket(conn, _model())
    r16 = next(r for r in out["rounds"] if r["stage"] == "R16")
    # R16-1 home comes from R32-1's ACTUAL winner (Weak), not the predicted (Strong).
    assert r16["matches"][0]["home"] == "Weak"
