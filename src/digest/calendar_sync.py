"""Sync event-items from store -> Feishu Calendar.

Pure orchestration: reads pending events from the store, formats them into
calendar payloads, calls a Feishu client to create all-day events, and writes
back to the idempotency ledger. The client is injected so tests don't touch
the network.

Description policy: registration_deadline / registration_url / location go in
the event description (per user choice "in_description" — we don't pollute
the start_date, which has to match the actual event day).
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from .feishu_calendar import CreateEventResult, FeishuCalendarError
from .store import get_unsynced_calendar_events, record_calendar_sync

log = logging.getLogger("calendar_sync")


@dataclass(frozen=True)
class PendingEvent:
    """Subset of fields needed to build a Feishu all-day event."""

    item_id: str
    title: str
    url: str
    event_date: str  # ISO YYYY-MM-DD; required (filtered upstream)
    registration_deadline: str | None
    location: str | None
    registration_url: str | None


@dataclass(frozen=True)
class SyncResult:
    item_id: str
    title: str
    event_date: str
    feishu_event_id: str | None  # None on dry-run or error
    error: str | None  # None == success or dry-run
    skipped_dry_run: bool = False

    @property
    def ok(self) -> bool:
        return self.error is None


class CalendarClient(Protocol):
    """Structural type matching the slice of FeishuCalendarClient we use.
    Lets tests inject a fake without inheriting from the real class."""

    def create_all_day_event(
        self,
        *,
        calendar_id: str,
        summary: str,
        description: str,
        start_date: str,
        end_date: str | None = ...,
        idempotency_key: str | None = ...,
    ) -> CreateEventResult: ...


def _row_to_pending(row: sqlite3.Row) -> PendingEvent:
    return PendingEvent(
        item_id=str(row["id"]),
        title=str(row["title"] or "(untitled event)"),
        url=str(row["url"] or ""),
        event_date=str(row["event_date"]),
        registration_deadline=row["registration_deadline"],
        location=row["location"],
        registration_url=row["registration_url"],
    )


def build_event_description(ev: PendingEvent) -> str:
    """Pack registration_deadline / location / registration_url / source URL
    into a plain-text description. Feishu calendar description doesn't render
    markdown, so we use bullet lines."""
    lines: list[str] = []
    if ev.registration_deadline:
        lines.append(f"报名截止: {ev.registration_deadline}")
    if ev.registration_url:
        lines.append(f"报名链接: {ev.registration_url}")
    if ev.location:
        lines.append(f"地点: {ev.location}")
    if lines:
        lines.append("")  # blank line between metadata and source
    if ev.url:
        lines.append(f"原文: {ev.url}")
    return "\n".join(lines) if lines else ev.url


def list_pending(
    conn: sqlite3.Connection,
    *,
    today: str,
    limit: int | None = None,
) -> list[PendingEvent]:
    rows = get_unsynced_calendar_events(conn, today=today)
    if limit is not None:
        rows = rows[:limit]
    return [_row_to_pending(r) for r in rows]


def sync_pending_events(
    *,
    conn: sqlite3.Connection,
    client: CalendarClient,
    calendar_id: str,
    today: str,
    now: datetime,
    limit: int | None = None,
    dry_run: bool = True,
) -> list[SyncResult]:
    """Push pending events. Each event is its own transaction so a single
    failure doesn't roll back the others."""
    if not calendar_id:
        raise ValueError("calendar_id is required")

    pending = list_pending(conn, today=today, limit=limit)
    results: list[SyncResult] = []

    for ev in pending:
        if dry_run:
            log.info("[dry-run] would sync %s on %s: %s", ev.item_id, ev.event_date, ev.title)
            results.append(
                SyncResult(
                    item_id=ev.item_id,
                    title=ev.title,
                    event_date=ev.event_date,
                    feishu_event_id=None,
                    error=None,
                    skipped_dry_run=True,
                )
            )
            continue

        description = build_event_description(ev)
        try:
            created = client.create_all_day_event(
                calendar_id=calendar_id,
                summary=ev.title,
                description=description,
                start_date=ev.event_date,
            )
        except FeishuCalendarError as e:
            log.warning("create_all_day_event failed for %s: %s", ev.item_id, e)
            results.append(
                SyncResult(
                    item_id=ev.item_id,
                    title=ev.title,
                    event_date=ev.event_date,
                    feishu_event_id=None,
                    error=str(e),
                )
            )
            continue
        except Exception as e:  # network / unexpected — don't crash the loop
            log.exception("unexpected error creating event for %s", ev.item_id)
            results.append(
                SyncResult(
                    item_id=ev.item_id,
                    title=ev.title,
                    event_date=ev.event_date,
                    feishu_event_id=None,
                    error=f"{type(e).__name__}: {e}",
                )
            )
            continue

        record_calendar_sync(
            conn,
            item_id=ev.item_id,
            calendar_id=calendar_id,
            feishu_event_id=created.event_id,
            synced_at=now,
        )
        conn.commit()
        results.append(
            SyncResult(
                item_id=ev.item_id,
                title=ev.title,
                event_date=ev.event_date,
                feishu_event_id=created.event_id,
                error=None,
            )
        )

    return results


def summarize(results: list[SyncResult]) -> dict[str, int]:
    """Aggregate counts for logging / dashboard display."""
    return {
        "total": len(results),
        "synced": sum(1 for r in results if r.ok and not r.skipped_dry_run),
        "dry_run": sum(1 for r in results if r.skipped_dry_run),
        "failed": sum(1 for r in results if not r.ok),
    }
