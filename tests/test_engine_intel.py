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
