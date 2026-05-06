from datetime import UTC, datetime
from unittest.mock import patch

import feedparser

from digest.fetchers.base import FetchError
from digest.fetchers.rss import RSSConfig, RSSFetcher, _strip_html

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


def test_strip_html_drops_style_block() -> None:
    raw = '<style>.x{color:red}</style>Hello world'
    assert _strip_html(raw) == "Hello world"


def test_strip_html_drops_script_block() -> None:
    raw = "<script>alert(1)</script>Body text"
    assert _strip_html(raw) == "Body text"


def test_strip_html_strips_tags_and_decodes_entities() -> None:
    raw = "<p>A &amp; B &nbsp; C</p>"
    out = _strip_html(raw)
    assert "<" not in out
    assert "&amp;" not in out
    assert "A & B" in out


def test_strip_html_unwraps_cdata() -> None:
    raw = "<![CDATA[<style>.x{}</style>real text]]>"
    assert _strip_html(raw) == "real text"


def test_strip_html_collapses_whitespace() -> None:
    raw = "  one\n\ntwo\t\tthree   "
    assert _strip_html(raw) == "one two three"


def test_strip_html_empty_input() -> None:
    assert _strip_html("") == ""
    assert _strip_html(None) is None  # type: ignore[arg-type]


def test_strip_html_real_wewe_rss_sample() -> None:
    """Mimics the wewe-rss output that polluted classifier snippets:
    CDATA → style block → real article. After strip, only article body remains.
    """
    raw = (
        "<![CDATA[<style>.rich_media_content{font-size:18px;color:#222;}</style>"
        "<p>实测 Claude Opus 4.7，详细对比一下…</p>]]>"
    )
    out = _strip_html(raw)
    assert out.startswith("实测 Claude Opus 4.7")
    assert "rich_media_content" not in out
    assert "<" not in out


def test_rss_fetcher_strips_html_in_extracted_content() -> None:
    """End-to-end: feedparser → _entry_to_item → content stripped."""
    rss = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
<channel><title>x</title><link>x</link><description>x</description>
<item>
  <title>Article</title>
  <link>https://e.com/1</link>
  <description><![CDATA[<style>.x{color:red}</style><p>Real body content</p>]]></description>
</item>
</channel></rss>"""
    parsed = feedparser.parse(rss)
    with patch("digest.fetchers.rss.feedparser.parse", return_value=parsed):
        items = RSSFetcher(RSSConfig(url="x")).fetch()
    assert items[0].content == "Real body content"


def test_rss_fetcher_tolerates_bozo_with_entries() -> None:
    """Many CN feeds emit encoding warnings (bozo=1) but still parse usable entries."""
    parsed = feedparser.parse(SAMPLE_RSS)
    parsed["bozo"] = 1  # simulate warning
    parsed["bozo_exception"] = Exception("encoding warning")

    with patch("digest.fetchers.rss.feedparser.parse", return_value=parsed):
        items = RSSFetcher(RSSConfig(url="x")).fetch()
    assert len(items) == 2  # still returns entries
