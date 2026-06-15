from worldcup_predictor import player_status as ps


def test_status_mult():
    assert ps.status_mult("key", "out") == 0.72
    assert ps.status_mult("fringe", "doubtful") == 0.98
    assert ps.status_mult("key", "available") == 1.0  # unknown pair => no effect
    assert ps.status_mult("nonsense", "out") == 1.0


def test_derive_credibility():
    assert ps.derive_credibility(1, official=False) == 0.50
    assert ps.derive_credibility(2, official=False) == 0.80
    assert ps.derive_credibility(1, official=True) == 0.95
