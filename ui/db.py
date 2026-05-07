"""Read-only SQLite queries for the local dashboard."""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from render import render_markdown

CN_TZ = ZoneInfo("Asia/Shanghai")
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "items.db"


@dataclass(frozen=True)
class DigestEntry:
    id: str
    digest_date: str
    kind: str
    content_md: str
    content_html: str
    pushed_at: str | None
    archived_at: str | None
    push_attempts: int
    archive_attempts: int


@dataclass(frozen=True)
class TopicItem:
    id: str
    title: str
    url: str
    kind: str
    source: str
    published_at: str | None
    fetched_at: str | None
    notion_archived_at: str | None


@dataclass
class TopicGroup:
    id: str
    name: str
    summary: str | None
    created_at: str | None
    items: list[TopicItem] = field(default_factory=list)


@dataclass(frozen=True)
class EventItem:
    id: str
    title: str
    url: str
    source: str
    published_at: str | None
    fetched_at: str | None
    event_date: str | None
    registration_deadline: str | None
    location: str | None
    registration_url: str | None
    pushed_at: str | None


@dataclass(frozen=True)
class CalendarSyncedEvent:
    id: str
    title: str
    event_date: str | None
    feishu_event_id: str
    synced_at: str | None


@dataclass(frozen=True)
class CalendarPendingEvent:
    id: str
    title: str
    event_date: str
    registration_deadline: str | None


@dataclass(frozen=True)
class CalendarSyncStatus:
    table_exists: bool
    synced_today: list[CalendarSyncedEvent]
    pending_upcoming: list[CalendarPendingEvent]


@dataclass(frozen=True)
class DigestDashboard:
    target_date: date
    db_path: Path
    db_exists: bool
    db_error: str | None
    digests: dict[str, DigestEntry]
    topics: list[TopicGroup]
    events: list[EventItem]
    recent_dates: list[str]
    calendar: CalendarSyncStatus

    @property
    def previous_date(self) -> date:
        return self.target_date - timedelta(days=1)

    @property
    def next_date(self) -> date:
        return self.target_date + timedelta(days=1)

    @property
    def has_data(self) -> bool:
        return bool(self.digests or self.topics or self.events)


def today_cn() -> date:
    return datetime.now(CN_TZ).date()


def configured_db_path() -> Path:
    override = os.environ.get("AI_DIGEST_DB_PATH")
    if override:
        return Path(override).expanduser()
    return DEFAULT_DB_PATH


@contextmanager
def open_readonly_db(db_path: Path | None = None) -> Iterator[sqlite3.Connection]:
    path = (db_path or configured_db_path()).resolve()
    uri = f"file:{path.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def load_digest_dashboard(target_date: date | None = None) -> DigestDashboard:
    selected_date = target_date or today_cn()
    db_path = configured_db_path()
    if not db_path.exists():
        return _empty_dashboard(
            selected_date,
            db_path,
            db_exists=False,
            db_error=f"Database not found at {db_path}",
        )

    try:
        with open_readonly_db(db_path) as conn:
            return DigestDashboard(
                target_date=selected_date,
                db_path=db_path,
                db_exists=True,
                db_error=None,
                digests=_load_digests(conn, selected_date),
                topics=_load_topics(conn, selected_date),
                events=_load_events(conn, selected_date),
                recent_dates=_load_recent_dates(conn),
                calendar=_load_calendar_status(conn, selected_date),
            )
    except sqlite3.Error as exc:
        return _empty_dashboard(
            selected_date,
            db_path,
            db_exists=True,
            db_error=f"Could not read dashboard data: {exc}",
        )


def _empty_dashboard(
    target_date: date, db_path: Path, *, db_exists: bool, db_error: str | None
) -> DigestDashboard:
    return DigestDashboard(
        target_date=target_date,
        db_path=db_path,
        db_exists=db_exists,
        db_error=db_error,
        digests={},
        topics=[],
        events=[],
        recent_dates=[],
        calendar=CalendarSyncStatus(table_exists=False, synced_today=[], pending_upcoming=[]),
    )


def _load_digests(conn: sqlite3.Connection, target_date: date) -> dict[str, DigestEntry]:
    rows = conn.execute(
        """
        SELECT id, digest_date, kind, content_md, pushed_at, archived_at,
               push_attempts, archive_attempts
          FROM digests
         WHERE digest_date = ?
         ORDER BY kind
        """,
        (target_date.isoformat(),),
    ).fetchall()
    entries: dict[str, DigestEntry] = {}
    for row in rows:
        content_md = str(row["content_md"] or "")
        entries[str(row["kind"])] = DigestEntry(
            id=str(row["id"]),
            digest_date=_as_text(row["digest_date"]) or target_date.isoformat(),
            kind=str(row["kind"]),
            content_md=content_md,
            content_html=render_markdown(content_md),
            pushed_at=_as_text(row["pushed_at"]),
            archived_at=_as_text(row["archived_at"]),
            push_attempts=_as_int(row["push_attempts"]),
            archive_attempts=_as_int(row["archive_attempts"]),
        )
    return entries


def _load_topics(conn: sqlite3.Connection, target_date: date) -> list[TopicGroup]:
    rows = conn.execute(
        """
        SELECT t.id AS topic_id, t.name, t.summary, t.created_at,
               i.id AS item_id, i.title, i.url, i.kind, i.published_at, i.fetched_at,
               i.notion_archived_at,
               COALESCE(s.display_name, i.source_id) AS source_name
          FROM topics t
          LEFT JOIN item_topic_assignments ita
                 ON ita.topic_id = t.id
                AND ita.digest_date = t.digest_date
          LEFT JOIN items i ON i.id = ita.item_id
          LEFT JOIN sources s ON s.id = i.source_id
         WHERE t.digest_date = ?
         ORDER BY t.created_at ASC,
                  (i.published_at IS NULL) ASC,
                  i.published_at DESC,
                  i.fetched_at DESC
        """,
        (target_date.isoformat(),),
    ).fetchall()

    by_topic: dict[str, TopicGroup] = {}
    for row in rows:
        topic_id = str(row["topic_id"])
        group = by_topic.setdefault(
            topic_id,
            TopicGroup(
                id=topic_id,
                name=str(row["name"]),
                summary=_as_text(row["summary"]),
                created_at=_as_text(row["created_at"]),
            ),
        )
        if row["item_id"] is None:
            continue
        group.items.append(
            TopicItem(
                id=str(row["item_id"]),
                title=str(row["title"] or "(untitled)"),
                url=str(row["url"] or ""),
                kind=str(row["kind"] or "unclassified"),
                source=str(row["source_name"] or "unknown"),
                published_at=_as_text(row["published_at"]),
                fetched_at=_as_text(row["fetched_at"]),
                notion_archived_at=_as_text(row["notion_archived_at"]),
            )
        )
    return list(by_topic.values())


def _load_events(conn: sqlite3.Connection, target_date: date) -> list[EventItem]:
    rows = conn.execute(
        """
        SELECT i.id, i.title, i.url, i.published_at, i.fetched_at,
               COALESCE(s.display_name, i.source_id) AS source_name,
               em.event_date, em.registration_deadline, em.location, em.registration_url,
               ep.pushed_at AS event_pushed_at
          FROM items i
          LEFT JOIN sources s ON s.id = i.source_id
          LEFT JOIN event_metadata em ON em.item_id = i.id
          LEFT JOIN event_pushes ep ON ep.item_id = i.id
          LEFT JOIN digests d ON d.id = ep.digest_id
         WHERE i.kind = 'event'
           AND (
                date(i.published_at) = ?
             OR date(i.fetched_at) = ?
             OR d.digest_date = ?
           )
         ORDER BY (i.published_at IS NULL) ASC,
                  i.published_at DESC,
                  i.fetched_at DESC
        """,
        (target_date.isoformat(), target_date.isoformat(), target_date.isoformat()),
    ).fetchall()

    return [
        EventItem(
            id=str(row["id"]),
            title=str(row["title"] or "(untitled)"),
            url=str(row["url"] or ""),
            source=str(row["source_name"] or "unknown"),
            published_at=_as_text(row["published_at"]),
            fetched_at=_as_text(row["fetched_at"]),
            event_date=_as_text(row["event_date"]),
            registration_deadline=_as_text(row["registration_deadline"]),
            location=_as_text(row["location"]),
            registration_url=_as_text(row["registration_url"]),
            pushed_at=_as_text(row["event_pushed_at"]),
        )
        for row in rows
    ]


def _load_calendar_status(
    conn: sqlite3.Connection, target_date: date
) -> CalendarSyncStatus:
    """Calendar sync state for the target_date (synced) + pending upcoming
    events. Tolerates legacy DBs missing the feishu_calendar_events table
    (calendar sync hasn't run yet)."""
    table_exists = (
        conn.execute(
            "SELECT 1 FROM sqlite_master "
            "WHERE type='table' AND name='feishu_calendar_events'"
        ).fetchone()
        is not None
    )
    if not table_exists:
        return CalendarSyncStatus(
            table_exists=False, synced_today=[], pending_upcoming=[]
        )

    iso = target_date.isoformat()
    synced_rows = conn.execute(
        """
        SELECT i.id, i.title, em.event_date, fce.feishu_event_id, fce.synced_at
          FROM feishu_calendar_events fce
          JOIN items i ON i.id = fce.item_id
          LEFT JOIN event_metadata em ON em.item_id = i.id
         WHERE date(fce.synced_at) = ?
         ORDER BY fce.synced_at DESC
        """,
        (iso,),
    ).fetchall()
    synced = [
        CalendarSyncedEvent(
            id=str(r["id"]),
            title=str(r["title"] or "(untitled)"),
            event_date=_as_text(r["event_date"]),
            feishu_event_id=str(r["feishu_event_id"]),
            synced_at=_as_text(r["synced_at"]),
        )
        for r in synced_rows
    ]

    pending_rows = conn.execute(
        """
        SELECT i.id, i.title, em.event_date, em.registration_deadline
          FROM items i
          JOIN event_metadata em ON em.item_id = i.id
          LEFT JOIN feishu_calendar_events fce ON fce.item_id = i.id
         WHERE i.kind = 'event'
           AND fce.item_id IS NULL
           AND em.event_date IS NOT NULL
           AND em.event_date >= ?
         ORDER BY em.event_date ASC
        """,
        (iso,),
    ).fetchall()
    pending = [
        CalendarPendingEvent(
            id=str(r["id"]),
            title=str(r["title"] or "(untitled)"),
            event_date=str(r["event_date"]),
            registration_deadline=_as_text(r["registration_deadline"]),
        )
        for r in pending_rows
    ]

    return CalendarSyncStatus(
        table_exists=True, synced_today=synced, pending_upcoming=pending
    )


def _load_recent_dates(conn: sqlite3.Connection, limit: int = 14) -> list[str]:
    rows = conn.execute(
        """
        SELECT digest_date
          FROM (
                SELECT digest_date FROM digests
                UNION
                SELECT digest_date FROM topics
          )
         ORDER BY digest_date DESC
         LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [str(row["digest_date"]) for row in rows]


def _as_text(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _as_int(value: Any) -> int:
    if value is None:
        return 0
    return int(value)
