import pandas as pd

from worldcup_predictor import backtest, config, db, engine, tune


def _conn(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.init_schema(conn)
    return conn


def test_load_store_model_params(tmp_path):
    conn = _conn(tmp_path)
    assert tune.load_model_params(conn) == {}
    assert tune.current_xi(conn) == config.TIME_DECAY_XI  # default when unset
    tune.store_model_params(conn, {"time_decay_xi": 0.003}, meta={"rps": 0.17})
    assert tune.load_model_params(conn)["time_decay_xi"] == 0.003
    assert tune.current_xi(conn) == 0.003


def test_tune_decay_picks_min_rps(tmp_path, monkeypatch):
    conn = _conn(tmp_path)

    def fake_wf(_conn, xi=None, **k):
        # the target xi predicts the (home-win) outcomes confidently; others are vague
        if abs(xi - 0.002) < 1e-9:
            return [{"p_home": 0.9, "p_draw": 0.05, "p_away": 0.05, "outcome": 0}] * 10
        return [{"p_home": 0.34, "p_draw": 0.33, "p_away": 0.33, "outcome": 0}] * 10

    monkeypatch.setattr(backtest, "walk_forward_predictions", fake_wf)
    rep = tune.tune_decay(conn, grid=[0.001, 0.002, 0.005])
    assert rep["best"]["xi"] == 0.002
    assert all(r["n"] == 10 for r in rep["results"])


def test_tune_decay_includes_current_xi(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    tune.store_model_params(conn, {"time_decay_xi": 0.0042})
    monkeypatch.setattr(
        backtest,
        "walk_forward_predictions",
        lambda _c, xi=None, **k: [{"p_home": 0.5, "p_draw": 0.3, "p_away": 0.2, "outcome": 1}] * 5,
    )
    rep = tune.tune_decay(conn, grid=[0.001, 0.005])
    assert any(abs(r["xi"] - 0.0042) < 1e-12 for r in rep["results"])  # current always evaluated


def test_run_tuning_guardrail_and_apply(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    improved = {
        "results": [{"xi": 0.001, "rps": 0.20, "n": 100}, {"xi": 0.003, "rps": 0.18, "n": 100}],
        "best": {"xi": 0.003, "rps": 0.18, "n": 100},
        "current_xi": 0.001,
        "current_rps": 0.20,
    }
    monkeypatch.setattr(tune, "tune_decay", lambda *a, **k: improved)
    rep = engine.run_tuning(conn, apply=True)
    assert rep["would_adopt"] is True and rep["applied"] is True
    assert tune.current_xi(conn) == 0.003  # adopted + stored

    # a within-epsilon "improvement" must NOT be adopted
    marginal = {
        "results": [{"xi": 0.003, "rps": 0.20, "n": 100}, {"xi": 0.0005, "rps": 0.1999, "n": 100}],
        "best": {"xi": 0.0005, "rps": 0.1999, "n": 100},
        "current_xi": 0.003,
        "current_rps": 0.20,
    }
    monkeypatch.setattr(tune, "tune_decay", lambda *a, **k: marginal)
    rep2 = engine.run_tuning(conn, apply=True)
    assert rep2["would_adopt"] is False and rep2["applied"] is False
    assert tune.current_xi(conn) == 0.003  # unchanged


def test_get_model_refits_on_xi_change(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    calls = {"n": 0}

    class _FakeModel:
        def fit(self, frame, xi=None):
            calls["n"] += 1
            self.xi = xi
            return self

    monkeypatch.setattr(engine, "GoalModel", _FakeModel)
    monkeypatch.setattr(engine, "history_frame", lambda _c: pd.DataFrame({"home_team": [1]}))
    engine._reset_model_cache()
    xi_box = [0.001]
    monkeypatch.setattr(engine._tune, "current_xi", lambda _c: xi_box[0])

    engine.get_model(conn)
    assert calls["n"] == 1
    engine.get_model(conn)
    assert calls["n"] == 1  # cached
    xi_box[0] = 0.003
    engine.get_model(conn)
    assert calls["n"] == 2  # refit because the tuned xi changed
    engine._reset_model_cache()
