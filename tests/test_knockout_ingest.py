from worldcup_predictor import db, ingest


def _conn(tmp_path):
    conn = db.connect(tmp_path / "k.db")
    db.init_schema(conn)
    ingest.seed_teams_and_fixtures(conn)
    return conn


def _ko(ext_id, stage, home, away, status="TIMED", hs=None, as_=None, winner=None):
    return {
        "id": ext_id,
        "stage": stage,
        "utcDate": "2026-06-28T19:00:00Z",
        "status": status,
        "homeTeam": {"name": home} if home else None,
        "awayTeam": {"name": away} if away else None,
        "score": {"winner": winner, "fullTime": {"home": hs, "away": as_}},
    }


def test_stage_map_covers_all_knockout_rounds():
    assert ingest._STAGE_MAP == {
        "GROUP_STAGE": "group",
        "LAST_32": "R32",
        "LAST_16": "R16",
        "QUARTER_FINALS": "QF",
        "SEMI_FINALS": "SF",
        "THIRD_PLACE": "3RD",
        "FINAL": "FINAL",
    }


def test_apply_knockout_inserts_with_real_teams(tmp_path):
    conn = _conn(tmp_path)
    n = ingest.apply_knockout_fixtures(conn, {"matches": [_ko(900, "LAST_32", "Spain", "Japan")]})
    assert n == 1
    row = conn.execute(
        "SELECT stage, home_team, away_team, kickoff, neutral, status, ext_id "
        "FROM matches WHERE ext_id=900"
    ).fetchone()
    assert row["stage"] == "R32"
    assert (row["home_team"], row["away_team"]) == ("Spain", "Japan")
    assert row["neutral"] == 1 and row["status"] == "SCHEDULED"


def test_apply_knockout_stores_null_teams_for_tbd(tmp_path):
    conn = _conn(tmp_path)
    ingest.apply_knockout_fixtures(conn, {"matches": [_ko(901, "SEMI_FINALS", None, None)]})
    row = conn.execute(
        "SELECT stage, home_team, away_team FROM matches WHERE ext_id=901"
    ).fetchone()
    assert row["stage"] == "SF"
    assert row["home_team"] is None and row["away_team"] is None


def test_apply_knockout_is_idempotent_and_fills_in(tmp_path):
    conn = _conn(tmp_path)
    # First fetch: teams TBD.
    ingest.apply_knockout_fixtures(conn, {"matches": [_ko(902, "LAST_16", None, None)]})
    # Later fetch: teams now known + finished with a penalty winner on a 1-1 draw.
    ingest.apply_knockout_fixtures(
        conn,
        {
            "matches": [
                _ko(
                    902,
                    "LAST_16",
                    "Brazil",
                    "Croatia",
                    status="FINISHED",
                    hs=1,
                    as_=1,
                    winner="AWAY_TEAM",
                )
            ]
        },
    )
    rows = conn.execute("SELECT * FROM matches WHERE ext_id=902").fetchall()
    assert len(rows) == 1  # updated in place, not duplicated
    r = rows[0]
    assert (r["home_team"], r["away_team"]) == ("Brazil", "Croatia")
    assert r["status"] == "FINISHED" and (r["home_score"], r["away_score"]) == (1, 1)
    assert r["winner_team"] == "Croatia"  # penalty winner from score.winner


def test_group_functions_ignore_knockout_matches(tmp_path):
    conn = _conn(tmp_path)
    # A knockout rematch of a real group pair must NOT overwrite the group row.
    grp = conn.execute(
        "SELECT home_team, away_team FROM matches WHERE stage='group' LIMIT 1"
    ).fetchone()
    payload = {
        "matches": [
            {
                "stage": "QUARTER_FINALS",
                "utcDate": "2026-07-09T19:00:00Z",
                "status": "FINISHED",
                "homeTeam": {"name": grp["home_team"]},
                "awayTeam": {"name": grp["away_team"]},
                "score": {"winner": "HOME_TEAM", "fullTime": {"home": 3, "away": 0}},
            }
        ]
    }
    ingest.apply_results_payload(conn, payload)  # group-only: must skip this knockout match
    g = conn.execute(
        "SELECT status FROM matches WHERE stage='group' AND home_team=? AND away_team=?",
        (grp["home_team"], grp["away_team"]),
    ).fetchone()
    assert g["status"] != "FINISHED"  # untouched by the knockout payload
