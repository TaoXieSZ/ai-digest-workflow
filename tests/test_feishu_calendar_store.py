"""Tests for feishu_calendar_events table + sync ledger helpers."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from digest.store import (
    get_unsynced_calendar_events,
    init_schema,
    insert_item_if_new,
    is_calendar_synced,
    open_db,
    record_calendar_sync,
    set_item_classification,
    upsert_event_metadata,
    upsert_source,
)


def _make_db(tmp_path: Path) -> Path:
    db = tmp_path / "store.db"
    init_schema(db)
    return db


def _seed_event(
    conn: sqlite3.Connection,
    *,
    source_id: str = "src",
    url: str,
    title: str,
    event_date: str | None,
    registration_deadline: str | None = None,
    location: str | None = None,
    registration_url: str | None = None,
) -> str:
    upsert_source(
        conn,
        source_id=source_id,
        display_name=source_id,
        fetcher_type="rss",
        config_json="{}",
    )
    insert_item_if_new(
        conn,
        source_id=source_id,
        canonical_url=url,
        raw_url=url,
        title=title,
        content=None,
        author=None,
        published_at=None,
        fetched_at=datetime.now(UTC),
    )
    iid = conn.execute(
        "SELECT id FROM items WHERE source_id=? AND url=?", (source_id, url)
    ).fetchone()["id"]
    set_item_classification(
        conn,
        item_id=iid,
        kind="event",
        classified_at=datetime.now(UTC),
    )
    upsert_event_metadata(
        conn,
        item_id=iid,
        event_date=event_date,
        registration_deadline=registration_deadline,
        location=location,
        registration_url=registration_url,
        extracted_at=datetime.now(UTC),
    )
    return str(iid)


def test_init_schema_creates_feishu_calendar_events_table(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    with open_db(db) as conn:
        cols = {
            row["name"]
            for row in conn.execute(
                "PRAGMA table_info(feishu_calendar_events)"
            ).fetchall()
        }
    assert cols == {"item_id", "calendar_id", "feishu_event_id", "synced_at"}


def test_record_and_lookup_sync(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    with open_db(db) as conn:
        iid = _seed_event(
            conn,
            url="https://e/a",
            title="Future event",
            event_date="2099-01-01",
        )
        assert not is_calendar_synced(conn, iid)
        record_calendar_sync(
            conn,
            item_id=iid,
            calendar_id="cal-1",
            feishu_event_id="ev-1",
            synced_at=datetime(2026, 5, 6, 12, tzinfo=UTC),
        )
        assert is_calendar_synced(conn, iid)


def test_record_calendar_sync_is_idempotent(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    with open_db(db) as conn:
        iid = _seed_event(
            conn, url="https://e/a", title="t", event_date="2099-01-01"
        )
        record_calendar_sync(
            conn,
            item_id=iid,
            calendar_id="cal-1",
            feishu_event_id="ev-1",
            synced_at=datetime(2026, 5, 6, 12, tzinfo=UTC),
        )
        record_calendar_sync(
            conn,
            item_id=iid,
            calendar_id="cal-2",
            feishu_event_id="ev-2",
            synced_at=datetime(2026, 5, 7, 12, tzinfo=UTC),
        )
        row = conn.execute(
            "SELECT calendar_id, feishu_event_id FROM feishu_calendar_events WHERE item_id=?",
            (iid,),
        ).fetchone()
    assert row["calendar_id"] == "cal-2"
    assert row["feishu_event_id"] == "ev-2"


def test_get_unsynced_filters_past_events(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    with open_db(db) as conn:
        iid_future = _seed_event(
            conn, url="https://e/f", title="future", event_date="2099-01-01"
        )
        _seed_event(
            conn, url="https://e/p", title="past", event_date="2024-01-01"
        )

    with open_db(db) as conn:
        rows = get_unsynced_calendar_events(conn, today="2026-05-06")
    assert {r["id"] for r in rows} == {iid_future}


def test_get_unsynced_excludes_already_synced(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    with open_db(db) as conn:
        iid_a = _seed_event(
            conn, url="https://e/a", title="A", event_date="2099-01-01"
        )
        iid_b = _seed_event(
            conn, url="https://e/b", title="B", event_date="2099-02-02"
        )
        record_calendar_sync(
            conn,
            item_id=iid_a,
            calendar_id="cal",
            feishu_event_id="ev-a",
            synced_at=datetime(2026, 5, 6, tzinfo=UTC),
        )

    with open_db(db) as conn:
        rows = get_unsynced_calendar_events(conn, today="2026-05-06")
    assert {r["id"] for r in rows} == {iid_b}


def test_get_unsynced_skips_items_without_event_date(tmp_path: Path) -> None:
    """Feishu all-day events require a start date — date-less rows are skipped."""
    db = _make_db(tmp_path)
    with open_db(db) as conn:
        _seed_event(conn, url="https://e/a", title="no-date", event_date=None)
        iid_dated = _seed_event(
            conn, url="https://e/b", title="dated", event_date="2099-01-01"
        )

    with open_db(db) as conn:
        rows = get_unsynced_calendar_events(conn, today="2026-05-06")
    assert {r["id"] for r in rows} == {iid_dated}


def test_get_unsynced_skips_non_event_kind(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    with open_db(db) as conn:
        upsert_source(
            conn, source_id="s", display_name="s", fetcher_type="rss", config_json="{}"
        )
        insert_item_if_new(
            conn,
            source_id="s",
            canonical_url="https://e/news",
            raw_url="https://e/news",
            title="news item",
            content=None,
            author=None,
            published_at=None,
            fetched_at=datetime.now(UTC),
        )
        nid = conn.execute(
            "SELECT id FROM items WHERE url='https://e/news'"
        ).fetchone()["id"]
        set_item_classification(
            conn, item_id=nid, kind="news", classified_at=datetime.now(UTC)
        )
        # Even if event_metadata is somehow attached, kind='news' must filter it out.
        upsert_event_metadata(
            conn,
            item_id=nid,
            event_date="2099-01-01",
            registration_deadline=None,
            location=None,
            registration_url=None,
            extracted_at=datetime.now(UTC),
        )

    with open_db(db) as conn:
        rows = get_unsynced_calendar_events(conn, today="2026-05-06")
    assert rows == []


def test_get_unsynced_orders_by_event_date_ascending(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    with open_db(db) as conn:
        iid_late = _seed_event(
            conn, url="https://e/late", title="late", event_date="2099-12-31"
        )
        iid_soon = _seed_event(
            conn, url="https://e/soon", title="soon", event_date="2099-01-01"
        )

    with open_db(db) as conn:
        rows = get_unsynced_calendar_events(conn, today="2026-05-06")
    assert [r["id"] for r in rows] == [iid_soon, iid_late]


def test_include_undated_returns_null_date_rows_after_dated(tmp_path: Path) -> None:
    """include_undated=True surfaces event_date IS NULL rows ordered last so
    callers can still process real upcoming events first."""
    db = _make_db(tmp_path)
    with open_db(db) as conn:
        iid_undated = _seed_event(
            conn, url="https://e/u", title="undated", event_date=None
        )
        iid_dated = _seed_event(
            conn, url="https://e/d", title="dated", event_date="2099-01-01"
        )

    with open_db(db) as conn:
        # default: undated excluded
        rows_default = get_unsynced_calendar_events(conn, today="2026-05-06")
        assert {r["id"] for r in rows_default} == {iid_dated}

        # include_undated: both present, dated first
        rows_all = get_unsynced_calendar_events(
            conn, today="2026-05-06", include_undated=True
        )
        assert [r["id"] for r in rows_all] == [iid_dated, iid_undated]
        assert rows_all[1]["event_date"] is None
