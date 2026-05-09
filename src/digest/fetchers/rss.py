"""Generic RSS fetcher (feedparser-backed)."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import feedparser

from .base import FetchedItem, FetchError

# CDATA may not be unwrapped by feedparser when nested; handle defensively.
_CDATA_RE = re.compile(r"<!\[CDATA\[(.*?)\]\]>", re.DOTALL)
# Drop entire <style>/<script> blocks (with their contents) before tag stripping —
# wewe-rss articles ship inline CSS that otherwise dominates the text.
_BLOCK_TAGS_RE = re.compile(r"<(style|script)[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class RSSConfig:
    url: str
    max_items: int = 50
    user_agent: str = "ai-digest/0.1 (+https://github.com/txie)"


class RSSFetcher:
    """Pulls a feed, normalizes entries to FetchedItem.

    Failure semantics: raises FetchError only when feedparser can't produce ANY entries
    AND signals a bozo (parse) error. Partial bozo (e.g. encoding warnings) with
    non-empty entries is treated as success — many CN feeds emit warnings but parse fine.
    """

    def __init__(self, config: RSSConfig) -> None:
        self.config = config

    def fetch(self) -> list[FetchedItem]:
        feed = feedparser.parse(
            self.config.url,
            agent=self.config.user_agent,
        )
        if not feed.entries and bool(getattr(feed, "bozo", False)):
            raise FetchError(f"feed parse failed: {feed.get('bozo_exception')!r}")

        items: list[FetchedItem] = []
        for entry in feed.entries[: self.config.max_items]:
            items.append(_entry_to_item(entry))
        return items


def _entry_to_item(entry: Any) -> FetchedItem:
    url = getattr(entry, "link", "") or ""
    title = getattr(entry, "title", "") or ""
    content = _extract_content(entry)
    author = getattr(entry, "author", None)
    published_at = _parse_published(entry)
    return FetchedItem(
        url=url,
        title=title,
        content=content,
        author=author,
        published_at=published_at,
    )


def _extract_content(entry: Any) -> str | None:
    # Prefer full content[0].value, fall back to summary/description.
    content = getattr(entry, "content", None)
    raw: str | None = None
    if content:
        try:
            raw = str(content[0].value)
        except (AttributeError, IndexError):
            raw = None
    if raw is None:
        raw = getattr(entry, "summary", None) or getattr(entry, "description", None)
    if not raw:
        return raw
    return _strip_html(raw)


def _strip_html(s: str) -> str:
    """Best-effort HTML→plain-text for classifier/cluster snippets.

    Order matters: unwrap CDATA → drop <style>/<script> blocks → drop other tags
    → decode entities → collapse whitespace. Doesn't preserve link URLs or list
    structure; the LLM only needs words for classification.
    """
    if not s:
        return s
    s = _CDATA_RE.sub(r"\1", s)
    s = _BLOCK_TAGS_RE.sub(" ", s)
    s = _TAG_RE.sub(" ", s)
    s = html.unescape(s)
    s = _WHITESPACE_RE.sub(" ", s).strip()
    return s


def _parse_published(entry: Any) -> datetime | None:
    parsed = getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed", None)
    if parsed is None:
        return None
    try:
        return datetime(
            parsed[0],
            parsed[1],
            parsed[2],
            parsed[3],
            parsed[4],
            parsed[5],
            tzinfo=UTC,
        )
    except (TypeError, ValueError, IndexError):
        return None
