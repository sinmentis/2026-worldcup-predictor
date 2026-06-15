import sqlite3
import time

from worldcup_predictor import db
from worldcup_predictor.evaluate import score_finished_predictions


def _setup(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.init_schema(conn)
    conn.execute(
        "INSERT INTO matches(id,stage,home_team,away_team,home_score,away_score,status)"
        " VALUES (1,'group','A','B',2,0,'FINISHED')"
    )
    conn.execute(
        "INSERT INTO predictions(match_id,created_at,p_home,p_draw,p_away,"
        "exp_home_goals,exp_away_goals,ml_home,ml_away,model_version,reasoning)"
        " VALUES (1,?,0.7,0.2,0.1,1.8,0.6,2,0,'v','')",
        (time.time(),),
    )
    conn.commit()
    return conn


def test_score_finished_predictions(tmp_path):
    conn = _setup(tmp_path)
    summary = score_finished_predictions(conn)
    assert summary["n"] == 1
    assert 0.0 <= summary["model_rps"] <= 1.0
    assert "baseline_rps" in summary
    # model predicted home strongly and home won -> model beats base rate
    assert summary["model_rps"] < summary["baseline_rps"]
    assert conn.execute("SELECT COUNT(*) FROM metrics").fetchone()[0] >= 1


def test_score_finished_predictions_accepts_plain_sqlite_connection(tmp_path):
    conn = sqlite3.connect(tmp_path / "plain.db")
    db.init_schema(conn)
    conn.execute(
        "INSERT INTO matches(id,stage,home_team,away_team,home_score,away_score,status)"
        " VALUES (1,'group','A','B',0,1,'FINISHED')"
    )
    conn.execute(
        "INSERT INTO predictions(match_id,created_at,p_home,p_draw,p_away,"
        "exp_home_goals,exp_away_goals,ml_home,ml_away,model_version,reasoning)"
        " VALUES (1,?,0.2,0.3,0.5,0.6,1.4,0,1,'v','')",
        (time.time(),),
    )
    conn.commit()

    summary = score_finished_predictions(conn)

    assert summary["n"] == 1


def test_scores_only_latest_prediction_per_match(tmp_path):
    conn = _setup(tmp_path)
    # A second (stale) prediction for the same finished match must NOT be double-counted.
    conn.execute(
        "INSERT INTO predictions(match_id,created_at,p_home,p_draw,p_away,"
        "exp_home_goals,exp_away_goals,ml_home,ml_away,model_version,reasoning)"
        " VALUES (1,?,0.33,0.34,0.33,1.0,1.0,1,1,'v','')",
        (time.time() + 1,),
    )
    conn.commit()
    summary = score_finished_predictions(conn)
    assert summary["n"] == 1


def test_per_match_breakdown_uses_earliest_prediction(tmp_path):
    from worldcup_predictor.evaluate import per_match_breakdown

    conn = db.connect(tmp_path / "t.db")
    db.init_schema(conn)
    conn.execute(
        "INSERT INTO matches(id,stage,group_id,home_team,away_team,home_score,away_score,status)"
        " VALUES (1,'group','A','Spain','Brazil',1,0,'FINISHED')"
    )
    # original (earliest) prediction: strong home, scoreline 1-0 (exact + correct pick)
    conn.execute(
        "INSERT INTO predictions(match_id,created_at,p_home,p_draw,p_away,"
        "exp_home_goals,exp_away_goals,ml_home,ml_away,model_version,reasoning)"
        " VALUES (1,100,0.7,0.2,0.1,1.8,0.6,1,0,'v','')"
    )
    # later (stale) prediction must be ignored by the breakdown
    conn.execute(
        "INSERT INTO predictions(match_id,created_at,p_home,p_draw,p_away,"
        "exp_home_goals,exp_away_goals,ml_home,ml_away,model_version,reasoning)"
        " VALUES (1,200,0.1,0.2,0.7,0.6,1.8,0,1,'v','')"
    )
    conn.commit()
    rows = per_match_breakdown(conn)
    assert len(rows) == 1
    r = rows[0]
    assert r["p_home"] == 0.7  # earliest, not the stale 0.1
    assert r["outcome"] == 0 and r["pred_pick"] == 0
    assert r["pick_correct"] is True
    assert r["exact_scoreline"] is True
    assert r["model_rps"] < r["baseline_rps"]
