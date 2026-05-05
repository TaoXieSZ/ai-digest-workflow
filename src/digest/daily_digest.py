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

from .classifier import LLMClient
from .cluster import ClusterInput, cluster
from .dedup import DedupItem, deduplicate
from .digest_builder import DigestItem, render_daily_digest
from .push_feishu import push_card, render_daily_digest_card
from .store import (
    get_unclustered_non_event_items,
    mark_digest_pushed,
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
    fetch_limit: int = 50  # cap LLM input size per run
    timezone: ZoneInfo = CN_TZ
    digest_dir: Path = Path("data/digests")  # markdown archive (wiki ingest source)


@dataclass(frozen=True)
class DigestResult:
    candidate_items: int
    after_dedup: int
    topics: int
    items_assigned: int
    pushed: bool


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

    # Phase 6: push
    card = render_daily_digest_card(digest_md, digest_date=digest_date)
    push_card(config.feishu_webhook_url, card)  # raises FeishuPushError on failure

    # Phase 7: persist topic assignments + mark digest pushed (post-push only)
    mark_digest_pushed(conn, digest_id=digest_id, pushed_at=now)
    items_assigned = 0
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
            items_assigned += 1

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
    )
