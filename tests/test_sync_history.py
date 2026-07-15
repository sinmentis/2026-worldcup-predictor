from worldcup_predictor import db, ingest


def _conn(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    db.init_schema(conn)
    ingest.seed_teams_and_fixtures(conn)
    return conn


def _finish(conn, home, away, hs, as_, stage="group", winner=None, ext_id=5000, date="2026-06-20"):
    conn.execute(
        "INSERT INTO matches(id, stage, home_team, away_team, kickoff, neutral, "
        "home_score, away_score, status, ext_id, winner_team) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (ext_id, stage, home, away, f"{date}T19:00:00Z", 1, hs, as_, "FINISHED", ext_id, winner),
    )
    conn.commit()


def _hist(conn, date, home, away, hs, as_, neutral=1):
    conn.execute(
        "INSERT OR IGNORE INTO historical_matches"
        "(date, home_team, away_team, home_score, away_score, tournament, neutral)"
        " VALUES(?,?,?,?,?,?,?)",
        (date, home, away, hs, as_, "FIFA World Cup", neutral),
    )
    conn.commit()


def test_sync_appends_finished_match_to_history(tmp_path):
    conn = _conn(tmp_path)
    _finish(conn, "England", "Croatia", 3, 2)
    n = ingest.sync_finished_to_history(conn)
    assert n == 1
    row = conn.execute(
        "SELECT date, home_score, away_score, tournament, neutral "
        "FROM historical_matches WHERE home_team='England' AND away_team='Croatia'"
    ).fetchone()
    assert row["date"] == "2026-06-20"
    assert (row["home_score"], row["away_score"]) == (3, 2)
    assert row["tournament"] == "FIFA World Cup"
    assert row["neutral"] == 1


def test_sync_is_idempotent(tmp_path):
    conn = _conn(tmp_path)
    _finish(conn, "England", "Croatia", 3, 2)
    assert ingest.sync_finished_to_history(conn) == 1
    assert ingest.sync_finished_to_history(conn) == 0
    cnt = conn.execute(
        "SELECT COUNT(*) FROM historical_matches WHERE home_team='England' AND away_team='Croatia'"
    ).fetchone()[0]
    assert cnt == 1


def test_sync_marks_host_home_game_non_neutral(tmp_path):
    conn = _conn(tmp_path)
    _finish(conn, "Mexico", "South Africa", 2, 0)
    ingest.sync_finished_to_history(conn)
    row = conn.execute(
        "SELECT neutral FROM historical_matches "
        "WHERE home_team='Mexico' AND away_team='South Africa'"
    ).fetchone()
    assert row["neutral"] == 0


def test_sync_skips_unfinished_matches(tmp_path):
    conn = _conn(tmp_path)
    n = ingest.sync_finished_to_history(conn)
    assert n == 0
    assert conn.execute("SELECT COUNT(*) FROM historical_matches").fetchone()[0] == 0


def test_sync_records_shootout_as_regulation_draw(tmp_path):
    conn = _conn(tmp_path)
    _finish(conn, "England", "Argentina", 1, 1, stage="SF", winner="England")
    ingest.sync_finished_to_history(conn)
    row = conn.execute(
        "SELECT home_score, away_score FROM historical_matches "
        "WHERE home_team='England' AND away_team='Argentina'"
    ).fetchone()
    assert row["home_score"] == row["away_score"]


def test_sync_dedups_flipped_orientation(tmp_path):
    conn = _conn(tmp_path)
    # Seed already has the game with one orientation; the live feed recorded it flipped.
    _hist(conn, "2026-06-13", "Qatar", "Switzerland", 1, 1)
    _finish(conn, "Switzerland", "Qatar", 1, 1, ext_id=6001, date="2026-06-13")
    assert ingest.sync_finished_to_history(conn) == 0
    cnt = conn.execute(
        "SELECT COUNT(*) FROM historical_matches "
        "WHERE home_team IN ('Qatar', 'Switzerland') AND away_team IN ('Qatar', 'Switzerland')"
    ).fetchone()[0]
    assert cnt == 1


def test_sync_dedups_one_day_date_shift(tmp_path):
    conn = _conn(tmp_path)
    # Seed date and live UTC kickoff date differ by one day (timezone rollover).
    _hist(conn, "2026-06-12", "United States", "Paraguay", 4, 1, neutral=0)
    _finish(conn, "United States", "Paraguay", 4, 1, ext_id=6002, date="2026-06-13")
    assert ingest.sync_finished_to_history(conn) == 0
    cnt = conn.execute(
        "SELECT COUNT(*) FROM historical_matches WHERE home_team='United States'"
    ).fetchone()[0]
    assert cnt == 1
