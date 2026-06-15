from worldcup_predictor.models import MatchPrediction


def test_match_prediction_probs_sum_to_one():
    p = MatchPrediction(
        home_team="Brazil",
        away_team="Germany",
        p_home=0.5,
        p_draw=0.3,
        p_away=0.2,
        exp_home_goals=1.6,
        exp_away_goals=1.1,
        ml_home=2,
        ml_away=1,
        factors=[],
    )
    assert abs((p.p_home + p.p_draw + p.p_away) - 1.0) < 1e-9
    assert p.most_likely_scoreline == "2-1"
