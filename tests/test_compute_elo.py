from worldcup_predictor import db, ingest, ratings

CSV = """date,home_team,away_team,home_score,away_score,tournament,city,country,neutral
2024-01-01,A,B,3,0,FIFA World Cup,X,Y,True
2024-02-01,A,B,2,0,FIFA World Cup,X,Y,True
2024-03-01,A,B,1,0,FIFA World Cup,X,Y,True
"""


def test_compute_elo_winner_above_loser(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.init_schema(conn)
    ingest.load_history_from_text(conn, CSV)
    table = ratings.compute_elo_ratings(conn)
    assert table["A"] > table["B"]
    # persisted to teams
    elo_a = conn.execute("SELECT elo FROM teams WHERE name='A'").fetchone()
    # team rows only exist after seeding; compute should upsert ratings into teams too
    assert elo_a is not None


def test_k_factor_lookup():
    assert ratings.k_for_tournament("FIFA World Cup") == 60
    assert ratings.k_for_tournament("FIFA World Cup qualification") == 40
    assert ratings.k_for_tournament("Friendly") == 20
    assert ratings.k_for_tournament("UEFA Euro") == 50
