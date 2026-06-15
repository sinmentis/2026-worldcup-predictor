from worldcup_predictor import db, engine, ingest

HISTORY = """date,home_team,away_team,home_score,away_score,tournament,city,country,neutral
2024-01-02,France,Senegal,3,1,Friendly,X,Y,True
2024-01-03,Senegal,France,2,3,Friendly,X,Y,True
2024-01-04,France,Norway,1,0,Friendly,X,Y,True
2024-01-05,Senegal,Norway,3,2,Friendly,X,Y,True
2024-01-06,France,Iraq,2,0,Friendly,X,Y,True
2024-01-07,Senegal,Iraq,3,0,Friendly,X,Y,True
2024-01-08,Norway,Iraq,1,1,Friendly,X,Y,True
2024-01-09,Norway,France,0,2,Friendly,X,Y,True
2024-01-10,France,Senegal,3,1,Friendly,X,Y,True
2024-01-11,Senegal,France,0,1,Friendly,X,Y,True
2024-01-12,France,Norway,4,2,Friendly,X,Y,True
2024-01-13,Senegal,Norway,3,1,Friendly,X,Y,True
2024-01-14,France,Iraq,4,0,Friendly,X,Y,True
2024-01-15,Senegal,Iraq,2,1,Friendly,X,Y,True
2024-01-16,Norway,Iraq,1,0,Friendly,X,Y,True
2024-01-17,Norway,France,0,2,Friendly,X,Y,True
"""


def test_active_status_shifts_prediction_and_pending_does_not(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.init_schema(conn)
    ingest.seed_teams_and_fixtures(conn)
    ingest.load_history_from_text(conn, HISTORY)

    mid = conn.execute(
        "SELECT id FROM matches WHERE home_team='France' AND away_team='Senegal'"
    ).fetchone()[0]

    base = engine.predict_fixture(conn, mid)["p_home"]

    pending = engine.upsert_player_status(
        conn,
        team="France",
        player="Star",
        tier="key",
        status="out",
        confidence=0.9,
        source_url="https://rumour",
    )
    assert pending["status"] == "pending"
    assert engine.predict_fixture(conn, mid)["p_home"] == base

    active = engine.upsert_player_status(
        conn,
        team="France",
        player="Star",
        tier="key",
        status="out",
        confidence=0.9,
        source_url="https://federation",
        official=True,
    )
    assert active["status"] == "active"
    after = engine.predict_fixture(conn, mid)["p_home"]
    assert after < base
