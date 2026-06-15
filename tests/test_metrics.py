import pytest

from worldcup_predictor.evaluate import log_loss_score, multiclass_brier, rps


def test_rps_perfect_is_zero():
    assert rps([1.0, 0.0, 0.0], 0) == pytest.approx(0.0)


def test_rps_known_value():
    # probs H/D/A = .5/.3/.2, outcome draw(1): F1=.5,F2=.8 ; O1=0,O2=1
    # rps = ((.5-0)^2 + (.8-1)^2)/2 = (.25 + .04)/2 = .145
    assert rps([0.5, 0.3, 0.2], 1) == pytest.approx(0.145)


def test_brier_perfect_is_zero():
    assert multiclass_brier([0.0, 1.0, 0.0], 1) == pytest.approx(0.0)


def test_log_loss_penalizes_confident_wrong():
    good = log_loss_score([0.8, 0.1, 0.1], 0)
    bad = log_loss_score([0.01, 0.1, 0.89], 0)
    assert bad > good
