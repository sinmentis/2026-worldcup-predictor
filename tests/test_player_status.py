import json

import pytest

from worldcup_predictor import db
from worldcup_predictor import player_status as ps


def _conn(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.init_schema(conn)
    return conn


def test_status_mult():
    assert ps.status_mult("key", "out") == 0.72
    assert ps.status_mult("fringe", "doubtful") == 0.98
    assert ps.status_mult("key", "available") == 1.0  # unknown pair => no effect
    assert ps.status_mult("nonsense", "out") == 1.0


def test_derive_credibility():
    assert ps.derive_credibility(1, official=False) == 0.50
    assert ps.derive_credibility(2, official=False) == 0.80
    assert ps.derive_credibility(1, official=True) == 0.95


def test_single_source_is_pending(tmp_path):
    conn = _conn(tmp_path)
    out = ps.upsert_status(conn, "France", "Star", "key", "out", 0.9, "https://a")
    assert out["status"] == "pending"  # single non-official source => credibility 0.5 < 0.70
    row = conn.execute("SELECT pending, credibility FROM player_status").fetchone()
    assert row["pending"] == 1
    assert row["credibility"] == 0.50


def test_second_source_corroborates_to_active(tmp_path):
    conn = _conn(tmp_path)
    ps.upsert_status(conn, "France", "Star", "key", "out", 0.9, "https://a")
    out = ps.upsert_status(conn, "France", "Star", "key", "out", 0.9, "https://b")
    assert out["status"] == "active"
    row = conn.execute("SELECT pending, credibility, sources FROM player_status").fetchone()
    assert row["pending"] == 0
    assert row["credibility"] == 0.80
    assert len(json.loads(row["sources"])) == 2  # one row, two sources (no stacking)


def test_official_source_is_active_immediately(tmp_path):
    conn = _conn(tmp_path)
    out = ps.upsert_status(conn, "Spain", "Keeper", "key", "out", 0.9, "https://fed", official=True)
    assert out["status"] == "active"
    assert conn.execute("SELECT credibility FROM player_status").fetchone()[0] == 0.95


def test_available_clears_the_row(tmp_path):
    conn = _conn(tmp_path)
    ps.upsert_status(conn, "Brazil", "Striker", "key", "out", 0.9, "https://a", official=True)
    ps.upsert_status(conn, "Brazil", "Striker", "key", "available", 0.9, "https://b")
    assert conn.execute("SELECT COUNT(*) FROM player_status WHERE team='Brazil'").fetchone()[0] == 0


def test_upsert_validates_inputs(tmp_path):
    conn = _conn(tmp_path)

    with pytest.raises(ValueError):
        ps.upsert_status(conn, "France", "X", "superstar", "out", 0.9, "https://a")
    with pytest.raises(ValueError):
        ps.upsert_status(conn, "France", "X", "key", "out", 0.9, "")


def test_team_status_factor_weakens_team(tmp_path):
    conn = _conn(tmp_path)
    ps.upsert_status(conn, "France", "Star", "key", "out", 0.9, "https://fed", official=True)
    atk, dfn, factors = ps.team_status_factor(conn, "France")
    # key/out mult 0.72, credibility 0.95 => atk = 0.95 * (0.72 - 1) = -0.266 (attack default)
    assert abs(atk - (0.95 * (0.72 - 1.0))) < 1e-9
    assert dfn == 0.0
    assert len(factors) == 1


def test_team_status_factor_ignores_pending(tmp_path):
    conn = _conn(tmp_path)
    ps.upsert_status(conn, "France", "X", "key", "out", 0.9, "https://a")  # single => pending
    atk, dfn, factors = ps.team_status_factor(conn, "France")
    assert atk == 0.0
    assert dfn == 0.0
    assert factors == []


def test_list_approve_reject_pending(tmp_path):
    conn = _conn(tmp_path)
    ps.upsert_status(conn, "France", "X", "key", "out", 0.9, "https://a")  # pending
    pend = ps.list_pending(conn)
    assert len(pend) == 1
    sid = pend[0]["id"]
    ps.approve(conn, sid)
    assert conn.execute("SELECT pending FROM player_status WHERE id=?", (sid,)).fetchone()[0] == 0
    ps.reject(conn, sid)
    assert conn.execute("SELECT COUNT(*) FROM player_status WHERE id=?", (sid,)).fetchone()[0] == 0


def test_purge_expired(tmp_path):
    conn = _conn(tmp_path)
    ps.upsert_status(
        conn,
        "France",
        "X",
        "key",
        "out",
        0.9,
        "https://fed",
        official=True,
        valid_until="2000-01-01",
    )
    assert ps.purge_expired(conn) == 1
    assert conn.execute("SELECT COUNT(*) FROM player_status").fetchone()[0] == 0


def test_corroboration_does_not_demote_active(tmp_path):
    # An active status must not be re-pended by a later lower-confidence corroboration.
    conn = _conn(tmp_path)
    ps.upsert_status(conn, "France", "Star", "key", "out", 0.9, "https://fed", official=True)
    out = ps.upsert_status(conn, "France", "Star", "key", "out", 0.4, "https://b")
    assert out["status"] == "active"
    row = conn.execute("SELECT pending, credibility FROM player_status").fetchone()
    assert row["pending"] == 0
    assert row["credibility"] == 0.95  # credibility unchanged/raised, never lost


def test_approved_status_not_re_pended_by_low_confidence(tmp_path):
    # A human-approved item must not be silently re-pended by a later low-confidence report.
    conn = _conn(tmp_path)
    ps.upsert_status(conn, "France", "X", "key", "out", 0.9, "https://a")  # single => pending
    sid = ps.list_pending(conn)[0]["id"]
    ps.approve(conn, sid)
    ps.upsert_status(conn, "France", "X", "key", "out", 0.3, "https://a2")
    assert conn.execute("SELECT pending FROM player_status WHERE id=?", (sid,)).fetchone()[0] == 0


def test_affects_defaults_to_attack(tmp_path):
    conn = _conn(tmp_path)
    ps.upsert_status(conn, "France", "Mbappe", "key", "out", 0.9, "https://fed", official=True)
    row = conn.execute("SELECT affects FROM player_status WHERE player='Mbappe'").fetchone()
    assert row["affects"] == "attack"


def test_affects_defense_is_stored(tmp_path):
    conn = _conn(tmp_path)
    ps.upsert_status(
        conn,
        "Germany",
        "Schlotterbeck",
        "key",
        "out",
        0.9,
        "https://fed",
        official=True,
        affects="defense",
    )
    row = conn.execute("SELECT affects FROM player_status WHERE player='Schlotterbeck'").fetchone()
    assert row["affects"] == "defense"


def test_affects_preserved_on_corroboration(tmp_path):
    conn = _conn(tmp_path)
    ps.upsert_status(
        conn,
        "Germany",
        "Schlotterbeck",
        "key",
        "out",
        0.9,
        "https://a",
        official=True,
        affects="defense",
    )
    # later corroboration omits affects -> must NOT reset to 'attack'
    ps.upsert_status(
        conn, "Germany", "Schlotterbeck", "key", "out", 0.9, "https://b", official=True
    )
    row = conn.execute("SELECT affects FROM player_status WHERE player='Schlotterbeck'").fetchone()
    assert row["affects"] == "defense"


def test_invalid_affects_raises(tmp_path):
    conn = _conn(tmp_path)
    with pytest.raises(ValueError):
        ps.upsert_status(
            conn, "France", "Mbappe", "key", "out", 0.9, "https://a", affects="midfield"
        )
