import math

from worldcup_predictor import calibrate_totals as ct
from worldcup_predictor import db


def test_apply_identity_when_unset():
    assert ct.apply(0.62, None) == 0.62


def test_apply_identity_at_default_params():
    out = ct.apply(0.62, {"temperature": 1.0, "over_mult": 1.0})
    assert abs(out - 0.62) < 1e-9


def test_temperature_flattens_overconfident_over_toward_half():
    out = ct.apply(0.80, {"temperature": 2.0, "over_mult": 1.0})
    assert 0.5 < out < 0.80  # pulled toward 0.5, still above it


def test_temperature_flattens_overconfident_under_toward_half():
    out = ct.apply(0.20, {"temperature": 2.0, "over_mult": 1.0})
    assert 0.20 < out < 0.5  # a confident-under prob also rises toward 0.5


def test_over_mult_above_one_raises_over():
    out = ct.apply(0.50, {"temperature": 1.0, "over_mult": 1.2})
    assert out > 0.50


def test_over_mult_below_one_lowers_over():
    out = ct.apply(0.50, {"temperature": 1.0, "over_mult": 0.8})
    assert out < 0.50


def test_apply_handles_extremes_and_tau_zero():
    assert ct.apply(1.0, {"temperature": 1.5, "over_mult": 1.0}) == 1.0
    assert ct.apply(0.0, {"temperature": 1.5, "over_mult": 1.0}) == 0.0
    # tau == 0 must not divide by zero; treated as identity exponent
    assert abs(ct.apply(0.6, {"temperature": 0.0, "over_mult": 1.0}) - 0.6) < 1e-9


def test_fit_returns_identity_on_calibrated_data():
    # model says P(over)=0.6 and overs really happen 60% of the time -> no correction
    oos = [{"p_over_2_5": 0.6, "total_goals": 3}] * 60 + [
        {"p_over_2_5": 0.6, "total_goals": 2}
    ] * 40
    params = ct.fit(oos)
    assert params["temperature"] == 1.0
    assert params["over_mult"] == 1.0


def test_fit_tames_overconfidence_and_improves_logloss():
    # model is wildly overconfident (P(over)=0.8) but overs happen only ~50%
    oos = [{"p_over_2_5": 0.8, "total_goals": 3}] * 50 + [
        {"p_over_2_5": 0.8, "total_goals": 2}
    ] * 50
    params = ct.fit(oos)
    assert params["temperature"] > 1.0  # learns to flatten

    def mean_ll(p):
        tot = 0.0
        for r in oos:
            po = max(1e-12, min(1 - 1e-12, ct.apply(r["p_over_2_5"], p)))
            y = 1 if r["total_goals"] > ct.LINE else 0
            tot += -(y * math.log(po) + (1 - y) * math.log(1 - po))
        return tot / len(oos)

    assert mean_ll(params) < mean_ll(None)  # calibration improves out-of-sample log-loss


def test_store_and_load_roundtrip(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.init_schema(conn)
    assert ct.load(conn) is None  # unset -> None (totals stay raw)
    ct.store(conn, {"temperature": 1.4, "over_mult": 1.1}, meta={"n_test": 100})
    assert ct.load(conn) == {"temperature": 1.4, "over_mult": 1.1}
