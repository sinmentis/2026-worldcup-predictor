import pytest

from worldcup_predictor import db, results_refresh


def _conn(tmp_path):
    conn = db.connect(tmp_path / "refresh.db")
    db.init_schema(conn)
    conn.execute(
        "INSERT INTO matches(id,stage,home_team,away_team,kickoff,home_score,away_score,status) "
        "VALUES(1,'group','England','Croatia','2026-06-20T19:00:00Z',3,2,'FINISHED')"
    )
    conn.commit()
    return conn


def test_refresh_detects_same_count_score_correction(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    calls = {"sync": 0, "rate": 0, "simulate": 0}

    monkeypatch.setattr(
        results_refresh.ingest,
        "sync_finished_to_history",
        lambda _conn: calls.__setitem__("sync", calls["sync"] + 1) or 1,
    )
    monkeypatch.setattr(
        results_refresh.ratings,
        "calculate_elo_ratings",
        lambda _conn: calls.__setitem__("rate", calls["rate"] + 1) or {},
    )
    monkeypatch.setattr(
        results_refresh.engine,
        "calculate_simulation",
        lambda _conn, n, seed: calls.__setitem__("simulate", calls["simulate"] + 1) or {},
    )

    first = results_refresh.refresh_results_model(conn, n=20, seed=1)
    assert first.processed is True
    assert calls == {"sync": 1, "rate": 1, "simulate": 1}

    unchanged = results_refresh.refresh_results_model(conn, n=20, seed=1)
    assert unchanged.processed is False
    assert calls == {"sync": 1, "rate": 1, "simulate": 1}

    conn.execute("UPDATE matches SET away_score=1 WHERE id=1")
    conn.commit()
    corrected = results_refresh.refresh_results_model(conn, n=20, seed=1)
    assert corrected.processed is True
    assert calls == {"sync": 2, "rate": 2, "simulate": 2}
    assert corrected.revision != first.revision


def test_refresh_retries_after_simulation_failure(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    calls = {"simulate": 0}

    monkeypatch.setattr(results_refresh.ingest, "sync_finished_to_history", lambda _conn: 1)
    monkeypatch.setattr(results_refresh.ratings, "calculate_elo_ratings", lambda _conn: {})

    def fail_once(_conn, n, seed):
        calls["simulate"] += 1
        if calls["simulate"] == 1:
            raise RuntimeError("sim failed")
        return {}

    monkeypatch.setattr(results_refresh.engine, "calculate_simulation", fail_once)

    with pytest.raises(RuntimeError, match="sim failed"):
        results_refresh.refresh_results_model(conn, n=20, seed=1)
    assert results_refresh.processed_results_revision(conn) is None

    retried = results_refresh.refresh_results_model(conn, n=20, seed=1)
    assert retried.processed is True
    assert calls["simulate"] == 2
    assert results_refresh.processed_results_revision(conn) == retried.revision


def test_refresh_processes_history_only_revision_change(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    calls = {"simulate": 0}
    monkeypatch.setattr(results_refresh.ingest, "sync_finished_to_history", lambda _conn: 0)
    monkeypatch.setattr(results_refresh.ratings, "calculate_elo_ratings", lambda _conn: {})
    monkeypatch.setattr(
        results_refresh.engine,
        "calculate_simulation",
        lambda _conn, n, seed: calls.__setitem__("simulate", calls["simulate"] + 1) or {},
    )
    assert results_refresh.refresh_results_model(conn, n=20).processed is True
    assert calls["simulate"] == 1

    db.bump_history_revision(conn)
    conn.commit()

    assert results_refresh.refresh_results_model(conn, n=20).processed is True
    assert calls["simulate"] == 2


def test_refresh_does_not_mark_match_state_that_changed_during_simulation(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    monkeypatch.setattr(results_refresh.ingest, "sync_finished_to_history", lambda _conn: 0)
    monkeypatch.setattr(results_refresh.ratings, "calculate_elo_ratings", lambda _conn: {})

    def mutate_match(_conn, n, seed):
        _conn.execute("UPDATE matches SET away_score=1 WHERE id=1")
        _conn.commit()
        return {}

    monkeypatch.setattr(results_refresh.engine, "calculate_simulation", mutate_match)

    with pytest.raises(RuntimeError, match="changed during refresh"):
        results_refresh.refresh_results_model(conn, n=20)
    assert results_refresh.processed_results_revision(conn) is None


def test_refresh_marks_final_revision_after_real_history_sync(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    monkeypatch.setattr(results_refresh.ratings, "calculate_elo_ratings", lambda _conn: {})
    monkeypatch.setattr(results_refresh.engine, "calculate_simulation", lambda _conn, n, seed: {})

    result = results_refresh.refresh_results_model(conn, n=20)

    assert result.processed is True
    assert db.history_revision(conn) == 1
    assert result.revision == results_refresh.refresh_revision(conn)
    assert results_refresh.processed_results_revision(conn) == result.revision
    assert results_refresh.refresh_results_model(conn, n=20).processed is False


def test_refresh_does_not_publish_staged_output_when_other_writer_changes_matches(
    tmp_path, monkeypatch
):
    conn = _conn(tmp_path)
    other = db.connect(tmp_path / "refresh.db")
    monkeypatch.setattr(results_refresh.ingest, "sync_finished_to_history", lambda _conn: 0)
    monkeypatch.setattr(results_refresh.ratings, "calculate_elo_ratings", lambda _conn: {})

    staged = {
        "England": {
            "advance": 1.0,
            "r16": 1.0,
            "qf": 1.0,
            "sf": 1.0,
            "final": 1.0,
            "title": 1.0,
        }
    }

    def calculate(_conn, n, seed):
        other.execute("UPDATE matches SET away_score=1 WHERE id=1")
        other.commit()
        return staged

    monkeypatch.setattr(results_refresh.engine, "calculate_simulation", calculate)

    with pytest.raises(RuntimeError, match="changed during refresh"):
        results_refresh.refresh_results_model(conn, n=20)

    assert conn.execute("SELECT COUNT(*) FROM sim_results").fetchone()[0] == 0
    other.close()
