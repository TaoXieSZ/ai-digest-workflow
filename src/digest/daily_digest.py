"""Daily digest orchestration: dedup → cluster → render → push → record.

Mirror of event_radar but for non-event items. One run produces at most one
daily digest card per (digest_date, kind='daily_digest') row.

Idempotency:
- digest row uses upsert_daily_digest (same fix as event_batch — ON CONFLICT
  DO UPDATE preserves the canonical id).
- topic + item_topic_assignment writes happen AFTER the Feishu push returns
  200; on push failure we leave the digest row with pushed_at=NULL and items
  unassigned, so the next run re-tries from scratch.
- A successful run also persists the markdown to data/digests/daily-{date}.md
  so the OMC wiki can ingest it later (no MCP coupling here).
"""

from __future__ import annotations

import logging
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .archive_notion import ArchiveItem, NotionClient, archive_items
from .classifier import LLMClient
from .cluster import ClusterInput, cluster
from .dedup import DedupItem, deduplicate
from .digest_builder import DigestItem, render_daily_digest
from .push_feishu import push_card, render_daily_digest_card
from .store import (
    get_digest_push_attempts,
    get_unclustered_non_event_items,
    mark_digest_pushed,
    mark_item_archived_to_notion,
    record_item_topic_assignment,
    record_topic,
    upsert_daily_digest,
)

log = logging.getLogger("daily_digest")
CN_TZ = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True)
class DigestConfig:
    feishu_webhook_url: str
    rolling_assignment_days: int = 7  # don't re-cluster items assigned in this window
    fetch_limit: int = 200  # cap LLM input size per run
    timezone: ZoneInfo = CN_TZ
    digest_dir: Path = Path("data/digests")  # markdown archive (wiki ingest source)
    # Notion archive — when both fields are set, each clustered item is also
    # written as a row in the configured Notion database.
    notion_token: str | None = None
    notion_database_id: str | None = None
    notion_retry_queue: Path = Path("data/notion_retry_queue.jsonl")


@dataclass(frozen=True)
class DigestResult:
    candidate_items: int
    after_dedup: int
    topics: int
    items_assigned: int
    pushed: bool
    notion_archived: int = 0
    notion_failed: int = 0


def run_daily_digest(
    conn: sqlite3.Connection,
    llm: LLMClient,
    config: DigestConfig,
    *,
    now: datetime | None = None,
) -> DigestResult:
    """Execute one daily digest cycle. Returns counts for observability."""
    now = now or datetime.now(config.timezone)
    digest_date = now.date().isoformat()

    # Phase 1: pull eligible items (classified non-event, not in a recent topic)
    cutoff = (now - timedelta(days=config.rolling_assignment_days)).date().isoformat()
    rows = get_unclustered_non_event_items(
        conn, exclude_assigned_since=cutoff, limit=config.fetch_limit
    )
    if not rows:
        log.info("no eligible items; skipping digest")
        return DigestResult(0, 0, 0, 0, pushed=False)

    # Phase 2: dedup
    dedup_inputs = [
        DedupItem(
            id=r["id"],
            title=r["title"] or "",
            canonical_url=r["url"],
            published_at=r["published_at"],
            content_len=len(r["content"] or ""),
        )
        for r in rows
    ]
    deduped = deduplicate(dedup_inputs)
    survivors = {it.id for it in deduped}
    rows_kept = [r for r in rows if r["id"] in survivors]

    # Phase 3: cluster
    cluster_inputs = [
        ClusterInput(
            item_id=r["id"], title=r["title"] or "", snippet=(r["content"] or "")[:200]
        )
        for r in rows_kept
    ]
    topics = cluster(cluster_inputs, llm)
    if not topics:
        log.info("LLM returned no topics; skipping push")
        return DigestResult(len(rows), len(rows_kept), 0, 0, pushed=False)

    # Phase 4: render
    item_lookup = {
        r["id"]: DigestItem(
            item_id=r["id"],
            title=r["title"] or "(无标题)",
            url=r["url"],
            source=r["source_id"],
            published_at=r["published_at"],
        )
        for r in rows_kept
    }
    digest_md = render_daily_digest(topics, item_lookup, digest_date=digest_date)
    if not digest_md:
        log.info("render produced empty markdown; skipping push")
        return DigestResult(len(rows), len(rows_kept), len(topics), 0, pushed=False)

    # Phase 5: upsert digest row BEFORE the network call (idempotency root)
    digest_id = upsert_daily_digest(
        conn,
        digest_date=digest_date,
        candidate_id=str(uuid.uuid4()),
        content_md=digest_md,
    )

    # Phase 6: push (header gets "#N" suffix on 2nd+ same-day push)
    attempt = get_digest_push_attempts(conn, digest_id=digest_id) + 1
    card = render_daily_digest_card(
        digest_md, digest_date=digest_date, attempt=attempt
    )
    push_card(config.feishu_webhook_url, card)  # raises FeishuPushError on failure

    # Phase 7: persist topic assignments + mark digest pushed (post-push only)
    mark_digest_pushed(conn, digest_id=digest_id, pushed_at=now)
    items_assigned = 0
    item_topic_label: dict[str, tuple[str, str]] = {}  # iid -> (topic_name, topic_summary)
    for topic in topics:
        topic_id = str(uuid.uuid4())
        record_topic(
            conn,
            topic_id=topic_id,
            name=topic.name,
            summary=topic.summary,
            digest_date=digest_date,
            created_at=now,
        )
        for iid in topic.item_ids:
            if iid not in item_lookup:
                continue  # cluster.py already filters but be defensive
            record_item_topic_assignment(
                conn, item_id=iid, topic_id=topic_id, digest_date=digest_date
            )
            item_topic_label[iid] = (topic.name, topic.summary)
            items_assigned += 1

    # Phase 7.5: optionally archive each clustered item to Notion.
    # Skipped silently if either token or db id is unset.
    notion_archived = 0
    notion_failed = 0
    if config.notion_token and config.notion_database_id:
        kind_lookup = {r["id"]: r["kind"] for r in rows_kept}
        archive_targets = [
            ArchiveItem(
                item_id=iid,
                title=item_lookup[iid].title,
                kind=kind_lookup.get(iid, "other"),
                url=item_lookup[iid].url,
                source=item_lookup[iid].source,
                summary=item_topic_label[iid][1],
                topic=item_topic_label[iid][0],
                digest_date=digest_date,
            )
            for iid in item_topic_label
        ]
        client = NotionClient(
            token=config.notion_token, database_id=config.notion_database_id
        )
        succ_ids: list[str] = []
        result = archive_items(
            client,
            archive_targets,
            retry_queue=config.notion_retry_queue,
            on_success=succ_ids,
        )
        notion_archived = result.succeeded
        notion_failed = result.failed
        for iid in succ_ids:
            mark_item_archived_to_notion(conn, item_id=iid, archived_at=now)

    # Phase 8: write markdown archive for wiki ingestion (best-effort)
    try:
        config.digest_dir.mkdir(parents=True, exist_ok=True)
        archive = config.digest_dir / f"daily-{digest_date}.md"
        archive.write_text(digest_md + "\n", encoding="utf-8")
    except OSError as e:
        log.warning("failed to write digest archive: %r", e)

    return DigestResult(
        candidate_items=len(rows),
        after_dedup=len(rows_kept),
        topics=len(topics),
        items_assigned=items_assigned,
        pushed=True,
        notion_archived=notion_archived,
        notion_failed=notion_failed,
    )
