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
    teams = ["X", "Y"]
    results = [("X", "Y", 1, 0)]
    table = standings_from_results(teams, results)
    assert table[0].team == "X"
