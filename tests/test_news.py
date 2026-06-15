from worldcup_predictor import db, news

RSS = """<?xml version="1.0"?>
<rss version="2.0"><channel>
  <item><title>France star ruled out of World Cup with injury</title>
    <description>Key forward will miss the tournament.</description>
    <link>https://example.com/a1</link><pubDate>Mon, 15 Jun 2026 10:00:00 GMT</pubDate></item>
  <item><title>Brazil name unchanged squad</title>
    <description>No changes.</description>
    <link>https://example.com/a2</link><pubDate>Mon, 15 Jun 2026 11:00:00 GMT</pubDate></item>
</channel></rss>"""


def test_parse_feed_text_returns_items():
    items = news.parse_feed_text("BBC Sport", RSS)
    assert len(items) == 2
    assert items[0]["title"].startswith("France star")
    assert items[0]["url"] == "https://example.com/a1"
    assert items[0]["source"] == "BBC Sport"


def test_store_articles_dedups_by_url(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.init_schema(conn)
    items = news.parse_feed_text("BBC Sport", RSS)
    assert news.store_articles(conn, items) == 2
    assert news.store_articles(conn, items) == 0  # same URLs => no duplicates
    assert conn.execute("SELECT COUNT(*) FROM news_articles").fetchone()[0] == 2
