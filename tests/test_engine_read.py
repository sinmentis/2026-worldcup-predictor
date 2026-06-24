from worldcup_predictor import db, engine, ingest


def _conn(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.init_schema(conn)
    ingest.seed_teams_and_fixtures(conn)
    return conn


def test_group_standings_shape(tmp_path):
    conn = _conn(tmp_path)
    conn.execute(
        "UPDATE matches SET home_score=1, away_score=0, status='FINISHED' "
        "WHERE group_id='A' AND home_team=? AND away_team=?",
        (
            conn.execute("SELECT home_team FROM matches WHERE group_id='A' LIMIT 1").fetchone()[0],
            conn.execute("SELECT away_team FROM matches WHERE group_id='A' LIMIT 1").fetchone()[0],
        ),
    )
    conn.commit()
    rows = engine.get_group_standings(conn, "A")
    assert len(rows) == 4
    assert {"team", "played", "won", "drawn", "lost", "gf", "ga", "gd", "pts"} <= set(rows[0])


def test_upcoming_matches(tmp_path):
    conn = _conn(tmp_path)
    ups = engine.get_upcoming_matches(conn, limit=5)
    assert len(ups) == 5
    assert ups[0]["status"] == "SCHEDULED"


def test_get_forecast_orders_by_title(tmp_path):
    conn = _conn(tmp_path)
    conn.execute(
        "INSERT INTO sim_results(created_at,team,advance_prob,r16_prob,qf_prob,sf_prob,"
        "final_prob,title_prob,n_iter) VALUES (0,'Spain',0.99,0.8,0.6,0.3,0.18,0.11,1000),"
        "(0,'Brazil',0.98,0.7,0.5,0.25,0.14,0.07,1000)"
    )
    conn.commit()
    fc = engine.get_forecast(conn)
    assert [r["team"] for r in fc] == ["Spain", "Brazil"]
    assert {"team", "title_prob", "advance_prob"} <= set(fc[0])


def test_get_accuracy_aggregates(tmp_path):
    conn = _conn(tmp_path)
    conn.execute("UPDATE matches SET home_score=2, away_score=0, status='FINISHED' WHERE id=1")
    m = conn.execute("SELECT home_team, away_team FROM matches WHERE id=1").fetchone()
    conn.execute(
        "INSERT INTO predictions(match_id,created_at,p_home,p_draw,p_away,"
        "exp_home_goals,exp_away_goals,ml_home,ml_away,model_version,reasoning)"
        " VALUES (1,100,0.7,0.2,0.1,1.9,0.5,2,0,'v','')"
    )
    conn.commit()
    acc = engine.get_accuracy(conn)
    assert acc["aggregate"]["n"] == 1
    assert acc["aggregate"]["beats_baseline"] is True
    assert acc["aggregate"]["pick_hit_rate"] == 1.0
    assert acc["matches"][0]["home_team"] == m["home_team"]


def test_get_upcoming_predictions_shape(tmp_path, monkeypatch):
    import numpy as np
    import pandas as pd

    from worldcup_predictor import engine as eng
    from worldcup_predictor.goal_model import GoalModel

    conn = db.connect(tmp_path / "u.db")
    db.init_schema(conn)
    # one scheduled fixture between two teams the model knows
    conn.execute(
        "INSERT INTO matches(id,stage,group_id,home_team,away_team,neutral,status)"
        " VALUES (1,'group','A','Strong','Weak',1,'SCHEDULED')"
    )
    conn.commit()
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
    history = pd.DataFrame(
        rows, columns=["date", "home_team", "away_team", "home_goals", "away_goals", "neutral"]
    )
    model = GoalModel().fit(history)
    monkeypatch.setattr(eng, "get_model", lambda _conn, refit=False: model)

    out = eng.get_upcoming_predictions(conn, limit=10)
    assert out["remaining"] == 1
    assert len(out["matches"]) == 1
    mm = out["matches"][0]
    assert mm["home_team"] == "Strong"
    assert abs(mm["p_home"] + mm["p_draw"] + mm["p_away"] - 1.0) < 1e-6
    assert "factors" in mm


def test_get_upcoming_predictions_persists_one_snapshot_per_match(tmp_path, monkeypatch):
    # The public read path must persist at most ONE prediction per match (the original
    # snapshot the accuracy page reads via MIN(id)) and must NOT amplify writes on repeat
    # hits — otherwise anonymous traffic bloats the predictions table without bound.
    import numpy as np
    import pandas as pd

    from worldcup_predictor import engine as eng
    from worldcup_predictor.goal_model import GoalModel

    conn = db.connect(tmp_path / "u.db")
    db.init_schema(conn)
    conn.execute(
        "INSERT INTO matches(id,stage,group_id,home_team,away_team,neutral,status)"
        " VALUES (1,'group','A','Strong','Weak',1,'SCHEDULED'),"
        "(2,'group','A','Strong','Weak',1,'SCHEDULED')"
    )
    conn.commit()
    rng = np.random.default_rng(1)
    rows = []
    for _ in range(60):
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
    history = pd.DataFrame(
        rows, columns=["date", "home_team", "away_team", "home_goals", "away_goals", "neutral"]
    )
    model = GoalModel().fit(history)
    monkeypatch.setattr(eng, "get_model", lambda _conn, refit=False: model)

    eng.get_upcoming_predictions(conn, limit=10)
    n1 = conn.execute("SELECT COUNT(*) FROM predictions").fetchone()[0]
    eng.get_upcoming_predictions(conn, limit=10)  # repeat hit
    eng.get_upcoming_predictions(conn, limit=10)  # and again
    n2 = conn.execute("SELECT COUNT(*) FROM predictions").fetchone()[0]

    assert n1 == 2  # one original snapshot per scheduled match
    assert n2 == n1  # repeat reads add no rows (no write amplification)
    dupes = conn.execute(
        "SELECT match_id, COUNT(*) c FROM predictions GROUP BY match_id HAVING c > 1"
    ).fetchall()
    assert dupes == []  # never more than one stored prediction per match


def test_get_bracket_projection(tmp_path):
    conn = _conn(tmp_path)
    conn.execute(
        "INSERT INTO sim_results(created_at,team,advance_prob,r16_prob,qf_prob,sf_prob,"
        "final_prob,title_prob,n_iter) VALUES "
        "(0,'Mexico',0.80,0.55,0.30,0.15,0.08,0.04,1000),"
        "(0,'South Korea',0.60,0.35,0.18,0.08,0.03,0.01,1000),"
        "(0,'Argentina',0.97,0.80,0.55,0.30,0.18,0.11,1000)"
    )
    conn.commit()
    proj = engine.get_bracket_projection(conn)
    assert proj["n_iter"] == 1000
    # group A teams ranked by advance prob (Mexico ahead of South Korea)
    a = proj["groups"]["A"]
    names = [t["team"] for t in a]
    assert names.index("Mexico") < names.index("South Korea")
    assert all("group" in t for t in proj["teams"])
    # heatmap ordering: Argentina (deepest run) first
    assert proj["teams"][0]["team"] == "Argentina"


def test_match_detail_h2h_and_odds(tmp_path):
    conn = _conn(tmp_path)
    conn.execute(
        "UPDATE matches SET home_team='Brazil', away_team='Morocco', status='SCHEDULED' WHERE id=1"
    )
    conn.execute(
        "INSERT INTO historical_matches(date,home_team,away_team,home_score,away_score,tournament)"
        " VALUES ('2022-01-01','Brazil','Morocco',2,0,'f'),"
        "('2021-01-01','Morocco','Brazil',1,1,'f')"
    )
    conn.execute(
        "INSERT INTO odds(match_id,bookmaker,price_home,price_draw,price_away,fetched_at)"
        " VALUES (1,'a',1.8,3.5,4.5,0)"
    )
    conn.commit()
    d = engine.get_match_detail(conn, 1)
    assert d["h2h"]["home_wins"] == 1 and d["h2h"]["draws"] == 1  # Brazil: 1 win, 1 draw
    assert len(d["h2h"]["meetings"]) == 2
    assert d["odds"]["n_books"] == 1 and "consensus" in d["odds"]
    assert "scorelines" not in d  # no history loaded -> grid section skipped


def test_top_scorelines_orders_by_prob():
    import numpy as np

    from worldcup_predictor.goal_model import ScoreGrid

    mtx = np.zeros((3, 3))
    mtx[1, 0] = 0.4
    mtx[1, 1] = 0.3
    mtx[0, 0] = 0.3
    top = engine._top_scorelines(ScoreGrid(matrix=mtx), n=2)
    assert top[0] == {"home": 1, "away": 0, "prob": 0.4}
    assert len(top) == 2
