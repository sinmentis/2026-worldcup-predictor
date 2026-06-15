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
