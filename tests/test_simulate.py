import numpy as np
import pandas as pd

from worldcup_predictor import config, db, ingest
from worldcup_predictor.goal_model import GoalModel
from worldcup_predictor.simulate import simulate_tournament


def _history() -> pd.DataFrame:
    rng = np.random.default_rng(2)
    teams = [t for ts in config.GROUPS.values() for t in ts]
    rows = []
    for _ in range(2000):
        h, a = rng.choice(teams, 2, replace=False)
        rows.append(("2024-01-01", h, a, int(rng.poisson(1.3)), int(rng.poisson(1.2)), True))
    return pd.DataFrame(
        rows, columns=["date", "home_team", "away_team", "home_goals", "away_goals", "neutral"]
    )


def test_simulation_probabilities_valid(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.init_schema(conn)
    ingest.seed_teams_and_fixtures(conn)
    model = GoalModel().fit(_history())

    result = simulate_tournament(conn, model, n=200, seed=7)
    # 48 teams present
    assert len(result) == 48
    # each prob in [0,1], title <= final <= sf <= qf <= r16 <= advance
    for p in result.values():
        for v in p.values():
            assert 0.0 <= v <= 1.0
        assert p["title"] <= p["final"] <= p["sf"] <= p["qf"] <= p["r16"] <= p["advance"] + 1e-9
    # exactly one champion's worth of probability mass
    assert abs(sum(p["title"] for p in result.values()) - 1.0) < 1e-9
    # advancement mass equals 32 teams
    assert abs(sum(p["advance"] for p in result.values()) - 32.0) < 1e-6
    # persisted
    assert conn.execute("SELECT COUNT(*) FROM sim_results").fetchone()[0] == 48


def test_intel_lowers_simulated_advancement(tmp_path):
    # The tournament simulation must honor off-pitch intel (not just single-match predict).
    conn = db.connect(tmp_path / "t.db")
    db.init_schema(conn)
    ingest.seed_teams_and_fixtures(conn)
    model = GoalModel().fit(_history())

    target = config.GROUPS["A"][0]
    before = simulate_tournament(conn, model, n=500, seed=3)[target]["advance"]

    from worldcup_predictor import intel
    from worldcup_predictor.models import IntelEvent

    intel.record_intel(
        conn, IntelEvent(target, "injury", "weaken", 0.6, "https://x", 1.0, player="key")
    )
    after = simulate_tournament(conn, model, n=500, seed=3)[target]["advance"]
    assert after < before


def test_simulation_conditions_on_finished_group_matches(tmp_path):
    # When every group-A match is already played with Mexico winning all of them,
    # Mexico must advance in every simulation and the 4th-placed team never advances.
    conn = db.connect(tmp_path / "t.db")
    db.init_schema(conn)
    ingest.seed_teams_and_fixtures(conn)
    model = GoalModel().fit(_history())

    def finish(home, away, hs, as_):
        conn.execute(
            "UPDATE matches SET home_score=?, away_score=?, status='FINISHED' "
            "WHERE group_id='A' AND home_team=? AND away_team=?",
            (hs, as_, home, away),
        )

    finish("Mexico", "South Africa", 2, 0)
    finish("Mexico", "South Korea", 2, 0)
    finish("Mexico", "Czech Republic", 2, 0)
    finish("South Africa", "South Korea", 1, 0)
    finish("South Africa", "Czech Republic", 1, 0)
    finish("South Korea", "Czech Republic", 1, 0)
    conn.commit()

    result = simulate_tournament(conn, model, n=300, seed=5)
    assert result["Mexico"]["advance"] > 0.999  # 9 pts, group fully decided
    assert result["Czech Republic"]["advance"] == 0.0  # 4th place never qualifies
