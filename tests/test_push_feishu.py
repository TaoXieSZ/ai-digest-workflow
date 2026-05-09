from datetime import UTC, datetime
from unittest.mock import patch

import httpx
import pytest

from digest.push_feishu import (
    EventCardItem,
    FeishuPushError,
    push_card,
    push_text,
    render_daily_digest_card,
    render_event_batch_card,
)


def _ok_response(json_body: dict | None = None) -> httpx.Response:
    body = json_body if json_body is not None else {"StatusCode": 0, "StatusMessage": "ok"}
    return httpx.Response(200, json=body)


def test_render_card_includes_title_meta_and_links() -> None:
    items = [
        EventCardItem(
            title="深圳 AI 黑客松招募",
            source="linux_do",
            url="https://linux.do/t/100",
            event_date="2026-06-15",
            registration_deadline="2026-06-10",
            location="深圳南山",
            registration_url="https://example.com/signup",
        )
    ]
    payload = render_event_batch_card(items, digest_date="2026-05-04")
    assert payload["msg_type"] == "interactive"
    md = payload["card"]["elements"][0]["content"]
    # Title rendered as link to the original post.
    assert "[**深圳 AI 黑客松招募**](https://linux.do/t/100)" in md
    assert "📆 2026-06-15" in md
    assert "⏰ 截止 2026-06-10" in md
    assert "📍 深圳南山" in md
    assert "[报名](https://example.com/signup)" in md
    # Compact: single bullet line, no separator/source clutter.
    assert md.startswith("- ")
    assert md.count("\n") == 0  # one event = one line


def test_render_card_handles_missing_fields() -> None:
    items = [
        EventCardItem(
            title="某活动",
            source="xhs",
            url="https://xhs.com/x",
            event_date=None,
            registration_deadline=None,
            location=None,
            registration_url=None,
        )
    ]
    payload = render_event_batch_card(items, digest_date="2026-05-04")
    md = payload["card"]["elements"][0]["content"]
    # When no metadata, line is just the linked title.
    assert md == "- [**某活动**](https://xhs.com/x)"
    assert "[报名]" not in md  # no registration url


def test_render_card_uses_contact_fallback_when_no_url() -> None:
    """No registration_url but contact present → render '📩 <contact>' instead."""
    items = [
        EventCardItem(
            title="XHS 私信报名活动",
            source="xhs_events",
            url="https://xhs.com/p/abc",
            event_date="2026-06-15",
            registration_deadline=None,
            location="线上",
            registration_url=None,
            registration_contact="私信博主",
        )
    ]
    md = render_event_batch_card(items, digest_date="2026-05-04")["card"]["elements"][0]["content"]
    assert "[报名]" not in md
    assert "📩 私信博主" in md


def test_render_card_prefers_url_over_contact() -> None:
    """When both url and contact are present, url wins (real link is more useful)."""
    items = [
        EventCardItem(
            title="活动",
            source="s",
            url="https://e/x",
            event_date=None,
            registration_deadline=None,
            location=None,
            registration_url="https://example.com/signup",
            registration_contact="私信博主",
        )
    ]
    md = render_event_batch_card(items, digest_date="2026-05-04")["card"]["elements"][0]["content"]
    assert "[报名](https://example.com/signup)" in md
    assert "📩 私信博主" not in md  # contact suppressed when url present


def test_render_event_card_appends_attempt_suffix() -> None:
    items = [
        EventCardItem(
            title="x",
            source="s",
            url="https://e/x",
            event_date=None,
            registration_deadline=None,
            location=None,
            registration_url=None,
        )
    ]
    payload1 = render_event_batch_card(items, digest_date="2026-05-06", attempt=1)
    payload2 = render_event_batch_card(items, digest_date="2026-05-06", attempt=2)
    title1 = payload1["card"]["header"]["title"]["content"]
    title2 = payload2["card"]["header"]["title"]["content"]
    assert " #" not in title1  # 1st push: no suffix
    assert " #2 " in title2 or " #2 ·" in title2  # 2nd push: gets #2


def test_render_daily_card_appends_attempt_suffix() -> None:
    p1 = render_daily_digest_card("- x", digest_date="2026-05-06")
    p2 = render_daily_digest_card("- x", digest_date="2026-05-06", attempt=3)
    assert "📰 AI 资讯日报 · 2026-05-06" == p1["card"]["header"]["title"]["content"]
    assert p2["card"]["header"]["title"]["content"].endswith("#3")


def test_render_card_includes_published_date_when_present() -> None:
    items = [
        EventCardItem(
            title="某活动",
            source="xhs",
            url="https://x.co/1",
            event_date=None,
            registration_deadline=None,
            location=None,
            registration_url=None,
            published_at=datetime(2026, 5, 4, 12, 30, tzinfo=UTC),
        )
    ]
    md = render_event_batch_card(items, digest_date="2026-05-04")["card"]["elements"][0]["content"]
    # Time marker prefixed before the title for top-to-bottom date scanning.
    assert md.startswith("- 🕒 05-04 · [**某活动**]")


def test_render_card_omits_time_marker_when_no_published_at() -> None:
    items = [
        EventCardItem(
            title="x",
            source="xhs",
            url="https://x.co/1",
            event_date=None,
            registration_deadline=None,
            location=None,
            registration_url=None,
            published_at=None,
        )
    ]
    md = render_event_batch_card(items, digest_date="2026-05-04")["card"]["elements"][0]["content"]
    assert "🕒" not in md


def test_render_card_separates_multiple_items() -> None:
    items = [
        EventCardItem(
            title=f"event-{i}",
            source="linux_do",
            url=f"https://e.com/{i}",
            event_date="2026-06-15",
            registration_deadline=None,
            location=None,
            registration_url=None,
        )
        for i in range(3)
    ]
    payload = render_event_batch_card(items, digest_date="2026-05-04")
    md = payload["card"]["elements"][0]["content"]
    # 3 bullet lines, no horizontal-rule separators.
    assert md.count("event-") == 3
    assert md.count("\n") == 2  # 3 lines = 2 newlines
    assert "---" not in md


def test_render_card_rejects_empty_list() -> None:
    with pytest.raises(ValueError):
        render_event_batch_card([], digest_date="2026-05-04")


def test_push_text_posts_correct_payload() -> None:
    with patch("digest.push_feishu.httpx.post") as mock_post:
        mock_post.return_value = _ok_response()
        push_text("https://feishu.example/webhook/abc", "hello")
    args, kwargs = mock_post.call_args
    assert args[0] == "https://feishu.example/webhook/abc"
    assert kwargs["json"] == {"msg_type": "text", "content": {"text": "hello"}}


def test_push_card_posts_payload_directly() -> None:
    payload = {"msg_type": "interactive", "card": {"foo": "bar"}}
    with patch("digest.push_feishu.httpx.post") as mock_post:
        mock_post.return_value = _ok_response()
        push_card("https://feishu.example/webhook/abc", payload)
    _, kwargs = mock_post.call_args
    assert kwargs["json"] == payload


def test_push_raises_on_http_error() -> None:
    with patch("digest.push_feishu.httpx.post") as mock_post:
        mock_post.return_value = httpx.Response(500, text="server error")
        with pytest.raises(FeishuPushError, match="http 500"):
            push_text("https://x", "hi")


def test_push_raises_on_feishu_business_error() -> None:
    with patch("digest.push_feishu.httpx.post") as mock_post:
        mock_post.return_value = _ok_response({"code": 9499, "msg": "invalid sign"})
        with pytest.raises(FeishuPushError, match="rejected"):
            push_text("https://x", "hi")


def test_push_accepts_modern_code_zero_response() -> None:
    with patch("digest.push_feishu.httpx.post") as mock_post:
        mock_post.return_value = _ok_response({"code": 0, "msg": "success"})
        push_text("https://x", "hi")  # should not raise


def test_push_raises_on_network_error() -> None:
    with patch("digest.push_feishu.httpx.post") as mock_post:
        mock_post.side_effect = httpx.ConnectError("dns fail")
        with pytest.raises(FeishuPushError, match="network error"):
            push_text("https://x", "hi")
