from worldcup_predictor import db, engine, ingest


def test_record_result_updates_and_touches(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.init_schema(conn)
    ingest.seed_teams_and_fixtures(conn)
    mid = conn.execute("SELECT id FROM matches LIMIT 1").fetchone()[0]
    engine.record_result(conn, mid, 3, 1)
    row = conn.execute(
        "SELECT home_score, away_score, status FROM matches WHERE id=?", (mid,)
    ).fetchone()
    assert (row["home_score"], row["away_score"], row["status"]) == (3, 1, "FINISHED")
    assert engine.get_last_update_ts(conn) is not None


def test_record_intel_event(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.init_schema(conn)
    engine.record_intel_event(
        conn,
        team="France",
        event_type="injury",
        direction="weaken",
        magnitude=-0.25,
        source_url="https://x",
        credibility=0.9,
        player="Star",
    )
    n = conn.execute("SELECT COUNT(*) FROM intel_events WHERE team='France'").fetchone()[0]
    assert n == 1
