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
