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
    assert "World Cup Predictor" in root.text

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
