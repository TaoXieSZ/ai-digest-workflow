"""newsnow API fetcher.

Wraps the public/self-hosted instance of [ourongxing/newsnow]. Each newsnow
"source" id (e.g. "v2ex", "36kr", "juejin") gets one ai-digest source entry.

API shape (verified against https://newsnow.busiyi.world):
    GET /api/s?id=<source_id>
    →  { "items": [ { "id", "title", "url",
                      "extra": {"date": <iso str | unix ms>},
                      "pubDate"?: "YYYY-MM-DD HH:MM:SS" }, ... ] }

Auth: none for public instance. Browser-like UA required (the host blocks
default httpx UA with 403). Self-hosted instances may not need this.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from ..fetchers.base import FetchedItem, FetchError

DEFAULT_BASE = "https://newsnow.busiyi.world"
# Cloudflare-like edges 403 the default httpx UA. Use a plausible Chrome UA.
BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)
CN_TZ = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True)
class NewsNowConfig:
    source_id: str  # newsnow internal id (e.g. "v2ex", "ithome")
    base_url: str = DEFAULT_BASE
    max_items: int = 30
    timeout_seconds: float = 15.0


class NewsNowFetcher:
    """One fetch = one HTTP call to newsnow's /api/s endpoint."""

    def __init__(self, config: NewsNowConfig) -> None:
        self.config = config

    def fetch(self) -> list[FetchedItem]:
        url = f"{self.config.base_url.rstrip('/')}/api/s?id={self.config.source_id}"
        headers = {
            "user-agent": BROWSER_UA,
            "accept": "application/json,text/plain,*/*",
            "accept-language": "zh-CN,zh;q=0.9",
            "referer": self.config.base_url.rstrip("/") + "/",
        }
        try:
            r = httpx.get(url, headers=headers, timeout=self.config.timeout_seconds)
        except httpx.RequestError as e:
            raise FetchError(f"newsnow network error: {e!r}") from e

        if r.status_code != 200:
            raise FetchError(
                f"newsnow http {r.status_code}: {r.text[:200]}"
            )
        try:
            body = r.json()
        except ValueError as e:
            raise FetchError(f"newsnow non-json: {r.text[:200]}") from e

        items_raw = body.get("items") if isinstance(body, dict) else None
        if not isinstance(items_raw, list):
            raise FetchError(
                f"newsnow shape: missing 'items' list (got {type(body).__name__})"
            )

        out: list[FetchedItem] = []
        for it in items_raw[: self.config.max_items]:
            fi = _to_item(it)
            if fi is not None:
                out.append(fi)
        return out


def _to_item(it: Any) -> FetchedItem | None:
    if not isinstance(it, dict):
        return None
    url = it.get("url")
    title = it.get("title")
    if not isinstance(url, str) or not url:
        return None
    if not isinstance(title, str) or not title:
        return None
    return FetchedItem(
        url=url,
        title=title,
        content=None,  # newsnow only gives titles + urls; no body
        author=None,
        published_at=_parse_date(it),
    )


def _parse_date(it: dict[str, Any]) -> datetime | None:
    """Try the three date shapes newsnow emits, in order of reliability."""
    extra_raw = it.get("extra")
    extra: dict[str, Any] = extra_raw if isinstance(extra_raw, dict) else {}
    extra_date = extra.get("date")

    # Shape 1: unix ms timestamp (e.g. 1778041127603)
    if isinstance(extra_date, (int, float)):
        try:
            return datetime.fromtimestamp(extra_date / 1000, tz=UTC)
        except (OverflowError, OSError, ValueError):
            pass

    # Shape 2: ISO 8601 string (e.g. "2026-05-06T04:28:16+00:00")
    if isinstance(extra_date, str):
        try:
            return datetime.fromisoformat(extra_date)
        except ValueError:
            pass

    # Shape 3: top-level pubDate "YYYY-MM-DD HH:MM:SS" — naive, assume CN local.
    pub = it.get("pubDate")
    if isinstance(pub, str):
        try:
            naive = datetime.strptime(pub, "%Y-%m-%d %H:%M:%S")
            return naive.replace(tzinfo=CN_TZ).astimezone(UTC)
        except ValueError:
            pass

    return None
