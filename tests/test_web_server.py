from fastapi.testclient import TestClient


def test_api_endpoints(tmp_path, monkeypatch):
    db_path = tmp_path / "web.db"
    monkeypatch.setenv("WC_DB_PATH", str(db_path))
    from worldcup_predictor import db, ingest

    conn = db.connect(db_path)
    db.init_schema(conn)
    ingest.seed_teams_and_fixtures(conn)

    from worldcup_predictor.web_server import app

    client = TestClient(app)

    root = client.get("/")
    assert root.status_code == 200
    assert "世界杯预测" in root.text
    # static assets are cache-busted so browsers pick up new builds
    assert "/static/app.js?v=" in root.text
    assert root.headers.get("cache-control") == "no-cache"

    r = client.get("/api/groups/A/standings")
    assert r.status_code == 200
    assert len(r.json()) == 4

    r2 = client.get("/api/matches/upcoming?limit=3")
    assert r2.status_code == 200
    assert len(r2.json()) == 3

    r3 = client.get("/api/knockout/bracket")
    assert r3.status_code == 200
    assert "R32" in r3.json()


def test_web_validation(tmp_path, monkeypatch):
    db_path = tmp_path / "web2.db"
    monkeypatch.setenv("WC_DB_PATH", str(db_path))
    from worldcup_predictor import db, ingest

    conn = db.connect(db_path)
    db.init_schema(conn)
    ingest.seed_teams_and_fixtures(conn)

    from worldcup_predictor.web_server import app

    client = TestClient(app)

    # invalid group -> 404 (was a 500 KeyError before)
    assert client.get("/api/groups/Z/standings").status_code == 404
    # unknown match id -> 404
    assert client.get("/api/matches/999999").status_code == 404
    # negative limit is clamped (was: SQLite LIMIT -1 returned all 72 rows)
    clamped = client.get("/api/matches/upcoming?limit=-5")
    assert clamped.status_code == 200
    assert 1 <= len(clamped.json()) <= 100


def test_forecast_endpoint(tmp_path, monkeypatch):
    db_path = tmp_path / "web3.db"
    monkeypatch.setenv("WC_DB_PATH", str(db_path))
    from worldcup_predictor import db

    conn = db.connect(db_path)
    db.init_schema(conn)
    conn.execute(
        "INSERT INTO sim_results(created_at,team,advance_prob,r16_prob,qf_prob,sf_prob,"
        "final_prob,title_prob,n_iter) VALUES (0,'Argentina',0.97,0.78,0.55,0.29,0.18,0.11,1000)"
    )
    conn.commit()

    from worldcup_predictor.web_server import app

    client = TestClient(app)
    r = client.get("/api/forecast")
    assert r.status_code == 200
    body = r.json()
    assert body[0]["team"] == "Argentina"
    assert body[0]["title_prob"] == 0.11


def test_accuracy_endpoint(tmp_path, monkeypatch):
    db_path = tmp_path / "web_acc.db"
    monkeypatch.setenv("WC_DB_PATH", str(db_path))
    from worldcup_predictor import db

    conn = db.connect(db_path)
    db.init_schema(conn)
    conn.execute(
        "INSERT INTO matches(id,stage,group_id,home_team,away_team,home_score,away_score,status)"
        " VALUES (1,'group','A','Spain','Brazil',1,0,'FINISHED')"
    )
    conn.execute(
        "INSERT INTO predictions(match_id,created_at,p_home,p_draw,p_away,"
        "exp_home_goals,exp_away_goals,ml_home,ml_away,model_version,reasoning)"
        " VALUES (1,100,0.7,0.2,0.1,1.8,0.6,1,0,'v','')"
    )
    conn.commit()

    from worldcup_predictor.web_server import app

    client = TestClient(app)
    r = client.get("/api/accuracy")
    assert r.status_code == 200
    body = r.json()
    assert body["aggregate"]["n"] == 1
    assert body["matches"][0]["pick_correct"] is True


def test_upcoming_predictions_endpoint(tmp_path, monkeypatch):
    db_path = tmp_path / "web_up.db"
    monkeypatch.setenv("WC_DB_PATH", str(db_path))
    from worldcup_predictor import db, engine

    conn = db.connect(db_path)
    db.init_schema(conn)

    canned = {
        "remaining": 3,
        "matches": [
            {
                "match_id": 1,
                "group": "A",
                "home_team": "Spain",
                "away_team": "Brazil",
                "kickoff": "2026-06-20T19:00:00Z",
                "p_home": 0.5,
                "p_draw": 0.3,
                "p_away": 0.2,
                "ml_home": 1,
                "ml_away": 0,
                "exp_home_goals": 1.4,
                "exp_away_goals": 0.8,
                "factors": [],
            }
        ],
    }
    monkeypatch.setattr(engine, "get_upcoming_predictions", lambda _c, limit=12: canned)

    from worldcup_predictor.web_server import app

    client = TestClient(app)
    r = client.get("/api/upcoming-predictions?limit=5")
    assert r.status_code == 200
    body = r.json()
    assert body["remaining"] == 3
    assert body["matches"][0]["home_team"] == "Spain"


def test_value_bets_endpoint(tmp_path, monkeypatch):
    db_path = tmp_path / "web_vb.db"
    monkeypatch.setenv("WC_DB_PATH", str(db_path))
    from worldcup_predictor import db, engine

    conn = db.connect(db_path)
    db.init_schema(conn)
    canned = [
        {
            "match_id": 1,
            "home_team": "Strong",
            "away_team": "Weak",
            "group": "A",
            "kickoff": None,
            "outcome": "home",
            "our_prob": 0.7,
            "market_prob": 0.55,
            "best_price": 1.9,
            "bookmaker": "soft",
            "edge": 0.15,
            "ev": 0.33,
            "kelly": 0.1,
        }
    ]
    monkeypatch.setattr(engine, "get_value_bets", lambda _c, min_edge=0.05: canned)

    from worldcup_predictor.web_server import app

    client = TestClient(app)
    r = client.get("/api/value-bets")
    assert r.status_code == 200
    assert r.json()["bets"][0]["outcome"] == "home"


def test_bracket_projection_endpoint(tmp_path, monkeypatch):
    db_path = tmp_path / "web_bk.db"
    monkeypatch.setenv("WC_DB_PATH", str(db_path))
    from worldcup_predictor import db

    conn = db.connect(db_path)
    db.init_schema(conn)
    conn.execute(
        "INSERT INTO sim_results(created_at,team,advance_prob,r16_prob,qf_prob,sf_prob,"
        "final_prob,title_prob,n_iter) VALUES (0,'Argentina',0.97,0.8,0.55,0.3,0.18,0.11,1000)"
    )
    conn.commit()
    from worldcup_predictor.web_server import app

    client = TestClient(app)
    r = client.get("/api/bracket-projection")
    assert r.status_code == 200
    body = r.json()
    assert body["n_iter"] == 1000
    assert body["teams"][0]["team"] == "Argentina"
