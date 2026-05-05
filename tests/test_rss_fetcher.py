from datetime import UTC, datetime
from unittest.mock import patch

import feedparser

from digest.fetchers.base import FetchError
from digest.fetchers.rss import RSSConfig, RSSFetcher

SAMPLE_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
<channel>
<title>linux.do test</title>
<link>https://linux.do</link>
<description>x</description>
<item>
  <title>Hello AI</title>
  <link>https://linux.do/t/100</link>
  <description>some body</description>
  <author>alice</author>
  <pubDate>Mon, 04 May 2026 12:00:00 GMT</pubDate>
</item>
<item>
  <title>Second</title>
  <link>https://linux.do/t/101</link>
  <description>body 2</description>
</item>
</channel>
</rss>
"""


def test_rss_fetcher_parses_entries() -> None:
    parsed = feedparser.parse(SAMPLE_RSS)
    with patch("digest.fetchers.rss.feedparser.parse", return_value=parsed):
        items = RSSFetcher(RSSConfig(url="https://example.com/rss")).fetch()

    assert len(items) == 2
    assert items[0].url == "https://linux.do/t/100"
    assert items[0].title == "Hello AI"
    assert items[0].author == "alice"
    assert items[0].published_at == datetime(2026, 5, 4, 12, 0, tzinfo=UTC)
    assert items[1].url == "https://linux.do/t/101"
    assert items[1].published_at is None


def test_rss_fetcher_respects_max_items() -> None:
    parsed = feedparser.parse(SAMPLE_RSS)
    with patch("digest.fetchers.rss.feedparser.parse", return_value=parsed):
        items = RSSFetcher(RSSConfig(url="x", max_items=1)).fetch()
    assert len(items) == 1


def test_rss_fetcher_raises_on_total_failure() -> None:
    fake = feedparser.FeedParserDict()
    fake["bozo"] = 1
    fake["bozo_exception"] = Exception("connection refused")
    fake["entries"] = []

    with patch("digest.fetchers.rss.feedparser.parse", return_value=fake):
        try:
            RSSFetcher(RSSConfig(url="x")).fetch()
        except FetchError as e:
            assert "connection refused" in str(e)
        else:
            raise AssertionError("expected FetchError")


def test_rss_fetcher_tolerates_bozo_with_entries() -> None:
    """Many CN feeds emit encoding warnings (bozo=1) but still parse usable entries."""
    parsed = feedparser.parse(SAMPLE_RSS)
    parsed["bozo"] = 1  # simulate warning
    parsed["bozo_exception"] = Exception("encoding warning")

    with patch("digest.fetchers.rss.feedparser.parse", return_value=parsed):
        items = RSSFetcher(RSSConfig(url="x")).fetch()
    assert len(items) == 2  # still returns entries
