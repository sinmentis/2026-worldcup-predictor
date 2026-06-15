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
