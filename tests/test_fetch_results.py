from worldcup_predictor import db, ingest

PAYLOAD = {
    "matches": [
        {
            "homeTeam": {"name": "Brazil"},
            "awayTeam": {"name": "Morocco"},
            "score": {"fullTime": {"home": 2, "away": 0}},
            "status": "FINISHED",
        }
    ]
}


def test_apply_results_payload_updates_match(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.init_schema(conn)
    ingest.seed_teams_and_fixtures(conn)
    updated = ingest.apply_results_payload(conn, PAYLOAD)
    assert updated == 1
    row = conn.execute(
        "SELECT home_score, away_score, status FROM matches "
        "WHERE home_team='Brazil' AND away_team='Morocco'"
    ).fetchone()
    assert (row["home_score"], row["away_score"], row["status"]) == (2, 0, "FINISHED")


def test_apply_results_payload_handles_reversed_orientation(tmp_path):
    # Seeded fixture is (Brazil home, Morocco away) via combinations order; the API reports
    # the same match with Morocco at home. Scores must be stored in the seeded orientation.
    conn = db.connect(tmp_path / "t.db")
    db.init_schema(conn)
    ingest.seed_teams_and_fixtures(conn)
    payload = {
        "matches": [
            {
                "homeTeam": {"name": "Morocco"},
                "awayTeam": {"name": "Brazil"},
                "score": {"fullTime": {"home": 1, "away": 3}},
                "status": "FINISHED",
            }
        ]
    }
    updated = ingest.apply_results_payload(conn, payload)
    assert updated == 1
    row = conn.execute(
        "SELECT home_score, away_score, status FROM matches "
        "WHERE home_team='Brazil' AND away_team='Morocco'"
    ).fetchone()
    # Brazil (seeded home) scored 3, Morocco (seeded away) scored 1.
    assert (row["home_score"], row["away_score"], row["status"]) == (3, 1, "FINISHED")


def test_apply_results_payload_aliases_api_team_names(tmp_path):
    # football-data.org uses "Czechia"; our canonical name is "Czech Republic".
    conn = db.connect(tmp_path / "t.db")
    db.init_schema(conn)
    ingest.seed_teams_and_fixtures(conn)
    payload = {
        "matches": [
            {
                "homeTeam": {"name": "South Korea"},
                "awayTeam": {"name": "Czechia"},
                "score": {"fullTime": {"home": 2, "away": 1}},
                "status": "FINISHED",
            }
        ]
    }
    assert ingest.apply_results_payload(conn, payload) == 1
    row = conn.execute(
        "SELECT home_score, away_score, status FROM matches "
        "WHERE home_team='South Korea' AND away_team='Czech Republic'"
    ).fetchone()
    assert (row["home_score"], row["away_score"], row["status"]) == (2, 1, "FINISHED")


def test_apply_fixtures_payload_sets_kickoff(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.init_schema(conn)
    ingest.seed_teams_and_fixtures(conn)
    payload = {
        "matches": [
            {
                "homeTeam": {"name": "Morocco"},
                "awayTeam": {"name": "Brazil"},
                "utcDate": "2026-06-20T19:00:00Z",
                "status": "TIMED",
            },
            {
                "homeTeam": {"name": "Mexico"},
                "awayTeam": {"name": "South Africa"},
                "utcDate": "2026-06-11T18:00:00Z",
                "score": {"fullTime": {"home": 2, "away": 0}},
                "status": "FINISHED",
            },
        ]
    }
    n = ingest.apply_fixtures_payload(conn, payload)
    assert n == 2
    # kickoff set order-independently on the seeded fixture
    row = conn.execute(
        "SELECT kickoff FROM matches WHERE home_team='Brazil' AND away_team='Morocco'"
    ).fetchone()
    assert row["kickoff"] == "2026-06-20T19:00:00Z"
    # finished score + kickoff both applied
    fin = conn.execute(
        "SELECT kickoff, home_score, away_score, status FROM matches "
        "WHERE home_team='Mexico' AND away_team='South Africa'"
    ).fetchone()
    assert fin["kickoff"] == "2026-06-11T18:00:00Z"
    assert (fin["home_score"], fin["away_score"], fin["status"]) == (2, 0, "FINISHED")
