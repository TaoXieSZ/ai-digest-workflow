"""Tests for digest.calendar_sync (Feishu calendar sync orchestration)."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from digest.calendar_sync import (
    PLACEHOLDER_DESCRIPTION_BANNER,
    PLACEHOLDER_OFFSET_DAYS,
    PLACEHOLDER_TITLE_PREFIX,
    PendingEvent,
    build_event_description,
    build_event_title,
    list_pending,
    summarize,
    sync_pending_events,
)
from digest.feishu_calendar import CreateEventResult, FeishuCalendarError
from digest.store import (
    init_schema,
    insert_item_if_new,
    is_calendar_synced,
    open_db,
    set_item_classification,
    upsert_event_metadata,
    upsert_source,
)


@dataclass
class _FakeClient:
    """Records calls; lets tests script success / failure per item."""

    next_event_id: str = "ev-default"
    fail_with: Exception | None = None
    calls: list[dict[str, Any]] = field(default_factory=list)

    def create_all_day_event(
        self,
        *,
        calendar_id: str,
        summary: str,
        description: str,
        start_date: str,
        end_date: str | None = None,
        idempotency_key: str | None = None,
    ) -> CreateEventResult:
        self.calls.append(
            {
                "calendar_id": calendar_id,
                "summary": summary,
                "description": description,
                "start_date": start_date,
                "end_date": end_date,
                "idempotency_key": idempotency_key,
            }
        )
        if self.fail_with:
            raise self.fail_with
        return CreateEventResult(event_id=self.next_event_id, raw={})


# ---- helpers --------------------------------------------------------------


def _seed_event_item(
    conn: sqlite3.Connection,
    *,
    url: str,
    title: str,
    event_date: str,
    registration_deadline: str | None = None,
    location: str | None = None,
    registration_url: str | None = None,
) -> str:
    upsert_source(
        conn,
        source_id="src",
        display_name="src",
        fetcher_type="rss",
        config_json="{}",
    )
    insert_item_if_new(
        conn,
        source_id="src",
        canonical_url=url,
        raw_url=url,
        title=title,
        content=None,
        author=None,
        published_at=None,
        fetched_at=datetime.now(UTC),
    )
    iid = conn.execute("SELECT id FROM items WHERE url=?", (url,)).fetchone()["id"]
    set_item_classification(
        conn, item_id=iid, kind="event", classified_at=datetime.now(UTC)
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


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    p = tmp_path / "items.db"
    init_schema(p)
    return p


# ---- description formatting ----------------------------------------------


def test_build_description_full_metadata() -> None:
    ev = PendingEvent(
        item_id="i",
        title="t",
        url="https://example.com/article",
        event_date="2099-06-01",
        registration_deadline="2099-05-30",
        location="深圳",
        registration_url="https://example.com/signup",
    )
    desc = build_event_description(ev)
    assert "报名截止: 2099-05-30" in desc
    assert "报名链接: https://example.com/signup" in desc
    assert "地点: 深圳" in desc
    assert "原文: https://example.com/article" in desc
    # blank line between metadata block and source link
    assert "\n\n原文:" in desc


def test_build_description_only_url_when_metadata_missing() -> None:
    ev = PendingEvent(
        item_id="i",
        title="t",
        url="https://example.com/x",
        event_date="2099-06-01",
        registration_deadline=None,
        location=None,
        registration_url=None,
    )
    assert build_event_description(ev) == "原文: https://example.com/x"


def test_build_description_empty_when_no_url_and_no_metadata() -> None:
    """Defensive: items without URL or metadata still produce a string (event
    title alone is the only signal — empty description is fine)."""
    ev = PendingEvent(
        item_id="i",
        title="t",
        url="",
        event_date="2099-06-01",
        registration_deadline=None,
        location=None,
        registration_url=None,
    )
    assert build_event_description(ev) == ""


def test_build_description_uses_contact_fallback_when_no_url() -> None:
    """No registration_url but contact present → description shows '报名方式: <contact>'."""
    ev = PendingEvent(
        item_id="i",
        title="t",
        url="https://example.com/article",
        event_date="2099-06-01",
        registration_deadline=None,
        location="线上",
        registration_url=None,
        registration_contact="加微信 abc123",
    )
    desc = build_event_description(ev)
    assert "报名方式: 加微信 abc123" in desc
    assert "报名链接:" not in desc


def test_build_description_url_wins_over_contact() -> None:
    ev = PendingEvent(
        item_id="i",
        title="t",
        url="https://example.com/article",
        event_date="2099-06-01",
        registration_deadline=None,
        location=None,
        registration_url="https://example.com/signup",
        registration_contact="私信博主",
    )
    desc = build_event_description(ev)
    assert "报名链接: https://example.com/signup" in desc
    assert "报名方式:" not in desc  # suppressed when url present


# ---- list_pending --------------------------------------------------------


def test_list_pending_returns_pending_events_ordered(db: Path) -> None:
    with open_db(db) as conn:
        iid_late = _seed_event_item(
            conn, url="https://e/late", title="late", event_date="2099-12-01"
        )
        iid_soon = _seed_event_item(
            conn, url="https://e/soon", title="soon", event_date="2099-01-01"
        )
    with open_db(db) as conn:
        events = list_pending(conn, today="2026-05-06")
    assert [e.item_id for e in events] == [iid_soon, iid_late]


def test_list_pending_respects_limit(db: Path) -> None:
    with open_db(db) as conn:
        _seed_event_item(conn, url="https://e/a", title="a", event_date="2099-01-01")
        _seed_event_item(conn, url="https://e/b", title="b", event_date="2099-02-01")
        _seed_event_item(conn, url="https://e/c", title="c", event_date="2099-03-01")
    with open_db(db) as conn:
        events = list_pending(conn, today="2026-05-06", limit=2)
    assert len(events) == 2


# ---- sync_pending_events -------------------------------------------------


def test_dry_run_does_not_call_client_or_write_ledger(db: Path) -> None:
    with open_db(db) as conn:
        iid = _seed_event_item(
            conn, url="https://e/a", title="A", event_date="2099-01-01"
        )
    client = _FakeClient()
    with open_db(db) as conn:
        results = sync_pending_events(
            conn=conn,
            client=client,
            calendar_id="cal",
            today="2026-05-06",
            now=datetime(2026, 5, 6, 12, tzinfo=UTC),
            dry_run=True,
        )
    assert client.calls == []
    assert len(results) == 1 and results[0].skipped_dry_run and results[0].ok
    with open_db(db) as conn:
        assert not is_calendar_synced(conn, iid)


def test_confirm_creates_event_and_records_ledger(db: Path) -> None:
    with open_db(db) as conn:
        iid = _seed_event_item(
            conn,
            url="https://e/a",
            title="WAIC 2099",
            event_date="2099-06-01",
            registration_deadline="2099-05-25",
            location="上海",
            registration_url="https://e/signup",
        )
    client = _FakeClient(next_event_id="feishu-evt-1")
    with open_db(db) as conn:
        results = sync_pending_events(
            conn=conn,
            client=client,
            calendar_id="cal-X",
            today="2026-05-06",
            now=datetime(2026, 5, 6, 12, tzinfo=UTC),
            dry_run=False,
        )
    assert len(client.calls) == 1
    call = client.calls[0]
    assert call["calendar_id"] == "cal-X"
    assert call["summary"] == "WAIC 2099"
    assert call["start_date"] == "2099-06-01"
    assert "报名截止: 2099-05-25" in call["description"]
    assert "上海" in call["description"]
    assert "原文: https://e/a" in call["description"]

    assert results[0].ok and results[0].feishu_event_id == "feishu-evt-1"
    with open_db(db) as conn:
        assert is_calendar_synced(conn, iid)


def test_failure_does_not_write_ledger_and_continues(db: Path) -> None:
    with open_db(db) as conn:
        iid_a = _seed_event_item(
            conn, url="https://e/a", title="A", event_date="2099-01-01"
        )
        iid_b = _seed_event_item(
            conn, url="https://e/b", title="B", event_date="2099-02-01"
        )

    # Fail every call: confirms loop continues and ledger stays clean.
    client = _FakeClient(fail_with=FeishuCalendarError("boom"))
    with open_db(db) as conn:
        results = sync_pending_events(
            conn=conn,
            client=client,
            calendar_id="cal",
            today="2026-05-06",
            now=datetime(2026, 5, 6, 12, tzinfo=UTC),
            dry_run=False,
        )

    assert len(results) == 2
    assert all(not r.ok for r in results)
    assert all(r.error == "boom" for r in results)
    with open_db(db) as conn:
        assert not is_calendar_synced(conn, iid_a)
        assert not is_calendar_synced(conn, iid_b)


def test_unexpected_exception_caught_per_item(db: Path) -> None:
    with open_db(db) as conn:
        _seed_event_item(conn, url="https://e/a", title="A", event_date="2099-01-01")
    client = _FakeClient(fail_with=RuntimeError("network died"))
    with open_db(db) as conn:
        results = sync_pending_events(
            conn=conn,
            client=client,
            calendar_id="cal",
            today="2026-05-06",
            now=datetime(2026, 5, 6, 12, tzinfo=UTC),
            dry_run=False,
        )
    assert results[0].error and "RuntimeError" in results[0].error


def test_re_run_after_confirm_is_no_op(db: Path) -> None:
    """End-to-end idempotency: same DB, two confirm runs → second sees nothing."""
    with open_db(db) as conn:
        _seed_event_item(conn, url="https://e/a", title="A", event_date="2099-01-01")
    client = _FakeClient(next_event_id="ev-1")
    with open_db(db) as conn:
        first = sync_pending_events(
            conn=conn,
            client=client,
            calendar_id="cal",
            today="2026-05-06",
            now=datetime(2026, 5, 6, 12, tzinfo=UTC),
            dry_run=False,
        )
    assert len(first) == 1 and first[0].ok

    client2 = _FakeClient(next_event_id="should-not-be-called")
    with open_db(db) as conn:
        second = sync_pending_events(
            conn=conn,
            client=client2,
            calendar_id="cal",
            today="2026-05-06",
            now=datetime(2026, 5, 6, 13, tzinfo=UTC),
            dry_run=False,
        )
    assert second == []
    assert client2.calls == []


def test_calendar_id_required(db: Path) -> None:
    with open_db(db) as conn:
        with pytest.raises(ValueError, match="calendar_id is required"):
            sync_pending_events(
                conn=conn,
                client=_FakeClient(),
                calendar_id="",
                today="2026-05-06",
                now=datetime(2026, 5, 6, tzinfo=UTC),
            )


# ---- include_undated (placeholder) mode --------------------------------


def _seed_undated_event_item(
    conn: sqlite3.Connection,
    *,
    url: str,
    title: str,
) -> str:
    """Like _seed_event_item but explicitly leaves event_date NULL (LLM
    couldn't extract it — e.g. solidot summary, title-only XHS post)."""
    upsert_source(
        conn,
        source_id="src",
        display_name="src",
        fetcher_type="rss",
        config_json="{}",
    )
    insert_item_if_new(
        conn,
        source_id="src",
        canonical_url=url,
        raw_url=url,
        title=title,
        content=None,
        author=None,
        published_at=None,
        fetched_at=datetime.now(UTC),
    )
    iid = conn.execute("SELECT id FROM items WHERE url=?", (url,)).fetchone()["id"]
    set_item_classification(
        conn, item_id=iid, kind="event", classified_at=datetime.now(UTC)
    )
    upsert_event_metadata(
        conn,
        item_id=iid,
        event_date=None,
        registration_deadline=None,
        location=None,
        registration_url=None,
        extracted_at=datetime.now(UTC),
    )
    return str(iid)


def test_default_excludes_undated_events(db: Path) -> None:
    """Backwards compat: undated events stay invisible without the flag."""
    with open_db(db) as conn:
        _seed_undated_event_item(conn, url="https://e/u", title="undated")
        iid_dated = _seed_event_item(
            conn, url="https://e/d", title="dated", event_date="2099-01-01"
        )
    with open_db(db) as conn:
        events = list_pending(conn, today="2026-05-06")
    assert [e.item_id for e in events] == [iid_dated]
    assert not events[0].is_placeholder_date


def test_include_undated_synthesizes_placeholder(db: Path) -> None:
    with open_db(db) as conn:
        iid_undated = _seed_undated_event_item(
            conn, url="https://e/u", title="Cursor Meetup Shenzhen来啦"
        )
    with open_db(db) as conn:
        events = list_pending(conn, today="2026-05-07", include_undated=True)
    assert len(events) == 1
    ev = events[0]
    assert ev.item_id == iid_undated
    assert ev.is_placeholder_date is True
    # placeholder = today + PLACEHOLDER_OFFSET_DAYS
    assert ev.event_date == "2026-05-14"


def test_include_undated_orders_dated_first_then_undated(db: Path) -> None:
    """Dated upcoming events must come before placeholders so the next-up
    real event is created first; undated rows tail the queue."""
    with open_db(db) as conn:
        _seed_undated_event_item(conn, url="https://e/u", title="undated")
        iid_far = _seed_event_item(
            conn, url="https://e/far", title="far", event_date="2099-12-01"
        )
        iid_soon = _seed_event_item(
            conn, url="https://e/soon", title="soon", event_date="2099-01-01"
        )
    with open_db(db) as conn:
        events = list_pending(conn, today="2026-05-06", include_undated=True)
    # all 3 returned, dated first by date, undated last
    assert [e.item_id for e in events][:2] == [iid_soon, iid_far]
    assert events[-1].is_placeholder_date is True


def test_build_title_prefixes_placeholder_marker() -> None:
    real = PendingEvent(
        item_id="i", title="Real Event", url="u",
        event_date="2099-01-01", registration_deadline=None,
        location=None, registration_url=None,
    )
    placeholder = PendingEvent(
        item_id="i", title="Cursor Meetup Shenzhen来啦", url="u",
        event_date="2026-05-14", registration_deadline=None,
        location=None, registration_url=None,
        is_placeholder_date=True,
    )
    assert build_event_title(real) == "Real Event"
    assert build_event_title(placeholder).startswith(PLACEHOLDER_TITLE_PREFIX)
    assert "Cursor Meetup" in build_event_title(placeholder)


def test_build_description_adds_banner_for_placeholder() -> None:
    ev = PendingEvent(
        item_id="i", title="t", url="https://e/x",
        event_date="2026-05-14", registration_deadline=None,
        location="深圳", registration_url=None,
        is_placeholder_date=True,
    )
    desc = build_event_description(ev)
    assert PLACEHOLDER_DESCRIPTION_BANNER in desc
    # banner must be on the first line (visible at a glance)
    assert desc.splitlines()[0] == PLACEHOLDER_DESCRIPTION_BANNER
    # other metadata + url still rendered
    assert "地点: 深圳" in desc
    assert "原文: https://e/x" in desc


def test_confirm_with_include_undated_pushes_placeholder_event(db: Path) -> None:
    """End-to-end: undated row gets pushed with placeholder date + marked
    title, ledger records it, re-run is no-op."""
    with open_db(db) as conn:
        iid = _seed_undated_event_item(
            conn, url="https://e/x", title="Cursor Meetup Shenzhen来啦"
        )
    client = _FakeClient(next_event_id="ph-1")
    with open_db(db) as conn:
        results = sync_pending_events(
            conn=conn,
            client=client,
            calendar_id="cal",
            today="2026-05-07",
            now=datetime(2026, 5, 7, 12, tzinfo=UTC),
            dry_run=False,
            include_undated=True,
        )

    assert len(client.calls) == 1
    call = client.calls[0]
    assert call["start_date"] == "2026-05-14"  # placeholder = today + 7
    assert call["summary"].startswith(PLACEHOLDER_TITLE_PREFIX)
    assert call["summary"].endswith("Cursor Meetup Shenzhen来啦")
    assert PLACEHOLDER_DESCRIPTION_BANNER in call["description"]

    assert results[0].ok and results[0].feishu_event_id == "ph-1"
    # idempotency: re-run with the same flag picks up nothing
    with open_db(db) as conn:
        again = sync_pending_events(
            conn=conn,
            client=_FakeClient(next_event_id="should-not-call"),
            calendar_id="cal",
            today="2026-05-07",
            now=datetime(2026, 5, 7, 13, tzinfo=UTC),
            dry_run=False,
            include_undated=True,
        )
    assert again == []
    # also: subsequent run WITHOUT the flag still sees nothing for this id
    with open_db(db) as conn:
        no_flag = sync_pending_events(
            conn=conn,
            client=_FakeClient(),
            calendar_id="cal",
            today="2026-05-07",
            now=datetime(2026, 5, 7, 14, tzinfo=UTC),
            dry_run=False,
        )
    assert all(r.item_id != iid for r in no_flag)


def test_placeholder_offset_constant_is_used() -> None:
    """Lock the policy so a code refactor doesn't silently change the slot."""
    assert PLACEHOLDER_OFFSET_DAYS == 7


def test_summarize_counts() -> None:
    from digest.calendar_sync import SyncResult

    rs = [
        SyncResult("a", "A", "2099-01-01", "ev-a", None),
        SyncResult("b", "B", "2099-01-02", None, "boom"),
        SyncResult("c", "C", "2099-01-03", None, None, skipped_dry_run=True),
    ]
    s = summarize(rs)
    assert s == {"total": 3, "synced": 1, "dry_run": 1, "failed": 1}
