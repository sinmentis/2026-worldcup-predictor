import pandas as pd

from worldcup_predictor import backtest, db


def _synth(n, start="2021-01-01"):
    dates = pd.date_range(start, periods=n, freq="3D")
    rows = []
    for i, d in enumerate(dates):
        rows.append((d, "A" if i % 2 else "B", "B" if i % 2 else "A", i % 3, (i + 1) % 2, False))
    return pd.DataFrame(
        rows, columns=["date", "home_team", "away_team", "home_goals", "away_goals", "neutral"]
    )


class _FakeGrid:
    home_win = 0.5
    draw = 0.3
    away_win = 0.2

    def over(self, line: float) -> float:
        return 0.55


class _FakeModel:
    def fit(self, df, xi=None):
        return self

    def predict_grid(self, home, away, neutral=True):
        return _FakeGrid()


def test_iter_chunks_no_lookahead():
    df = _synth(300)
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)
    cutoff = df["date"].iloc[150]
    seen = 0
    for train, chunk in backtest.iter_chunks(df, cutoff, refit_days=30, train_years=4):
        seen += 1
        assert chunk["date"].min() >= cutoff
        if not train.empty:
            # the core guarantee: nothing from the chunk (or later) leaks into training
            assert train["date"].max() < chunk["date"].min()
    assert seen > 0


def test_walk_forward_outputs_out_of_sample(tmp_path, monkeypatch):
    conn = db.connect(tmp_path / "t.db")
    db.init_schema(conn)
    df = _synth(400)
    monkeypatch.setattr(backtest, "history_frame", lambda _c: df)
    monkeypatch.setattr(backtest, "GoalModel", _FakeModel)
    monkeypatch.setattr(backtest, "MIN_TRAIN", 10)

    oos = backtest.walk_forward_predictions(conn, test_years=1, refit_days=30, train_years=4)
    assert len(oos) > 0
    for r in oos:
        assert abs(r["p_home"] + r["p_draw"] + r["p_away"] - 1.0) < 1e-9
        assert r["outcome"] in (0, 1, 2)
        assert {"date", "home", "away"} <= set(r)
        assert {"p_over_2_5", "total_goals"} <= set(r)
        assert r["p_over_2_5"] == 0.55
        assert isinstance(r["total_goals"], int)


def test_reliability_and_metrics():
    oos = [{"p_home": 0.7, "p_draw": 0.2, "p_away": 0.1, "outcome": 0}] * 8
    oos += [{"p_home": 0.7, "p_draw": 0.2, "p_away": 0.1, "outcome": 2}] * 2
    rel = backtest.reliability(oos, n_bins=10)
    assert rel["ece"] >= 0
    assert sum(b["n"] for b in rel["bins"]) == 10

    m = backtest.metrics(oos, params={"draw_mult": 1.2, "temperature": 1.3})
    assert m["n"] == 10
    assert {"model_rps", "baseline_rps", "calibrated_rps", "model_brier"} <= set(m)


def test_metrics_empty():
    assert backtest.metrics([]) == {"n": 0}


def test_engine_run_backtest_reports_and_fits(tmp_path, monkeypatch):
    from worldcup_predictor import calibrate, engine

    conn = db.connect(tmp_path / "bt.db")
    db.init_schema(conn)
    # 1X2: draw-heavy. Totals: model says P(over)=0.8 but overs land only 1/3 of the time.
    oos = [
        {
            "p_home": 0.7,
            "p_draw": 0.15,
            "p_away": 0.15,
            "outcome": 1,
            "p_over_2_5": 0.8,
            "total_goals": 2,
        }
    ] * 20
    oos += [
        {
            "p_home": 0.7,
            "p_draw": 0.15,
            "p_away": 0.15,
            "outcome": 0,
            "p_over_2_5": 0.8,
            "total_goals": 4,
        }
    ] * 10
    monkeypatch.setattr(backtest, "walk_forward_predictions", lambda *a, **k: oos)

    rep = engine.run_backtest(conn, fit_calibration=True)
    assert rep["n"] == 30
    assert "model_rps" in rep and "reliability" in rep and "ece" in rep
    assert "calibration" in rep
    assert calibrate.load(conn) is not None  # 1X2 params persisted
    assert rep["calibration"]["draw_mult"] > 1.0  # learned to raise draws
    assert rep["calibration"]["rps_after"] <= rep["calibration"]["rps_before"]

    from worldcup_predictor import calibrate_totals

    assert "calibration_totals" in rep
    assert calibrate_totals.load(conn) is not None  # totals params persisted
    assert rep["calibration_totals"]["temperature"] > 1.0  # learned to flatten
    assert rep["calibration_totals"]["logloss_after"] <= rep["calibration_totals"]["logloss_before"]


def test_engine_run_backtest_empty(tmp_path, monkeypatch):
    from worldcup_predictor import engine

    conn = db.connect(tmp_path / "bt2.db")
    db.init_schema(conn)
    monkeypatch.setattr(backtest, "walk_forward_predictions", lambda *a, **k: [])
    assert engine.run_backtest(conn) == {"n": 0}


def test_walk_forward_threads_xi_and_real_neutral(tmp_path, monkeypatch):
    conn = db.connect(tmp_path / "wf.db")
    db.init_schema(conn)
    seen = {"xi": [], "neutral": []}

    class _SpyModel:
        def fit(self, df, xi=None):
            seen["xi"].append(xi)
            return self

        def predict_grid(self, home, away, neutral=True):
            seen["neutral"].append(neutral)
            return _FakeGrid()

    df = _synth(400)  # neutral column is all False
    monkeypatch.setattr(backtest, "history_frame", lambda _c: df)
    monkeypatch.setattr(backtest, "GoalModel", _SpyModel)
    monkeypatch.setattr(backtest, "MIN_TRAIN", 10)

    backtest.walk_forward_predictions(conn, xi=0.003, test_years=1, refit_days=60)
    assert seen["xi"] and all(x == 0.003 for x in seen["xi"])  # xi threaded to fit
    assert seen["neutral"] and all(v is False for v in seen["neutral"])  # used the real flag

    seen["neutral"].clear()
    backtest.walk_forward_predictions(conn, neutral=True, test_years=1, refit_days=60)
    assert all(v is True for v in seen["neutral"])  # explicit override still honoured
