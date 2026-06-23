from worldcup_predictor import db, intel
from worldcup_predictor.models import IntelEvent


def test_injury_weakens_team_lambda(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.init_schema(conn)
    intel.record_intel(
        conn,
        IntelEvent(
            team="France",
            player="Star Striker",
            event_type="injury",
            direction="weaken",
            magnitude=-0.30,
            source_url="https://example.com/news",
            credibility=1.0,
            notes="ruled out",
        ),
    )
    lh, la, factors = intel.apply_intel(2.0, 1.0, home="France", away="Iraq", conn=conn)
    assert lh < 2.0  # France weakened
    assert la == 1.0  # Iraq unchanged
    assert len(factors) == 1
    assert factors[0].team == "France"


def test_no_intel_is_noop(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.init_schema(conn)
    lh, la, factors = intel.apply_intel(1.5, 1.2, home="A", away="B", conn=conn)
    assert (lh, la) == (1.5, 1.2)
    assert factors == []


def test_credibility_scales_effect(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.init_schema(conn)
    intel.record_intel(
        conn,
        IntelEvent("A", "injury", "weaken", -0.40, "u", 0.5, player="x"),
    )
    lh, _, _ = intel.apply_intel(2.0, 1.0, home="A", away="B", conn=conn)
    assert lh == 2.0 * (1 + 0.5 * -0.40)


def test_adjust_clamp_bounds_delta_on_multiple_events(tmp_path):
    """Test that ADJUST_CLAMP bounds the combined delta when multiple events exceed it."""
    conn = db.connect(tmp_path / "t.db")
    db.init_schema(conn)
    # Two events for team A, each with magnitude -0.5 and credibility 1.0
    # Raw delta: -0.5 + -0.5 = -1.0, which would be clamped to -0.6
    intel.record_intel(
        conn,
        IntelEvent(
            team="A",
            event_type="injury",
            direction="weaken",
            magnitude=-0.5,
            source_url="u1",
            credibility=1.0,
            player="Player1",
        ),
    )
    intel.record_intel(
        conn,
        IntelEvent(
            team="A",
            event_type="injury",
            direction="weaken",
            magnitude=-0.5,
            source_url="u2",
            credibility=1.0,
            player="Player2",
        ),
    )
    # Apply with base lambda 2.0
    # Expected: delta clamped to -0.6, so adjusted lambda = 2.0 * (1 + (-0.6)) = 2.0 * 0.4 = 0.8
    lh, _, factors = intel.apply_intel(2.0, 1.0, home="A", away="B", conn=conn)
    assert lh == 0.8
    assert len(factors) == 2
    # Verify neither factor individually exceeds the clamp (each is -0.5)
    assert all(abs(f.lambda_delta) <= 0.6 for f in factors if f.team == "A")


def test_adjust_clamp_upper_bound_positive_delta(tmp_path):
    """Test that ADJUST_CLAMP upper bound (+0.6) clamps positive delta on multiple events."""
    conn = db.connect(tmp_path / "t.db")
    db.init_schema(conn)
    # Two strengthening events for team A, each with magnitude +0.5 and credibility 1.0
    # Raw delta: +0.5 + 0.5 = +1.0, which clamps to +0.6
    intel.record_intel(
        conn,
        IntelEvent(
            team="A",
            event_type="key_signing",
            direction="strengthen",
            magnitude=0.5,
            source_url="u1",
            credibility=1.0,
            player="NewStar",
        ),
    )
    intel.record_intel(
        conn,
        IntelEvent(
            team="A",
            event_type="tactical_adjustment",
            direction="strengthen",
            magnitude=0.5,
            source_url="u2",
            credibility=1.0,
            player="Coach",
        ),
    )
    # Apply with base home lambda 2.0
    # Expected: delta clamped to +0.6, so adjusted lambda = 2.0 * (1 + 0.6) = 2.0 * 1.6 = 3.2
    lh, _, factors = intel.apply_intel(2.0, 1.0, home="A", away="B", conn=conn)
    assert lh == 3.2
    assert len(factors) == 2
    # Verify each factor's raw delta is the individual contribution
    assert all(f.lambda_delta == 0.5 for f in factors if f.team == "A")


def test_lambda_min_floor_applied_after_clamped_adjustment(tmp_path):
    """Test that LAMBDA_MIN floor is applied after clamped delta adjustment."""
    conn = db.connect(tmp_path / "t.db")
    db.init_schema(conn)
    # Create weakening intel sufficient to clamp to -0.6
    intel.record_intel(
        conn,
        IntelEvent(
            team="A",
            event_type="injury",
            direction="weaken",
            magnitude=-0.6,
            source_url="u1",
            credibility=1.0,
            player="Midfielder",
        ),
    )
    # Apply with very small base lambda 0.1
    # Clamped delta is -0.6 (single event already at clamp)
    # Raw adjusted: 0.1 * (1 + (-0.6)) = 0.1 * 0.4 = 0.04
    # Final: max(0.05, 0.04) = 0.05
    lh, _, factors = intel.apply_intel(0.1, 1.0, home="A", away="B", conn=conn)
    assert lh == 0.05  # Floor applied
    assert len(factors) == 1
    assert factors[0].lambda_delta == -0.6


def test_direction_is_authoritative_over_magnitude_sign(tmp_path):
    # A caller passing a POSITIVE magnitude with direction="weaken" must still weaken.
    conn = db.connect(tmp_path / "t.db")
    db.init_schema(conn)
    intel.record_intel(conn, IntelEvent("A", "injury", "weaken", 0.40, "u", 1.0, player="x"))
    lh, _, factors = intel.apply_intel(2.0, 1.0, home="A", away="B", conn=conn)
    assert lh < 2.0
    assert factors[0].lambda_delta < 0


def test_apply_intel_includes_player_status(tmp_path):
    from worldcup_predictor import intel as intel_mod
    from worldcup_predictor import player_status

    conn = db.connect(tmp_path / "t.db")
    db.init_schema(conn)
    player_status.upsert_status(
        conn, "France", "Star", "key", "out", 0.9, "https://fed", official=True
    )
    lh, la, factors = intel_mod.apply_intel(2.0, 1.0, home="France", away="Iraq", conn=conn)
    assert lh < 2.0
    assert la == 1.0
    assert any(f.team == "France" for f in factors)


def test_apply_intel_includes_team_signal(tmp_path):
    from worldcup_predictor import intel as intel_mod
    from worldcup_predictor import team_signal

    conn = db.connect(tmp_path / "t.db")
    db.init_schema(conn)
    # A team-level strengthen signal must raise that team's lambda.
    team_signal.upsert_signal(
        conn, "Brazil", "tactical", "strengthen", "major", 0.9, "https://fed", official=True
    )
    lh, la, factors = intel_mod.apply_intel(2.0, 1.0, home="Brazil", away="Haiti", conn=conn)
    assert lh > 2.0
    assert la == 1.0
    assert any(f.team == "Brazil" and "tactical" in f.description for f in factors)


def test_apply_intel_sums_all_three_sources(tmp_path):
    # legacy intel_events + player_status + team_signal all stack onto one team's delta.
    from worldcup_predictor import intel as intel_mod
    from worldcup_predictor import player_status, team_signal

    conn = db.connect(tmp_path / "t.db")
    db.init_schema(conn)
    intel_mod.record_intel(
        conn, IntelEvent("Spain", "rotation", "weaken", -0.05, "u", 1.0, player="x")
    )
    player_status.upsert_status(
        conn, "Spain", "Keeper", "regular", "doubtful", 0.9, "https://fed", official=True
    )
    team_signal.upsert_signal(
        conn, "Spain", "fatigue", "weaken", "minor", 0.9, "https://fed", official=True
    )
    lh, _, factors = intel_mod.apply_intel(2.0, 1.0, home="Spain", away="Haiti", conn=conn)
    assert lh < 2.0
    teams_desc = {(f.team, f.description.split(":")[0]) for f in factors}
    assert ("Spain", "fatigue") in teams_desc  # team-signal factor present
    assert len(factors) == 3


def test_defense_signal_raises_opponent_not_self(tmp_path):
    from worldcup_predictor import player_status

    conn = db.connect(tmp_path / "t.db")
    db.init_schema(conn)
    # Key CB out, tagged defense: Germany's OWN lambda unchanged; opponent's lambda up.
    player_status.upsert_status(
        conn, "Germany", "CB", "key", "out", 0.9, "https://fed", official=True, affects="defense"
    )
    lh, la, factors = intel.apply_intel(2.0, 1.0, home="Germany", away="Ecuador", conn=conn)
    # Germany (home) attack unchanged:
    assert lh == 2.0
    # Ecuador (away) scores more: cred 0.95, key/out mult 0.72 -> def = -0.95*(0.72-1) = +0.266
    assert la == 1.0 * (1 + 0.95 * (1 - 0.72))
    assert any("defense" in f.description for f in factors)


def test_defense_strengthen_lowers_opponent(tmp_path):
    from worldcup_predictor import team_signal

    conn = db.connect(tmp_path / "t.db")
    db.init_schema(conn)
    # A strong defence (strengthen) tagged defense lowers the opponent's lambda.
    team_signal.upsert_signal(
        conn,
        "Italy",
        "tactical",
        "strengthen",
        "major",
        0.9,
        "https://fed",
        official=True,
        affects="defense",
    )
    lh, la, _ = intel.apply_intel(2.0, 1.0, home="France", away="Italy", conn=conn)
    # France (home) lambda is lowered by Italy's (away) defence: signal_mult strengthen/major = 1.06
    # base = 0.95*(1.06-1)=+0.057 ; def = -base = -0.057 -> lam_h *= (1 - 0.057)
    assert lh == 2.0 * (1 + (-(0.95 * (1.06 - 1.0))))
    assert la == 1.0  # Italy's own attack unchanged


def test_both_splits_half_each(tmp_path):
    from worldcup_predictor import player_status

    conn = db.connect(tmp_path / "t.db")
    db.init_schema(conn)
    # regular/out, affects=both: own attack -base/2, opponent +(-base)/2 (=+|base|/2)
    player_status.upsert_status(
        conn, "Ghana", "DM", "regular", "out", 0.9, "https://fed", official=True, affects="both"
    )
    lh, la, _ = intel.apply_intel(2.0, 1.0, home="Ghana", away="USA", conn=conn)
    base = 0.95 * (0.85 - 1.0)  # regular/out mult 0.85
    assert lh == 2.0 * (1 + 0.5 * base)  # Ghana own attack, half
    assert la == 1.0 * (1 + 0.5 * (-base))  # USA scores more, half


def test_attack_and_defense_compose_in_one_match(tmp_path):
    from worldcup_predictor import player_status, team_signal

    conn = db.connect(tmp_path / "t.db")
    db.init_schema(conn)
    # Home team has its OWN attacking loss AND the away team has a defensive loss.
    player_status.upsert_status(
        conn, "Spain", "ST", "key", "out", 0.9, "https://fed", official=True, affects="attack"
    )
    team_signal.upsert_signal(
        conn,
        "Qatar",
        "tactical",
        "weaken",
        "moderate",
        0.9,
        "https://fed",
        official=True,
        affects="defense",
    )
    lh, la, _ = intel.apply_intel(2.0, 1.0, home="Spain", away="Qatar", conn=conn)
    atk_spain = 0.95 * (0.72 - 1.0)  # key/out
    def_qatar = -(0.95 * (0.93 - 1.0))  # weaken/moderate signal_mult 0.93 -> def positive
    assert lh == 2.0 * (1 + atk_spain) * (1 + def_qatar)
    assert la == 1.0  # Qatar's own attack unchanged (no attack signal)
