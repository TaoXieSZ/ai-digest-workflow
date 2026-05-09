"""Sync event-items from store -> Feishu Calendar.

Pure orchestration: reads pending events from the store, formats them into
calendar payloads, calls a Feishu client to create all-day events, and writes
back to the idempotency ledger. The client is injected so tests don't touch
the network.

Description policy: registration_deadline / registration_url / location go in
the event description (per user choice "in_description" — we don't pollute
the start_date, which has to match the actual event day).

Undated event handling (opt-in via include_undated=True):
- LLM classifier sometimes labels an item kind='event' but can't extract a
  date (empty content from solidot, or a roundup post listing many events).
- With include_undated, those rows get a placeholder date = today + 7 days
  (a uniform "review zone"), title prefix '[日期待定]', and a description
  banner. This makes them visible in the calendar without polluting actual
  upcoming events.
- KNOWN LIMIT: if the LLM later extracts a real date, the placeholder is NOT
  auto-updated (would need an extra column tracking "synced as placeholder"
  + Feishu update_event API). To switch a placeholder to its real date today,
  delete the Feishu event manually + delete the row from feishu_calendar_events.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Protocol

from .feishu_calendar import CreateEventResult, FeishuCalendarError
from .store import get_unsynced_calendar_events, record_calendar_sync

log = logging.getLogger("calendar_sync")

PLACEHOLDER_OFFSET_DAYS = 7
PLACEHOLDER_TITLE_PREFIX = "[日期待定] "
PLACEHOLDER_DESCRIPTION_BANNER = "⚠️ 日期未提取，请查看原文确认"


@dataclass(frozen=True)
class PendingEvent:
    """Subset of fields needed to build a Feishu all-day event."""

    item_id: str
    title: str
    url: str
    event_date: str  # ISO YYYY-MM-DD; may be a synthesized placeholder
    registration_deadline: str | None
    location: str | None
    registration_url: str | None
    registration_contact: str | None = None
    is_placeholder_date: bool = False  # True if event_date was synthesized


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


def _coerce_iso_date(value: Any) -> str | None:
    """Best-effort 'YYYY-MM-DD' from a TEXT/TIMESTAMP/datetime column."""
    if value is None:
        return None
    if hasattr(value, "date"):  # datetime
        return str(value.date().isoformat())
    if hasattr(value, "isoformat"):  # date
        return str(value.isoformat())
    s = str(value)
    return s[:10] if len(s) >= 10 else None


def _placeholder_date(today: str, *, offset_days: int = PLACEHOLDER_OFFSET_DAYS) -> str:
    """Uniform placeholder for undated events: today + 7d. Predictable so users
    know to look at one calendar slot for "events the LLM couldn't date"."""
    return (date.fromisoformat(today) + timedelta(days=offset_days)).isoformat()


def _row_to_pending(row: sqlite3.Row, *, today: str) -> PendingEvent:
    raw_date = _coerce_iso_date(row["event_date"])
    contact = row["registration_contact"] if "registration_contact" in row.keys() else None
    if raw_date is None:
        return PendingEvent(
            item_id=str(row["id"]),
            title=str(row["title"] or "(untitled event)"),
            url=str(row["url"] or ""),
            event_date=_placeholder_date(today),
            registration_deadline=row["registration_deadline"],
            location=row["location"],
            registration_url=row["registration_url"],
            registration_contact=contact,
            is_placeholder_date=True,
        )
    return PendingEvent(
        item_id=str(row["id"]),
        title=str(row["title"] or "(untitled event)"),
        url=str(row["url"] or ""),
        event_date=raw_date,
        registration_deadline=row["registration_deadline"],
        location=row["location"],
        registration_url=row["registration_url"],
        registration_contact=contact,
    )


def build_event_title(ev: PendingEvent) -> str:
    """Display title — prefixes a placeholder marker when the date is fake so
    the user can spot it in month view."""
    if ev.is_placeholder_date:
        return f"{PLACEHOLDER_TITLE_PREFIX}{ev.title}"
    return ev.title


def build_event_description(ev: PendingEvent) -> str:
    """Pack registration_deadline / location / registration_url / source URL
    into a plain-text description. Feishu calendar description doesn't render
    markdown, so we use bullet lines. Placeholder events get a banner on top."""
    lines: list[str] = []
    if ev.is_placeholder_date:
        lines.append(PLACEHOLDER_DESCRIPTION_BANNER)
        lines.append("")
    if ev.registration_deadline:
        lines.append(f"报名截止: {ev.registration_deadline}")
    if ev.registration_url:
        lines.append(f"报名链接: {ev.registration_url}")
    elif ev.registration_contact:
        lines.append(f"报名方式: {ev.registration_contact}")
    if ev.location:
        lines.append(f"地点: {ev.location}")
    if any(line for line in lines if line and not line.startswith(PLACEHOLDER_DESCRIPTION_BANNER)):
        lines.append("")  # blank line between metadata and source
    if ev.url:
        lines.append(f"原文: {ev.url}")
    return "\n".join(lines) if lines else ev.url


def list_pending(
    conn: sqlite3.Connection,
    *,
    today: str,
    limit: int | None = None,
    include_undated: bool = False,
) -> list[PendingEvent]:
    rows = get_unsynced_calendar_events(conn, today=today, include_undated=include_undated)
    if limit is not None:
        rows = rows[:limit]
    return [_row_to_pending(r, today=today) for r in rows]


def sync_pending_events(
    *,
    conn: sqlite3.Connection,
    client: CalendarClient,
    calendar_id: str,
    today: str,
    now: datetime,
    limit: int | None = None,
    dry_run: bool = True,
    include_undated: bool = False,
) -> list[SyncResult]:
    """Push pending events. Each event is its own transaction so a single
    failure doesn't roll back the others.

    include_undated: opt in to also push event-items without an extracted
    event_date, using a synthesized placeholder (today + 7d) and a marked
    title/description so they're distinguishable from real upcoming events.
    """
    if not calendar_id:
        raise ValueError("calendar_id is required")

    pending = list_pending(conn, today=today, limit=limit, include_undated=include_undated)
    results: list[SyncResult] = []

    for ev in pending:
        display_title = build_event_title(ev)
        if dry_run:
            log.info(
                "[dry-run] would sync %s on %s: %s",
                ev.item_id,
                ev.event_date,
                display_title,
            )
            results.append(
                SyncResult(
                    item_id=ev.item_id,
                    title=display_title,
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
                summary=display_title,
                description=description,
                start_date=ev.event_date,
            )
        except FeishuCalendarError as e:
            log.warning("create_all_day_event failed for %s: %s", ev.item_id, e)
            results.append(
                SyncResult(
                    item_id=ev.item_id,
                    title=display_title,
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
                    title=display_title,
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
                title=display_title,
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
