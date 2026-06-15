from worldcup_predictor import db


def test_init_schema_creates_tables(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.init_schema(conn)
    names = {
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    expected = {
        "teams",
        "matches",
        "historical_matches",
        "predictions",
        "intel_events",
        "ratings_history",
        "sim_results",
        "metrics",
        "tuning_params",
        "meta",
    }
    assert expected <= names


def test_set_and_get_last_update(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.init_schema(conn)
    db.touch_update(conn)
    ts1 = db.get_last_update_ts(conn)
    assert ts1 is not None
