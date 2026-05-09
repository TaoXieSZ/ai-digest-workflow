"""Event radar orchestration: classify pending items, push fresh events.

Hourly entry point. For each unclassified item:
1. Run classifier
2. Persist kind + (if event) event_metadata
3. Collect fresh events not yet pushed → batch push to Feishu

Idempotency: `event_pushes(item_id)` is the source of truth. A crash mid-push
leaves NO record, so the next run re-attempts — but `digests` UUID + per-item
record_event_push happens AFTER successful 飞书 200, so no duplicate cards.

Quiet hours: 23:00-07:00 local (Asia/Shanghai). Inside the window, classification
still runs but no push happens — events queue up and go out at 07:00.
"""

from __future__ import annotations

import logging
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, time
from zoneinfo import ZoneInfo

from .classifier import Classifier
from .push_feishu import EventCardItem, push_card, render_event_batch_card
from .store import (
    get_digest_push_attempts,
    get_unclassified_items,
    get_unpushed_events,
    mark_digest_pushed,
    record_event_push,
    set_item_classification,
    upsert_event_batch_digest,
    upsert_event_metadata,
)

log = logging.getLogger("event_radar")

CN_TZ = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True)
class RadarConfig:
    feishu_webhook_url: str
    quiet_start_hour: int = 23  # inclusive
    quiet_end_hour: int = 7  # exclusive (so 07:00 is OK to push)
    classify_batch: int = 50
    classify_concurrency: int = 8  # parallel LLM calls in classifier.classify_many
    timezone: ZoneInfo = CN_TZ


@dataclass(frozen=True)
class RadarResult:
    classified: int
    events_found: int
    events_pushed: int
    skipped_quiet: bool


def is_quiet_hours(now: datetime, *, start: int, end: int) -> bool:
    """True iff `now` (tz-aware) falls within the [start, end) silent window.

    The window wraps midnight when start > end (e.g. 23-07).
    """
    t = now.time()
    s, e = time(hour=start), time(hour=end)
    if start < end:
        return s <= t < e
    # wraps midnight
    return t >= s or t < e


def run_radar(
    conn: sqlite3.Connection,
    classifier: Classifier,
    config: RadarConfig,
    *,
    now: datetime | None = None,
) -> RadarResult:
    """One hourly tick: classify pending items + push fresh events if not silent."""
    now = now or datetime.now(config.timezone)

    # Phase 1: classify (concurrent — N items in parallel via ThreadPoolExecutor)
    pending = get_unclassified_items(conn, limit=config.classify_batch)
    inputs = [(r["title"] or "", r["content"]) for r in pending]
    results = classifier.classify_many(inputs, concurrency=config.classify_concurrency)

    classified = 0
    for row, cls in zip(pending, results, strict=True):
        if cls is None:
            log.warning("classifier failed for item %s; leaving unclassified", row["id"])
            continue

        set_item_classification(
            conn,
            item_id=row["id"],
            kind=cls.kind,
            classified_at=now,
        )
        if cls.kind == "event" and cls.event_metadata is not None:
            em = cls.event_metadata
            upsert_event_metadata(
                conn,
                item_id=row["id"],
                event_date=em.event_date,
                registration_deadline=em.registration_deadline,
                location=em.location,
                registration_url=em.registration_url,
                registration_contact=em.registration_contact,
                extracted_at=now,
            )
        classified += 1

    # Phase 2: collect fresh events that haven't started yet
    # (event_date >= today; events without a date are kept — they are usually
    # long-running posts like 招募 / coffee chat that don't have a fixed date)
    fresh = get_unpushed_events(conn, today=now.date().isoformat())
    events_found = len(fresh)

    # Phase 3: silent window check
    if is_quiet_hours(now, start=config.quiet_start_hour, end=config.quiet_end_hour):
        log.info(
            "quiet hours (%02d:00-%02d:00); deferring %d events",
            config.quiet_start_hour,
            config.quiet_end_hour,
            events_found,
        )
        return RadarResult(
            classified=classified,
            events_found=events_found,
            events_pushed=0,
            skipped_quiet=True,
        )

    if events_found == 0:
        return RadarResult(
            classified=classified,
            events_found=0,
            events_pushed=0,
            skipped_quiet=False,
        )

    # Phase 4: push as one batched card
    card_items = [
        EventCardItem(
            title=r["title"] or "(无标题)",
            source=r["source_id"],
            url=r["url"],
            event_date=r["event_date"],
            registration_deadline=r["registration_deadline"],
            location=r["location"],
            registration_url=r["registration_url"],
            registration_contact=r["registration_contact"],
            published_at=r["published_at"],
        )
        for r in fresh
    ]
    digest_date = now.date().isoformat()

    # Upsert FIRST so we have a digest_id to query push_attempts on; same-day
    # repushes reuse the row to avoid the UNIQUE(digest_date, kind) FK trap.
    # We seed content_md with a placeholder; the real card_payload string is
    # set right after we know the attempt number.
    digest_id = upsert_event_batch_digest(
        conn,
        digest_date=digest_date,
        candidate_id=str(uuid.uuid4()),
        content_md="",  # placeholder; overwritten below
    )
    attempt = get_digest_push_attempts(conn, digest_id=digest_id) + 1
    card_payload = render_event_batch_card(card_items, digest_date=digest_date, attempt=attempt)
    # Refresh the stored markdown with the actual rendered card.
    upsert_event_batch_digest(
        conn,
        digest_date=digest_date,
        candidate_id=digest_id,
        content_md=str(card_payload),
    )

    push_card(config.feishu_webhook_url, card_payload)

    # Only mark items as pushed AFTER 飞书 returns 200 (FeishuPushError otherwise re-raises).
    mark_digest_pushed(conn, digest_id=digest_id, pushed_at=now)
    for r in fresh:
        record_event_push(
            conn,
            item_id=r["id"],
            pushed_at=now,
            digest_id=digest_id,
        )

    return RadarResult(
        classified=classified,
        events_found=events_found,
        events_pushed=events_found,
        skipped_quiet=False,
    )
