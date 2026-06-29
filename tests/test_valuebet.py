import numpy as np
import pandas as pd

from worldcup_predictor import db, valuebet
from worldcup_predictor.goal_model import GoalModel


def _model():
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


def _conn(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.init_schema(conn)
    conn.execute(
        "INSERT INTO matches(id,stage,group_id,home_team,away_team,neutral,status)"
        " VALUES (1,'group','A','Strong','Weak',1,'SCHEDULED')"
    )
    conn.commit()
    return conn


def test_best_prices_picks_highest(tmp_path):
    conn = _conn(tmp_path)
    conn.execute(
        "INSERT INTO odds(match_id,bookmaker,price_home,price_draw,price_away,fetched_at)"
        " VALUES (1,'a',1.5,3.5,6.0,0),(1,'b',1.6,3.4,5.5,0)"
    )
    conn.commit()
    best = valuebet.best_prices(conn, 1)
    assert best[0] == (1.6, "b")
    assert best[1] == (3.5, "a")


def test_consensus_probs_demargined_median(tmp_path):
    conn = _conn(tmp_path)
    conn.execute(
        "INSERT INTO odds(match_id,bookmaker,price_home,price_draw,price_away,fetched_at)"
        " VALUES (1,'a',2.0,3.5,4.0,0),(1,'b',2.1,3.4,3.8,0)"
    )
    conn.commit()
    cons = valuebet.consensus_probs(conn, 1)
    assert cons is not None
    assert abs(sum(cons) - 1.0) < 1e-9
    assert cons[0] > cons[1] > cons[2]  # home favourite


def test_value_flagged_when_we_beat_market(tmp_path):
    conn = _conn(tmp_path)
    model = _model()  # Strong heavily favoured (p_home high)
    # market prices Strong's home win around 0.48 (de-margined); our model says much higher
    conn.execute(
        "INSERT INTO odds(match_id,bookmaker,price_home,price_draw,price_away,fetched_at)"
        " VALUES (1,'soft',2.0,3.5,4.0,0)"
    )
    conn.commit()
    bets = valuebet.value_bets(conn, model, min_edge=0.05)
    home = [b for b in bets if b["outcome"] == "home"]
    assert home, "expected a value flag where our prob >> market consensus"
    b = home[0]
    assert b["edge"] > 0.05  # our_prob - market_prob
    assert b["our_prob"] > b["market_prob"]
    assert b["best_price"] == 2.0 and b["bookmaker"] == "soft"
    assert b["ev"] is not None and 0.0 < b["kelly"] <= 1.0


def test_no_value_when_market_agrees(tmp_path):
    conn = _conn(tmp_path)
    model = _model()
    # market also makes Strong a heavy favourite -> no outcome where we beat the market by 5pts
    conn.execute(
        "INSERT INTO odds(match_id,bookmaker,price_home,price_draw,price_away,fetched_at)"
        " VALUES (1,'sharp',1.05,8.0,15.0,0)"
    )
    conn.commit()
    assert valuebet.value_bets(conn, model, min_edge=0.05) == []


def test_value_bets_empty_without_odds(tmp_path):
    conn = _conn(tmp_path)
    model = _model()
    assert valuebet.value_bets(conn, model) == []


def test_value_bets_totals_flags_over(tmp_path):
    conn = _conn(tmp_path)
    model = _model()  # Strong vs Weak: high-scoring, P(over 2.5) high
    conn.execute(
        "INSERT INTO odds_totals(match_id,bookmaker,line,price_over,price_under,fetched_at)"
        " VALUES (1,'a',2.5,2.2,1.7,0),(1,'b',2.5,2.1,1.75,0)"
    )
    conn.commit()
    bets = valuebet.value_bets_totals(conn, model, min_edge=0.05)
    over = [b for b in bets if b["outcome"] == "over"]
    assert over, "expected an over value bet (our P(over) >> market)"
    b = over[0]
    assert b["market"] == "totals" and b["line"] == 2.5
    assert b["edge"] > 0.05 and b["our_prob"] > b["market_prob"]
    assert b["best_price"] == 2.2  # best (highest) over price across books


def test_value_bets_totals_empty_without_odds(tmp_path):
    conn = _conn(tmp_path)
    assert valuebet.value_bets_totals(conn, _model()) == []


def test_value_bets_excludes_started_matches(tmp_path):
    conn = _conn(tmp_path)
    conn.execute(
        "UPDATE matches SET kickoff='2020-01-01T00:00:00Z' WHERE id=1"
    )  # already kicked off
    conn.execute(
        "INSERT INTO odds(match_id,bookmaker,price_home,price_draw,price_away,fetched_at)"
        " VALUES (1,'a',2.0,3.5,4.0,0)"
    )
    conn.commit()
    assert valuebet.value_bets(conn, _model(), min_edge=0.05) == []  # stale odds -> excluded


def test_best_prices_ignores_corrupt_outlier(tmp_path):
    conn = _conn(tmp_path)
    # Four sane books cluster ~1.5 on the home favourite; one corrupt book offers an
    # impossible 4.8. best_prices must ignore the outlier, not flag it as the "best" price.
    conn.execute(
        "INSERT INTO odds(match_id,bookmaker,price_home,price_draw,price_away,fetched_at)"
        " VALUES (1,'a',1.50,4.40,6.5,0),(1,'b',1.52,4.30,6.7,0),(1,'c',1.49,4.50,7.0,0),"
        "(1,'d',1.54,4.50,7.6,0),(1,'bad',4.80,1.25,9.7,0)"
    )
    conn.commit()
    best = valuebet.best_prices(conn, 1)
    assert best[0] == (1.54, "d")  # outlier 4.80 ignored; sane best is 1.54


def test_best_total_prices_ignores_corrupt_outlier(tmp_path):
    conn = _conn(tmp_path)
    # Four sane books cluster ~1.9 on over 2.5; one corrupt book offers an impossible 5.00.
    # _best_total_prices must ignore the outlier, mirroring the 1x2/spreads guard.
    conn.execute(
        "INSERT INTO odds_totals(match_id,bookmaker,line,price_over,price_under,fetched_at)"
        " VALUES (1,'a',2.5,1.90,1.95,0),(1,'b',2.5,1.88,1.98,0),(1,'c',2.5,1.92,1.90,0),"
        "(1,'d',2.5,1.95,1.88,0),(1,'bad',2.5,5.00,1.10,0)"
    )
    conn.commit()
    bo, _bu = valuebet._best_total_prices(conn, 1, 2.5)
    assert bo == (1.95, "d")  # corrupt 5.00 over ignored; sane best is 1.95


def test_value_bets_totals_applies_calibrator(tmp_path):
    from worldcup_predictor import calibrate_totals as ct

    conn = _conn(tmp_path)
    model = _model()  # Strong vs Weak -> high raw P(over 2.5)
    conn.execute(
        "INSERT INTO odds_totals(match_id,bookmaker,line,price_over,price_under,fetched_at)"
        " VALUES (1,'bookA',2.5,1.90,1.90,1.0),(1,'bookB',2.5,1.92,1.88,1.0)"
    )
    conn.commit()

    raw = valuebet.value_bets_totals(conn, model, min_edge=0.05)
    raw_over = [b for b in raw if b["outcome"] == "over"]
    assert raw_over, "expected a raw over value bet before calibration"

    # A strong flatten + lower-over calibrator must shrink our over prob toward the market,
    # removing the over edge.
    ct.store(conn, {"temperature": 2.5, "over_mult": 0.70})
    cal = valuebet.value_bets_totals(conn, model, min_edge=0.05)
    cal_over = [b for b in cal if b["outcome"] == "over"]
    assert not cal_over, "calibration should remove the fake over edge"


def test_value_bets_dc_flags_safe_double_chance(tmp_path):
    conn = _conn(tmp_path)
    m = _model()
    conn.execute(
        "INSERT INTO odds(match_id,bookmaker,price_home,price_draw,price_away,fetched_at)"
        " VALUES (1,'b',1.30,6.0,9.0,1.0),(1,'c',1.32,6.2,9.2,1.0)"
    )
    conn.commit()
    bets = valuebet.value_bets_dc(conn, m, min_edge=0.01)
    assert any(b["market"] == "double_chance" and b["outcome"] == "1x" for b in bets)


def test_value_bets_dnb_normalises(tmp_path):
    conn = _conn(tmp_path)
    m = _model()
    conn.execute(
        "INSERT INTO odds(match_id,bookmaker,price_home,price_draw,price_away,fetched_at)"
        " VALUES (1,'b',1.30,6.0,9.0,1.0),(1,'c',1.32,6.2,9.2,1.0)"
    )
    conn.commit()
    bets = valuebet.value_bets_dnb(conn, m, min_edge=0.0)
    assert all(b["market"] == "dnb" for b in bets) and all(b["best_price"] > 1 for b in bets)
