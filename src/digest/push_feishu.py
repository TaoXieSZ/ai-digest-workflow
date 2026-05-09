"""Feishu webhook push.

Two payload shapes:
- text: simple {msg_type: "text", content: {text: ...}}
- interactive: card with markdown element, used for the event radar batch.

We do NOT sign requests in PR-A; assume the bot uses URL-secret only.
Per spec: 23:00-07:00 silent window enforced upstream by event_radar (not here).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx

DEFAULT_TIMEOUT = 10.0


class FeishuPushError(Exception):
    """Raised when Feishu rejects the message or HTTP fails."""


@dataclass(frozen=True)
class EventCardItem:
    title: str
    source: str
    url: str
    event_date: str | None
    registration_deadline: str | None
    location: str | None
    registration_url: str | None
    # Non-URL contact (微信号 / 私信 / 扫码描述) for posts without a real http link.
    # Rendered as a plain "📩 ..." segment so feishu doesn't try to linkify.
    registration_contact: str | None = None
    # When the original post was published (RSS pubDate / XHS createTime).
    # Used both for display ("🕒 MM-DD") and as the sort key (most recent first).
    published_at: datetime | None = None


def render_event_batch_card(
    items: list[EventCardItem],
    *,
    digest_date: str,
    attempt: int = 1,
) -> dict[str, Any]:
    """Render a list of events into a single Feishu interactive card.

    `attempt` is the 1-indexed push count for this (date, kind) — second
    same-day push gets " #2" appended to header so chat threads stay distinct.
    """
    if not items:
        raise ValueError("empty event list — caller should skip pushing")

    # Compact rendering: one bullet line per event so 20+ events fit on screen.
    # Title is the link to the original post; metadata follows separated by " · ".
    md_lines: list[str] = []
    for it in items:
        parts: list[str] = []
        if it.published_at is not None:
            parts.append(f"🕒 {it.published_at.strftime('%m-%d')}")
        parts.append(f"[**{it.title}**]({it.url})")
        if it.event_date:
            parts.append(f"📆 {it.event_date}")
        if it.registration_deadline:
            parts.append(f"⏰ 截止 {it.registration_deadline}")
        if it.location:
            parts.append(f"📍 {it.location}")
        if it.registration_url:
            parts.append(f"[报名]({it.registration_url})")
        elif it.registration_contact:
            # Fallback: no real URL, but classifier captured a contact string.
            parts.append(f"📩 {it.registration_contact}")
        md_lines.append("- " + " · ".join(parts))

    md = "\n".join(md_lines)
    suffix = f" #{attempt}" if attempt > 1 else ""

    return {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {
                    "tag": "plain_text",
                    "content": (
                        f"📡 AI 事件雷达 · {digest_date}{suffix} · "
                        f"{len(items)} 个新事件"
                    ),
                },
                "template": "blue",
            },
            "elements": [
                {"tag": "markdown", "content": md},
            ],
        },
    }


def render_daily_digest_card(
    digest_md: str,
    *,
    digest_date: str,
    attempt: int = 1,
) -> dict[str, Any]:
    """Wrap a pre-rendered daily digest markdown into a Feishu interactive card.

    `attempt` is the 1-indexed push count for this date — second same-day push
    gets " #2" appended so chat threads don't all look identical.
    """
    if not digest_md.strip():
        raise ValueError("empty digest — caller should skip pushing")
    suffix = f" #{attempt}" if attempt > 1 else ""
    return {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {
                    "tag": "plain_text",
                    "content": f"📰 AI 资讯日报 · {digest_date}{suffix}",
                },
                "template": "green",
            },
            "elements": [{"tag": "markdown", "content": digest_md}],
        },
    }


def push_text(webhook_url: str, text: str, *, timeout: float = DEFAULT_TIMEOUT) -> None:
    """Send a plain-text message."""
    payload = {"msg_type": "text", "content": {"text": text}}
    _post(webhook_url, payload, timeout=timeout)


def push_card(
    webhook_url: str,
    card_payload: dict[str, Any],
    *,
    timeout: float = DEFAULT_TIMEOUT,
) -> None:
    """Send a pre-rendered interactive card payload."""
    _post(webhook_url, card_payload, timeout=timeout)


def _post(webhook_url: str, payload: dict[str, Any], *, timeout: float) -> None:
    try:
        response = httpx.post(webhook_url, json=payload, timeout=timeout)
    except httpx.RequestError as e:
        raise FeishuPushError(f"network error: {e!r}") from e

    if response.status_code != 200:
        raise FeishuPushError(
            f"feishu http {response.status_code}: {response.text[:200]}"
        )

    # Feishu returns {"StatusCode":0, "StatusMessage":"success"} (legacy)
    # or {"code":0, "msg":"success"} on newer endpoints. Either signals success.
    body = _parse_json_safe(response)
    code = body.get("StatusCode") if "StatusCode" in body else body.get("code")
    if code not in (0, None):
        raise FeishuPushError(f"feishu rejected: {body}")


def _parse_json_safe(response: httpx.Response) -> dict[str, Any]:
    try:
        data = response.json()
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}
