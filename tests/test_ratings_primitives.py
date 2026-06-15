import pytest

from worldcup_predictor import ratings


def test_elo_expected_even():
    assert ratings.elo_expected(1500, 1500) == pytest.approx(0.5)


def test_elo_expected_home_advantage_neutral_false():
    # +100 to home when not neutral
    assert ratings.elo_expected(1500, 1500, neutral=False) == pytest.approx(
        1 / (10 ** (-100 / 400) + 1)
    )


def test_goal_diff_multiplier():
    assert ratings.goal_diff_multiplier(0) == 1.0
    assert ratings.goal_diff_multiplier(1) == 1.0
    assert ratings.goal_diff_multiplier(2) == 1.5
    assert ratings.goal_diff_multiplier(3) == pytest.approx(1.75)
    assert ratings.goal_diff_multiplier(5) == pytest.approx(2.0)


def test_elo_update_winner_gains():
    we = ratings.elo_expected(1500, 1500)
    new = ratings.elo_update(1500, k=60, g=1.5, w=1.0, we=we)
    assert new > 1500
