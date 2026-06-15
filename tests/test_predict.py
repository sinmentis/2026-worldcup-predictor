import numpy as np
import pandas as pd

from worldcup_predictor import db, intel
from worldcup_predictor.goal_model import GoalModel
from worldcup_predictor.models import IntelEvent
from worldcup_predictor.predict import predict_match


def _history() -> pd.DataFrame:
    rng = np.random.default_rng(1)
    rows = []
    for _ in range(80):
        rows.append(
            (
                "2024-01-01",
                "Strong",
                "Weak",
                int(rng.integers(2, 5)),
                int(rng.integers(0, 2)),
                False,
            )
        )
        rows.append(
            (
                "2024-01-01",
                "Weak",
                "Strong",
                int(rng.integers(0, 2)),
                int(rng.integers(2, 5)),
                False,
            )
        )
    return pd.DataFrame(
        rows, columns=["date", "home_team", "away_team", "home_goals", "away_goals", "neutral"]
    )


def test_predict_match_persists_and_sums(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.init_schema(conn)
    conn.execute(
        "INSERT INTO matches(id, stage, home_team, away_team, neutral, status)"
        " VALUES (1,'group','Strong','Weak',1,'SCHEDULED')"
    )
    conn.commit()
    model = GoalModel().fit(_history())

    pred = predict_match(conn, model, match_id=1, home="Strong", away="Weak", neutral=True)
    assert abs(pred.p_home + pred.p_draw + pred.p_away - 1.0) < 1e-6
    assert pred.p_home > pred.p_away
    stored = conn.execute("SELECT p_home, p_away FROM predictions WHERE match_id=1").fetchone()
    assert stored is not None


def test_intel_shifts_prediction(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.init_schema(conn)
    model = GoalModel().fit(_history())
    base = predict_match(conn, model, match_id=None, home="Strong", away="Weak", neutral=True)
    intel.record_intel(conn, IntelEvent("Strong", "injury", "weaken", -0.5, "u", 1.0, player="key"))
    after = predict_match(conn, model, match_id=None, home="Strong", away="Weak", neutral=True)
    assert after.p_home < base.p_home  # weakened favourite
    assert any(f.team == "Strong" for f in after.factors)


def test_host_adjust_pure():
    from worldcup_predictor.predict import host_adjust

    h, a = host_adjust(1.0, 1.0, "United States", "Paraguay")
    assert h > 1.0 and a == 1.0  # host at home gets the bump
    h, a = host_adjust(1.0, 1.0, "Paraguay", "United States")
    assert a > 1.0 and h == 1.0  # host away gets the bump
    assert host_adjust(1.0, 1.0, "Brazil", "Argentina") == (1.0, 1.0)  # neither host
    assert host_adjust(1.0, 1.0, "Mexico", "Canada") == (1.0, 1.0)  # both hosts -> no edge


def test_adjusted_grid_applies_host_advantage(tmp_path, monkeypatch):
    from worldcup_predictor import config
    from worldcup_predictor.predict import adjusted_grid

    conn = db.connect(tmp_path / "t.db")
    db.init_schema(conn)
    model = GoalModel().fit(_history())

    monkeypatch.setattr(config, "HOSTS", {"Strong"})
    grid_host, _ = adjusted_grid(conn, model, "Strong", "Weak", neutral=True)
    monkeypatch.setattr(config, "HOSTS", set())
    grid_base, _ = adjusted_grid(conn, model, "Strong", "Weak", neutral=True)
    assert grid_host.home_win > grid_base.home_win  # host edge raises the host's win prob


def test_predict_match_applies_stored_calibration(tmp_path):
    from worldcup_predictor import calibrate

    conn = db.connect(tmp_path / "t.db")
    db.init_schema(conn)
    model = GoalModel().fit(_history())
    base = predict_match(conn, model, "Strong", "Weak", neutral=True)  # no calibration -> raw
    calibrate.store(conn, {"draw_mult": 1.6, "temperature": 1.3})
    cal = predict_match(conn, model, "Strong", "Weak", neutral=True)
    assert cal.p_draw > base.p_draw  # draw boost raised the draw probability
    assert abs(cal.p_home + cal.p_draw + cal.p_away - 1.0) < 1e-9
