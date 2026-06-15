from worldcup_predictor import config


def test_groups_complete():
    assert len(config.GROUPS) == 12
    assert sorted(config.GROUPS) == list("ABCDEFGHIJKL")
    for teams in config.GROUPS.values():
        assert len(teams) == 4
    all_teams = [t for ts in config.GROUPS.values() for t in ts]
    assert len(all_teams) == 48
    assert len(set(all_teams)) == 48
    assert "Argentina" in config.GROUPS["J"]


def test_k_table_has_world_cup():
    assert config.K_TABLE["world_cup"] == 60


def test_phase2a_config():
    from worldcup_predictor import config

    assert config.LAMBDA_MIN == 0.05
    assert config.ADJUST_CLAMP == (-0.6, 0.6)
    assert len(config.RSS_FEEDS) >= 3
    assert all(u.startswith("http") for u in config.RSS_FEEDS.values())
