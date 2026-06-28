from worldcup_predictor import bracket_topology as bt


def test_fixture_ranges():
    assert bt.R32_FIXTURES == tuple(range(73, 89))
    assert bt.R16_FIXTURES == tuple(range(89, 97))
    assert bt.QF_FIXTURES == tuple(range(97, 101))
    assert bt.SF_FIXTURES == (101, 102)
    assert bt.THIRD_FIXTURE == 103 and bt.FINAL_FIXTURE == 104


def test_feeders_match_official_structure():
    assert bt.FEEDERS == {
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
    # 3rd place is handled separately (SF losers), so it is NOT a feeder entry.
    assert bt.THIRD_FIXTURE not in bt.FEEDERS


def test_r32_template_has_16_pairs():
    assert len(bt.R32_TEMPLATE) == 16
    assert bt.R32_TEMPLATE[0] == ("RU_A", "RU_B")  # fixture 73
    assert bt.R32_TEMPLATE[1] == ("W_E", "3")  # fixture 74


def test_progress_pairs_via_official_feeders_not_consecutive():
    # Winner of fixture 73+i is labelled "W{73+i}".
    r32 = [f"W{73 + i}" for i in range(16)]
    seen: list[tuple[str, str]] = []

    def pick(a: str, b: str) -> str:
        seen.append((a, b))
        return a  # deterministic: first side always advances

    win = bt.progress(r32, pick)
    # R16 fixture 89 is decided between winners of fixtures 73 and 75 (official),
    # NOT 73 and 74 (the old consecutive bug). The 8 R16 ties resolve first, so they are
    # seen[:8]; scope the negative check there because W73 legitimately meets W74 at QF 97.
    assert ("W73", "W75") in seen[:8]
    assert ("W73", "W74") not in seen[:8]
    # Every fixture resolved; champion (104) flows from its feeders.
    assert set(win) == set(range(73, 89)) | set(bt.FEEDERS)
    assert win[89] == "W73" and win[104] == win[101]
