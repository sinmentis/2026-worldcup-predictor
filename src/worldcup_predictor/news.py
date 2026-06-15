from __future__ import annotations

import sqlite3
import time
from typing import Any

import feedparser  # type: ignore[import-untyped]

from worldcup_predictor import config


def parse_feed_text(source: str, text: str) -> list[dict[str, Any]]:
    parsed = feedparser.parse(text)
    items: list[dict[str, Any]] = []
    for e in parsed.entries:
        url = e.get("link")
        if not url:
            continue
        items.append(
            {
                "source": source,
                "url": url,
                "title": e.get("title", ""),
                "summary": e.get("summary", e.get("description", "")),
                "published_at": e.get("published", ""),
            }
        )
    return items


def store_articles(conn: sqlite3.Connection, items: list[dict[str, Any]]) -> int:
    count = 0
    for it in items:
        cur = conn.execute(
            "INSERT OR IGNORE INTO news_articles"
            "(source, url, title, summary, published_at, fetched_at, processed)"
            " VALUES (?,?,?,?,?,?,0)",
            (it["source"], it["url"], it["title"], it["summary"], it["published_at"], time.time()),
        )
        count += cur.rowcount
    conn.commit()
    return count


def fetch_news(conn: sqlite3.Connection) -> int:
    """Fetch all configured RSS feeds and store new articles. Per-feed failures are skipped."""
    import httpx

    total = 0
    for source, url in config.RSS_FEEDS.items():
        try:
            resp = httpx.get(url, timeout=30.0, follow_redirects=True)
            resp.raise_for_status()
            total += store_articles(conn, parse_feed_text(source, resp.text))
        except Exception:  # noqa: BLE001, RUF100 - one bad feed must not break the run
            continue
    return total
