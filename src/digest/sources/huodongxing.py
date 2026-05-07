"""活动行 (huodongxing.com) HTML scraper.

Searches a city subdomain for a keyword (and optional category) and yields
event-shaped FetchedItem records. We bake the parsed event date / location /
organizer into the item content so the downstream LLM classifier can extract
event_date without re-parsing relative-time strings.

Why HTML scrape (not API):
    huodongxing has no public listing API. The list page HTML structure has
    been stable for years (`div.search-tab-content-item-mesh` cards) and is
    cheap to parse with bs4 + lxml.

Politeness:
    Default `politeness_seconds=2` between cards is a no-op for now (we only
    fetch the list page, not detail pages), but the field is wired so a future
    detail-enrichment step has a knob to tune.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup, Tag

from ..fetchers.base import FetchedItem, FetchError
from ._chinese_dates import parse_chinese_relative_date

# Cloudflare-like protection rejects default httpx UA. Use a real Chrome UA.
BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)

# Default exclude keywords. Huodongxing AI search returns a lot of unrelated
# noise (相亲 / 移民 / 招生 / 资产局). Per-source config can override.
DEFAULT_EXCLUDE_KEYWORDS: tuple[str, ...] = (
    "相亲", "单身", "交友", "脱单",
    "移民", "身份", "海外身份", "拿身份",
    "资产", "理财", "投资课", "财富管理",
    "MBA", "EMBA", "招生", "考研",
    "K12", "幼小衔接",
)


@dataclass(frozen=True)
class HuodongxingConfig:
    """One huodongxing source config = one (city, keyword[, category]) tuple."""

    city: str                          # subdomain prefix: "sz", "bj", "sh", "gz", "hz"
    keyword: str                       # search keyword, e.g. "AI"
    category: str | None = None        # optional huodongxing category id, e.g. "22000700"
    max_items: int = 20                # cap on cards parsed per fetch
    timeout_seconds: float = 15.0
    politeness_seconds: float = 0.0    # delay between (future) detail fetches
    # Huodongxing's keyword search hits full-text including description/tags,
    # which lets unrelated activities (相亲/读书会/演讲口才...) match "AI".
    # Default to title-must-contain to keep precision high. Multi-keyword
    # source can pass match_keywords=("AI", "人工智能", ...) instead.
    require_keyword_in_title: bool = True
    match_keywords: tuple[str, ...] = ()  # if non-empty, ANY of these in title
    exclude_keywords: tuple[str, ...] = field(
        default_factory=lambda: DEFAULT_EXCLUDE_KEYWORDS
    )


class HuodongxingFetcher:
    """One fetch = one HTTP GET to the city's /events search endpoint."""

    def __init__(self, config: HuodongxingConfig) -> None:
        self.config = config

    def fetch(self) -> list[FetchedItem]:
        url = self._build_search_url()
        try:
            r = httpx.get(
                url,
                headers={
                    "user-agent": BROWSER_UA,
                    "accept": "text/html,application/xhtml+xml",
                    "accept-language": "zh-CN,zh;q=0.9",
                },
                timeout=self.config.timeout_seconds,
                follow_redirects=True,
            )
        except httpx.RequestError as e:
            raise FetchError(f"huodongxing network error: {e!r}") from e

        if r.status_code != 200:
            raise FetchError(
                f"huodongxing http {r.status_code}: {r.text[:200]}"
            )

        # `match_keywords` overrides the single keyword if provided; otherwise
        # require the search keyword to literally appear in the title.
        title_keywords: tuple[str, ...] = self.config.match_keywords or (
            (self.config.keyword,) if self.config.require_keyword_in_title else ()
        )
        items = parse_event_cards(
            r.text,
            today=date.today(),
            exclude_keywords=self.config.exclude_keywords,
            require_title_match=title_keywords,
            base_url=str(r.url),
            max_items=self.config.max_items,
        )

        # Politeness pause is a no-op until we add detail fetching, but keep
        # the hook for future use.
        if items and self.config.politeness_seconds > 0:
            time.sleep(self.config.politeness_seconds)

        return items

    def _build_search_url(self) -> str:
        base = f"https://{self.config.city}.huodongxing.com/events"
        params = ["orderby=newest"]
        if self.config.category:
            params.append(f"category={self.config.category}")
        if self.config.keyword:
            # huodongxing accepts ASCII keywords without explicit url-encoding
            # for common queries; let httpx handle the rest if user passes 中文.
            params.append(f"keyword={self.config.keyword}")
        return f"{base}?{'&'.join(params)}"


def parse_event_cards(
    html: str,
    *,
    today: date,
    exclude_keywords: tuple[str, ...] = DEFAULT_EXCLUDE_KEYWORDS,
    require_title_match: tuple[str, ...] = (),
    base_url: str = "https://www.huodongxing.com/",
    max_items: int = 20,
) -> list[FetchedItem]:
    """Parse a search-results HTML page into FetchedItem records.

    Pure function so tests can feed in fixture HTML.
    """
    soup = BeautifulSoup(html, "lxml")
    cards = soup.find_all("div", class_="search-tab-content-item-mesh")

    out: list[FetchedItem] = []
    seen_ids: set[str] = set()
    for card in cards:
        if len(out) >= max_items:
            break
        item = _card_to_item(card, today=today, base_url=base_url)
        if item is None:
            continue
        # Hard noise filter (相亲/移民/etc.)
        if exclude_keywords and any(kw in item.title for kw in exclude_keywords):
            continue
        # Optional: keep only titles literally mentioning at least one keyword
        # (used to combat huodongxing's loose full-text search).
        if require_title_match and not any(
            kw.lower() in item.title.lower() for kw in require_title_match
        ):
            continue
        # Dedup within this page
        eid = _extract_event_id(item.url)
        if eid is None or eid in seen_ids:
            continue
        seen_ids.add(eid)
        out.append(item)
    return out


def _card_to_item(card: Tag, *, today: date, base_url: str) -> FetchedItem | None:
    """Extract one event card.

    Returns None if the card is missing the essentials (link or title).
    """
    link = card.find("a", href=re.compile(r"^/event/\d+"))
    if link is None or not isinstance(link, Tag):
        return None
    href_raw = link.get("href")
    if not isinstance(href_raw, str):
        return None
    # Strip tracking params; canonical URL form is /event/<id>.
    href_clean = href_raw.split("?", 1)[0]
    full_url = urljoin(base_url, href_clean)

    # Title: huodongxing renders the title twice — once as alt-less text, once
    # inside an <img alt="..."> wrapper. Prefer the first non-empty text-only
    # anchor pointing at the same href.
    title = _extract_title(card, href_clean)
    if not title:
        return None

    full_text = card.get_text(" ", strip=True)
    parsed_date = parse_chinese_relative_date(full_text, today=today)
    location = _extract_location(full_text)
    organizer = _extract_organizer(card)

    content_lines: list[str] = []
    if parsed_date is not None:
        if parsed_date.end is not None and parsed_date.end != parsed_date.start:
            content_lines.append(
                f"📅 时间: {parsed_date.start.isoformat()} ~ {parsed_date.end.isoformat()}"
            )
        else:
            content_lines.append(f"📅 时间: {parsed_date.start.isoformat()}")
        # Keep the raw human-readable string too — helps the LLM reason about
        # exact start time when we only stored the date.
        content_lines.append(f"⏰ 原始时间表达: {parsed_date.raw}")
    if location:
        content_lines.append(f"📍 地点: {location}")
    if organizer:
        content_lines.append(f"👤 主办方: {organizer}")
    content = "\n".join(content_lines) if content_lines else None

    published_at: datetime | None = None
    if parsed_date is not None:
        # Use start date at noon as a stable proxy for "when this event happens".
        # downstream code can override via event_metadata.event_date later.
        published_at = datetime.combine(
            parsed_date.start, datetime.min.time()
        ).replace(hour=12, tzinfo=UTC)

    return FetchedItem(
        url=full_url,
        title=title,
        content=content,
        author=organizer,
        published_at=published_at,
    )


def _extract_title(card: Tag, href_clean: str) -> str | None:
    """Title is in a sibling/child anchor pointing at the same /event/<id>."""
    for a in card.find_all("a"):
        if not isinstance(a, Tag):
            continue
        h = a.get("href")
        if not isinstance(h, str) or h.split("?", 1)[0] != href_clean:
            continue
        # Skip wrapper that just contains an <img> with no useful text
        text = a.get_text(strip=True)
        if text:
            return text
    return None


_LOCATION_PATTERN = re.compile(
    r"(广东深圳|上海|北京|广州|深圳|杭州|成都|南京|武汉|苏州|西安|"
    r"重庆|天津|长沙|青岛|大连|宁波|无锡|郑州|福州|厦门|济南|"
    r"沈阳|哈尔滨|长春|合肥|南昌|昆明|贵阳|石家庄|太原|兰州|"
    r"乌鲁木齐|银川|西宁|呼和浩特|拉萨|海口|香港|澳门|台北|"
    r"线上|全国|线上活动)"
)


def _extract_location(full_text: str) -> str | None:
    m = _LOCATION_PATTERN.search(full_text)
    return m.group(1) if m else None


def _extract_organizer(card: Tag) -> str | None:
    """Organizer link points to /org/<id>, with text being the org name.

    Cards render the org block twice (icon-only link, then name+stats). We
    iterate links and pick the shortest non-empty text — that's the bare name
    without the trailing "粉丝 N" / "活动 N" / "+关注" stats.
    """
    org_links = card.find_all("a", href=re.compile(r"^/org/\d+"))
    candidates: list[str] = []
    for a in org_links:
        if not isinstance(a, Tag):
            continue
        text = a.get_text(strip=True)
        if text:
            # Strip trailing stats blob if present (defensive).
            text = re.split(r"\s*粉丝\s+", text, maxsplit=1)[0]
            text = re.split(r"\s*活动\s+\d", text, maxsplit=1)[0]
            text = text.strip()
            if text:
                candidates.append(text)
    if not candidates:
        return None
    # Shortest = least padded with stats text
    return min(candidates, key=len)


def _extract_event_id(url: str) -> str | None:
    m = re.search(r"/event/(\d+)", url)
    return m.group(1) if m else None
