"""Tests for huodongxing HTML scraper."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from digest.sources.huodongxing import (
    DEFAULT_EXCLUDE_KEYWORDS,
    HuodongxingConfig,
    HuodongxingFetcher,
    parse_event_cards,
)

FIXTURE = Path(__file__).parent / "fixtures" / "huodongxing_sz_ai.html"
TODAY = date(2026, 5, 7)


@pytest.fixture
def fixture_html() -> str:
    return FIXTURE.read_text()


def test_parse_returns_event_items(fixture_html: str) -> None:
    items = parse_event_cards(fixture_html, today=TODAY)
    assert len(items) > 0
    # Every item has the canonical /event/<id> URL form
    for item in items:
        assert item.url.startswith("https://www.huodongxing.com/event/")
        assert item.title


def test_parse_strips_tracking_query_params(fixture_html: str) -> None:
    items = parse_event_cards(fixture_html, today=TODAY)
    for item in items:
        assert "?" not in item.url, f"tracking params leaked: {item.url}"


def test_parse_dedups_within_page(fixture_html: str) -> None:
    items = parse_event_cards(fixture_html, today=TODAY)
    urls = [it.url for it in items]
    assert len(urls) == len(set(urls)), "duplicate event URLs in output"


def test_parse_max_items_caps_output(fixture_html: str) -> None:
    items = parse_event_cards(fixture_html, today=TODAY, max_items=3)
    assert len(items) == 3


def test_default_exclude_filters_known_noise(fixture_html: str) -> None:
    items = parse_event_cards(fixture_html, today=TODAY)
    titles = " ".join(it.title for it in items)
    # Defaults must drop the most common spam categories
    for noise in ("相亲", "单身"):
        assert noise not in titles


def test_title_keyword_filter_keeps_only_matching(fixture_html: str) -> None:
    items = parse_event_cards(
        fixture_html,
        today=TODAY,
        require_title_match=("AI", "人工智能"),
    )
    assert len(items) >= 1, "fixture should contain at least one AI event"
    for item in items:
        assert "AI" in item.title or "人工智能" in item.title


def test_title_keyword_filter_case_insensitive(fixture_html: str) -> None:
    items = parse_event_cards(
        fixture_html,
        today=TODAY,
        require_title_match=("ai",),
    )
    assert len(items) >= 1


def test_event_with_md_date_is_resolved(fixture_html: str) -> None:
    """The fixture has '解码AI黄金赛道...' on '05/24 周日 14:00' — must resolve."""
    items = parse_event_cards(fixture_html, today=TODAY, require_title_match=("AI",))
    target = next((it for it in items if "05" in it.url[-13:] or "AI黄金赛道" in it.title), None)
    target = next(it for it in items if "AI黄金赛道" in it.title)
    assert target.published_at is not None
    assert target.published_at.date() == date(2026, 5, 24)


def test_event_with_relative_date_is_resolved(fixture_html: str) -> None:
    """'明天 14:00' on AI 脉诊康养 should resolve to today+1."""
    items = parse_event_cards(fixture_html, today=TODAY, require_title_match=("AI",))
    target = next(it for it in items if "脉诊" in it.title)
    assert target.published_at is not None
    assert target.published_at.date() == date(2026, 5, 8)


def test_content_includes_date_location_organizer(fixture_html: str) -> None:
    items = parse_event_cards(fixture_html, today=TODAY, require_title_match=("AI",))
    target = next(it for it in items if "脉诊" in it.title)
    assert target.content is not None
    assert "📅 时间:" in target.content
    assert "📍 地点:" in target.content
    assert "👤 主办方:" in target.content


def test_organizer_strips_follower_stats(fixture_html: str) -> None:
    """organizer text 应该是干净的组织名，不带'粉丝 X'尾巴。"""
    items = parse_event_cards(fixture_html, today=TODAY, require_title_match=("AI",))
    for item in items:
        assert item.author is not None
        assert "粉丝" not in item.author
        assert "+关注" not in item.author


def test_empty_html_returns_empty_list() -> None:
    assert parse_event_cards("<html><body></body></html>", today=TODAY) == []


def test_malformed_html_does_not_raise() -> None:
    # bs4 is lenient; this should just return [].
    items = parse_event_cards("<div><a>boom", today=TODAY)
    assert items == []


def test_fetcher_builds_correct_search_url() -> None:
    cfg = HuodongxingConfig(city="sz", keyword="AI", category="22000700")
    f = HuodongxingFetcher(cfg)
    url = f._build_search_url()
    assert url.startswith("https://sz.huodongxing.com/events?")
    assert "orderby=newest" in url
    assert "category=22000700" in url
    assert "keyword=AI" in url


def test_fetcher_omits_optional_category() -> None:
    cfg = HuodongxingConfig(city="bj", keyword="LLM")
    url = HuodongxingFetcher(cfg)._build_search_url()
    assert "category=" not in url
    assert url.endswith("keyword=LLM")


def test_fetcher_default_exclude_includes_common_noise() -> None:
    """Sanity: hardening doesn't accidentally drop the noise blacklist."""
    cfg = HuodongxingConfig(city="sz", keyword="AI")
    assert "相亲" in cfg.exclude_keywords
    assert "移民" in cfg.exclude_keywords
    # And the module-level constant matches
    assert "相亲" in DEFAULT_EXCLUDE_KEYWORDS


def test_fetcher_propagates_match_keywords(
    fixture_html: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If config.match_keywords is set, parser receives that as require_title_match."""
    captured: dict[str, object] = {}

    def fake_parse(html: str, **kwargs: object) -> list:  # type: ignore[type-arg]
        captured.update(kwargs)
        return []

    monkeypatch.setattr("digest.sources.huodongxing.parse_event_cards", fake_parse)

    class FakeResp:
        status_code = 200
        text = "<html></html>"
        url = "https://sz.huodongxing.com/events"

    def fake_get(*_a: object, **_k: object) -> FakeResp:
        return FakeResp()

    monkeypatch.setattr("digest.sources.huodongxing.httpx.get", fake_get)

    cfg = HuodongxingConfig(
        city="sz",
        keyword="AI",
        match_keywords=("AI", "人工智能", "AGI"),
    )
    HuodongxingFetcher(cfg).fetch()
    assert captured["require_title_match"] == ("AI", "人工智能", "AGI")


def test_fetcher_falls_back_to_keyword_when_match_keywords_empty(
    fixture_html: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, object] = {}

    def fake_parse(html: str, **kwargs: object) -> list:  # type: ignore[type-arg]
        captured.update(kwargs)
        return []

    monkeypatch.setattr("digest.sources.huodongxing.parse_event_cards", fake_parse)

    class FakeResp:
        status_code = 200
        text = "<html></html>"
        url = "https://sz.huodongxing.com/events"

    monkeypatch.setattr(
        "digest.sources.huodongxing.httpx.get",
        lambda *_a, **_k: FakeResp(),
    )

    cfg = HuodongxingConfig(city="sz", keyword="AI")
    HuodongxingFetcher(cfg).fetch()
    assert captured["require_title_match"] == ("AI",)


def test_fetcher_disable_title_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_parse(html: str, **kwargs: object) -> list:  # type: ignore[type-arg]
        captured.update(kwargs)
        return []

    monkeypatch.setattr("digest.sources.huodongxing.parse_event_cards", fake_parse)

    class FakeResp:
        status_code = 200
        text = "<html></html>"
        url = "https://sz.huodongxing.com/events"

    monkeypatch.setattr(
        "digest.sources.huodongxing.httpx.get",
        lambda *_a, **_k: FakeResp(),
    )

    cfg = HuodongxingConfig(city="sz", keyword="AI", require_keyword_in_title=False)
    HuodongxingFetcher(cfg).fetch()
    assert captured["require_title_match"] == ()
