"""Handicap / Asian-spread value bets: grid cover prob, odds parse/store, value, settlement."""

import numpy as np
import pandas as pd

from worldcup_predictor import db, odds, papertrade, valuebet
from worldcup_predictor.goal_model import GoalModel, ScoreGrid

# ---- ScoreGrid.cover ----------------------------------------------------------------------


def test_cover_sums_margin_cells():
    m = np.zeros((3, 3))
    m[2, 0] = 0.5  # 2-0, home margin +2
    m[1, 0] = 0.3  # 1-0, home margin +1
    m[1, 1] = 0.2  # 1-1, margin 0
    g = ScoreGrid(matrix=m)
    # home -1.5 covers iff margin > 1.5 -> only 2-0
    assert abs(g.cover(-1.5) - 0.5) < 1e-9
    # home -0.5 covers iff margin > 0.5 -> 2-0 and 1-0
    assert abs(g.cover(-0.5) - 0.8) < 1e-9
    # home +0.5 (underdog) covers iff margin > -0.5 -> all three
    assert abs(g.cover(0.5) - 1.0) < 1e-9


# ---- odds parse / store -------------------------------------------------------------------

SPREADS_PAYLOAD = [
    {
        "id": "1",
        "commence_time": "2026-06-23T23:00:00Z",
        "home_team": "Croatia",
        "away_team": "Panama",
        "bookmakers": [
            {
                "key": "pin",
                "markets": [
                    {
                        "key": "spreads",
                        "outcomes": [
                            {"name": "Croatia", "price": 1.90, "point": -1.5},
                            {"name": "Panama", "price": 1.90, "point": 1.5},
                        ],
                    }
                ],
            },
            {
                "key": "quarter",  # quarter line -> skipped in v1
                "markets": [
                    {
                        "key": "spreads",
                        "outcomes": [
                            {"name": "Croatia", "price": 1.95, "point": -1.75},
                            {"name": "Panama", "price": 1.85, "point": 1.75},
                        ],
                    }
                ],
            },
        ],
    }
]


def test_parse_spreads_keeps_half_skips_quarter():
    parsed = odds.parse_spreads_payload(SPREADS_PAYLOAD)
    assert len(parsed) == 1
    lines = parsed[0]["lines"]
    assert [b["bookmaker"] for b in lines] == ["pin"]  # the -1.75 quarter line is dropped
    b = lines[0]
    assert b["line"] == -1.5 and b["price_home"] == 1.90 and b["price_away"] == 1.90


def test_store_spreads_orients_to_our_fixture(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.init_schema(conn)
    # Our seeded fixture is the REVERSE of the feed: home=Panama, away=Croatia.
    conn.execute(
        "INSERT INTO matches(id,stage,group_id,home_team,away_team,neutral,status)"
        " VALUES (1,'group','L','Panama','Croatia',1,'SCHEDULED')"
    )
    conn.commit()
    odds.store_spreads(conn, odds.parse_spreads_payload(SPREADS_PAYLOAD))
    row = conn.execute(
        "SELECT line, price_home, price_away FROM odds_spreads WHERE bookmaker='pin'"
    ).fetchone()
    # feed had Croatia -1.5 @1.90 / Panama +1.5 @1.90; in our (Panama-home) orientation the line
    # negates to +1.5 and the prices swap.
    assert row["line"] == 1.5
    assert row["price_home"] == 1.90 and row["price_away"] == 1.90


# ---- value_bets_spreads -------------------------------------------------------------------


def _strong_weak_model() -> GoalModel:
    rng = np.random.default_rng(1)
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
    hist = pd.DataFrame(
        rows, columns=["date", "home_team", "away_team", "home_goals", "away_goals", "neutral"]
    )
    return GoalModel().fit(hist)


def test_value_bets_spreads_flags_home_cover(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.init_schema(conn)
    conn.execute(
        "INSERT INTO matches(id,stage,group_id,home_team,away_team,neutral,status)"
        " VALUES (1,'group','A','Strong','Weak',1,'SCHEDULED')"
    )
    # Market prices the -1.5 home cover around 45%; our dominant model thinks it's higher.
    conn.execute(
        "INSERT INTO odds_spreads(match_id,bookmaker,line,price_home,price_away,fetched_at)"
        " VALUES (1,'a',-1.5,2.20,1.70,0),(1,'b',-1.5,2.15,1.72,0)"
    )
    conn.commit()
    bets = valuebet.value_bets_spreads(conn, _strong_weak_model(), min_edge=0.05)
    home = [b for b in bets if b["market"] == "spreads" and b["outcome"] == "home"]
    assert home, "expected a home handicap value bet"
    assert home[0]["line"] == -1.5
    assert home[0]["best_price"] in (2.20, 2.15)


def test_value_bets_spreads_ignores_outlier_price(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.init_schema(conn)
    conn.execute(
        "INSERT INTO matches(id,stage,group_id,home_team,away_team,neutral,status)"
        " VALUES (1,'group','A','Strong','Weak',1,'SCHEDULED')"
    )
    # Four sane books ~2.2 plus one corrupt 9.9 on the home cover.
    conn.execute(
        "INSERT INTO odds_spreads(match_id,bookmaker,line,price_home,price_away,fetched_at)"
        " VALUES (1,'a',-1.5,2.20,1.70,0),(1,'b',-1.5,2.15,1.72,0),"
        "(1,'c',-1.5,2.18,1.71,0),(1,'bad',-1.5,9.90,1.70,0)"
    )
    conn.commit()
    bets = valuebet.value_bets_spreads(conn, _strong_weak_model(), min_edge=0.05)
    home = [b for b in bets if b["outcome"] == "home"]
    assert home and home[0]["best_price"] != 9.90  # outlier ignored


# ---- settlement ---------------------------------------------------------------------------


def test_result_spreads_win_loss_push():
    r = papertrade._result_spreads
    # home -1.5, final 2-0 (margin +2): home covers
    assert r(2, 0, -1.5, "home") == "win"
    assert r(2, 0, -1.5, "away") == "loss"
    # home -1.0, final 1-0 (margin +1, +line == 0): push both sides
    assert r(1, 0, -1.0, "home") == "push"
    assert r(1, 0, -1.0, "away") == "push"
    # home -2.0, final 1-0 (margin +1 < 2): home fails to cover
    assert r(1, 0, -2.0, "home") == "loss"
    assert r(1, 0, -2.0, "away") == "win"
    # half line never pushes: home +0.5, final 1-1 (draw) -> home (underdog) covers
    assert r(1, 1, 0.5, "home") == "win"
    assert r(1, 1, 0.5, "away") == "loss"
