from worldcup_predictor import db, engine, news

RSS = """<?xml version="1.0"?><rss version="2.0"><channel>
  <item><title>t1</title><description>d1</description><link>https://x/1</link></item>
</channel></rss>"""


def _conn(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.init_schema(conn)
    return conn


def test_get_and_mark_news(tmp_path):
    conn = _conn(tmp_path)
    news.store_articles(conn, news.parse_feed_text("BBC Sport", RSS))
    items = engine.get_unprocessed_news(conn, limit=10)
    assert len(items) == 1 and items[0]["url"] == "https://x/1"
    assert engine.mark_news_processed(conn, [items[0]["id"]]) == 1
    assert engine.get_unprocessed_news(conn, limit=10) == []


def test_upsert_and_pending_flow(tmp_path):
    conn = _conn(tmp_path)
    out = engine.upsert_player_status(
        conn,
        team="France",
        player="X",
        tier="key",
        status="out",
        confidence=0.9,
        source_url="https://a",
    )
    assert out["status"] == "pending"
    pend = engine.list_pending_intel(conn)
    assert len(pend) == 1
    engine.approve_intel(conn, pend[0]["id"])
    assert engine.list_pending_intel(conn) == []


def test_team_signal_pending_flow(tmp_path):
    conn = _conn(tmp_path)
    out = engine.upsert_team_signal(
        conn,
        team="Brazil",
        category="tactical",
        direction="strengthen",
        magnitude_tier="moderate",
        confidence=0.9,
        source_url="https://a",
    )
    assert out["status"] == "pending"
    pend = engine.list_pending_intel(conn)
    assert len(pend) == 1
    assert pend[0]["kind"] == "team"
    assert pend[0]["ref"].startswith("ts:")
    engine.approve_intel(conn, pend[0]["ref"])
    assert engine.list_pending_intel(conn) == []


def test_pending_list_unifies_both_kinds(tmp_path):
    conn = _conn(tmp_path)
    engine.upsert_player_status(
        conn,
        team="France",
        player="X",
        tier="key",
        status="out",
        confidence=0.9,
        source_url="https://a",
    )
    engine.upsert_team_signal(
        conn,
        team="Brazil",
        category="morale",
        direction="weaken",
        magnitude_tier="major",
        confidence=0.9,
        source_url="https://b",
    )
    pend = engine.list_pending_intel(conn)
    kinds = {p["kind"] for p in pend}
    assert kinds == {"player", "team"}
    # reject the team one by its ref; the player one survives
    team_ref = next(p["ref"] for p in pend if p["kind"] == "team")
    engine.reject_intel(conn, team_ref)
    remaining = engine.list_pending_intel(conn)
    assert len(remaining) == 1
    assert remaining[0]["kind"] == "player"


def test_team_signal_raises_win_prob(tmp_path):
    import numpy as np
    import pandas as pd

    from worldcup_predictor.goal_model import GoalModel
    from worldcup_predictor.predict import predict_match

    conn = _conn(tmp_path)
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
    history = pd.DataFrame(
        rows, columns=["date", "home_team", "away_team", "home_goals", "away_goals", "neutral"]
    )
    model = GoalModel().fit(history)
    base = predict_match(conn, model, "Strong", "Weak", match_id=None, neutral=True)
    engine.upsert_team_signal(
        conn,
        team="Strong",
        category="tactical",
        direction="strengthen",
        magnitude_tier="major",
        confidence=0.9,
        source_url="https://fed",
        official=True,
    )
    after = predict_match(conn, model, "Strong", "Weak", match_id=None, neutral=True)
    assert after.p_home > base.p_home
    assert any(f.team == "Strong" for f in after.factors)
