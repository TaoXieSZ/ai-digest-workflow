"""Notion DB archiving for digested items.

Each item becomes one row in the Notion database. Properties match the schema
created by `scripts/setup_notion_db.py`:
- Title, kind, digest_date, source, url, summary, topic

Idempotency:
- Items already archived (items.notion_archived_at IS NOT NULL) are skipped
  by the caller; this module is unaware of DB state.
- A failed POST is appended to a JSONL retry queue; a future
  `scripts/replay_notion_queue.py` (out of scope here) drains it.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

NOTION_API = "https://api.notion.com/v1/pages"
NOTION_VERSION = "2022-06-28"
DEFAULT_TIMEOUT = 30.0
TEXT_LIMIT = 2000  # Notion rich_text per-block limit; truncate to be safe.

log = logging.getLogger("archive_notion")


class NotionArchiveError(Exception):
    """Raised on transport or 4xx/5xx from Notion."""


@dataclass(frozen=True)
class ArchiveItem:
    """One row to write to the Notion DB. All strings should be display-ready."""

    item_id: str
    title: str
    kind: str  # event | news | tool | other
    url: str
    source: str
    summary: str
    topic: str  # topic label or "" for events
    digest_date: str  # ISO YYYY-MM-DD


@dataclass(frozen=True)
class ArchiveResult:
    succeeded: int
    failed: int
    queued_for_retry: int


class NotionClient:
    """Thin httpx wrapper for the create-page endpoint we need.

    Not a full SDK. Caller is responsible for providing a valid database_id
    that has the schema produced by setup_notion_db.py.
    """

    def __init__(
        self,
        *,
        token: str,
        database_id: str,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self._token = token
        self._database_id = database_id
        self._timeout = timeout

    def create_page(self, item: ArchiveItem) -> str:
        """POST /v1/pages with the item payload. Returns the new Notion page id.

        Raises NotionArchiveError on transport failure or non-200 status.
        """
        payload = _build_payload(self._database_id, item)
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        }
        try:
            resp = httpx.post(NOTION_API, json=payload, headers=headers, timeout=self._timeout)
        except httpx.RequestError as e:
            raise NotionArchiveError(f"network error: {e!r}") from e

        if resp.status_code != 200:
            raise NotionArchiveError(f"notion http {resp.status_code}: {resp.text[:300]}")
        body = _parse_json_safe(resp)
        page_id = body.get("id")
        if not isinstance(page_id, str):
            raise NotionArchiveError(f"notion response missing 'id': {body}")
        return page_id


def archive_items(
    client: NotionClient,
    items: list[ArchiveItem],
    *,
    retry_queue: Path | None = None,
    on_success: list[str] | None = None,  # mutable list collects item_ids of successes
) -> ArchiveResult:
    """Archive a batch of items. Failures are queued; successes are reported.

    Returns counts. The `on_success` list (if passed) is mutated to add the
    item_id of each successful archive — caller uses this to mark
    items.notion_archived_at and avoid re-archiving on the next run.
    """
    succeeded = 0
    failed = 0
    queued = 0
    for item in items:
        try:
            client.create_page(item)
        except NotionArchiveError as e:
            failed += 1
            log.warning("archive failed for %s: %r", item.item_id, e)
            if retry_queue is not None:
                _enqueue_for_retry(retry_queue, item, repr(e))
                queued += 1
            continue
        succeeded += 1
        if on_success is not None:
            on_success.append(item.item_id)
    return ArchiveResult(succeeded=succeeded, failed=failed, queued_for_retry=queued)


def _enqueue_for_retry(path: Path, item: ArchiveItem, error: str) -> None:
    """Append one JSONL row capturing the failed item + error context."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(
            {
                "ts": datetime.utcnow().isoformat() + "Z",
                "error": error,
                "item": {
                    "item_id": item.item_id,
                    "title": item.title,
                    "kind": item.kind,
                    "url": item.url,
                    "source": item.source,
                    "summary": item.summary,
                    "topic": item.topic,
                    "digest_date": item.digest_date,
                },
            },
            ensure_ascii=False,
        )
        with path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError as e:  # disk full / perms — log + drop, do not raise
        log.error("retry queue write failed: %r (item %s)", e, item.item_id)


def _build_payload(database_id: str, item: ArchiveItem) -> dict[str, Any]:
    """Construct the Notion /v1/pages POST body for one item."""
    properties: dict[str, Any] = {
        "Title": {"title": [{"type": "text", "text": {"content": _trim(item.title)}}]},
        "url": {"url": item.url or None},
        "source": _rich_text(item.source),
        "summary": _rich_text(item.summary),
        "topic": _rich_text(item.topic),
        "digest_date": {"date": {"start": item.digest_date}},
    }
    if item.kind:
        properties["kind"] = {"select": {"name": item.kind}}
    return {
        "parent": {"database_id": database_id},
        "properties": properties,
    }


def _rich_text(s: str) -> dict[str, Any]:
    """Build a Notion rich_text property; empty string → empty array."""
    if not s:
        return {"rich_text": []}
    return {"rich_text": [{"type": "text", "text": {"content": _trim(s)}}]}


def _trim(s: str) -> str:
    """Notion rejects rich_text content > 2000 chars per block."""
    return s[:TEXT_LIMIT]


def _parse_json_safe(response: httpx.Response) -> dict[str, Any]:
    try:
        data = response.json()
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}
