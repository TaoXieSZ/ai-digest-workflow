"""Bridge to the installed `xiaohongshu` skill.

Calls the skill's bash scripts via subprocess. Requires:
1. `xiaohongshu-mcp` server running on default port 18060
   (start with `~/.claude/skills/xiaohongshu/scripts/start-mcp.sh`)
2. User has logged into XHS via the QR code flow once

If the MCP server is unreachable, fetch raises FetchError and run_fetch's
isolation kicks in — health score degrades, other sources still run.

PR-A status: usable but disabled by default. Enable in `config/sources.yaml`
after confirming MCP is running.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from ..fetchers.base import FetchedItem, FetchError


class XHSDetailCache(Protocol):
    """Cache surface for XHS post-detail responses, keyed by feed_id."""

    def get(self, feed_id: str) -> str | None: ...
    def put(
        self,
        *,
        feed_id: str,
        xsec_token: str,
        title: str | None,
        content: str | None,
    ) -> None: ...

log = logging.getLogger("xhs_bridge")

DEFAULT_SKILL_DIR = Path.home() / ".claude" / "skills" / "xiaohongshu" / "scripts"


@dataclass(frozen=True)
class XHSConfig:
    keywords: list[str]
    max_per_keyword: int = 10
    skill_scripts_dir: Path = DEFAULT_SKILL_DIR
    timeout_seconds: float = 60.0  # XHS search.sh empirically ~20s per keyword
    detail_timeout_seconds: float = 30.0  # post-detail.sh per-call cap
    mcp_url: str | None = None  # override MCP_URL env if set
    # When set, fetch full note content via post-detail.sh and cache by feed_id.
    # Detail fetch is fail-soft: a failure leaves the item's preview-text content intact.
    detail_cache: XHSDetailCache | None = None


@dataclass(frozen=True)
class XHSFetcher:
    config: XHSConfig = field(default_factory=lambda: XHSConfig(keywords=[]))

    def fetch(self) -> list[FetchedItem]:
        scripts = self.config.skill_scripts_dir
        search_sh = scripts / "search.sh"
        if not search_sh.exists():
            raise FetchError(f"xiaohongshu skill not found at {scripts}")

        env_overrides: dict[str, str] = {}
        if self.config.mcp_url:
            env_overrides["MCP_URL"] = self.config.mcp_url

        items: list[FetchedItem] = []
        seen_urls: set[str] = set()
        keyword_failures: list[str] = []

        for kw in self.config.keywords:
            try:
                feeds = self._search_one(search_sh, kw, env_overrides)
            except (FetchError, Exception) as e:
                # Per-keyword soft failure: timeouts, transient JSON parse errors,
                # one keyword being rate-limited shouldn't kill the others.
                log.warning("xhs search failed for %r: %r", kw, e)
                keyword_failures.append(kw)
                continue

            for feed in feeds[: self.config.max_per_keyword]:
                fi = _feed_to_item(feed)
                if fi is None or fi.url in seen_urls:
                    continue
                if self.config.detail_cache is not None:
                    fi = self._enrich_with_detail(fi, feed, scripts, env_overrides)
                seen_urls.add(fi.url)
                items.append(fi)

        # Only escalate to bridge-level failure if EVERY keyword failed
        # AND we got nothing — otherwise return whatever succeeded.
        if not items and len(keyword_failures) == len(self.config.keywords):
            raise FetchError(
                f"all {len(keyword_failures)} XHS keywords failed: {keyword_failures}"
            )

        return items

    def _search_one(
        self,
        search_sh: Path,
        keyword: str,
        env_overrides: dict[str, str],
    ) -> list[dict[str, Any]]:
        """Run search.sh and parse stdout JSON."""
        import os

        env = os.environ.copy()
        env.update(env_overrides)
        try:
            result = subprocess.run(
                [str(search_sh), keyword],
                capture_output=True,
                text=True,
                timeout=self.config.timeout_seconds,
                env=env,
                check=False,
            )
        except subprocess.TimeoutExpired as e:
            raise FetchError(f"xhs search.sh timed out for {keyword!r}") from e

        if result.returncode != 0:
            raise FetchError(
                f"xhs search.sh exit {result.returncode}: {result.stderr[:300]}"
            )

        try:
            outer = json.loads(result.stdout or "{}")
        except json.JSONDecodeError as e:
            raise FetchError(f"xhs search.sh non-json: {result.stdout[:200]}") from e

        # MCP JSON-RPC response shape:
        #   { "result": { "content": [{ "type": "text", "text": "<stringified JSON>" }] } }
        # The inner stringified JSON contains {"feeds": [...]}.
        try:
            content = outer.get("result", {}).get("content", [])
            text_block = next(
                (c for c in content if isinstance(c, dict) and c.get("type") == "text"),
                None,
            )
            if not text_block:
                return []
            inner = json.loads(text_block.get("text", "{}"))
        except (json.JSONDecodeError, AttributeError, TypeError) as e:
            raise FetchError(f"xhs search.sh inner json parse failed: {e}") from e

        feeds = inner.get("feeds")
        if not isinstance(feeds, list):
            return []
        return feeds

    def _enrich_with_detail(
        self,
        fi: FetchedItem,
        feed: dict[str, Any],
        scripts_dir: Path,
        env_overrides: dict[str, str],
    ) -> FetchedItem:
        """Replace fi.content with the full note body, using cache if available.

        Fail-soft: any error (cache miss + subprocess failure, parse failure)
        returns the original fi unchanged. Detail fetch must never break a fetch run.
        """
        cache = self.config.detail_cache
        if cache is None:
            return fi
        feed_id = feed.get("id")
        xsec = feed.get("xsecToken")
        if not isinstance(feed_id, str) or not isinstance(xsec, str):
            return fi

        cached = cache.get(feed_id)
        if cached:
            return dataclasses.replace(fi, content=cached)

        detail_sh = scripts_dir / "post-detail.sh"
        if not detail_sh.exists():
            log.warning("post-detail.sh missing at %s; skipping detail enrich", detail_sh)
            return fi
        title, content = _fetch_detail(
            detail_sh, feed_id, xsec, env_overrides, self.config.detail_timeout_seconds
        )
        if content is None:
            return fi
        try:
            cache.put(feed_id=feed_id, xsec_token=xsec, title=title, content=content)
        except Exception as e:  # cache write failure must not fail the fetch
            log.warning("xhs detail cache.put failed for %s: %r", feed_id, e)
        return dataclasses.replace(fi, content=content)


def _fetch_detail(
    detail_sh: Path,
    feed_id: str,
    xsec_token: str,
    env_overrides: dict[str, str],
    timeout_seconds: float,
) -> tuple[str | None, str | None]:
    """Run post-detail.sh and parse out (title, content). Returns (None, None) on any failure."""
    import os

    env = os.environ.copy()
    env.update(env_overrides)
    try:
        result = subprocess.run(
            [str(detail_sh), feed_id, xsec_token],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            env=env,
            check=False,
        )
    except subprocess.TimeoutExpired:
        log.warning("xhs post-detail.sh timed out for feed %s", feed_id)
        return (None, None)

    if result.returncode != 0:
        log.warning(
            "xhs post-detail.sh exit %d for feed %s: %s",
            result.returncode,
            feed_id,
            result.stderr[:200],
        )
        return (None, None)

    try:
        outer = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        log.warning("xhs post-detail.sh non-json for feed %s", feed_id)
        return (None, None)

    res = outer.get("result") or {}
    if res.get("isError"):
        # MCP-side error (e.g. note not found, login expired) — log and skip.
        log.info("xhs post-detail isError for feed %s", feed_id)
        return (None, None)

    content_blocks = res.get("content") or []
    text_block = next(
        (c for c in content_blocks if isinstance(c, dict) and c.get("type") == "text"),
        None,
    )
    if not text_block:
        return (None, None)
    text_str = text_block.get("text")
    if not isinstance(text_str, str):
        return (None, None)
    try:
        inner = json.loads(text_str)
    except json.JSONDecodeError:
        return (None, None)

    return _extract_detail_fields(inner)


def _extract_detail_fields(inner: object) -> tuple[str | None, str | None]:
    """Try common shapes that XHS MCP returns for note detail.

    Tries inner directly, then inner['feed'], inner['data'], inner['note'].
    For each, looks for noteCard subdict (or treats the candidate as the noteCard).
    Picks first 'desc' / 'content' field found, with title alongside.
    """
    if not isinstance(inner, dict):
        return (None, None)
    candidates: list[dict[str, Any]] = [inner]
    for key in ("feed", "data", "note"):
        sub = inner.get(key)
        if isinstance(sub, dict):
            candidates.append(sub)
    for c in candidates:
        nc = c.get("noteCard") if isinstance(c.get("noteCard"), dict) else c
        if not isinstance(nc, dict):
            continue
        title_raw = nc.get("title") or nc.get("displayTitle")
        content_raw = nc.get("desc") or nc.get("content")
        title = title_raw if isinstance(title_raw, str) and title_raw.strip() else None
        content = (
            content_raw if isinstance(content_raw, str) and content_raw.strip() else None
        )
        if content:
            return (title, content)
    return (None, None)


def _feed_to_item(feed: dict[str, Any]) -> FetchedItem | None:
    """Normalize one XHS feed entry into FetchedItem.

    Per skill docs the structure is `{ id, xsecToken, noteCard: {title, ...} }`.
    XHS notes don't have public stable URLs, so we synthesize one from id+token.
    """
    feed_id = feed.get("id")
    xsec = feed.get("xsecToken")
    if not feed_id or not xsec:
        return None
    note_card = feed.get("noteCard") or {}
    title = note_card.get("displayTitle") or note_card.get("title") or ""
    user = (note_card.get("user") or {}).get("nickname")

    # Synthetic URL — stable per (feed_id, xsec) for dedup purposes.
    url = f"https://www.xiaohongshu.com/explore/{feed_id}?xsec_token={xsec}"

    return FetchedItem(
        url=url,
        title=str(title),
        content=note_card.get("desc"),  # XHS preview text if present
        author=user,
        published_at=_parse_xhs_time(note_card.get("createTime")),
    )


def _parse_xhs_time(value: object) -> datetime | None:
    """XHS createTime is a millisecond unix epoch."""
    if not isinstance(value, (int, float)):
        return None
    try:
        return datetime.fromtimestamp(value / 1000, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None
