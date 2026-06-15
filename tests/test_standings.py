from worldcup_predictor.simulate import standings_from_results


def test_points_then_gd():
    # A beats B 2-0, A beats C 1-0, B beats C 3-0, C-? ... build a small group
    teams = ["A", "B", "C", "D"]
    results = [
        ("A", "B", 2, 0),
        ("A", "C", 1, 0),
        ("A", "D", 1, 0),
        ("B", "C", 3, 0),
        ("B", "D", 1, 0),
        ("C", "D", 0, 0),
    ]
    table = standings_from_results(teams, results)
    order = [row.team for row in table]
    assert order[0] == "A"  # 9 pts
    assert order[1] == "B"  # 6 pts
    # C vs D: C 1pt (0-0 draw + losses), D 1pt -> GD/GF tiebreak
    assert set(order[2:]) == {"C", "D"}


def test_head_to_head_breaks_equal_points():
    # X, Y, Z all finish on 3 pts and GD 0. Z is separated by fewer goals scored (GF=1).
    # X and Y stay tied on (pts, GD, GF); X beat Y head-to-head, so X must rank above Y.
    teams = ["X", "Y", "Z"]
    results = [
        ("X", "Y", 2, 1),  # X beats Y head-to-head
        ("X", "Z", 0, 1),  # Z beats X
        ("Y", "Z", 1, 0),  # Y beats Z
    ]
    table = standings_from_results(teams, results)
    order = [row.team for row in table]
    assert order == ["X", "Y", "Z"]


def test_head_to_head_not_polluted_by_already_separated_teams():
    # Regression for the h2h-pollution bug: B, C, D all finish on 6 pts.
    # D is separated by a superior overall GD (+5). B and C stay tied on (6, +1, GF=2);
    # B beat C head-to-head, so B must rank above C deterministically. Under the old code
    # (h2h taken over ALL equal-points teams) B and C tie on polluted h2h and the order
    # was decided by a coin flip.
    teams = ["A", "B", "C", "D"]
    results = [
        ("B", "A", 1, 0),
        ("C", "A", 1, 0),
        ("D", "A", 5, 0),  # D piles up goal difference
        ("B", "C", 1, 0),  # B beats C head-to-head
        ("C", "D", 1, 0),
        ("D", "B", 1, 0),
    ]
    for _ in range(5):  # deterministic regardless of the random final tiebreak
        order = [row.team for row in standings_from_results(teams, results)]
        assert order == ["D", "B", "C", "A"]
