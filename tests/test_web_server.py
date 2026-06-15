from fastapi.testclient import TestClient


def test_api_endpoints(tmp_path, monkeypatch):
    monkeypatch.setenv("WC_DB_PATH", str(tmp_path / "web.db"))
    from worldcup_predictor import db, ingest

    conn = db.connect()
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
