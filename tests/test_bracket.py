from worldcup_predictor import bracket
from worldcup_predictor.models import GroupRow
from worldcup_predictor.simulate import best_thirds, build_r32


def _finish_all_groups(conn):
    """Mark every group match FINISHED (home wins 1-0) so standings/fixture signatures resolve."""
    import itertools

    from worldcup_predictor import config

    for teams in config.GROUPS.values():
        for h, a in itertools.combinations(teams, 2):
            conn.execute(
                "UPDATE matches SET home_score=1, away_score=0, status='FINISHED' "
                "WHERE stage='group' AND home_team=? AND away_team=?",
                (h, a),
            )
    conn.commit()


def _all_teams_model():
    """A GoalModel fit on all 48 finalists so any real team predicts."""
    import numpy as np
    import pandas as pd

    from worldcup_predictor import config
    from worldcup_predictor.goal_model import GoalModel

    teams = [t for g in config.GROUPS.values() for t in g]
    rng = np.random.default_rng(0)
    rows = []
    for t in teams:
        for _ in range(4):
            opp = teams[int(rng.integers(0, len(teams)))]
            if opp == t:
                continue
            rows.append(
                ("2024-01-01", t, opp, int(rng.integers(0, 4)), int(rng.integers(0, 3)), True)
            )
    return GoalModel().fit(
        pd.DataFrame(
            rows, columns=["date", "home_team", "away_team", "home_goals", "away_goals", "neutral"]
        )
    )


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


def test_advance_prob_sums_to_one_and_splits_draw():
    ah, aa = bracket.advance_prob(0.5, 0.2, 0.3)
    assert abs(ah + aa - 1.0) < 1e-9
    # Stronger 90' side takes a larger share of the draw → advances more often than its 90' win.
    assert ah > 0.5
    # Even match → coin flip on the draw share.
    eh, ea = bracket.advance_prob(0.4, 0.2, 0.4)
    assert abs(eh - 0.5) < 1e-9 and abs(ea - 0.5) < 1e-9


def test_projection_pairs_via_official_feeders(tmp_path):
    from worldcup_predictor import bracket, db, ingest

    conn = db.connect(tmp_path / "p.db")
    db.init_schema(conn)
    ingest.seed_teams_and_fixtures(conn)
    _finish_all_groups(conn)
    winners, runners = bracket._group_winners_runners(conn)
    f73 = (runners["A"], runners["B"])  # fixture 73 = RU_A vs RU_B
    f75 = (winners["F"], runners["C"])  # fixture 75 = W_F vs RU_C  (official: 73 & 75 meet in R16)
    for ext, (h, a), ko in (
        (9073, f73, "2026-06-28T19:00:00Z"),
        (9075, f75, "2026-06-28T22:00:00Z"),
    ):
        conn.execute(
            "INSERT INTO matches(stage,home_team,away_team,kickoff,neutral,status,ext_id) "
            "VALUES ('R32',?,?,?,1,'SCHEDULED',?)",
            (h, a, ko, ext),
        )
    conn.commit()

    out = bracket.build_predicted_bracket(conn, _all_teams_model())
    r32 = next(r for r in out["rounds"] if r["stage"] == "R32")["matches"]
    # Full 16-slot R32 skeleton, ordered by fixture number (slot R32-1 = fixture 73, R32-3 = 75).
    assert len(r32) == 16
    assert r32[0]["home"] in f73 and r32[0]["away"] in f73  # fixture 73 first
    assert r32[2]["home"] in f75 and r32[2]["away"] in f75  # fixture 75 third
    assert any(m.get("kickoff") == "2026-06-28T19:00:00Z" for m in r32)
    # R16 fixture 89 = winners of fixtures 73 and 75 (NOT 73 and 74 — the old bug).
    r16 = next(r for r in out["rounds"] if r["stage"] == "R16")["matches"]
    m89 = r16[0]
    assert m89["home"] in f73 and m89["away"] in f75


def test_actual_result_overrides_predicted_winner(tmp_path):
    from worldcup_predictor import bracket, db, ingest

    conn = db.connect(tmp_path / "o.db")
    db.init_schema(conn)
    ingest.seed_teams_and_fixtures(conn)
    _finish_all_groups(conn)
    winners, runners = bracket._group_winners_runners(conn)
    home73, away73 = runners["A"], runners["B"]  # fixture 73
    # Fixture 73 FINISHED: the AWAY side wins on penalties (1-1, winner=away), overriding any pick.
    conn.execute(
        "INSERT INTO matches(stage,home_team,away_team,kickoff,neutral,status,"
        "home_score,away_score,winner_team,ext_id) "
        "VALUES ('R32',?,?,?,1,'FINISHED',1,1,?,9073)",
        (home73, away73, "2026-06-28T19:00:00Z", away73),
    )
    conn.execute(
        "INSERT INTO matches(stage,home_team,away_team,kickoff,neutral,status,ext_id) "
        "VALUES ('R32',?,?,?,1,'SCHEDULED',9075)",
        (winners["F"], runners["C"], "2026-06-28T22:00:00Z"),
    )
    conn.commit()

    out = bracket.build_predicted_bracket(conn, _all_teams_model())
    r16 = next(r for r in out["rounds"] if r["stage"] == "R16")["matches"]
    # R16 fixture 89's home comes from fixture 73's ACTUAL winner (the away side), not a prediction.
    assert r16[0]["home"] == away73


def test_group_winners_runners(tmp_path):
    from worldcup_predictor import bracket, db, ingest

    conn = db.connect(tmp_path / "g.db")
    db.init_schema(conn)
    ingest.seed_teams_and_fixtures(conn)
    # Make group A complete: Mexico wins all, South Africa second. (4 teams → 6 matches.)
    a = ["Mexico", "South Africa", "South Korea", "Czech Republic"]
    import itertools

    for h, away in itertools.combinations(a, 2):
        hs, as_ = (3, 0) if h == "Mexico" else (1, 0) if h == "South Africa" else (0, 0)
        conn.execute(
            "UPDATE matches SET home_score=?, away_score=?, status='FINISHED' "
            "WHERE stage='group' AND home_team=? AND away_team=?",
            (hs, as_, h, away),
        )
    conn.commit()
    winners, runners = bracket._group_winners_runners(conn)
    assert winners["A"] == "Mexico"
    assert runners["A"] == "South Africa"
    assert "B" not in winners  # group B not finished → absent


def test_fixture_of_r32_row_maps_by_signature():
    from worldcup_predictor import bracket

    winners = {g: f"W_{g}" for g in "ABCDEFGHIJKL"}
    runners = {g: f"RU_{g}" for g in "ABCDEFGHIJKL"}
    sigs = bracket._r32_signatures(winners, runners)
    # Fixture 73 = RU_A vs RU_B (a "pair" fixture): identified by its two-team set.
    assert bracket.fixture_of_r32_row("RU_A", "RU_B", sigs) == 73
    assert bracket.fixture_of_r32_row("RU_B", "RU_A", sigs) == 73  # order-independent
    # Fixture 74 = W_E vs 3rd (an "anchor" fixture): identified by the W_E side, any third.
    assert bracket.fixture_of_r32_row("W_E", "RU_C", sigs) == 74  # "RU_C" stands in for a 3rd
    assert bracket.fixture_of_r32_row("SomeThird", "W_E", sigs) == 74
    # A row whose teams match nothing yet → None.
    assert bracket.fixture_of_r32_row("Nobody", "Nobody2", sigs) is None


def test_fixture_of_r32_row_none_when_group_unfinished(tmp_path):
    # Task 4 relies on the None contract: an unfinished group yields a None-bearing signature that
    # must NOT spuriously match a feed row whose fixture depends on that group.
    from worldcup_predictor import bracket, db, ingest

    conn = db.connect(tmp_path / "u.db")
    db.init_schema(conn)
    ingest.seed_teams_and_fixtures(conn)
    _finish_all_groups(conn)
    _full_winners, full_runners = bracket._group_winners_runners(conn)
    ru_a, ru_b = full_runners["A"], full_runners["B"]  # the real fixture-73 pair (RU_A vs RU_B)
    # Reopen one of group A's matches so group A is no longer complete.
    conn.execute(
        "UPDATE matches SET status='SCHEDULED', home_score=NULL, away_score=NULL "
        "WHERE stage='group' AND group_id='A' AND id="
        "(SELECT MIN(id) FROM matches WHERE stage='group' AND group_id='A')"
    )
    conn.commit()
    winners, runners = bracket._group_winners_runners(conn)
    assert "A" not in winners  # group A incomplete → absent from standings
    sigs = bracket._r32_signatures(winners, runners)
    # Fixture 73 (RU_A vs RU_B) needs group A; with A unfinished its signature carries a None, so
    # even the real fixture-73 pair must NOT resolve to a fixture.
    assert bracket.fixture_of_r32_row(ru_a, ru_b, sigs) is None
