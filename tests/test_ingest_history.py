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
