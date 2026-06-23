import sqlite3

import pytest

from worldcup_predictor import db


def test_init_schema_creates_tables(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.init_schema(conn)
    names = {
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    expected = {
        "teams",
        "matches",
        "historical_matches",
        "predictions",
        "intel_events",
        "ratings_history",
        "sim_results",
        "metrics",
        "tuning_params",
        "meta",
    }
    assert expected <= names


def test_set_and_get_last_update(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.init_schema(conn)
    db.touch_update(conn)
    ts1 = db.get_last_update_ts(conn)
    assert ts1 is not None


def test_phase2a_tables_exist(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.init_schema(conn)
    names = {
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert {"news_articles", "player_status"} <= names
    # player_status enforces one current row per (team, player)
    conn.execute(
        "INSERT INTO player_status(team,player,tier,status,credibility,sources,as_of,pending)"
        " VALUES ('France','X','key','out',0.9,'[]',0,0)"
    )
    import sqlite3

    import pytest

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO player_status(team,player,tier,status,credibility,sources,as_of,pending)"
            " VALUES ('France','X','key','out',0.9,'[]',0,0)"
        )


def test_team_signal_table_exists(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.init_schema(conn)
    names = {
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert "team_signal" in names
    # team_signal enforces one current row per (team, category)
    conn.execute(
        "INSERT INTO team_signal"
        "(team,category,direction,magnitude_tier,credibility,sources,as_of,pending)"
        " VALUES ('Brazil','tactical','strengthen','minor',0.8,'[]',0,0)"
    )
    import sqlite3

    import pytest

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO team_signal"
            "(team,category,direction,magnitude_tier,credibility,sources,as_of,pending)"
            " VALUES ('Brazil','tactical','weaken','major',0.8,'[]',0,0)"
        )


def test_odds_table_exists(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.init_schema(conn)
    names = {
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert "odds" in names
    conn.execute(
        "INSERT INTO odds(match_id,bookmaker,price_home,price_draw,price_away,fetched_at)"
        " VALUES (1,'x',1.5,3.5,6.0,0)"
    )
    import sqlite3

    import pytest

    with pytest.raises(sqlite3.IntegrityError):  # UNIQUE(match_id, bookmaker)
        conn.execute(
            "INSERT INTO odds(match_id,bookmaker,price_home,price_draw,price_away,fetched_at)"
            " VALUES (1,'x',1.6,3.4,5.5,0)"
        )


def test_odds_totals_table_exists(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.init_schema(conn)
    names = {
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert "odds_totals" in names


def test_migrate_is_idempotent(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.init_schema(conn)  # runs migrate once internally
    db.migrate(conn)  # second explicit run must be a no-op
    db.migrate(conn)  # third for good measure
    for table in ("player_status", "team_signal"):
        cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
        assert cols.count("affects") == 1


def test_migrate_adds_affects_to_legacy_db(tmp_path):
    conn = db.connect(tmp_path / "old.db")
    # Pre-feature tables: prod shape WITHOUT `affects`.
    conn.executescript(
        """
        CREATE TABLE player_status (
            id INTEGER PRIMARY KEY, team TEXT NOT NULL, player TEXT NOT NULL,
            tier TEXT NOT NULL, status TEXT NOT NULL, credibility REAL NOT NULL,
            sources TEXT NOT NULL, official INTEGER DEFAULT 0, valid_until TEXT,
            as_of REAL NOT NULL, pending INTEGER DEFAULT 0, notes TEXT,
            UNIQUE(team, player)
        );
        CREATE TABLE team_signal (
            id INTEGER PRIMARY KEY, team TEXT NOT NULL, category TEXT NOT NULL,
            direction TEXT NOT NULL, magnitude_tier TEXT NOT NULL,
            credibility REAL NOT NULL, sources TEXT NOT NULL,
            official INTEGER DEFAULT 0, valid_until TEXT, as_of REAL NOT NULL,
            pending INTEGER DEFAULT 0, notes TEXT,
            UNIQUE(team, category)
        );
        """
    )
    conn.execute(
        "INSERT INTO player_status"
        "(team,player,tier,status,credibility,sources,official,valid_until,as_of,pending,notes)"
        " VALUES ('Brazil','Neymar','key','out',0.9,'[]',1,NULL,0.0,0,NULL)"
    )
    conn.execute(
        "INSERT INTO team_signal"
        "(team,category,direction,magnitude_tier,credibility,sources,official,valid_until,as_of,pending,notes)"
        " VALUES ('Brazil','morale','weaken','moderate',0.8,'[]',0,NULL,0.0,0,NULL)"
    )
    conn.commit()
    assert "affects" not in [r[1] for r in conn.execute("PRAGMA table_info(player_status)")]

    db.migrate(conn)

    for table in ("player_status", "team_signal"):
        info = {r[1]: r for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        assert "affects" in info
        assert info["affects"][3] == 1  # notnull flag
        rows = conn.execute(f"SELECT affects FROM {table}").fetchall()
        assert rows and all(r[0] == "attack" for r in rows)


def test_fresh_db_rejects_bad_affects(tmp_path):
    conn = db.connect(tmp_path / "fresh.db")
    db.init_schema(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO player_status"
            "(team,player,tier,status,credibility,sources,official,valid_until,as_of,pending,notes,affects)"
            " VALUES ('ARG','Messi','key','out',0.9,'[]',1,NULL,0.0,0,NULL,'midfield')"
        )
