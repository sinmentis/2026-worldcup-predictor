import json

import pytest

from worldcup_predictor import db
from worldcup_predictor import team_signal as ts


def _conn(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.init_schema(conn)
    return conn


def test_signal_mult():
    assert ts.signal_mult("weaken", "major") == 0.88
    assert ts.signal_mult("strengthen", "minor") == 1.02
    # strengthen swings are capped smaller than weaken swings
    assert (ts.signal_mult("strengthen", "major") - 1.0) < (1.0 - ts.signal_mult("weaken", "major"))
    assert ts.signal_mult("nonsense", "major") == 1.0  # unknown pair => no effect


def test_single_source_is_pending(tmp_path):
    conn = _conn(tmp_path)
    out = ts.upsert_signal(conn, "Brazil", "tactical", "strengthen", "moderate", 0.9, "https://a")
    assert out["status"] == "pending"  # single non-official => credibility 0.5 < 0.70
    row = conn.execute("SELECT pending, credibility FROM team_signal").fetchone()
    assert row["pending"] == 1
    assert row["credibility"] == 0.50


def test_second_source_corroborates_to_active(tmp_path):
    conn = _conn(tmp_path)
    ts.upsert_signal(conn, "Brazil", "morale", "weaken", "moderate", 0.9, "https://a")
    out = ts.upsert_signal(conn, "Brazil", "morale", "weaken", "moderate", 0.9, "https://b")
    assert out["status"] == "active"
    row = conn.execute("SELECT pending, credibility, sources FROM team_signal").fetchone()
    assert row["pending"] == 0
    assert row["credibility"] == 0.80
    assert len(json.loads(row["sources"])) == 2  # one row, two sources (no stacking)


def test_official_source_is_active_immediately(tmp_path):
    conn = _conn(tmp_path)
    out = ts.upsert_signal(
        conn, "Spain", "motivation", "strengthen", "minor", 0.9, "https://fed", official=True
    )
    assert out["status"] == "active"
    assert conn.execute("SELECT credibility FROM team_signal").fetchone()[0] == 0.95


def test_upsert_validates_inputs(tmp_path):
    conn = _conn(tmp_path)
    with pytest.raises(ValueError):
        ts.upsert_signal(conn, "Brazil", "vibes", "weaken", "minor", 0.9, "https://a")
    with pytest.raises(ValueError):
        ts.upsert_signal(conn, "Brazil", "tactical", "sideways", "minor", 0.9, "https://a")
    with pytest.raises(ValueError):
        ts.upsert_signal(conn, "Brazil", "tactical", "weaken", "huge", 0.9, "https://a")
    with pytest.raises(ValueError):
        ts.upsert_signal(conn, "Brazil", "tactical", "weaken", "minor", 0.9, "")


def test_factor_strengthen_is_positive(tmp_path):
    conn = _conn(tmp_path)
    ts.upsert_signal(
        conn, "Brazil", "tactical", "strengthen", "major", 0.9, "https://fed", official=True
    )
    delta, dfn, factors = ts.team_signal_factor(conn, "Brazil")
    # strengthen/major mult 1.06, credibility 0.95 => delta = 0.95 * (1.06 - 1) = +0.057
    assert abs(delta - (0.95 * (1.06 - 1.0))) < 1e-9
    assert delta > 0.0
    assert dfn == 0.0
    assert len(factors) == 1


def test_factor_weaken_is_negative(tmp_path):
    conn = _conn(tmp_path)
    ts.upsert_signal(conn, "Iran", "morale", "weaken", "major", 0.9, "https://fed", official=True)
    delta, _dfn, _factors = ts.team_signal_factor(conn, "Iran")
    assert delta < 0.0


def test_two_categories_coexist_and_sum(tmp_path):
    conn = _conn(tmp_path)
    ts.upsert_signal(
        conn, "Brazil", "tactical", "strengthen", "minor", 0.9, "https://fed", official=True
    )
    ts.upsert_signal(
        conn, "Brazil", "fatigue", "weaken", "minor", 0.9, "https://fed", official=True
    )
    assert conn.execute("SELECT COUNT(*) FROM team_signal WHERE team='Brazil'").fetchone()[0] == 2
    delta, dfn, factors = ts.team_signal_factor(conn, "Brazil")
    expected = 0.95 * (1.02 - 1.0) + 0.95 * (0.97 - 1.0)
    assert abs(delta - expected) < 1e-9
    assert len(factors) == 2
    assert dfn == 0.0


def test_factor_ignores_pending(tmp_path):
    conn = _conn(tmp_path)
    ts.upsert_signal(conn, "Brazil", "form", "strengthen", "minor", 0.9, "https://a")  # pending
    delta, dfn, factors = ts.team_signal_factor(conn, "Brazil")
    assert delta == 0.0
    assert dfn == 0.0
    assert factors == []


def test_list_approve_reject_pending(tmp_path):
    conn = _conn(tmp_path)
    ts.upsert_signal(conn, "Brazil", "form", "strengthen", "minor", 0.9, "https://a")  # pending
    pend = ts.list_pending(conn)
    assert len(pend) == 1
    sid = pend[0]["id"]
    ts.approve(conn, sid)
    assert conn.execute("SELECT pending FROM team_signal WHERE id=?", (sid,)).fetchone()[0] == 0
    ts.reject(conn, sid)
    assert conn.execute("SELECT COUNT(*) FROM team_signal WHERE id=?", (sid,)).fetchone()[0] == 0


def test_purge_expired(tmp_path):
    conn = _conn(tmp_path)
    ts.upsert_signal(
        conn,
        "Brazil",
        "tactical",
        "strengthen",
        "minor",
        0.9,
        "https://fed",
        official=True,
        valid_until="2000-01-01",
    )
    assert ts.purge_expired(conn) == 1
    assert conn.execute("SELECT COUNT(*) FROM team_signal").fetchone()[0] == 0


def test_corroboration_does_not_demote_active(tmp_path):
    conn = _conn(tmp_path)
    ts.upsert_signal(
        conn, "Brazil", "tactical", "strengthen", "minor", 0.9, "https://fed", official=True
    )
    out = ts.upsert_signal(conn, "Brazil", "tactical", "strengthen", "minor", 0.4, "https://b")
    assert out["status"] == "active"
    row = conn.execute("SELECT pending, credibility FROM team_signal").fetchone()
    assert row["pending"] == 0
    assert row["credibility"] == 0.95  # never lost


def test_expired_signal_excluded_from_factor(tmp_path):
    conn = _conn(tmp_path)
    ts.upsert_signal(
        conn,
        "Brazil",
        "form",
        "strengthen",
        "major",
        0.9,
        "https://fed",
        official=True,
        valid_until="2000-01-01",
    )
    delta, dfn, factors = ts.team_signal_factor(conn, "Brazil")
    assert delta == 0.0
    assert dfn == 0.0
    assert factors == []


def test_signal_affects_defaults_to_attack(tmp_path):
    conn = _conn(tmp_path)
    ts.upsert_signal(
        conn, "Brazil", "tactical", "weaken", "minor", 0.9, "https://fed", official=True
    )
    row = conn.execute("SELECT affects FROM team_signal WHERE team='Brazil'").fetchone()
    assert row["affects"] == "attack"


def test_signal_affects_defense_preserved_on_update(tmp_path):
    conn = _conn(tmp_path)
    ts.upsert_signal(
        conn,
        "Germany",
        "tactical",
        "weaken",
        "minor",
        0.9,
        "https://a",
        official=True,
        affects="defense",
    )
    ts.upsert_signal(
        conn, "Germany", "tactical", "weaken", "minor", 0.9, "https://b", official=True
    )
    row = conn.execute("SELECT affects FROM team_signal WHERE team='Germany'").fetchone()
    assert row["affects"] == "defense"


def test_signal_invalid_affects_raises(tmp_path):
    conn = _conn(tmp_path)
    with pytest.raises(ValueError):
        ts.upsert_signal(
            conn, "Brazil", "tactical", "weaken", "minor", 0.9, "https://a", affects="x"
        )
