import json

import pytest

from worldcup_predictor import calibrate, db


def test_apply_identity_when_unset():
    assert calibrate.apply(0.5, 0.3, 0.2, None) == (0.5, 0.3, 0.2)


def test_apply_identity_at_default_params():
    out = calibrate.apply(0.5, 0.3, 0.2, {"draw_mult": 1.0, "temperature": 1.0})
    assert abs(out[0] - 0.5) < 1e-9 and abs(out[1] - 0.3) < 1e-9 and abs(out[2] - 0.2) < 1e-9


def test_draw_boost_raises_draw_share_and_normalises():
    out = calibrate.apply(0.5, 0.3, 0.2, {"draw_mult": 1.5, "temperature": 1.0})
    assert out[1] > 0.3  # draw share rises
    assert abs(sum(out) - 1.0) < 1e-9


def test_temperature_flattens_overconfident_vector():
    raw = (0.85, 0.10, 0.05)
    out = calibrate.apply(*raw, {"draw_mult": 1.0, "temperature": 2.0})
    assert out[0] < 0.85  # top prob shrinks toward the rest
    assert out[2] > 0.05  # tail rises
    assert abs(sum(out) - 1.0) < 1e-9


def test_fit_recovers_draw_boost_on_draw_heavy_data():
    # Model says draws are ~0.25 but the realised outcomes are draws far more often.
    oos = []
    for _ in range(40):
        oos.append({"p_home": 0.5, "p_draw": 0.25, "p_away": 0.25, "outcome": 1})  # draw
    for _ in range(30):
        oos.append({"p_home": 0.5, "p_draw": 0.25, "p_away": 0.25, "outcome": 0})  # home
    params = calibrate.fit(oos)
    assert params["draw_mult"] > 1.0  # learns to raise draws

    from worldcup_predictor import evaluate

    def mean_rps(p):
        return sum(
            evaluate.rps(
                list(calibrate.apply(r["p_home"], r["p_draw"], r["p_away"], p)), r["outcome"]
            )
            for r in oos
        ) / len(oos)

    assert mean_rps(params) < mean_rps(None)  # calibration improves out-of-sample RPS


def test_store_and_load_roundtrip(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.init_schema(conn)
    assert calibrate.load(conn) is None  # unset -> None (predict stays raw)
    calibrate.store(conn, {"draw_mult": 1.25, "temperature": 1.4}, meta={"n_test": 100})
    loaded = calibrate.load(conn)
    assert loaded == {"draw_mult": 1.25, "temperature": 1.4}
    # meta is persisted alongside but load returns only the transform knobs
    raw = json.loads(
        conn.execute("SELECT value FROM tuning_params WHERE key='calibration'").fetchone()[0]
    )
    assert raw["n_test"] == 100


def test_apply_handles_extremes():
    out = calibrate.apply(1.0, 0.0, 0.0, {"draw_mult": 1.3, "temperature": 1.5})
    assert abs(sum(out) - 1.0) < 1e-9
    assert pytest.approx(out[0], abs=1e-9) == 1.0  # a certain outcome stays certain
