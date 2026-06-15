import pytest

from worldcup_predictor import db, odds

PAYLOAD = [
    {
        "id": "1",
        "commence_time": "2026-06-20T19:00:00Z",
        "home_team": "Brazil",
        "away_team": "Morocco",
        "bookmakers": [
            {
                "key": "pinnacle",
                "markets": [
                    {
                        "key": "h2h",
                        "outcomes": [
                            {"name": "Brazil", "price": 1.80},
                            {"name": "Morocco", "price": 4.50},
                            {"name": "Draw", "price": 3.60},
                        ],
                    }
                ],
            },
            {
                "key": "bet365",
                "markets": [
                    {
                        "key": "h2h",
                        "outcomes": [
                            {"name": "Brazil", "price": 1.85},
                            {"name": "Morocco", "price": 4.20},
                            {"name": "Draw", "price": 3.70},
                        ],
                    }
                ],
            },
        ],
    }
]


def test_parse_odds_payload():
    parsed = odds.parse_odds_payload(PAYLOAD)
    assert len(parsed) == 1
    m = parsed[0]
    assert m["home"] == "Brazil" and m["away"] == "Morocco"
    assert len(m["books"]) == 2
    pin = next(b for b in m["books"] if b["bookmaker"] == "pinnacle")
    assert pin["price_home"] == 1.80 and pin["price_away"] == 4.50 and pin["price_draw"] == 3.60


def test_store_odds_orients_to_our_fixture(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.init_schema(conn)
    # our fixture is seeded with Morocco at home (reversed vs the odds' Brazil-home orientation)
    conn.execute(
        "INSERT INTO matches(id,stage,group_id,home_team,away_team,neutral,status)"
        " VALUES (1,'group','A','Morocco','Brazil',1,'SCHEDULED')"
    )
    conn.commit()
    n = odds.store_odds(conn, odds.parse_odds_payload(PAYLOAD))
    assert n == 2
    row = conn.execute(
        "SELECT match_id, price_home, price_away FROM odds WHERE bookmaker='pinnacle'"
    ).fetchone()
    assert row["match_id"] == 1
    # our home is Morocco -> price_home must be Morocco's price, price_away Brazil's
    assert row["price_home"] == 4.50 and row["price_away"] == 1.80


def test_store_odds_skips_unmapped(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.init_schema(conn)  # no matches seeded
    assert odds.store_odds(conn, odds.parse_odds_payload(PAYLOAD)) == 0


def test_implied_probs_demargins():
    p = odds.implied_probs(1.80, 3.60, 4.50)
    assert abs(sum(p) - 1.0) < 1e-9
    assert p[0] > p[1] > p[2]  # home favourite


def test_fetch_odds_requires_key(tmp_path, monkeypatch):
    monkeypatch.delenv("ODDS_API_KEY", raising=False)  # .env may have set it
    conn = db.connect(tmp_path / "t.db")
    db.init_schema(conn)
    with pytest.raises(ValueError):
        odds.fetch_odds(conn, key="")


def test_fetch_odds_with_monkeypatched_http(tmp_path, monkeypatch):
    conn = db.connect(tmp_path / "t.db")
    db.init_schema(conn)
    conn.execute(
        "INSERT INTO matches(id,stage,group_id,home_team,away_team,neutral,status)"
        " VALUES (1,'group','A','Brazil','Morocco',1,'SCHEDULED')"
    )
    conn.commit()

    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return PAYLOAD

    monkeypatch.setattr(odds.httpx, "get", lambda *a, **k: _Resp())
    assert odds.fetch_odds(conn, key="dummy") == 2
