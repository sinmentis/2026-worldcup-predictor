from worldcup_predictor import db, ingest

CSV = """date,home_team,away_team,home_score,away_score,tournament,city,country,neutral
2024-06-01,Brazil,Germany,2,1,Friendly,Rio,Brazil,False
2024-09-01,Argentina,Brazil,1,1,FIFA World Cup qualification,Buenos Aires,Argentina,False
"""


def test_load_history_from_csv_text(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.init_schema(conn)
    n = ingest.load_history_from_text(conn, CSV)
    assert n == 2
    rows = conn.execute(
        "SELECT home_team, away_team, tournament, neutral FROM historical_matches ORDER BY date"
    ).fetchall()
    assert rows[0]["home_team"] == "Brazil"
    assert rows[0]["neutral"] == 0


def test_load_history_canonicalizes_team_names(tmp_path):
    # martj42 uses "Curaçao" (cedilla); GROUPS uses canonical "Curacao".
    csv_alias = (
        "date,home_team,away_team,home_score,away_score,tournament,city,country,neutral\n"
        "2025-06-01,Curaçao,Jamaica,2,1,Friendly,Willemstad,Curacao,False\n"
    )
    conn = db.connect(tmp_path / "t.db")
    db.init_schema(conn)
    ingest.load_history_from_text(conn, csv_alias)
    home = conn.execute("SELECT home_team FROM historical_matches").fetchone()[0]
    assert home == "Curacao"


def test_load_history_skips_unplayed_and_na_rows(tmp_path):
    csv_with_gaps = (
        "date,home_team,away_team,home_score,away_score,tournament,city,country,neutral\n"
        "2026-06-11,Mexico,South Africa,2,0,FIFA World Cup,Mexico City,Mexico,False\n"
        "2026-06-26,Spain,Uruguay,NA,NA,FIFA World Cup,Dallas,USA,True\n"
        "2026-06-27,France,Norway,,,FIFA World Cup,Boston,USA,True\n"
    )
    conn = db.connect(tmp_path / "t.db")
    db.init_schema(conn)
    n = ingest.load_history_from_text(conn, csv_with_gaps)
    assert n == 1  # only the played match is stored; NA / empty rows are skipped
    assert conn.execute("SELECT COUNT(*) FROM historical_matches").fetchone()[0] == 1


def test_load_history_is_idempotent(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.init_schema(conn)
    first = ingest.load_history_from_text(conn, CSV)
    second = ingest.load_history_from_text(conn, CSV)
    assert first == 2
    assert second == 0  # re-loading the same rows inserts nothing
    assert conn.execute("SELECT COUNT(*) FROM historical_matches").fetchone()[0] == 2
