from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from digest.classifier import Classification, EventMetadata
from digest.event_radar import RadarConfig, is_quiet_hours, run_radar
from digest.store import (
    init_schema,
    insert_item_if_new,
    open_db,
    upsert_source,
)

CN_TZ = ZoneInfo("Asia/Shanghai")


@dataclass
class FakeClassifier:
    """Returns a pre-baked Classification per (title) lookup."""

    by_title: dict[str, Classification]

    def classify(self, *, title: str, content: str | None) -> Classification:
        return self.by_title.get(
            title, Classification(kind="other", event_metadata=None)
        )


def _make_db(tmp_path: Path) -> Path:
    db = tmp_path / "test.db"
    init_schema(db)
    return db


def _seed_item(conn, *, source_id: str, url: str, title: str, content: str = "") -> str:
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
        content=content,
        author=None,
        published_at=None,
        fetched_at=datetime.now(CN_TZ),
    )
    cur = conn.execute(
        "SELECT id FROM items WHERE source_id = ? AND url = ?", (source_id, url)
    )
    return cur.fetchone()["id"]


# ---------- quiet hours ----------


@pytest.mark.parametrize(
    ("hour", "minute", "expected"),
    [
        (23, 0, True),
        (23, 59, True),
        (0, 0, True),
        (3, 30, True),
        (6, 59, True),
        (7, 0, False),
        (8, 0, False),
        (12, 0, False),
        (22, 0, False),
        (22, 59, False),
    ],
)
def test_quiet_hours_wrapping_window(hour: int, minute: int, expected: bool) -> None:
    now = datetime(2026, 5, 4, hour, minute, tzinfo=CN_TZ)
    assert is_quiet_hours(now, start=23, end=7) is expected


def test_quiet_hours_non_wrapping_window() -> None:
    """Non-wrapping window e.g. 13:00-15:00 (sanity check, not the spec config)."""
    assert is_quiet_hours(datetime(2026, 5, 4, 13, 0, tzinfo=CN_TZ), start=13, end=15)
    assert not is_quiet_hours(datetime(2026, 5, 4, 15, 0, tzinfo=CN_TZ), start=13, end=15)
    assert not is_quiet_hours(datetime(2026, 5, 4, 12, 59, tzinfo=CN_TZ), start=13, end=15)


# ---------- run_radar phases ----------


def test_radar_classifies_pending_items(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    with open_db(db) as conn:
        _seed_item(conn, source_id="s1", url="https://e.com/a", title="news A")
        _seed_item(conn, source_id="s1", url="https://e.com/b", title="event B")

    classifier = FakeClassifier(
        by_title={
            "news A": Classification(kind="news", event_metadata=None),
            "event B": Classification(
                kind="event",
                event_metadata=EventMetadata(
                    event_date="2026-06-15",
                    registration_deadline="2026-06-10",
                    location="深圳",
                    registration_url="https://x/sign",
                ),
            ),
        }
    )
    config = RadarConfig(feishu_webhook_url="https://feishu/x")

    with patch("digest.event_radar.push_card") as mock_push, open_db(db) as conn:
        result = run_radar(
            conn,
            classifier,
            config,
            now=datetime(2026, 5, 4, 9, 0, tzinfo=CN_TZ),
        )

    assert result.classified == 2
    assert result.events_found == 1
    assert result.events_pushed == 1
    assert result.skipped_quiet is False
    mock_push.assert_called_once()

    # Verify DB state
    with open_db(db) as conn:
        rows = conn.execute("SELECT title, kind FROM items ORDER BY title").fetchall()
        assert {(r["title"], r["kind"]) for r in rows} == {
            ("news A", "news"),
            ("event B", "event"),
        }
        em = conn.execute("SELECT * FROM event_metadata").fetchone()
        # SQLite DATE columns round-trip via PARSE_DECLTYPES into date objects.
        assert str(em["event_date"]) == "2026-06-15"
        assert em["location"] == "深圳"


def test_radar_idempotent_does_not_double_push(tmp_path: Path) -> None:
    """Running radar twice with same fresh event must push only once."""
    db = _make_db(tmp_path)
    with open_db(db) as conn:
        _seed_item(conn, source_id="s1", url="https://e.com/a", title="event A")
    classifier = FakeClassifier(
        by_title={
            "event A": Classification(
                kind="event",
                event_metadata=EventMetadata(
                    event_date="2026-06-15",
                    registration_deadline=None,
                    location=None,
                    registration_url=None,
                ),
            )
        }
    )
    config = RadarConfig(feishu_webhook_url="https://feishu/x")

    with patch("digest.event_radar.push_card") as mock_push:
        with open_db(db) as conn:
            run_radar(conn, classifier, config, now=datetime(2026, 5, 4, 9, 0, tzinfo=CN_TZ))
        with open_db(db) as conn:
            second = run_radar(
                conn, classifier, config, now=datetime(2026, 5, 4, 10, 0, tzinfo=CN_TZ)
            )

    assert mock_push.call_count == 1
    assert second.events_pushed == 0
    assert second.events_found == 0


def test_radar_skips_past_events_keeps_future_and_undated(tmp_path: Path) -> None:
    """Only events that haven't started yet (or have no date) get pushed.

    Past events (event_date < today) sit in DB but never make the card.
    Undated events are kept because XHS often posts long-running 招募 / coffee
    chat with no explicit date.
    """
    db = _make_db(tmp_path)
    with open_db(db) as conn:
        _seed_item(conn, source_id="s1", url="https://e.com/past", title="past event")
        _seed_item(conn, source_id="s1", url="https://e.com/future", title="future event")
        _seed_item(conn, source_id="s1", url="https://e.com/undated", title="undated event")
        _seed_item(conn, source_id="s1", url="https://e.com/today", title="today event")

    classifier = FakeClassifier(
        by_title={
            "past event": Classification(
                kind="event",
                event_metadata=EventMetadata(
                    event_date="2020-01-01",  # long past
                    registration_deadline=None,
                    location=None,
                    registration_url=None,
                ),
            ),
            "future event": Classification(
                kind="event",
                event_metadata=EventMetadata(
                    event_date="2030-12-31",  # far future
                    registration_deadline=None,
                    location=None,
                    registration_url=None,
                ),
            ),
            "undated event": Classification(
                kind="event",
                event_metadata=EventMetadata(
                    event_date=None,
                    registration_deadline=None,
                    location=None,
                    registration_url=None,
                ),
            ),
            "today event": Classification(
                kind="event",
                event_metadata=EventMetadata(
                    event_date="2026-05-04",  # exactly "today" — boundary case
                    registration_deadline=None,
                    location=None,
                    registration_url=None,
                ),
            ),
        }
    )
    config = RadarConfig(feishu_webhook_url="https://feishu/x")
    today = datetime(2026, 5, 4, 9, 0, tzinfo=CN_TZ)

    with patch("digest.event_radar.push_card") as mock_push, open_db(db) as conn:
        result = run_radar(conn, classifier, config, now=today)

    # past event excluded; future + undated + today (>= today) included
    assert result.events_found == 3
    assert result.events_pushed == 3
    mock_push.assert_called_once()

    # The past event is still classified as 'event' in the DB — just not pushed.
    with open_db(db) as conn:
        rows = conn.execute(
            "SELECT title, kind FROM items ORDER BY title"
        ).fetchall()
        assert {(r["title"], r["kind"]) for r in rows} == {
            ("past event", "event"),
            ("future event", "event"),
            ("undated event", "event"),
            ("today event", "event"),
        }
        # past event has no event_pushes row
        n_past_pushes = conn.execute(
            """
            SELECT count(*) FROM event_pushes ep
              JOIN items i ON i.id=ep.item_id
             WHERE i.title='past event'
            """
        ).fetchone()[0]
        assert n_past_pushes == 0


def test_radar_same_day_repush_does_not_break_on_unique_constraint(
    tmp_path: Path,
) -> None:
    """Regression: same-day second push used to FK-fail on event_pushes.digest_id.

    Sequence that broke prod:
      1. radar pushes event A → creates today's event_batch digest D1, marks A pushed.
      2. fetch adds new event B (still unpushed).
      3. radar runs again → tries to insert a new digest for today → silently
         IGNORE'd by UNIQUE(digest_date, kind) → record_event_push references
         orphan uuid → FOREIGN KEY constraint failed.
    Fix: upsert_event_batch_digest reuses the existing digest row's id.
    """
    db = _make_db(tmp_path)
    with open_db(db) as conn:
        _seed_item(conn, source_id="s1", url="https://e.com/a", title="event A")
    classifier = FakeClassifier(
        by_title={
            "event A": Classification(
                kind="event",
                event_metadata=EventMetadata(
                    event_date="2026-06-15",
                    registration_deadline=None,
                    location=None,
                    registration_url=None,
                ),
            ),
            "event B": Classification(
                kind="event",
                event_metadata=EventMetadata(
                    event_date="2026-07-20",
                    registration_deadline=None,
                    location=None,
                    registration_url=None,
                ),
            ),
        }
    )
    config = RadarConfig(feishu_webhook_url="https://feishu/x")
    today = datetime(2026, 5, 4, 9, 0, tzinfo=CN_TZ)

    # First run: push A
    with patch("digest.event_radar.push_card") as mock_push, open_db(db) as conn:
        run_radar(conn, classifier, config, now=today)
    assert mock_push.call_count == 1

    # New item arrives later same day
    with open_db(db) as conn:
        _seed_item(conn, source_id="s1", url="https://e.com/b", title="event B")

    # Second run: must not FK-error; must push the new event B
    with patch("digest.event_radar.push_card") as mock_push, open_db(db) as conn:
        result = run_radar(
            conn, classifier, config, now=today.replace(hour=15)
        )
    assert mock_push.call_count == 1
    assert result.events_pushed == 1
    assert result.events_found == 1

    # Both items end up recorded as pushed; only ONE digest row exists for today.
    with open_db(db) as conn:
        n_pushes = conn.execute("SELECT count(*) FROM event_pushes").fetchone()[0]
        n_digests = conn.execute(
            "SELECT count(*) FROM digests WHERE digest_date='2026-05-04' AND kind='event_batch'"
        ).fetchone()[0]
    assert n_pushes == 2
    assert n_digests == 1


def test_radar_quiet_hours_skips_push_but_classifies(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    with open_db(db) as conn:
        _seed_item(conn, source_id="s1", url="https://e.com/a", title="event A")

    classifier = FakeClassifier(
        by_title={
            "event A": Classification(
                kind="event",
                event_metadata=EventMetadata(
                    event_date="2026-06-15",
                    registration_deadline=None,
                    location=None,
                    registration_url=None,
                ),
            )
        }
    )
    config = RadarConfig(feishu_webhook_url="https://feishu/x")

    with patch("digest.event_radar.push_card") as mock_push, open_db(db) as conn:
        result = run_radar(
            conn,
            classifier,
            config,
            now=datetime(2026, 5, 4, 2, 30, tzinfo=CN_TZ),  # quiet
        )

    assert result.classified == 1
    assert result.events_found == 1
    assert result.events_pushed == 0
    assert result.skipped_quiet is True
    mock_push.assert_not_called()

    # Next run at 8am should push the queued event
    with patch("digest.event_radar.push_card") as mock_push, open_db(db) as conn:
        result2 = run_radar(
            conn,
            classifier,
            config,
            now=datetime(2026, 5, 4, 8, 0, tzinfo=CN_TZ),
        )
    assert result2.events_pushed == 1
    mock_push.assert_called_once()


def test_radar_classifier_failure_is_isolated(tmp_path: Path) -> None:
    """One classifier exception must not kill the whole tick."""
    db = _make_db(tmp_path)
    with open_db(db) as conn:
        _seed_item(conn, source_id="s1", url="https://e.com/a", title="boom")
        _seed_item(conn, source_id="s1", url="https://e.com/b", title="ok")

    class FlakyClassifier:
        def classify(self, *, title: str, content: str | None) -> Classification:
            if title == "boom":
                raise RuntimeError("oops")
            return Classification(kind="news", event_metadata=None)

    config = RadarConfig(feishu_webhook_url="https://feishu/x")
    with patch("digest.event_radar.push_card"), open_db(db) as conn:
        result = run_radar(
            conn,
            FlakyClassifier(),
            config,
            now=datetime(2026, 5, 4, 9, 0, tzinfo=CN_TZ),
        )

    assert result.classified == 1  # only "ok" succeeded


def test_radar_no_events_no_push(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    with open_db(db) as conn:
        _seed_item(conn, source_id="s1", url="https://e.com/a", title="news only")

    classifier = FakeClassifier(
        by_title={"news only": Classification(kind="news", event_metadata=None)}
    )
    config = RadarConfig(feishu_webhook_url="https://feishu/x")

    with patch("digest.event_radar.push_card") as mock_push, open_db(db) as conn:
        result = run_radar(
            conn,
            classifier,
            config,
            now=datetime(2026, 5, 4, 9, 0, tzinfo=CN_TZ),
        )

    assert result.events_pushed == 0
    mock_push.assert_not_called()
