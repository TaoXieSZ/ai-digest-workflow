"""Unit tests for src/digest/sources/newsnow.py.

httpx is mocked; no live API calls in tests.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import patch

import httpx
import pytest

from digest.fetchers.base import FetchError
from digest.sources.newsnow import (
    NewsNowConfig,
    NewsNowFetcher,
    _parse_date,
    _to_item,
)


def _ok(json_body: dict) -> httpx.Response:
    return httpx.Response(200, json=json_body)


# ---------- _to_item ----------


def test_to_item_basic() -> None:
    out = _to_item(
        {"id": "x", "title": "T", "url": "https://e/x", "extra": {"date": 1700000000000}}
    )
    assert out is not None
    assert out.url == "https://e/x"
    assert out.title == "T"
    assert out.content is None  # newsnow has no body
    assert out.published_at is not None


def test_to_item_drops_missing_url() -> None:
    assert _to_item({"title": "T", "url": ""}) is None


def test_to_item_drops_missing_title() -> None:
    assert _to_item({"title": "", "url": "https://e/x"}) is None


def test_to_item_drops_non_dict() -> None:
    assert _to_item("not a dict") is None
    assert _to_item(None) is None


# ---------- _parse_date ----------


def test_parse_date_unix_ms() -> None:
    out = _parse_date({"extra": {"date": 1700000000000}})
    assert out is not None
    assert out.year == 2023
    assert out.tzinfo is UTC


def test_parse_date_iso_string() -> None:
    out = _parse_date({"extra": {"date": "2026-05-06T04:28:16+00:00"}})
    assert out == datetime(2026, 5, 6, 4, 28, 16, tzinfo=UTC)


def test_parse_date_pubdate_cn_local() -> None:
    """`pubDate: "YYYY-MM-DD HH:MM:SS"` is naive — we assume CN local time."""
    out = _parse_date({"pubDate": "2026-05-06 12:00:00"})
    assert out is not None
    # CN noon → UTC 04:00
    assert out.hour == 4
    assert out.tzinfo is UTC


def test_parse_date_no_signal() -> None:
    assert _parse_date({}) is None
    assert _parse_date({"extra": {}}) is None
    assert _parse_date({"pubDate": "garbage"}) is None


# ---------- NewsNowFetcher ----------


def test_fetcher_happy_path() -> None:
    body = {
        "items": [
            {"id": "1", "title": "A", "url": "https://e/a", "extra": {"date": 1700000000000}},
            {"id": "2", "title": "B", "url": "https://e/b"},
        ]
    }
    cfg = NewsNowConfig(source_id="v2ex")
    with patch("digest.sources.newsnow.httpx.get", return_value=_ok(body)) as mock_get:
        items = NewsNowFetcher(cfg).fetch()
    assert len(items) == 2
    assert items[0].title == "A"
    assert items[1].published_at is None
    # Verify we sent browser UA + referer (cloudflare 403s default httpx UA).
    _, kwargs = mock_get.call_args
    headers = kwargs["headers"]
    assert "Mozilla/5.0" in headers["user-agent"]
    assert "referer" in headers


def test_fetcher_url_includes_source_id() -> None:
    cfg = NewsNowConfig(source_id="36kr", base_url="https://example.com/")
    with patch("digest.sources.newsnow.httpx.get", return_value=_ok({"items": []})) as mock_get:
        NewsNowFetcher(cfg).fetch()
    args, _ = mock_get.call_args
    assert args[0] == "https://example.com/api/s?id=36kr"  # trailing slash stripped


def test_fetcher_respects_max_items() -> None:
    body = {"items": [{"title": f"T{i}", "url": f"https://e/{i}"} for i in range(50)]}
    cfg = NewsNowConfig(source_id="v2ex", max_items=10)
    with patch("digest.sources.newsnow.httpx.get", return_value=_ok(body)):
        items = NewsNowFetcher(cfg).fetch()
    assert len(items) == 10


def test_fetcher_drops_invalid_items_silently() -> None:
    body = {"items": [
        {"title": "good", "url": "https://e/g"},
        "not a dict",
        {"title": "no url"},
        {"url": "https://e/no-title"},
        42,
    ]}
    with patch("digest.sources.newsnow.httpx.get", return_value=_ok(body)):
        items = NewsNowFetcher(NewsNowConfig(source_id="v2ex")).fetch()
    assert len(items) == 1


def test_fetcher_raises_on_403() -> None:
    """Cloudflare 403 happens when running from a blocked region/UA."""
    with patch(
        "digest.sources.newsnow.httpx.get",
        return_value=httpx.Response(403, text="Forbidden"),
    ):
        with pytest.raises(FetchError, match="http 403"):
            NewsNowFetcher(NewsNowConfig(source_id="v2ex")).fetch()


def test_fetcher_raises_on_5xx() -> None:
    with patch(
        "digest.sources.newsnow.httpx.get",
        return_value=httpx.Response(500, text="boom"),
    ):
        with pytest.raises(FetchError, match="http 500"):
            NewsNowFetcher(NewsNowConfig(source_id="v2ex")).fetch()


def test_fetcher_raises_on_network_error() -> None:
    with patch(
        "digest.sources.newsnow.httpx.get",
        side_effect=httpx.ConnectError("dns fail"),
    ):
        with pytest.raises(FetchError, match="network error"):
            NewsNowFetcher(NewsNowConfig(source_id="v2ex")).fetch()


def test_fetcher_raises_on_non_json() -> None:
    with patch(
        "digest.sources.newsnow.httpx.get",
        return_value=httpx.Response(200, text="<html>"),
    ):
        with pytest.raises(FetchError, match="non-json"):
            NewsNowFetcher(NewsNowConfig(source_id="v2ex")).fetch()


def test_fetcher_raises_on_missing_items_key() -> None:
    with patch(
        "digest.sources.newsnow.httpx.get", return_value=_ok({"data": []}),
    ):
        with pytest.raises(FetchError, match="missing 'items'"):
            NewsNowFetcher(NewsNowConfig(source_id="v2ex")).fetch()
